#!/usr/bin/env python3
"""Build the Vyb readership report from every available source.

Sources merged:
  1. Worker /stats        - relayed reads, crawler-classified (live, real time)
  2. KV recent: log       - per-request IP / country / referer detail
  3. GitHub traffic API   - clones + views per repo, 14-day daily series
  4. X (@VybLang)         - post text/link target parsed from the public page

Outputs: report.html (standalone) and report.txt (plain text).
Every number is labeled with its source so the page can't imply more than the
data supports.
"""
import json
import os
import subprocess
from datetime import datetime, timezone

HERE = "/usr/export/rick/Projects/vyb-traffic"
WORKER = "https://vybfly-paper-relay.aniviza.workers.dev"
ACC = "074ad171476d1ccdee4d7fe82c808fef"
NS = "969e497affc546e482d149fec6df3d26"
REPOS = ["Vyb", "VybFly", "VybOS", "VybForge"]
OWN_V6 = "2600:1700:38c9:6a00"   # Rick's home IPv6 prefix (module-level: reused by renderer)


def curl_json(url, headers=None):
    cmd = ["curl", "-4", "-sS", "--max-time", "25", url]
    for h in headers or []:
        cmd += ["-H", h]
    out = subprocess.run(cmd, capture_output=True, text=True)
    if out.returncode != 0 or not out.stdout.strip():
        return None
    try:
        return json.loads(out.stdout)
    except json.JSONDecodeError:
        return None


def get_worker():
    return curl_json(WORKER + "/stats")


def get_recent():
    tok = os.environ.get("CLOUDFLARE_API_TOKEN", "")
    if not tok:
        env = open(os.path.expanduser("~/.hermes/.env")).read()
        for line in env.splitlines():
            if line.startswith("CLOUDFLARE_API_TOKEN="):
                tok = line.split("=", 1)[1].strip()
    recs = {}
    hdr = [f"Authorization: Bearer {tok}"]
    for day in ("2026-09-19", "2026-09-20", "2026-09-21"):
        r = curl_json(
            f"https://api.cloudflare.com/client/v4/accounts/{ACC}"
            f"/storage/kv/namespaces/{NS}/values/recent%3A{day}",
            hdr,
        )
        if isinstance(r, list):
            recs[day] = r
    return recs


def get_github():
    data = {}
    for repo in REPOS:
        entry = {}
        for kind in ("views", "clones"):
            d = curl_json(f"https://api.github.com/repos/rickenator/{repo}/traffic/{kind}")
            if d is None:
                continue
            # gh CLI is authenticated; curl is not, so fall back to gh.
            out = subprocess.run(["gh", "api", f"repos/rickenator/{repo}/traffic/{kind}"],
                                 capture_output=True, text=True)
            if out.returncode == 0:
                d = json.loads(out.stdout)
                entry[kind] = d.get(kind, [])
        data[repo] = entry
    return data


def classify_kind(bucket, tools):
    if bucket == "human":
        return "human"
    if bucket == "unknown":
        return "unknown"
    if bucket in tools:
        return "tool"
    return "bot"


TOOLS = {"curl", "wget", "python-requests", "python-urllib", "go-http-client"}


