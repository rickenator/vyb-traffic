#!/usr/bin/env python3
"""Render report.txt's data as a standalone HTML page.

Reads the same sources as report.py by importing it, then writes report.html:
a dark dashboard with sparkline bars, per-repo breakdown, and the relay detail.
No external assets, no JS dependencies - one file you can open or host.
"""
import html
import json
from datetime import datetime, timezone

import report as R

REPOS = R.REPOS
ACCENT = {"human": "#3fb950", "bot": "#f85149", "tool": "#bc8cff", "unknown": "#8b949e"}

txt, worker, days, day_list, real = R.build()

routes = worker.get("routes", {})
totals = worker.get("totals", {})

# ---- sparkline geometry -------------------------------------------------
peaks = []
for d in day_list:
    peaks.append(sum(days[d].get(r, {}).get("clones", {}).get("count", 0) for r in REPOS))
top = max(peaks) or 1


def bar(v, w=120, h=26):
    """Inline SVG bar, width proportional to value."""
    bw = max(2, int(round(v / top * w)))
    return (f'<svg width="{w}" height="{h}" style="vertical-align:middle">'
            f'<rect x="0" y="{h//2-5}" width="{bw}" height="10" rx="2" '
            f'fill="#58a6ff" opacity="0.75"/></svg>')


def esc(s):
    return html.escape(str(s))


rows = []
for i, d in enumerate(day_list):
    cells = ""
    total = 0
    for r in REPOS:
        c = days[d].get(r, {}).get("clones", {}).get("count", 0)
        total += c
        cells += f'<td class="num">{c if c else "-"}</td>'
    # Emphasize spike days above 40% of the peak.
    cls = ' class="spike"' if peaks[i] > top * 0.4 else ""
    rows.append(f"<tr{cls}><td class='mono'>{d}</td>"
                f"{cells}<td class='num strong'>{total}</td>"
                f"<td>{bar(peaks[i])}</td></tr>")

route_rows = ""
for route, v in sorted(routes.items(), key=lambda kv: -kv[1].get("total", 0)):
    named = ", ".join(f"{b} {n}" for b, n in
                      sorted(v.get("bots", {}).items(), key=lambda kv: -kv[1]))
    route_rows += (
        f"<tr><td class='mono'>{esc(route)}</td>"
        f"<td class='num h'>{v.get('human',0)}</td>"
        f"<td class='num b'>{v.get('bot',0)}</td>"
        f"<td class='num t'>{v.get('tool',0)}</td>"
        f"<td class='num strong'>{v.get('total',0)}</td>"
        f"<td class='tiny'>{esc(named) or '-'}</td></tr>")

req_rows = ""
for r in real:
    is_probe = r.get("bucket") == "gptbot" and r.get("ip", "").startswith(R.OWN_V6)
    tag = ' <span class="probe">my probe</span>' if is_probe else ""
    req_rows += (
        f"<tr><td class='mono'>{esc(r.get('ts','?'))[:19]}</td>"
        f"<td class='bucket'>{esc(r.get('bucket','?'))}{tag}</td>"
        f"<td class='mono tiny'>{esc(r.get('ip','')[:34])}</td>"
        f"<td>{esc(r.get('country',''))}</td>"
        f"<td class='tiny'>{esc(r.get('ref',''))[:44] or '<i>none</i>'}</td></tr>")

cards = ""
kinds = worker.get("kinds", {})
for key, label, color in (("human", "Humans", ACCENT["human"]),
                          ("bot", "Crawlers", ACCENT["bot"]),
                          ("tool", "Tools", ACCENT["tool"]),
                          ("unknown", "Unknown", ACCENT["unknown"])):
    val = kinds.get(key, 0)
    cards += (f'<div class="card"><div class="lbl">{label}</div>'
              f'<div class="n" style="color:{color}">{val}</div></div>')
cards += (f'<div class="card"><div class="lbl">Total reads</div>'
          f'<div class="n">{sum(kinds.values())}</div></div>')

gh_total = sum(peaks)
gh_uniq = sum(days[d].get(r, {}).get("clones", {}).get("uniques", 0)
              for d in day_list for r in REPOS)

page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Vyb readership</title>
<style>
:root {{ color-scheme: dark; }}
* {{ box-sizing: border-box; }}
body {{ margin:0; padding:24px; background:#0d1117; color:#c9d1d9;
  font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }}
