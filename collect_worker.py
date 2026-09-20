#!/usr/bin/env python3
"""Fetch the Worker's /stats endpoint and fold it into the traffic archive.

Unlike the GitHub harvest, these numbers are crawled-vs-human classified:
the Worker records each request's User-Agent, so bot names are real and
humans are a separate bucket.

Usage:
  python3 collect_worker.py                 # uses the URL in WORKER_URL, or
  python3 collect_worker.py <worker-url>    # pass the workers.dev base URL
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

STORE = "/usr/export/rick/Projects/vyb-traffic/traffic.json"
DEFAULT_URL = os.environ.get("VYBFLY_WORKER_URL", "")


def fetch_stats(base):
    url = base.rstrip("/") + "/stats"
    # -4: IPv6 to the workers.dev host fails TLS handshake from this box, so
    # force IPv4 rather than letting curl sit until the timeout fires.
    out = subprocess.run(["curl", "-4", "-sS", "--max-time", "20", url],
                         capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit(f"curl failed: {out.stderr.strip()}")
    return json.loads(out.stdout)


def main():
    base = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL
    if not base:
        raise SystemExit("No Worker URL. Pass it as an argument or set VYBFLY_WORKER_URL.")

    stats = fetch_stats(base)
    store = json.load(open(STORE))
    w = store.setdefault("worker", {})
    w["_url"] = base
    w["_last_run_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Merge defensively. The Worker counts KEYS per day/route/bucket, so its
    # numbers are absolute for that day -- but Workers KV is eventually
    # consistent, so a read can briefly return fewer keys than a moment ago.
    # Never lower a stored value: only raise it. That makes the merge both
    # monotonic and idempotent, so re-runs can't corrupt history.
    #
    # Shape note: the Worker's `days` is day -> route -> bucket -> count.
    # Older archives stored day -> bucket -> count (pre-route-table). Accept
    # both so existing history keeps merging instead of being misinterpreted.
    days = w.setdefault("days", {})
    added = 0
    for day, per_route in stats.get("days", {}).items():
        merged = days.setdefault(day, {})
        # Normalize to ONE shape: day -> route -> bucket -> count. The archive
        # previously held a flat day -> bucket -> count; upserting the new
        # nested shape beside it would leave a day with BOTH, double-counting
        # in any reader that sums buckets. Move stray flat entries under
        # "_legacy" first so each day has exactly one shape.
        for key in list(merged.keys()):
            if not isinstance(merged[key], dict):
                dest = merged.setdefault("_legacy", {})
                dest[key] = max(dest.get(key, 0), merged.pop(key))
        for route, buckets in per_route.items():
            if not isinstance(buckets, dict):
                # Flat shape from the Worker: this key is a bucket, not a route.
                buckets = {route: buckets}
                route = "_legacy"
            dest = merged.setdefault(route, {})
            for bucket, n in buckets.items():
                if n > dest.get(bucket, 0):
                    dest[bucket] = n
                    added += 1

    w["routes"] = stats.get("routes", {})
    w["totals"] = stats.get("totals", {})
    json.dump(store, open(STORE, "w"), indent=2, sort_keys=True)
    open(STORE.replace(".json", ".txt"), "w").write(rollup(store))
    print(f"worker stats: {len(stats.get('days', {}))} days, {added} bucket updates")
    print("totals:", json.dumps(w["totals"], indent=2))
    for day in sorted(days)[-3:]:
        print(f"  {day}: {json.dumps(days[day], sort_keys=True)}")


def rollup(store):
    out = ["GitHub traffic archive for the Vyb-world repos", ""]
    out.append("last run: " + store.get("_meta", {}).get("last_run_utc", "?"))
    out.append("")
    out.append(f"{'repo':10s} {'clones':>8s} {'views':>7s}")
    for r, v in store.get("repos", {}).items():
        cl = sum(x["count"] for x in v.get("clones", {}).values())
        vw = sum(x["count"] for x in v.get("views", {}).values())
        out.append(f"{r:10s} {cl:8d} {vw:7d}")
    w = store.get("worker", {})
    if w.get("routes"):
        out.append("")
        out.append("paper-relay Worker (per route; humans vs crawlers vs tools):")
        out.append(f"  {'route':24s} {'human':>6s} {'bot':>6s} {'tool':>6s} {'total':>6s}")
        for route, v in sorted(w["routes"].items(), key=lambda kv: -kv[1].get("total", 0)):
            out.append(
                f"  {route:24s} {v.get('human',0):6d} {v.get('bot',0):6d} "
                f"{v.get('tool',0):6d} {v.get('total',0):6d}"
            )
            named = ", ".join(f"{b} {n}" for b, n in sorted(v.get("bots", {}).items(), key=lambda kv: -kv[1]))
            if named:
                out.append(f"      crawlers: {named}")
    elif w.get("totals"):
        out.append("")
        out.append("paper-relay Worker (crawler-classified requests):")
        for bucket, n in sorted(w["totals"].items(), key=lambda kv: -kv[1]):
            out.append(f"  {bucket:24s} {n:6d}")
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    main()
