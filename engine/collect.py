#!/usr/bin/env python3
"""
Daily collector for the AI model price index.

Primary source is the OpenRouter public models API: ~445 models with
structured per-token pricing, context length, modality and deprecation
dates, no auth required. Secondary source is a set of first-party vendor
pricing pages, fingerprinted so we can say "Anthropic touched their pricing
page on date X" even when we cannot parse it.

The asset being built here is the TIME SERIES. Any single day's snapshot is
replaceable; years of daily diffs is not. Everything below optimises for
never missing a day and never silently recording garbage.

stdlib + certifi only.
"""
import json, hashlib, time, datetime, pathlib, sys, re, os
import urllib.request, urllib.error, urllib.robotparser
from urllib.parse import urlparse

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "models.json"
SNAPDIR = ROOT / "data" / "snapshots"
CHANGES = ROOT / "data" / "changes.jsonl"

OPENROUTER_API = "https://openrouter.ai/api/v1/models"
UA = "ai-price-index/0.1 (daily price tracker; 1 request per source per day)"
TIMEOUT = 30
DELAY = 4.0

# --- TLS: use certifi when present (Windows boxes often ship no CA file at
# all); fall back to system defaults, which is what CI runners have. ---
try:
    import ssl, certifi
    SSLCTX = ssl.create_default_context(cafile=certifi.where())
except Exception:
    SSLCTX = None

_robots = {}


def robots_allows(url):
    p = urlparse(url)
    base = p.scheme + "://" + p.netloc
    if base not in _robots:
        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(base + "/robots.txt")
        try:
            rp.read()
        except Exception:
            rp = None
        _robots[base] = rp
    rp = _robots[base]
    if rp is None:
        return True
    try:
        return rp.can_fetch(UA, url)
    except Exception:
        return False


def fetch(url, check_robots=True):
    if check_robots and not robots_allows(url):
        return None, "blocked by robots.txt"
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept": "application/json,text/html", "Accept-Language": "en"})
    try:
        kw = {"timeout": TIMEOUT}
        if SSLCTX:
            kw["context"] = SSLCTX
        with urllib.request.urlopen(req, **kw) as r:
            raw = r.read(12_000_000)
            enc = r.headers.get_content_charset() or "utf-8"
            return raw.decode(enc, errors="replace"), None
    except urllib.error.HTTPError as e:
        return None, "HTTP " + str(e.code)
    except Exception as e:
        return None, type(e).__name__ + ": " + str(e)