h1 {{ font-size:22px; margin:0 0 4px; }}
h2 {{ font-size:15px; margin:32px 0 10px; color:#8b949e;
  text-transform:uppercase; letter-spacing:.06em; font-weight:600; }}
.sub {{ color:#8b949e; margin:0 0 20px; }}
.sub code {{ color:#58a6ff; }}
table {{ width:100%; border-collapse:collapse; margin-top:6px;
  background:#161b22; border:1px solid #30363d; border-radius:8px; overflow:hidden; }}
th, td {{ padding:7px 10px; text-align:left; border-bottom:1px solid #21262d; }}
th {{ font-size:11px; text-transform:uppercase; letter-spacing:.05em;
  color:#8b949e; background:#1c2128; }}
tr:last-child td {{ border-bottom:none; }}
.num {{ text-align:right; font-variant-numeric:tabular-nums; }}
.mono {{ font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:12px; }}
.tiny {{ font-size:11px; color:#8b949e; }}
.strong {{ font-weight:600; color:#e6edf3; }}
.h {{ color:{ACCENT['human']}; }}
.b {{ color:{ACCENT['bot']}; }}
.t {{ color:{ACCENT['tool']}; }}
tr.spike td {{ background:#1f2a17; }}
tr.spike td:first-child {{ box-shadow: inset 3px 0 0 {ACCENT['human']}; }}
.cards {{ display:flex; gap:12px; flex-wrap:wrap; }}
.card {{ flex:1 1 110px; background:#161b22; border:1px solid #30363d;
  border-radius:8px; padding:12px 14px; }}
.card .lbl {{ font-size:11px; color:#8b949e; text-transform:uppercase;
  letter-spacing:.05em; }}
.card .n {{ font-size:26px; font-weight:600; margin-top:4px;
  font-variant-numeric:tabular-nums; }}
.bucket {{ font-family:ui-monospace,monospace; font-size:12px; }}
.probe {{ background:#3d2b1f; color:#d29922; padding:1px 6px; border-radius:4px;
  font-size:10px; text-transform:uppercase; letter-spacing:.04em; }}
.note {{ background:#161b22; border:1px solid #30363d; border-left:3px solid #d29922;
  border-radius:6px; padding:12px 14px; margin-top:10px; color:#c9d1d9; }}
.note b {{ color:#d29922; }}
</style></head><body>

<h1>Vyb readership</h1>
<p class="sub">Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} &middot;
live relay: <code>{esc(R.WORKER)}</code></p>

<div class="cards">{cards}</div>

<h2>Relay reads by route <span class="tiny">&mdash; live, per request</span></h2>
<table>
<tr><th>Route</th><th class="num">Humans</th><th class="num">Crawlers</th>
<th class="num">Tools</th><th class="num">Total</th><th>Crawler identity</th></tr>
{route_rows or '<tr><td colspan="6" class="tiny">no routes counted yet</td></tr>'}
</table>

<h2>Every third-party request <span class="tiny">&mdash; IP, referer, UA class</span></h2>
<table>
<tr><th>Time (UTC)</th><th>Classified as</th><th>IP</th><th>CC</th><th>Referer</th></tr>
{req_rows}
</table>

<h2>GitHub clones by day <span class="tiny">&mdash; all Vyb repos, 14-day window</span></h2>
<table>
<tr><th>Date</th><th class="num">Vyb</th><th class="num">VybFly</th>
<th class="num">VybOS</th><th class="num">VybForge</th><th class="num">Total</th><th></th></tr>
{''.join(rows)}
</table>
<p class="tiny">{gh_total} clones, {gh_uniq} unique cloners over the window.
Highlighted rows are spike days (&gt;40% of peak).</p>

<div class="note">
<b>Two things this page cannot show.</b>
The X post's 4,000+ hits bypass the relay entirely &mdash; it links to
<code>github.com/rickenator/VybFly</code>, not to the relay, so no click is
attributable. Pointing the post or the README at the relay makes every reader
countable and named. And GitHub clones are not readers: CI jobs, fleet pulls and
mirrors all count. Only the relay separates a person from a machine.
</div>

</body></html>
"""

open(f"{R.HERE}/report.html", "w").write(page)
print(f"wrote {R.HERE}/report.html ({len(page)} bytes)")
print(f"wrote {R.HERE}/report.txt")