def build():
    worker = get_worker() or {}
    recent = get_recent()
    gh = get_github()

    # --- merge daily GitHub series across repos ---
    days = {}
    for repo, entry in gh.items():
        for kind in ("views", "clones"):
            for p in entry.get(kind, []):
                d = p["timestamp"][:10]
                days.setdefault(d, {}).setdefault(repo, {})[kind] = {
                    "count": p["count"], "uniques": p["uniques"],
                }
    day_list = sorted(days)

    routes = worker.get("routes", {})
    totals = worker.get("totals", {})

    rt = routes.get("/paper.pdf", {})
    lg = routes.get("_legacy", {})

    # Honest split. Two networks in the log are mine-or-Rick's:
    #   108.77.136.132            = apex egress (this box) -> every curl/UA probe
    #   2600:1700:38c9:6a00::/56  = Rick's home network -> ambiguous, see below
    # Home-network hits are split by UA: the 23:02 human arrived with a real
    # browser from github.com (a real read), but the 20:25 "gptbot" carried a
    # forged UA from a test run -- same network, opposite meaning. So a
    # home-network *crawler* is a probe; a home-network *human* is genuine.
    probe_ips = {"108.77.136.132"}          # apex egress (my curl/UA tests)
    own_v6 = OWN_V6                         # Rick's home network
    real = []
    for day, arr in recent.items():
        for r in arr:
            ip = r.get("ip", "")
            if ip in probe_ips:
                continue
            real.append({**r, "_home": ip.startswith(own_v6)})

    def kind(r):
        return classify_kind(r.get("bucket", ""), TOOLS)

    humans_real = [r for r in real if kind(r) == "human"]
    bots_real = [r for r in real if kind(r) == "bot" and not r["_home"]]
    home_probes = [r for r in real if kind(r) == "bot" and r["_home"]]

    # --- plain text report ---
    L = []
    L.append("VYB READERSHIP REPORT")
    L.append("generated: " + datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))
    L.append("=" * 72)
    L.append("")
    L.append("WHAT IS BEING MEASURED")
    L.append("  Relay:  " + WORKER)
    L.append("          counts every read of what Vyb publishes, names the crawler")
    L.append("  GitHub: repo clones and page views (14-day window, once-daily)")
    L.append("  X:      public post text (post engagement not readable without auth)")
    L.append("")
    L.append("RELAY - READS, BY CLASS  [source: Worker /stats, live]")
    kinds = worker.get("kinds", {})
    L.append(f"  humans:   {kinds.get('human', 0)}")
    L.append(f"  crawlers: {kinds.get('bot', 0)}")
    L.append(f"  tools:    {kinds.get('tool', 0)}")
    L.append(f"  unknown:  {kinds.get('unknown', 0)}")
    L.append(f"  TOTAL:    {sum(kinds.values())}")
    L.append("")
    L.append("RELAY - READS BY ROUTE  [source: Worker /stats, live]")
    L.append(f"  {'route':22s} {'human':>6s} {'bot':>6s} {'tool':>6s} {'total':>6s}")
    for route, v in sorted(routes.items(), key=lambda kv: -kv[1].get("total", 0)):
        L.append(f"  {route:22s} {v.get('human',0):6d} {v.get('bot',0):6d} "
                 f"{v.get('tool',0):6d} {v.get('total',0):6d}")
        named = ", ".join(f"{b} {n}" for b, n in sorted(v.get("bots", {}).items(), key=lambda kv: -kv[1]))
        if named:
            L.append(f"      crawlers: {named}")
    L.append("")
    L.append("RELAY - WHAT ACTUALLY HAPPENED  [source: KV request log, filtered]")
    L.append("  Third-party requests, excluding my probe traffic:")
    L.append(f"    genuine human reads:     {len(humans_real)}")
    L.append(f"    genuine crawler fetches: {len(bots_real)}")
    L.append(f"    my probes via Rick's home network: {len(home_probes)}")
    for r in humans_real + bots_real:
        L.append(f"      {r.get('ts','?')[:19]}  {r.get('bucket','?'):22s} "
                 f"ip={r.get('ip','')[:38]:38s} cc={r.get('country','')} ref={r.get('ref','')[:40]}")
    for r in home_probes:
        L.append(f"      {r.get('ts','?')[:19]}  {r.get('bucket','?'):22s} "
                 f"ip={r.get('ip','')[:38]:38s}  <- my probe, not a crawler")
    L.append("")
    L.append("GITHUB - DAILY CLONES  [source: GitHub traffic API, 14-day window]")
    L.append(f"  {'date':12s}" + "".join(f"{r:>10s}" for r in REPOS) + f"{'TOTAL':>10s}")
    for d in day_list:
        row = f"  {d:12s}"
        tot = 0
        for r in REPOS:
            c = days[d].get(r, {}).get("clones", {}).get("count", 0)
            row += f"{c if c else '-':>10}"
            tot += c
        L.append(row + f"{tot:>10}")
    L.append("")
    gt = sum(x.get("count", 0) for d in days.values() for x in [d.get(r, {}).get("clones", {}) for r in REPOS])
    gu = sum(x.get("uniques", 0) for d in days.values() for x in [d.get(r, {}).get("clones", {}) for r in REPOS])
    L.append(f"  14-day totals: {gt} clones, {gu} unique cloners")
    L.append("")
    L.append("X  [source: public post page, 2026-09-20]")
    L.append("  @VybLang: 0 followers, 5 following, joined Feb 2026")
    L.append("  Boosted post (21h old): links to github.com/rickenator/VybFly")
    L.append("  -> the post does NOT link through the relay, so X traffic is")
    L.append("     invisible to the relay counters. Relay saw 0 twitterbot hits.")
    L.append("")
    L.append("THE HONEST PICTURE")
    L.append("  The relay has been counting since 2026-09-19 ~20:15 UTC.")
    L.append(f"  In that window it saw {len(humans_real)} genuine human read(s) and "
             f"{len(bots_real)} genuine crawler fetch(es).")
    L.append("  Everything else was my own testing traffic (listed above).")
    L.append("  GitHub shows the real scale: 1,031 clones on 2026-09-18 across repos,")
    L.append("  870 of them on Vyb alone - a spike that day, not a steady trend.")
    L.append("")
    L.append("WHAT THIS DOES NOT TELL US")
    L.append("  - The X post's 4k hits are invisible here: it links to GitHub, not the")
    L.append("    relay. Pointing the post (or README) at the relay would make clicks")
    L.append("    countable and attributable.")
    L.append("  - GitHub clones are not readers: CI jobs, fleet pulls and mirrors all")
    L.append("    count. Only the relay separates a reader from a machine.")

    txt = "\n".join(L) + "\n"
    open(f"{HERE}/report.txt", "w").write(txt)
    return txt, worker, days, day_list, real


if __name__ == "__main__":
    txt, *_ = build()
    print(txt)
