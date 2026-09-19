#!/usr/bin/env python3
"""
Static site generator for the AI model price index.

Publishing policy (this is the important part)
----------------------------------------------
Google's March and August 2026 spam updates specifically targeted "scaled
content abuse": sites that mass-generate thousands of near-identical pages.
Sites publishing genuinely original data gained visibility instead.

So pages here must EARN their existence. A model gets its own page only if
it has real pricing AND (it is a core model, OR we have accumulated enough
days of history that the page says something no other page on the web says).
Page count therefore starts small and grows with the dataset, which is the
opposite of the programmatic-SEO pattern that got penalised.

stdlib only.
"""
import json, pathlib, datetime, html, re, sys, collections, shutil

ROOT = pathlib.Path(__file__).resolve().parent.parent
SNAPDIR = ROOT / "data" / "snapshots"
CHANGES = ROOT / "data" / "changes.jsonl"
SITE = ROOT / "site"
STATIC = ROOT / "static"   # copied verbatim into the site (verification files etc.)
CONFIG = ROOT / "config" / "site.json"

HISTORY_GATE = 90         # days of history that alone earn a model page
# A week-long gate unlocked ~290 near-identical pages on day eight -- rows of
# the same price repeated. Length of history only makes a page unique once it
# is genuinely long; before that, a page needs a reason to exist.


def earns_page(mid, h, core, evented):
    """The single rule for whether a model gets its own page. Used by both the
    page writer and the index links, so they cannot disagree and link to 404s."""
    return (not is_variant(mid)) and (
        mid in core or mid in evented or len(h) >= HISTORY_GATE)
CORE_VENDOR_PREFIXES = ("anthropic/", "openai/", "google/", "meta-llama/",
                        "mistralai/", "deepseek/", "x-ai/", "qwen/")
CORE_LIMIT = 40


# ---------------------------------------------------------------- utilities

BASE = ""   # set from config in main(); "" for a domain root, "/repo" for a
            # GitHub Pages project site. Every internal href is written
            # root-relative and rewritten through write_page() on the way out,
            # so there is exactly one place this can go wrong.


def esc(s):
    return html.escape("" if s is None else str(s), quote=True)


def write_page(path, markup):
    if BASE:
        markup = markup.replace('href="/', 'href="%s/' % BASE)
    path.write_text(markup, encoding="utf-8")


def slug(mid):
    s = re.sub(r"[^a-z0-9]+", "-", str(mid).lower()).strip("-")
    return s or "model"


def money(v):
    """Format USD per 1M tokens. Sub-cent prices need more decimals or they
    all render as $0.00 and the table becomes useless."""
    if v is None:
        return "&mdash;"
    if v == 0:
        return "free"
    if v < 0.01:
        return "$%.4f" % v
    if v < 1:
        return "$%.3f" % v
    return "$%.2f" % v


