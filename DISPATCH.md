# Vyb readership tracking — dispatch

*Status as of 2026-09-20 00:5x UTC.*

A capability, not a campaign. The point is to be able to say **who read
what, when, and whether it was a person or a machine** — for anything the
Vyb world publishes — and to keep that history past GitHub's 14-day window.
Tally size is a byproduct.

---

## 1. What exists

| Piece | Where | State |
|---|---|---|
| **Counted relay** | `https://vybfly-paper-relay.aniviza.workers.dev` | live, v`3102b052` |
| **Dashboard** | that URL's `/` | live — cards, per-route, per-day, GitHub series |
| **JSON API** | `/stats`, `/stats?path=/paper.pdf` | live |
| **Archive** | `traffic.json`, `traffic.txt` | maintained, monotonic |
| **Daily harvest** | cron `c319186c23bb`, 06:00 | scheduled — **dormant, gateway down** |
| **Tests** | `test_classify.py` (16/16), Worker integration (18/18) | green |

### Routes wired

| Route | Serves | Type |
|---|---|---|
| `/paper.pdf` | FlyScale paper | `application/pdf` |
| `/cover.png` | FlyScale cover art | `image/png` |
| `/gallery.png` | FlyScale scale gallery | `image/png` |

Each is a 3-line entry in `worker/src/index.js`. Adding a route costs one
deploy. `/x/raw` and `/x` count as the **same** route.

---

## 2. What it measures — the three-way split

The core capability is separating **who** fetched. Every request lands in
exactly one bucket, then rolls up to a kind:

| Kind | Meaning | Examples |
|---|---|---|
| **human** | a real browser | Firefox, Chrome, Safari |
| **bot** | a named crawler | googlebot, bingbot, gptbot, facebookexternalhit |
| **tool** | a command-line client or library | curl, wget, python-requests, axios |
| **unknown** | unrecognized UA | — |

**Why `tool` matters:** curl and wget were originally counted as crawlers.
That made my own test traffic look like a crawl wave and inflated the
crawler column — the one number this project exists to get right. Tools
now have their own column.

**Counts are exact under concurrency.** Workers KV has no atomic
increment; a naive read-modify-write counter loses concurrent updates
(measured: 3 simultaneous hits → recorded as 1). The fix is one KV key
per request (`req:<date>:<route>:<bucket>:<uuid>`), counted at read time.

---

## 3. What it deliberately does NOT measure

- **X post engagement.** Not routed through the relay. The boosted post
  links to `github.com/rickenator/VybFly`, so those clicks are invisible
  here. No X write path exists on this machine (`xurl` absent, no X
  credentials).
- **Own probe traffic.** My curl/UA verification requests are counted —
  they are not filtered — but they land in `tool` or are labeled as
  probes in the report, so they cannot be mistaken for an audience.
- **404s.** Unknown paths write nothing. Typos cannot inflate the tally.
- **GitHub traffic beyond 14 days.** GitHub serves only a rolling
  window; the archive preserves it, but days older than the window are
  frozen at whatever was captured.

---

## 4. The honest read, right now

Relay has been counting since 2026-09-19 ~20:15 UTC. Live `/stats` at
time of writing: `human 4, bot 6, tool 2, unknown 0` — 12 keys.

**Genuine third-party traffic: 1 human read, 1 crawler fetch.**

| Time (UTC) | Class | Source | Note |
|---|---|---|---|
| 23:02:19 | human | referer `github.com` | a real read |
| 21:59:03 | facebookexternalhit | Meta `2a03:2880::/32` | someone pasted the link |
| 20:25:32 | "gptbot" | home network | **my probe**, flagged |

Everything else in the log is my own testing — the `tool: 2` above is two
curl probes from today, and `bot: 6` is five gptbot and one bingbot/
googlebot probe that predate the `tool` kind. That is expected: the relay
has been live ~5 hours and nothing points at it yet except two READMEs.

**GitHub shows the real scale** — 2,316 clones / 645 unique over 14 days,
with spikes on Sep 11 (fleet pull) and Sep 18 (FlyScale launch, 1,031).

---

## 5. Dispatch — what is wired vs. what is pending

### Live now

- ✅ Relay counting per-route, three-way split
- ✅ Dashboard at `/`, JSON at `/stats`
- ✅ FlyScale README → paper through the relay
- ✅ Archive preserving GitHub history past 14 days

### Pending

| Item | Blocker | Owner |
|---|---|---|
| **PR #311** — Vyb README → paper via relay | CI: ASan job still pending | CI, then you merge |
| **Daily harvest** | gateway not installed | your call (`hermes gateway install`) |
| **New X post** with the relay URL | no X session; needs you | you (text drafted below) |
| **More routes** (`/cover.png`, `/gallery.png` already wired; more on request) | you name the targets | you |
| **Token revocation** | Cloudflare token still live | your call |

### The X text, ready to paste

```
FlyScale: a fruit-fly brain, redrawn bigger. We run it twice — Python
reference vs Vyb-native — and made them agree tick for tick. That port
found twelve compiler defects, including a store that wrote 8 bytes into
a 4-byte slot.

The paper: https://vybfly-paper-relay.aniviza.workers.dev/paper.pdf
```

Raw URL, not a shortener, so the click stays attributable.

---

## 6. Known limits, stated plainly

- **IPv6 to `workers.dev` times out from this box.** Collector is pinned
  to `-4`. Believed local; untested from another machine.
- **Workers KV eventual consistency** — a key may take seconds to appear.
  The archive merge is monotonic (only ever raises a stored value), so
  slow reads can't corrupt history.
- **Free-tier ceilings:** Workers 100k req/day; KV 100k reads/day,
  **1,000 writes/day**. At one key per request, the binding limit is
  **1,000 counted reads/day**. Fine at current volume; worth remembering
  if a post goes viral.
- **The relay counts what goes through it.** A link to GitHub bypasses it
  entirely — that is the whole reason the README links were repointed.

---

## 7. Extending it

**Add a route** — one entry in `ROUTES` (`worker/src/index.js`), then
`npx --yes wrangler@4 deploy`.

**Run a report by hand:**
```
cd /usr/export/rick/Projects/vyb-traffic
export CLOUDFLARE_API_TOKEN=$(grep -m1 '^CLOUDFLARE_API_TOKEN=' ~/.hermes/.env | cut -d= -f2-)
python3 collect.py && python3 collect_worker.py https://vybfly-paper-relay.aniviza.workers.dev
python3 report.py && python3 render_report.py
```
