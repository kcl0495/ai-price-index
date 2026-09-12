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


def prev_snapshot(exclude):
    files = sorted(p for p in SNAPDIR.glob("*.json") if p.name != exclude)
    if not files:
        return None
    try:
        return json.loads(files[-1].read_text(encoding="utf-8"))
    except Exception:
        return None


def diff(prev, cur, today):
    """Emit one event per meaningful change. This feed is the product."""
    ev = []
    pm = (prev or {}).get("models") or {}
    cm = cur.get("models") or {}
    pdate = (prev or {}).get("date")

    for mid in sorted(set(cm) - set(pm)):
        ev.append({"date": today, "type": "model_added", "model": mid,
                   "name": cm[mid].get("name"),
                   "in": cm[mid].get("in"), "out": cm[mid].get("out")})
    for mid in sorted(set(pm) - set(cm)):
        ev.append({"date": today, "type": "model_removed", "model": mid,
                   "name": pm[mid].get("name")})

    for mid in sorted(set(pm) & set(cm)):
        a, b = pm[mid], cm[mid]
        for field in ("in", "out"):
            x, y = a.get(field), b.get(field)
            # Only report a real number moving to a different real number.
            # None -> number is a data-coverage change, not a price change.
            if x is None or y is None or x == y:
                continue
            pct = round((y - x) / x * 100, 2) if x else None
            ev.append({"date": today, "prev_date": pdate, "type": "price_changed",
                       "model": mid, "name": b.get("name"), "field": field,
                       "from": x, "to": y, "pct": pct})
        if a.get("ctx") != b.get("ctx") and a.get("ctx") and b.get("ctx"):
            ev.append({"date": today, "prev_date": pdate, "type": "context_changed",
                       "model": mid, "name": b.get("name"),
                       "from": a["ctx"], "to": b["ctx"]})
        if not a.get("expires") and b.get("expires"):
            ev.append({"date": today, "type": "deprecation_announced",
                       "model": mid, "name": b.get("name"), "expires": b["expires"]})

    pv = (prev or {}).get("vendor_pages") or {}
    for key, c in (cur.get("vendor_pages") or {}).items():
        o = pv.get(key)
        if o and o.get("ok") and c.get("ok") and o.get("fingerprint") != c.get("fingerprint"):
            ev.append({"date": today, "prev_date": pdate, "type": "pricing_page_changed",
                       "vendor": key, "url": c["url"],
                       "len_delta": c.get("text_len", 0) - o.get("text_len", 0)})
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
    prev = prev_snapshot(out.name)
    out.write_text(json.dumps(snap, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    print("[snapshot] %s (%d KB)" % (out.name, out.stat().st_size // 1024))

    if prev:
        ev = diff(prev, snap, today)
        if ev:
            with CHANGES.open("a", encoding="utf-8") as f:
                for e in ev:
                    f.write(json.dumps(e, ensure_ascii=False) + "\n")
        by = {}
        for e in ev:
            by[e["type"]] = by.get(e["type"], 0) + 1
        print("[changes] %d events vs %s: %s" % (len(ev), prev["date"], by or "none"))
    else:
        print("[changes] baseline established (no previous snapshot)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