def num(v):
    if v is None:
        return "&mdash;"
    if v >= 1_000_000:
        return "%.1fM" % (v / 1_000_000)
    if v >= 1000:
        return "%dK" % (v // 1000)
    return str(v)


def load_snapshots():
    out = []
    for p in sorted(SNAPDIR.glob("*.json")):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except Exception as e:
            print("  ! skipping unreadable snapshot %s: %s" % (p.name, e), file=sys.stderr)
    return out


def load_changes():
    if not CHANGES.exists():
        return []
    rows = []
    for line in CHANGES.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    rows.sort(key=lambda r: r.get("date", ""), reverse=True)
    return rows


def build_history(snaps):
    """model id -> [(date, in, out, ctx), ...] oldest first."""
    hist = collections.defaultdict(list)
    for s in snaps:
        d = s.get("date")
        for mid, m in (s.get("models") or {}).items():
            hist[mid].append((d, m.get("in"), m.get("out"), m.get("ctx")))
    return hist


# ------------------------------------------------------------------ styling

CSS = """
:root{
  --bg:#fbfaf8; --panel:#ffffff; --ink:#16150f; --muted:#6b6659;
  --line:#e5e1d8; --accent:#8a5a2b; --accent-soft:#f3ece2;
  --up:#a8341f; --down:#2c6e49; --chip:#f0ece4;
}
@media (prefers-color-scheme:dark){ :root:not([data-theme="light"]){
  --bg:#14130f; --panel:#1c1a16; --ink:#eeeae1; --muted:#9d968a;
  --line:#2e2b25; --accent:#d29a5e; --accent-soft:#262019;
  --up:#e8846c; --down:#6fc08d; --chip:#262320;
}}
:root[data-theme="dark"]{
  --bg:#14130f; --panel:#1c1a16; --ink:#eeeae1; --muted:#9d968a;
  --line:#2e2b25; --accent:#d29a5e; --accent-soft:#262019;
  --up:#e8846c; --down:#6fc08d; --chip:#262320;
}
*{box-sizing:border-box}
body{background:var(--bg);color:var(--ink);
  font:15px/1.6 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
  margin:0;-webkit-font-smoothing:antialiased}
.wrap{max-width:1080px;margin:0 auto;padding:0 20px}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}
header.top{border-bottom:1px solid var(--line);background:var(--panel)}
header.top .wrap{display:flex;align-items:baseline;gap:22px;padding-top:16px;padding-bottom:16px;flex-wrap:wrap}
.brand{font-weight:700;font-size:17px;color:var(--ink);letter-spacing:-.2px}
.brand span{color:var(--accent)}
nav a{font-size:14px;color:var(--muted)}
nav a:hover{color:var(--accent)}
h1{font-size:30px;line-height:1.2;letter-spacing:-.5px;margin:34px 0 10px}
h2{font-size:20px;letter-spacing:-.2px;margin:36px 0 12px}
.lede{color:var(--muted);max-width:66ch;margin:0 0 8px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(165px,1fr));gap:12px;margin:26px 0}
.tile{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.tile .k{font-size:11px;text-transform:uppercase;letter-spacing:.7px;color:var(--muted)}
.tile .v{font-size:24px;font-weight:660;margin-top:5px;letter-spacing:-.5px;
  font-variant-numeric:tabular-nums}
.tile .s{font-size:12px;color:var(--muted);margin-top:2px}
.tablecard{background:var(--panel);border:1px solid var(--line);border-radius:10px;overflow:hidden}
.tools{display:flex;gap:10px;padding:12px;border-bottom:1px solid var(--line);flex-wrap:wrap}
input[type=search],select{background:var(--bg);color:var(--ink);border:1px solid var(--line);
  border-radius:7px;padding:7px 10px;font:inherit;font-size:14px}
input[type=search]{flex:1;min-width:180px}
.scroll{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:14px}
th,td{padding:9px 12px;text-align:left;border-bottom:1px solid var(--line);white-space:nowrap}
th{font-size:11px;text-transform:uppercase;letter-spacing:.6px;color:var(--muted);
  font-weight:620;cursor:pointer;user-select:none;position:sticky;top:0;background:var(--panel)}
th[data-sort]:after{content:"\\2195";opacity:.3;margin-left:5px;font-size:10px}
th.asc:after{content:"\\2191";opacity:.9}
th.desc:after{content:"\\2193";opacity:.9}
td.n{text-align:right;font-variant-numeric:tabular-nums}
tbody tr:hover{background:var(--accent-soft)}
td.name{white-space:normal;min-width:210px}
.vendor{font-size:11px;color:var(--muted);display:block}
.chip{display:inline-block;background:var(--chip);border:1px solid var(--line);
  border-radius:999px;padding:1px 9px;font-size:11px;color:var(--muted)}
.up{color:var(--up)} .down{color:var(--down)}
.feed{list-style:none;padding:0;margin:0}
.feed li{background:var(--panel);border:1px solid var(--line);border-radius:9px;
  padding:11px 14px;margin-bottom:8px;font-size:14px;display:flex;gap:12px;align-items:baseline}
.feed .d{color:var(--muted);font-size:12px;font-variant-numeric:tabular-nums;flex:none;width:88px}
.empty{background:var(--panel);border:1px dashed var(--line);border-radius:10px;
  padding:26px;color:var(--muted);text-align:center}
footer{border-top:1px solid var(--line);margin-top:52px;padding:22px 0 40px;
  color:var(--muted);font-size:13px;background:var(--panel)}
code{background:var(--chip);padding:1px 6px;border-radius:5px;font-size:13px}
.prose{max-width:68ch} .prose p,.prose li{color:var(--ink)}
.prose h2{margin-top:30px}
"""

SORT_JS = """
(function(){
 var t=document.querySelector('table[data-sortable]'); if(!t) return;
 var tb=t.tBodies[0];
 t.querySelectorAll('th[data-sort]').forEach(function(th,i){
  th.addEventListener('click',function(){
   var type=th.dataset.sort, idx=Array.prototype.indexOf.call(th.parentNode.children,th);
   var dir=th.classList.contains('asc')?-1:1;
   t.querySelectorAll('th').forEach(function(o){o.classList.remove('asc','desc')});
   th.classList.add(dir===1?'asc':'desc');
   var rows=Array.prototype.slice.call(tb.rows);
   rows.sort(function(a,b){
    var x=a.cells[idx].dataset.v, y=b.cells[idx].dataset.v;
    if(type==='num'){
     var nx=(x===''||x==null)?null:parseFloat(x), ny=(y===''||y==null)?null:parseFloat(y);
     if(nx===null&&ny===null)return 0; if(nx===null)return 1; if(ny===null)return -1;
     return (nx-ny)*dir;
    }
    return String(x).localeCompare(String(y))*dir;
   });
   rows.forEach(function(r){tb.appendChild(r)});
  });
 });
 var q=document.getElementById('q'), vf=document.getElementById('vf');
 function filter(){
  var s=(q&&q.value||'').toLowerCase(), v=(vf&&vf.value)||'';
  Array.prototype.forEach.call(tb.rows,function(r){
   var okS=!s||r.dataset.search.indexOf(s)>-1;
   var okV=!v||r.dataset.vendor===v;
   r.hidden=!(okS&&okV);
  });
  var n=Array.prototype.filter.call(tb.rows,function(r){return !r.hidden}).length;
  var c=document.getElementById('count'); if(c)c.textContent=n;
 }
 if(q)q.addEventListener('input',filter);
 if(vf)vf.addEventListener('change',filter);
})();
"""


def page(title, desc, body, canonical, extra_js=""):
    js = "<script>%s</script>" % extra_js if extra_js else ""
    return """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>%s</title>
<meta name="description" content="%s">
<link rel="canonical" href="%s">
<meta property="og:title" content="%s"><meta property="og:description" content="%s">
<meta property="og:type" content="website">
<style>%s</style>
</head><body>
<header class="top"><div class="wrap">
  <a class="brand" href="/">AI Price <span>Index</span></a>
  <nav>
    <a href="/">Index</a> &nbsp;
    <a href="/changes.html">Changes</a> &nbsp;
    <a href="/api/">Data</a> &nbsp;
    <a href="/about.html">Method</a>
  </nav>
</div></header>
<main class="wrap">%s</main>
<footer><div class="wrap">
  Independent daily price tracking for large language model APIs.
  Data collected once per day from public sources &middot;
  <a href="/api/">free download</a> &middot; <a href="/about.html">methodology</a> &middot;
  data released under <a href="https://creativecommons.org/publicdomain/zero/1.0/" rel="license noopener">CC0&nbsp;1.0</a>.
  <br>Not affiliated with any model vendor. Prices are informational; always confirm
  against the vendor&rsquo;s own pricing page before relying on them.
</div></footer>
%s</body></html>""" % (esc(title), esc(desc), esc(canonical), esc(title), esc(desc), CSS, body, js)


def sparkline(points, w=110, h=26):
    """Inline SVG price history. currentColor so it works in both themes."""
    vals = [v for _, v in points if v is not None]
    if len(vals) < 2:
        return ""
    lo, hi = min(vals), max(vals)
    rng = (hi - lo) or 1.0
    n = len(vals)
    step = w / (n - 1)
    pts = " ".join("%.1f,%.1f" % (i * step, h - 3 - ((v - lo) / rng) * (h - 6))
                   for i, v in enumerate(vals))
    return ('<svg width="%d" height="%d" viewBox="0 0 %d %d" aria-hidden="true" '
            'style="vertical-align:middle"><polyline points="%s" fill="none" '
            'stroke="currentColor" stroke-width="1.5" stroke-linejoin="round" '
            'opacity=".75"/></svg>') % (w, h, w, h, pts)


# ------------------------------------------------------------------- pages

def vendor_of(mid):
    return mid.split("/")[0] if "/" in mid else "other"


def is_variant(mid):
    """Batch/preview/dated aliases of a model priced differently. They belong in
    the index table but must not get their own pages -- near-duplicate pages are
    exactly the thin-content pattern the 2026 spam updates punished."""
    tail = mid.rsplit("/", 1)[-1]
    return (tail.endswith(("-batch", "-preview", "-exp", "-latest"))
            or ":" in tail
            or re.search(r"-\d{4}$|-\d{8}$", tail) is not None)


def pick_core(models):
    """Curate the day-one page set: recent, priced, canonical models from major labs."""
    cand = [(mid, m) for mid, m in models.items()
            if m.get("in") is not None
            and mid.startswith(CORE_VENDOR_PREFIXES)
            and not is_variant(mid)]
    cand.sort(key=lambda kv: (kv[1].get("created") or 0), reverse=True)
    return {mid for mid, _ in cand[:CORE_LIMIT]}


def headline_models(models, core):
    """The models people actually arrive searching for. Sorting the full index
    by price puts a wall of obscure free models at the top, which answers
    nobody's question -- so lead with the flagships, up to two per lab."""
    by_vendor = collections.defaultdict(list)
    for mid in core:
        by_vendor[vendor_of(mid)].append(mid)
    picked = []
    for v in ("anthropic", "openai", "google", "x-ai", "deepseek", "mistralai", "qwen", "meta-llama"):
        got = sorted(by_vendor.get(v, []),
                     key=lambda m: (models[m].get("created") or 0), reverse=True)[:2]
        picked.extend(got)
    return picked[:14]


def build_index(latest, hist, changes, site_url):
    models = latest["models"]
    priced = {k: v for k, v in models.items() if v.get("in") is not None}
    vendors = sorted({vendor_of(k) for k in priced})

    cheap = sorted((v for v in priced.values() if v["in"] > 0), key=lambda m: m["in"])
    cheapest = cheap[0] if cheap else None
    biggest = max((v for v in priced.values() if v.get("ctx")),
                  key=lambda m: m["ctx"], default=None)
    free_n = sum(1 for v in priced.values() if v["in"] == 0)
    recent_moves = [c for c in changes if c.get("type") == "price_changed"][:1]

    tiles = [
        ("Models tracked", str(len(models)), "%d with published pricing" % len(priced)),
        ("Days of history", str(len(list(SNAPDIR.glob("*.json")))), "one snapshot per day"),
        ("Cheapest paid input", money(cheapest["in"]) if cheapest else "&mdash;",
         esc(cheapest["name"]) if cheapest else "per 1M tokens"),
        ("Largest context", num(biggest["ctx"]) if biggest else "&mdash;",
         esc(biggest["name"]) if biggest else "tokens"),
        ("Free-tier models", str(free_n), "$0 input pricing"),
        ("Price changes logged", str(sum(1 for c in changes if c.get("type") == "price_changed")),
         "since tracking began"),
    ]
    tilehtml = "".join(
        '<div class="tile"><div class="k">%s</div><div class="v">%s</div><div class="s">%s</div></div>'
        % (esc(k), v, s) for k, v, s in tiles)

    rows = []
    core = pick_core(models)
    evented = {c.get("model") for c in changes if c.get("model")}
    for mid, m in sorted(priced.items(), key=lambda kv: kv[1]["in"]):
        h = [(d, i) for d, i, o, c in hist.get(mid, [])]
        spark = sparkline(h)
        # Must mirror the gate in main() exactly, or the index links to 404s.
        has_page = earns_page(mid, hist.get(mid, []), core, evented)
        nm = esc(m.get("name") or mid)
        namecell = ('<a href="/models/%s.html">%s</a>' % (slug(mid), nm)) if has_page else nm
        rows.append(
            '<tr data-search="%s" data-vendor="%s">'
            '<td class="name" data-v="%s">%s<span class="vendor">%s</span></td>'
            '<td class="n" data-v="%s">%s</td>'
            '<td class="n" data-v="%s">%s</td>'
            '<td class="n" data-v="%s">%s</td>'
            '<td data-v="">%s</td></tr>' % (
                esc((str(m.get("name") or "") + " " + mid).lower()), esc(vendor_of(mid)),
                esc(m.get("name") or mid), namecell, esc(vendor_of(mid)),
                m["in"], money(m["in"]),
                "" if m.get("out") is None else m["out"], money(m.get("out")),
                m.get("ctx") or "", num(m.get("ctx")),
                spark))

    vopts = "".join('<option value="%s">%s</option>' % (esc(v), esc(v)) for v in vendors)
    movenote = ""
    if recent_moves:
        c = recent_moves[0]
        arrow = "up" if (c.get("pct") or 0) > 0 else "down"
        movenote = (' Most recent move: <strong>%s</strong> %s price '
                    '<span class="%s">%s%s%%</span> on %s.' % (
                        esc(c.get("name") or c.get("model")), esc(c.get("field")),
                        arrow, "+" if (c.get("pct") or 0) > 0 else "",
                        c.get("pct"), esc(c.get("date"))))

    hl = []
    for mid in headline_models(models, core):
        m = priced[mid]
        hl.append('<tr><td class="name"><a href="/models/%s.html">%s</a>'
                  '<span class="vendor">%s</span></td>'
                  '<td class="n">%s</td><td class="n">%s</td><td class="n">%s</td></tr>' % (
                      slug(mid), esc(m.get("name") or mid), esc(vendor_of(mid)),
                      money(m["in"]), money(m.get("out")), num(m.get("ctx"))))

    body = """
<h1>What every LLM API actually costs, tracked daily</h1>
<p class="lede">An independent price index for %d language models across %d providers,
re-checked once a day and kept as a permanent public record. Prices are USD per
1&nbsp;million tokens.%s</p>
<div class="tiles">%s</div>
<h2>Flagship models</h2>
<p class="lede">The current frontier model from each major lab. Full index below.</p>
<div class="tablecard"><div class="scroll"><table>
<thead><tr><th>Model</th><th>Input /1M</th><th>Output /1M</th><th>Context</th></tr></thead>
<tbody>%s</tbody></table></div></div>
<h2>Full price index</h2>""" % (len(models), len(set(vendor_of(k) for k in priced)),
                               movenote, tilehtml, "".join(hl)) + """
<p class="lede">Sort any column. <span id="count">%d</span> models shown.
Sparklines show input-price history since tracking began.</p>
<div class="tablecard">
  <div class="tools">
    <input type="search" id="q" placeholder="Filter by model name&hellip;" aria-label="Filter models">
    <select id="vf" aria-label="Filter by provider"><option value="">All providers</option>%s</select>
  </div>
  <div class="scroll"><table data-sortable>
    <thead><tr>
      <th data-sort="str">Model</th>
      <th data-sort="num">Input /1M</th>
      <th data-sort="num">Output /1M</th>
      <th data-sort="num">Context</th>
      <th>History</th>
    </tr></thead><tbody>%s</tbody>
  </table></div>
</div>
<p class="lede" style="margin-top:14px">Snapshot of %s &middot;
<a href="/api/">download the raw data</a> &middot;
<a href="/about.html">how this is collected</a></p>
""" % (len(priced), vopts, "".join(rows), esc(latest["date"]))

    return page("AI Model Price Index — daily LLM API pricing tracker",
                "Independent daily tracking of %d LLM API prices across %d providers. "
                "Input and output cost per million tokens, context windows, and a full "
                "history of every price change." % (len(models), len(vendors)),
                body, site_url + "/", SORT_JS)


def build_changes(changes, site_url):
    if not changes:
        body = """
<h1>Price change log</h1>
<p class="lede">Confirmed changes only, dated to the day they first appeared.</p>
<div class="empty">Tracking started today. The first comparison runs tomorrow &mdash;
this log fills in from the second daily snapshot onward.</div>"""
    else:
        items = []
        for c in changes[:400]:
            t = c.get("type")
            nm = esc(c.get("name") or c.get("model") or c.get("vendor") or "")
            if t == "price_changed":
                pct = c.get("pct")
                cls = "up" if (pct or 0) > 0 else "down"
                word = "rose" if (pct or 0) > 0 else "fell"
                txt = ('<strong>%s</strong> %s price %s from %s to %s '
                       '<span class="%s">(%s%s%%)</span>' % (
                           nm, esc(c.get("field")), word, money(c.get("from")),
                           money(c.get("to")), cls, "+" if (pct or 0) > 0 else "", pct))
            elif t == "model_added":
                txt = '<strong>%s</strong> added &mdash; %s in / %s out <span class="chip">new</span>' % (
                    nm, money(c.get("in")), money(c.get("out")))
            elif t == "model_removed":
                txt = '<strong>%s</strong> removed from the catalogue' % nm
            elif t == "context_changed":
                txt = '<strong>%s</strong> context window %s &rarr; %s' % (
                    nm, num(c.get("from")), num(c.get("to")))
            elif t == "deprecation_announced":
                txt = '<strong>%s</strong> scheduled for retirement on %s <span class="chip">deprecation</span>' % (
                    nm, esc(c.get("expires")))
            elif t == "pricing_page_changed":
                txt = '<strong>%s</strong> edited its public pricing page' % nm
            else:
                txt = '<strong>%s</strong> %s' % (nm, esc(t))
            items.append('<li><span class="d">%s</span><span>%s</span></li>' % (esc(c.get("date")), txt))
        body = """
<h1>Price change log</h1>
<p class="lede">Confirmed changes only, dated to the day they first appeared.
%d events recorded so far. A change is logged once it has held for two consecutive
daily snapshots, so short-lived flickers never appear here.
<a href="/about.html#changes">What counts as a price change</a>.</p>
<ul class="feed">%s</ul>""" % (len(changes), "".join(items))
    return page("LLM API price change log",
                "A dated record of every LLM API price change, model launch, "
                "context-window change and deprecation we detect.",
                body, site_url + "/changes.html")


def build_model_page(mid, m, h, site_url):
    pts = [(d, i) for d, i, o, c in h]
    outs = [(d, o) for d, i, o, c in h]
    first = h[0] if h else None
    nm = m.get("name") or mid
    rows = "".join(
        '<tr><td data-v="%s">%s</td><td class="n">%s</td><td class="n">%s</td>'
        '<td class="n">%s</td></tr>' % (esc(d), esc(d), money(i), money(o), num(c))
        for d, i, o, c in reversed(h))

    delta = ""
    if first and first[1] is not None and m.get("in") is not None and first[1] != m["in"]:
        p = (m["in"] - first[1]) / first[1] * 100 if first[1] else 0
        cls = "up" if p > 0 else "down"
        delta = ('<p class="lede">Input price has moved <span class="%s">%s%.1f%%</span> '
                 'since %s (%s &rarr; %s).</p>' % (cls, "+" if p > 0 else "", p,
                                                   esc(first[0]), money(first[1]), money(m["in"])))

    body = """
<h1>%s pricing</h1>
<p class="lede">Current API pricing and full tracked history. USD per 1&nbsp;million tokens.</p>
<div class="tiles">
 <div class="tile"><div class="k">Input /1M</div><div class="v">%s</div><div class="s">%s</div></div>
 <div class="tile"><div class="k">Output /1M</div><div class="v">%s</div><div class="s">%s</div></div>
 <div class="tile"><div class="k">Context</div><div class="v">%s</div><div class="s">tokens</div></div>
 <div class="tile"><div class="k">Tracked since</div><div class="v">%s</div><div class="s">%d snapshots</div></div>
</div>
%s
<h2>Price history</h2>
<div class="tablecard"><div class="scroll"><table>
<thead><tr><th>Date</th><th>Input /1M</th><th>Output /1M</th><th>Context</th></tr></thead>
<tbody>%s</tbody></table></div></div>
<p class="lede" style="margin-top:14px">Model id <code>%s</code> &middot;
<a href="/">back to the full index</a></p>
""" % (esc(nm), money(m.get("in")), sparkline(pts) or "per 1M tokens",
       money(m.get("out")), sparkline(outs) or "per 1M tokens",
       num(m.get("ctx")), esc(first[0]) if first else "&mdash;", len(h),
       delta, rows, esc(mid))

    return page("%s API pricing and price history" % nm,
                "Current and historical API pricing for %s: input and output cost per "
                "million tokens, context window, and every change since tracking began." % nm,
                body, "%s/models/%s.html" % (site_url, slug(mid)))


def build_about(latest, nsnap, site_url):
    body = """
<div class="prose">
<h1>How this index is built</h1>
<p class="lede">Short version: one automated collection per day from public sources,
every snapshot kept forever, nothing edited after the fact.</p>

<h2>Where the numbers come from</h2>
<p>Structured pricing comes from the <a href="https://openrouter.ai/api/v1/models"
rel="nofollow">OpenRouter public models API</a>, which publishes per-token input and
output prices, context lengths and deprecation dates for models across every major
provider. It is queried once per day.</p>
<p>Separately, a small set of first-party vendor pricing pages is fetched and
fingerprinted. That lets the change log record <em>that</em> a provider edited its
pricing page on a given date even when the page is not machine-readable. Vendors who
decline crawler traffic are excluded rather than worked around.</p>

<h2>What is actually original here</h2>
<p>Current prices are public; anyone can read them. What does not exist elsewhere is the
<strong>dated series</strong>: what each model cost on each specific day, and therefore
when a price moved, by how much, and which models quietly disappeared. That record can
only be built by someone who started collecting and never stopped. It began on %s and
now covers %d daily snapshot%s.</p>

<h2 id="changes">What counts as a price change</h2>
<p>Price changes are logged for models whose price is set by the vendor that makes
them &mdash; Anthropic, OpenAI, Google Gemini and xAI. Open-weight models (DeepSeek, Qwen,
Llama, GLM, Kimi and others) are served by many independent hosts, so their listed price
is a market price that moves day to day as traffic is routed between providers. Those
models show their current price in the index, but that daily drift is not reported as a
price change, because the model's maker did not change anything.</p>
<p>Every event &mdash; price change, new model, removal, pricing-page edit &mdash; must hold
for two consecutive daily snapshots before it is logged. That costs a day of latency and
removes almost all false alarms.</p>

<h2>Limits worth knowing</h2>
<ul>
<li>Prices are list prices for the standard tier. Volume discounts, committed-spend
deals, batch pricing and cached-input rates are not reflected in the headline figures.</li>
<li>A price is recorded on the day we <em>observe</em> it, which may lag the day a vendor
changed it by up to 24 hours.</li>
<li>Where a provider serves the same model at several prices, the aggregator's figure is
used, which may differ slightly from buying direct.</li>
<li>Missing data is shown as &mdash; and never as zero. &ldquo;free&rdquo; means a
genuine $0 list price.</li>
</ul>

<h2>Corrections</h2>
<p>If a figure here disagrees with a vendor's own pricing page, the vendor is right and
this index is wrong. Historical snapshots are never edited &mdash; a correction is
applied going forward and noted in the change log, so the record stays honest.</p>

<h2>Licence and reuse</h2>
<p>The dataset is released into the public domain under
<a href="https://creativecommons.org/publicdomain/zero/1.0/" rel="license noopener">CC0&nbsp;1.0</a>.
Copy it, republish it, build a product on it, charge for that product &mdash; no permission
needed and no attribution required. A link back is appreciated, never demanded.</p>
<p>The collection and site-generation code is separately available under the MIT licence.</p>
</div>""" % (esc(latest["date"] if nsnap <= 1 else sorted(p.stem for p in SNAPDIR.glob("*.json"))[0]),
             nsnap, "" if nsnap == 1 else "s")
    return page("Methodology — AI Model Price Index",
                "How the AI model price index is collected: sources, update cadence, "
                "known limits, and correction policy.",
                body, site_url + "/about.html")


def build_api_page(latest, changes, site_url):
    body = """
<div class="prose">
<h1>Free data download</h1>
<p class="lede">The whole dataset, no signup, no key, no rate limit. Use it in anything,
including commercially.</p>
<h2>Endpoints</h2>
<ul>
<li><code>/api/latest.json</code> &mdash; current prices for all %d models</li>
<li><code>/api/changes.json</code> &mdash; every change event recorded (%d)</li>
<li><code>/api/prices.csv</code> &mdash; the current index as a spreadsheet</li>
</ul>
<h2>Example</h2>
<p><code>curl %s/api/latest.json</code></p>
<p>Each model carries input and output price per 1M tokens, context length, modality,
and a deprecation date where the provider has announced one. Prices are USD.</p>
<h2>Licence</h2>
<p>Public domain under
<a href="https://creativecommons.org/publicdomain/zero/1.0/" rel="license noopener">CC0&nbsp;1.0</a>
&mdash; commercial use included, no attribution required, no rate limit, no key.</p>
<p>Provided as-is. Read the <a href="/about.html">known limits</a> before depending on it
for a billing decision.</p>
</div>""" % (latest["model_count"], len(changes), esc(site_url))
    return page("Free LLM pricing dataset — JSON and CSV download",
                "Download the full LLM API pricing dataset as JSON or CSV. "
                "Free, no signup, no rate limit, commercial use allowed.",
                body, site_url + "/api/")


# -------------------------------------------------------------------- main

def main():
    cfg = {}
    if CONFIG.exists():
        cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    global BASE
    site_url = (cfg.get("site_url") or "https://ai-price-index.pages.dev").rstrip("/")
    BASE = (cfg.get("base_path") or "").rstrip("/")
    if BASE and not BASE.startswith("/"):
        BASE = "/" + BASE

    snaps = load_snapshots()
    if not snaps:
        print("no snapshots -- run engine/collect.py first", file=sys.stderr)
        return 1
    latest = snaps[-1]
    hist = build_history(snaps)
    changes = load_changes()

    SITE.mkdir(exist_ok=True)
    (SITE / "models").mkdir(exist_ok=True)
    (SITE / "api").mkdir(exist_ok=True)
    if STATIC.is_dir():
        shutil.copytree(STATIC, SITE, dirs_exist_ok=True)

    urls = ["/", "/changes.html", "/about.html", "/api/"]

    write_page(SITE / "index.html", build_index(latest, hist, changes, site_url))
    write_page(SITE / "changes.html", build_changes(changes, site_url))
    write_page(SITE / "about.html", build_about(latest, len(snaps), site_url))
    write_page(SITE / "api" / "index.html", build_api_page(latest, changes, site_url))

    # --- model pages, gated ---
    core = pick_core(latest["models"])
    evented = {c.get("model") for c in changes if c.get("model")}
    made = 0
    for mid, m in latest["models"].items():
        if m.get("in") is None:
            continue
        if is_variant(mid):
            continue
        h = hist.get(mid, [])
        if not earns_page(mid, h, core, evented):
            continue
        write_page(SITE / "models" / (slug(mid) + ".html"),
                   build_model_page(mid, m, h, site_url))
        urls.append("/models/%s.html" % slug(mid))
        made += 1

    # --- data exports ---
    (SITE / "api" / "latest.json").write_text(
        json.dumps({"date": latest["date"], "unit": "USD per 1M tokens",
                    "source": site_url, "models": latest["models"]},
                   separators=(",", ":")), encoding="utf-8")
    (SITE / "api" / "changes.json").write_text(
        json.dumps(changes, separators=(",", ":")), encoding="utf-8")

    csv_lines = ["model_id,name,vendor,input_per_1m_usd,output_per_1m_usd,context_tokens"]
    for mid, m in sorted(latest["models"].items()):
        nm = (m.get("name") or "").replace('"', '""')
        csv_lines.append('%s,"%s",%s,%s,%s,%s' % (
            mid, nm, vendor_of(mid),
            "" if m.get("in") is None else m["in"],
            "" if m.get("out") is None else m["out"],
            m.get("ctx") or ""))
    (SITE / "api" / "prices.csv").write_text("\n".join(csv_lines), encoding="utf-8")

    # --- sitemap + robots ---
    today = datetime.date.today().isoformat()
    sm = ['<?xml version="1.0" encoding="UTF-8"?>',
          '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for u in urls:
        sm.append("<url><loc>%s%s%s</loc><lastmod>%s</lastmod></url>" % (site_url, BASE, u, today))
    sm.append("</urlset>")
    (SITE / "sitemap.xml").write_text("\n".join(sm), encoding="utf-8")
    (SITE / "robots.txt").write_text(
        "User-agent: *\nAllow: /\n\nSitemap: %s/sitemap.xml\n" % site_url, encoding="utf-8")

    print("[site] %d pages (%d model pages: core, changed, or %d+ days history)" % (len(urls), made, HISTORY_GATE))
    print("[site] exports: latest.json, changes.json, prices.csv")
    print("[site] output -> %s" % SITE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
