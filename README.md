# vyb-traffic

Counted link relay + traffic archive for the Vyb world.

Answers one question GitHub's own traffic page can't: **who read this —
a person or a machine?** GitHub serves a rolling 14-day window of
aggregate clone/view counts, updated once a day, with no attribution.
This project counts every request in real time, names the crawler, and
keeps the history past the window.

**Live:** https://vybfly-paper-relay.aniviza.workers.dev

---

## The pieces

| Piece | File | What it does |
|---|---|---|
| **Worker** | `worker/src/index.js` | Cloudflare Worker: route table, per-request counting, `/stats` JSON, HTML dashboard at `/` |
| **Classifier** | `worker/src/index.js` (`classify`) | Maps User-Agent → bucket + kind (human / bot / tool / unknown) |
| **Worker collector** | `collect_worker.py` | Pulls `/stats`, merges into the archive (monotonic) |
| **GitHub collector** | `collect.py` | Pulls 14-day clone/view counts for 4 repos, preserves them forever |
| **Reports** | `report.py`, `render_report.py` | Text + HTML report merging all sources |
| **Dispatch** | `DISPATCH.md` | Status, pending work, known limits |

## Quick start

```bash
cd vyb-traffic
export CLOUDFLARE_API_TOKEN=$(grep -m1 '^CLOUDFLARE_API_TOKEN=' ~/.hermes/.env | cut -d= -f2-)

# Refresh everything
python3 collect.py
python3 collect_worker.py https://vybfly-paper-relay.aniviza.workers.dev
python3 report.py && python3 render_report.py

# Deploy the Worker after editing ROUTES
cd worker && npx --yes wrangler@4 deploy
```

## Endpoints

| Path | Returns |
|---|---|
| `/` | HTML dashboard — cards, per-route table, per-day table, GitHub series |
| `/stats` | JSON: `routes`, `totals`, `kinds`, `days` |
| `/stats?path=/paper.pdf` | JSON for a single route |
| `/paper.pdf` | FlyScale paper (302 → raw bytes, counted) |
| `/cover.png`, `/gallery.png` | FlyScale images (counted) |
| `/x/raw` | Same route as `/x`, but streams the body so the read is counted |

## Why the counting is exact

Workers KV has **no atomic increment**. The obvious read-modify-write
counter silently loses concurrent updates — measured: 3 simultaneous
hits recorded as 1. The fix is one KV key per request
(`req:<date>:<route>:<bucket>:<uuid>`), counted at read time.

Two consequences worth knowing:

- KV is eventually consistent; a key can take seconds to appear. Merge
  into the archive is **monotonic** (only ever raises a value), so slow
  reads can't corrupt history.
- Free tier allows 1,000 KV writes/day. At one key per request, the
  binding limit is **1,000 counted reads/day**.

## Why `tool` is a separate kind

`curl`/`wget`/`python-requests` were originally classified as crawlers.
That meant the maintainer's own test traffic read as a crawl wave and
inflated the crawler column — the one number this project exists to get
right. Tools now get their own column, distinct from both humans and bots.

## Tests

```bash
python3 test_classify.py          # 16/16 — classifier buckets and kinds
```

The Worker also has an integration suite that runs the real
`src/index.js` against a mock KV binding (route dispatch, `/raw`
canonicalization, 404-doesn't-count, tool-vs-bot, `/stats` kinds).

## Adding a route

One entry in `ROUTES` (`worker/src/index.js`), then deploy:

```js
"/slides.pdf": {
  upstream: "https://raw.githubusercontent.com/rickenator/VybFly/main/slides.pdf",
  type: "application/pdf",
  name: "Slide deck",
},
```

## Limits, stated plainly

- The relay **counts what goes through it**. A link straight to GitHub
  bypasses it entirely.
- Unknown paths are **not** counted — typos can't inflate the tally.
- The maintainer's own probes *are* counted, and land in `tool` (or are
  labeled as probes in the report) so they can't be mistaken for readers.
- IPv6 to `workers.dev` timed out from the dev box; collectors pin `-4`.