def to_per_million(v):
    """OpenRouter quotes USD per token as a string. Normalise to USD per 1M
    tokens, which is how every vendor actually advertises. Returns None for
    missing/unparseable/negative, never a bogus 0.0."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f < 0:
        return None
    return round(f * 1_000_000, 6)


def collect_openrouter():
    text, err = fetch(OPENROUTER_API, check_robots=False)  # documented public API
    if err:
        return None, err
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        return None, "bad JSON: " + str(e)
    rows = {}
    for m in data.get("data", []):
        mid = m.get("id")
        if not mid:
            continue
        p = m.get("pricing") or {}
        arch = m.get("architecture") or {}
        rows[mid] = {
            "name": m.get("name"),
            "slug": m.get("canonical_slug"),
            "in": to_per_million(p.get("prompt")),
            "out": to_per_million(p.get("completion")),
            "cache_read": to_per_million(p.get("input_cache_read")),
            "cache_write": to_per_million(p.get("input_cache_write")),
            "ctx": m.get("context_length"),
            "modality": arch.get("modality"),
            "created": m.get("created"),
            "cutoff": m.get("knowledge_cutoff"),
            "expires": m.get("expiration_date"),
        }
    return rows, None


TAG_RE = re.compile(r"<(script|style|noscript)\b.*?</\1>", re.S | re.I)
ANGLE_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")
ENTITIES = [("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
            ("&#39;", "'"), ("&quot;", '"')]


def visible_text(html):
    t = TAG_RE.sub(" ", html)
    t = ANGLE_RE.sub(" ", t)
    for a, b in ENTITIES:
        t = t.replace(a, b)
    return WS_RE.sub(" ", t).strip()


def collect_vendor_pages(cfg):
    out = {}
    for key, v in cfg["vendors"].items():
        url = v["pricing_url"]
        text, err = fetch(url)
        if err:
            out[key] = {"ok": False, "error": err, "url": url}
            print("  [vendor] %-10s FAIL %s" % (key, err), flush=True)
        else:
            vis = visible_text(text)
            # A JS-only shell yields near-zero visible text; record it as a
            # miss rather than fingerprinting an empty string forever.
            if len(vis) < 200:
                out[key] = {"ok": False, "url": url,
                            "error": "no server-rendered text (%d chars)" % len(vis)}
                print("  [vendor] %-10s SKIP js-only shell" % key, flush=True)
            else:
                fp = hashlib.sha256(vis.encode()).hexdigest()[:16]
                out[key] = {"ok": True, "url": url, "text_len": len(vis), "fingerprint": fp}
                print("  [vendor] %-10s ok  fp=%s" % (key, fp), flush=True)
        time.sleep(DELAY)
    return out


STABLE_DAYS = 2
# Vendors whose OpenRouter price IS the vendor's own list price. Everything
# else (open-weight models served by many third-party hosts) has a market
# price that drifts daily with provider routing; the first week of data showed
# 140 "price changes" there, almost all oscillation, and zero movement across
# the 152 first-party models. Logging that drift as price changes would make
# the change log wrong in a way anyone could check against the vendor.
FIRST_PARTY = ("anthropic/", "openai/", "google/gemini", "x-ai/")


def is_alias(mid):
    """'~vendor/model-latest' ids are floating pointers, not models."""
    return mid.startswith("~") or "/~" in mid


def is_first_party(mid):
    return mid.startswith(FIRST_PARTY) and "gpt-oss" not in mid


class Confirmer:
    """A value only becomes the confirmed value once it has been observed on
    STABLE_DAYS consecutive snapshots, which filters out A-B-A flapping."""

    def __init__(self):
        self.conf, self.pend = {}, {}

    def seed(self, key, val):
        self.conf.setdefault(key, val)

    def observe(self, key, val, date):
        if key not in self.conf:
            self.conf[key] = val
            return None
        if val == self.conf[key]:
            self.pend.pop(key, None)
            return None
        p = self.pend.get(key)
        p = (val, p[1], p[2] + 1) if p and p[0] == val else (val, date, 1)
        self.pend[key] = p
        if p[2] < STABLE_DAYS:
            return None
        old = self.conf[key]
        self.conf[key] = val
        del self.pend[key]
        return p[1], old, val   # dated to the day the change first appeared


def derive_changes(snaps):
    """Rebuild the whole change log from the immutable snapshots. Deriving it
    rather than appending day by day means the rules can be improved later
    without leaving old and new events disagreeing."""
    by_date = {s["date"]: s for s in snaps}
    c = Confirmer()
    seen, last = set(), {}
    ev = []
    for i, s in enumerate(snaps):
        d, models = s["date"], s.get("models") or {}
        cur = {m for m in models if not is_alias(m)}

        for mid in sorted(seen | cur):
            key = (mid, "present")
            if i > 0:
                c.seed(key, False)          # appeared after day one
            r = c.observe(key, mid in cur, d)
            if r:
                day = r[0]
                if r[2]:
                    m = by_date[day]["models"].get(mid) or models.get(mid) or {}
                    ev.append({"date": day, "type": "model_added", "model": mid,
                               "name": m.get("name"), "in": m.get("in"), "out": m.get("out")})
                else:
                    ev.append({"date": day, "type": "model_removed", "model": mid,
                               "name": last.get(mid, {}).get("name")})
        seen |= cur

        for mid in cur:
            m = models[mid]
            last[mid] = m
            r = c.observe((mid, "expires"), m.get("expires"), d)
            # Far-future dates (e.g. 2098-12-31) are placeholders, not retirements.
            if r and r[1] is None and r[2] and int(str(r[2])[:4]) - int(d[:4]) <= 5:
                ev.append({"date": r[0], "type": "deprecation_announced", "model": mid,
                           "name": m.get("name"), "expires": r[2]})
            if not is_first_party(mid):
                continue
            for f in ("in", "out"):
                if m.get(f) is None:
                    continue
                r = c.observe((mid, f), m[f], d)
                if r:
                    x, y = r[1], r[2]
                    ev.append({"date": r[0], "type": "price_changed", "model": mid,
                               "name": m.get("name"), "field": f, "from": x, "to": y,
                               "pct": round((y - x) / x * 100, 2) if x else None})
            if m.get("ctx"):
                r = c.observe((mid, "ctx"), m["ctx"], d)
                if r:
                    ev.append({"date": r[0], "type": "context_changed", "model": mid,
                               "name": m.get("name"), "from": r[1], "to": r[2]})

        for vk, vp in (s.get("vendor_pages") or {}).items():
            if not vp.get("ok"):
                continue
            r = c.observe(("page", vk), vp.get("fingerprint"), d)
            if r:
                ev.append({"date": r[0], "type": "pricing_page_changed",
                           "vendor": vk, "url": vp["url"]})

    ev.sort(key=lambda e: (e["date"], e["type"], e.get("model") or e.get("vendor") or ""))
    return ev


def main():
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    today = os.environ.get("COLLECT_DATE") or datetime.date.today().isoformat()
    print("=== collecting %s ===" % today, flush=True)

    print("[openrouter] fetching...", flush=True)
    models, err = collect_openrouter()
    if err:
        print("[openrouter] FAILED: " + err, file=sys.stderr)
        # Hard fail: without the primary source the snapshot would be a lie,
        # and a lying snapshot poisons every future diff.
        return 1
    priced = sum(1 for m in models.values() if m.get("in") is not None)
    print("[openrouter] %d models, %d with input pricing" % (len(models), priced), flush=True)

    print("[vendors] fetching pricing pages...", flush=True)
    vendor_pages = collect_vendor_pages(cfg)

    snap = {"date": today,
            "collected_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "source": OPENROUTER_API,
            "model_count": len(models),
            "models": models,
            "vendor_pages": vendor_pages}

    SNAPDIR.mkdir(parents=True, exist_ok=True)
    out = SNAPDIR / (today + ".json")
    out.write_text(json.dumps(snap, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    print("[snapshot] %s (%d KB)" % (out.name, out.stat().st_size // 1024))

    snaps = []
    for p in sorted(SNAPDIR.glob("*.json")):
        try:
            snaps.append(json.loads(p.read_text(encoding="utf-8")))
        except Exception as e:
            print("  ! unreadable snapshot %s: %s" % (p.name, e), file=sys.stderr)
    ev = derive_changes(snaps)
    CHANGES.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in ev),
                       encoding="utf-8")
    by = {}
    for e in ev:
        by[e["type"]] = by.get(e["type"], 0) + 1
    print("[changes] %d confirmed events across %d snapshots: %s" % (len(ev), len(snaps), by or "none"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
