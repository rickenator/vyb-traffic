#!/usr/bin/env python3
"""Collect GitHub traffic for the Vyb-world repos into a permanent local archive.

GitHub only serves 14 days of traffic (clones/views/referrers/paths). This
script backfills that whole window on every run and merges it into a JSON
store keyed by date, so history accumulates and re-runs are idempotent.
Run it daily via cron; each run also self-heals up to 14 days of missed days.
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone

import yaml

STORE = os.environ.get(
    "VYB_TRAFFIC_STORE",
    "/usr/export/rick/Projects/vyb-traffic/traffic.json",
)
REPOS = ["Vyb", "VybFly", "VybForge", "VybOS"]
OWNER = "rickenator"

# gh resolves credentials from its own config/keyring; do not touch .env.
BASE = ["gh", "api"]


def gh_json(path):
    """GET an api path, return parsed JSON. Returns None on failure."""
    try:
        out = subprocess.run(
            BASE + [path],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        print(f"  ! timeout: {path}", file=sys.stderr)
        return None
    if out.returncode != 0:
        msg = (out.stderr or "").strip().splitlines()
        print(f"  ! api error {path}: {msg[0] if msg else 'unknown'}", file=sys.stderr)
        return None
    try:
        return json.loads(out.stdout)
    except json.JSONDecodeError as exc:
        print(f"  ! bad json {path}: {exc}", file=sys.stderr)
        return None


def load_store():
    if not os.path.exists(STORE):
        return {"_meta": {}, "repos": {}}
    with open(STORE) as fh:
        return json.load(fh)


def save_store(store):
    os.makedirs(os.path.dirname(STORE), exist_ok=True)
    tmp = STORE + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(store, fh, indent=1, sort_keys=True)
    os.replace(tmp, STORE)


def day(ts):
    return ts[:10]


def merge_points(dst, series):
    """series: list of {timestamp,count,uniques}; dst: {date: {count,uniques}}."""
    added = 0
    for pt in series:
        d = day(pt["timestamp"])
        rec = {"count": pt["count"], "uniques": pt["uniques"]}
        if dst.get(d) != rec:
            dst[d] = rec
            added += 1
    return added


def merge_popular(dst, items, key):
    """referrers/paths have no date; keep latest snapshot under _latest."""
    latest = []
    for it in items:
        latest.append(
            {
                "count": it.get("count", 0),
                "uniques": it.get("uniques", 0),
                key: it.get(key),
                "title": it.get("title"),
            }
        )
    dst["_latest"] = latest
    return len(latest)


def collect_repo(store, repo):
    repo_store = store["repos"].setdefault(repo, {})
    repo_store.setdefault("clones", {})
    repo_store.setdefault("views", {})
    repo_store.setdefault("referrers", {})
    repo_store.setdefault("paths", {})

    clones = gh_json(f"repos/{OWNER}/{repo}/traffic/clones")
    views = gh_json(f"repos/{OWNER}/{repo}/traffic/views")
    refs = gh_json(f"repos/{OWNER}/{repo}/traffic/popular/referrers")
    paths = gh_json(f"repos/{OWNER}/{repo}/traffic/popular/paths")

    n_cl = merge_points(repo_store["clones"], (clones or {}).get("clones", []))
    n_vw = merge_points(repo_store["views"], (views or {}).get("views", []))
    if refs is not None:
        merge_popular(repo_store["referrers"], refs, "referrer")
    if paths is not None:
        merge_popular(repo_store["paths"], paths, "path")

    tot_cl = sum(r["count"] for r in repo_store["clones"].values())
    tot_vw = sum(r["count"] for r in repo_store["views"].values())
    print(
        f"  {repo}: days(clones)={len(repo_store['clones'])} "
        f"all-time-clones={tot_cl} all-time-views={tot_vw} "
        f"(new points this run: {n_cl} clones, {n_vw} views)"
    )
    return tot_cl, tot_vw


def main():
    store = load_store()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    store.setdefault("_meta", {})
    store["_meta"]["last_run_utc"] = now

    print(f"GitHub traffic collector @ {now}")
    print(f"store: {STORE}")
    grand_cl = grand_vw = 0
    for repo in REPOS:
        c, v = collect_repo(store, repo)
        grand_cl += c
        grand_vw += v

    store["_meta"]["totals"] = {"clones": grand_cl, "views": grand_vw}
    save_store(store)

    # human-readable rollup appended for at-a-glance reading
    txt = os.path.join(os.path.dirname(STORE), "traffic.txt")
    lines = [f"GitHub traffic archive for the Vyb-world repos",
             f"last run: {now}",
             f"days recorded: " + ", ".join(
                 f"{r}={len(store['repos'][r]['clones'])}" for r in REPOS),
             ""]
    lines.append(f"{'repo':10s} {'clones':>8s} {'views':>8s} {'unique cloners':>15s}")
    for repo in REPOS:
        cl = store["repos"][repo]["clones"]
        vw = store["repos"][repo]["views"]
        lines.append(
            f"{repo:10s} {sum(x['count'] for x in cl.values()):8d} "
            f"{sum(x['count'] for x in vw.values()):8d} "
            f"{max((x['uniques'] for x in cl.values()), default=0):15d}"
        )
    with open(txt, "w") as fh:
        fh.write("\n".join(lines) + "\n")

    print(f"\nTOTALS: clones={grand_cl} views={grand_vw}")
    print(f"wrote {STORE} and {txt}")


if __name__ == "__main__":
    main()
