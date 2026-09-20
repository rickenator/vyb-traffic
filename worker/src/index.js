// Cloudflare Worker: counted relay for things the Vyb world publishes.
//
// Purpose: GitHub can't render paper.pdf inline and its blob page hides the
// download control, so readers get nothing. This Worker serves routes from a
// table and logs every request with its User-Agent, then classifies crawlers
// vs humans so readership and crawling are measured separately.
//
// Routes:  GET /            -> HTML dashboard (per-route, crawler vs human)
//          GET /stats       -> JSON rollup of counts by route/date/bucket
//          GET /stats?path= -> JSON filtered to one route
//          GET /<route>     -> 302 to that route's upstream (counted)
//          GET /<route>/raw -> stream the upstream through the Worker, so the
//                              read is counted even if the client follows away
//          GET /health      -> plain OK (for uptime checks)
//
// Counting model: Workers KV has no atomic increment, so a shared counter key
// loses concurrent updates. Instead EVERY request writes its own key
// `req:<date>:<route>:<bucket>:<uuid>` ("1"). Distinct keys can't clobber one
// another, and the read side counts keys -- nothing is ever lost.
//
// Older keys written before routes existed are `req:<date>:<bucket>:<uuid>`
// (no route segment). The read side treats those as route "_legacy".

// ---------------------------------------------------------------- route table
// Each route maps a public path to the upstream URL it serves. Add entries
// here, then redeploy -- no other change is needed. `type` controls the
// Content-Type sent on /raw.
const ROUTES = {
  "/paper.pdf": {
    upstream: "https://raw.githubusercontent.com/rickenator/VybFly/main/paper.pdf",
    type: "application/pdf",
    name: "VybFly paper",
  },
  "/cover.png": {
    upstream: "https://raw.githubusercontent.com/rickenator/VybFly/main/cover.png",
    type: "image/png",
    name: "VybFly cover art",
  },
  "/gallery.png": {
    upstream: "https://raw.githubusercontent.com/rickenator/VybFly/main/scale-gallery.png",
    type: "image/png",
    name: "VybFly scale gallery",
  },
};

// The route the legacy hardcoded `/paper.pdf` behaviour maps to when a
// request arrives at "/" ... except "/" is the dashboard, so this is only used
// to keep old links working if the route is renamed.
const DEFAULT_ROUTE = "/paper.pdf";

// Known crawlers, matched case-insensitively as substrings of the User-Agent.
// Ordered longest-first inside each family so specific names win over generic.
const BOTS = [
  "gptbot",
  "oai-searchbot",
  "chatgpt-user",
  "claudebot",
  "anthropic-ai",
  "claude-web",
  "perplexitybot",
  "perplexity-user",
  "googlebot",
  "google-extended",
  "googleother",
  "bingbot",
  "msnbot",
  "duckduckbot",
  "baiduspider",
  "yandexbot",
  "applebot",
  "applebot-extended",
  "amazonbot",
  "ccbot",
  "facebookexternalhit",
  "facebot",
  "twitterbot",
  "linkedinbot",
  "slackbot",
  "discordbot",
  "telegrambot",
  "whatsapp",
  "embedly",
  "pinterest",
  "redditbot",
  "semrushbot",
  "ahrefsbot",
  "mj12bot",
  "dotbot",
  "petalbot",
  "bytespider",
  "ia_archiver",
  "archive.org_bot",
  "commoncrawl",
  "swiftbot",
  "dataforseo",
  "screaming frog",
  "siteauditbot",
  "seznam",
  "uptime",
  "pingdom",
  "statuscake",
];

// Browsers send a Mozilla token and a real Accept header. Anything with a
// Mozilla UA and no bot match is treated as a human.
const BOT_FAMILIES = {
  gptbot: "openai",
  "oai-searchbot": "openai",
  "chatgpt-user": "openai",
  claudebot: "anthropic",
  "anthropic-ai": "anthropic",
  "claude-web": "anthropic",
  perplexitybot: "perplexity",
  "perplexity-user": "perplexity",
  googlebot: "google",
  "google-extended": "google",
  googleother: "google",
  bingbot: "microsoft",
  msnbot: "microsoft",
  duckduckbot: "duckduckgo",
  baiduspider: "baidu",
  yandexbot: "yandex",
  amazonbot: "amazon",
  ccbot: "commoncrawl",
  facebookexternalhit: "meta",
};

// Command-line clients and libraries. These are NOT crawlers and NOT readers --
// lumping them in with the crawler column inflates bot counts and makes a
// probe-heavy day look like a crawl wave. Give them their own kind so the
// dashboard can show them in a separate "tools" column.
const TOOLS = new Set([
  "curl", "wget", "python-requests", "python-urllib", "go-http-client",
  "java/", "okhttp", "axios", "node-fetch", "libwww-perl", "httpclient",
  "postmanruntime", "insomnia",
]);

function classify(ua, accept) {
  const s = (ua || "").toLowerCase();
  if (!s) return { bucket: "unknown", family: "none", kind: "unknown" };
  for (const bot of BOTS) {
    if (s.includes(bot)) {
      return { bucket: bot, family: BOT_FAMILIES[bot] || "other", kind: "bot" };
    }
  }
  for (const t of TOOLS) {
    if (s.includes(t)) return { bucket: t, family: "tool", kind: "tool" };
  }
  // No bot match: treat a real browser UA as human.
  if (s.includes("mozilla") && !s.includes("bot") && !s.includes("crawler")) {
    return { bucket: "human", family: "none", kind: "human" };
  }
  return { bucket: "unknown", family: "none", kind: "unknown" };
}

async function bumpDay(env, dateKey, route, bucket) {
  // NOTE: Workers KV has no atomic increment, and a read-modify-write on a
  // shared key LOSES updates whenever two requests overlap (they read the
  // same value, then overwrite each other). Measured: 3 concurrent GPTBot
  // hits recorded as 1. So never accumulate into a shared counter here.
  //
  // Instead each request writes its OWN key. Distinct keys can't clobber one
  // another even under full concurrency, and the read side counts them.
  const key = "req:" + dateKey + ":" + route + ":" + bucket + ":" + crypto.randomUUID();
  await env.VYBFLY_STATS.put(key, "1", { expirationTtl: 60 * 60 * 24 * 400 });
}

// Parse a request key into {day, route, bucket}. Three shapes occur:
//   req:<day>:<route>:<bucket>:<uuid>      (current)
//   req:<day>:<bucket>:<uuid>              (legacy, pre-route-table)
//   req:<day>:<route>/raw:<bucket>:<uuid>  (early route-table build)
// A trailing "/raw" is stripped so "/x" and "/x/raw" count as ONE route
// rather than splitting a single thing's traffic across two rows.
function parseReqKey(name) {
  const p = name.split(":");
  if (p.length === 5) {
    return { day: p[1], route: canonicalRoute(p[2]), bucket: p[3] };
  }
  if (p.length === 4) return { day: p[1], route: "_legacy", bucket: p[2] };
  return null;
}

function canonicalRoute(route) {
  return route.endsWith("/raw") ? route.slice(0, -4) || "/" : route;
}

// Walk every `req:` key (following the list cursor so a busy day isn't
// silently truncated at 1000) and aggregate counts by day -> route -> bucket.
async function readCounts(env) {
  let cursor = undefined;
  const days = {};
  const routes = {};
  let keys = 0;
  const totals = {};
  const kinds = { human: 0, bot: 0, tool: 0, unknown: 0 };
  do {
    const page = await env.VYBFLY_STATS.list({ prefix: "req:", cursor });
    for (const k of page.keys) {
      const parsed = parseReqKey(k.name);
      if (!parsed) continue;
      keys++;
      const d = (days[parsed.day] = days[parsed.day] || {});
      const dr = (d[parsed.route] = d[parsed.route] || {});
      dr[parsed.bucket] = (dr[parsed.bucket] || 0) + 1;

      const r = (routes[parsed.route] = routes[parsed.route] || {});
      r[parsed.bucket] = (r[parsed.bucket] || 0) + 1;

      totals[parsed.bucket] = (totals[parsed.bucket] || 0) + 1;
      kinds[kindOf(parsed.bucket)]++;
    }
    cursor = page.list_complete ? undefined : page.cursor;
  } while (cursor);
  return { keys, days, routes, totals, kinds };
}

// Map a bucket onto a coarse kind. Tools get their own kind: a curl probe is
// neither a reader nor a crawler, and folding it into "bot" would report my
// own test traffic as crawling.
function kindOf(bucket) {
  if (bucket === "human") return "human";
  if (bucket === "unknown") return "unknown";
  if (TOOLS.has(bucket)) return "tool";
  return "bot";
}

function rollup(counts) {
  const perRoute = {};
  for (const [route, buckets] of Object.entries(counts.routes)) {
    let human = 0, bot = 0, unknown = 0, tool = 0;
    for (const [b, n] of Object.entries(buckets)) {
      const k = kindOf(b);
      if (k === "human") human += n;
      else if (k === "bot") bot += n;
      else if (k === "tool") tool += n;
      else unknown += n;
    }
    const meta = ROUTES[route];
    perRoute[route] = {
      name: meta ? meta.name : route === "_legacy" ? "legacy (pre-routes)" : route,
      human,
      bot,
      tool,
      unknown,
      total: human + bot + tool + unknown,
      bots: Object.fromEntries(
        Object.entries(buckets).filter(([b]) => kindOf(b) === "bot")
      ),
      tools: Object.fromEntries(
        Object.entries(buckets).filter(([b]) => kindOf(b) === "tool")
      ),
    };
  }
  return perRoute;
}

function utcDay() {
  return new Date().toISOString().slice(0, 10);
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const path = url.pathname.replace(/\/+$/, "") || "/";

    // ---- liveness: never counted, so uptime checks don't pollute stats ----
    if (path === "/health") {
      return new Response("OK\n", { headers: { "cache-control": "no-store" } });
    }

    // ---- dashboard: HTML view of the same numbers /stats returns ----------
    if (path === "/" || path === "/dashboard") {
      const counts = await readCounts(env);
      return new Response(await dashboard(counts, env), {
        headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" },
      });
    }

    // ---- stats endpoint: JSON rollup --------------------------------------
    if (path === "/stats") {
      const counts = await readCounts(env);
      const only = url.searchParams.get("path");
      const routes = rollup(counts);
      if (only) {
        const r = routes[only] || { name: only, human: 0, bot: 0, tool: 0, unknown: 0, total: 0, bots: {}, tools: {} };
        return Response.json({ path: only, ...r, _keys: counts.keys }, {
          headers: { "cache-control": "no-store" },
        });
      }
      return Response.json(
        {
          routes,
          totals: counts.totals,
          kinds: counts.kinds,
          days: counts.days,
          _keys: counts.keys,
          _routes_configured: Object.keys(ROUTES),
        },
        { headers: { "cache-control": "no-store" } }
      );
    }

    // ---- a counted route: log, then serve ---------------------------------
    // Accept both "/x" and "/x/raw". The /raw variant STREAMS the upstream so
    // the read is counted even if the client would otherwise follow a redirect
    // away -- but it is the SAME route, so both spellings must count together.
    let routePath = path;
    let stream = false;
    if (path.endsWith("/raw")) {
      routePath = path.slice(0, -4) || "/";
      stream = true;
    }

    const route = ROUTES[routePath];
    if (!route) {
      // Unknown path: do NOT record a count. It is not a read of anything we
      // publish, and recording typos/probes would inflate the tally with junk.
      return new Response(
        `Not found: ${routePath}\n\nConfigured routes:\n` +
          Object.keys(ROUTES).map((r) => "  " + r).join("\n") +
          "\n\nDashboard: " + url.origin + "/\n",
        { status: 404, headers: { "content-type": "text/plain; charset=utf-8" } }
      );
    }

    const ua = request.headers.get("user-agent") || "";
    const accept = request.headers.get("accept") || "";
    const cls = classify(ua, accept);

    // Log asynchronously so the reader never waits on KV.
    ctx.waitUntil(logHit(env, ctx, url, cls, routePath, request));

    if (stream) {
      const upstream = await fetch(route.upstream, {
        headers: { "user-agent": "vybfly-counted-relay/1.0" },
      });
      if (!upstream.ok) {
        return new Response(`Upstream error: HTTP ${upstream.status}\n`, { status: 502 });
      }
      const resp = new Response(upstream.body, upstream);
      resp.headers.set("content-type", route.type);
      resp.headers.set("cache-control", "public, max-age=300");
      return resp;
    }

    return Response.redirect(route.upstream, 302);
  },
};

// Record one hit: a per-request key (the real count) plus a capped raw sample.
async function logHit(env, ctx, url, cls, routePath, request) {
  const ua = request.headers.get("user-agent") || "";
  const rec = {
    bucket: cls.bucket,
    family: cls.family,
    kind: cls.kind,
    ua: ua.slice(0, 300),
    ip: request.headers.get("cf-connecting-ip") || "",
    country: request.headers.get("cf-ipcountry") || "",
    asn: request.headers.get("cf-asn") || "",
    ref: request.headers.get("referer") || "",
    route: routePath,
    path: url.pathname + url.search,
    ts: new Date().toISOString(),
  };
  await bumpDay(env, utcDay(), canonicalRoute(routePath), cls.bucket);
  const rkey = "recent:" + utcDay();
  const arr = (await env.VYBFLY_STATS.get(rkey, "json")) || [];
  arr.push(rec);
  if (arr.length > 500) arr.splice(0, arr.length - 500);
  await env.VYBFLY_STATS.put(rkey, JSON.stringify(arr));
}

function esc(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

async function dashboard(counts, env) {
  const routes = rollup(counts);
  const order = Object.keys(routes).sort((a, b) => routes[b].total - routes[a].total);

  let human = 0, bot = 0, unknown = 0, tool = 0;
  for (const [b, n] of Object.entries(counts.totals)) {
    const k = kindOf(b);
    if (k === "human") human += n;
    else if (k === "bot") bot += n;
    else if (k === "tool") tool += n;
    else unknown += n;
  }

  const rows = order
    .map((r) => {
      const v = routes[r];
      const bots = Object.entries(v.bots)
        .sort((a, b) => b[1] - a[1])
        .map(([b, n]) => `${esc(b)} ${n}`)
        .join(", ") || "—";
      return `<tr>
      <td><code>${esc(r)}</code><div class="nm">${esc(v.name)}</div></td>
      <td class="num">${v.human}</td>
      <td class="num">${v.bot}</td>
      <td class="num">${v.tool}</td>
      <td class="num">${v.unknown}</td>
      <td class="num"><b>${v.total}</b></td>
      <td class="bots">${bots}</td>
    </tr>`;
    })
    .join("") || `<tr><td colspan="7" class="empty">No requests recorded yet.</td></tr>`;

  const configured = Object.entries(ROUTES)
    .map(([r, v]) => `<li><code>${r}</code> — ${esc(v.name)}</li>`)
    .join("");

  // GitHub's once-daily 14-day traffic, archived into KV by collect_worker.py.
  // Optional: if the key is absent the section simply does not render.
  let gh = null;
  try {
    gh = await env.VYBFLY_STATS.get("github:series", "json");
  } catch (_) { /* not set yet */ }

  let ghSection = "";
  if (gh && gh.days) {
    const ghTotal = gh.totals ? gh.totals.clones : 0;
    const ghUnique = gh.totals ? gh.totals.uniques : 0;
    const peak = Math.max(1, ...Object.values(gh.days).map((d) =>
      Object.values(d).reduce((a, x) => a + ((x && x.count) || 0), 0)));
    const ghRows = Object.keys(gh.days).sort().reverse().map((d) => {
      const per = gh.days[d];
      const cells = gh.repos.map((r) => {
        const c = (per[r] && per[r].count) || 0;
        return `<td class="num">${c || "—"}</td>`;
      }).join("");
      const tot = gh.repos.reduce((a, r) => a + ((per[r] && per[r].count) || 0), 0);
      const w = Math.max(2, Math.round((tot / peak) * 100));
      const spike = tot > peak * 0.4 ? ' class="spike"' : "";
      return `<tr${spike}><td><code>${esc(d)}</code></td>${cells}` +
             `<td class="num"><b>${tot}</b></td>` +
             `<td class="bar"><span style="width:${w}%"></span></td></tr>`;
    }).join("");
    ghSection = `
  <h2>GitHub clones by day — all Vyb repos</h2>
  <div class="sub" style="margin:0 0 8px">${ghTotal} clones, ${ghUnique} unique
    cloners over the window. Highlighted = spike day. Source: GitHub traffic API,
    archived ${esc(gh.generated || "")}.</div>
  <table>
    <tr><th>Date</th>${gh.repos.map((r) => `<th class="num">${esc(r)}</th>`).join("")}
      <th class="num">Total</th><th></th></tr>
    ${ghRows}
  </table>`;
  }

  const days = Object.keys(counts.days).sort().reverse().slice(0, 14);
  const dayRows = days
    .map((d) => {
      const buckets = counts.days[d];
      const flat = {};
      for (const perRoute of Object.values(buckets)) {
        for (const [b, n] of Object.entries(perRoute)) flat[b] = (flat[b] || 0) + n;
      }
      const cell = Object.entries(flat)
        .sort((a, b) => b[1] - a[1])
        .map(([b, n]) => `${esc(b)} ${n}`)
        .join(", ") || "—";
      return `<tr><td><code>${esc(d)}</code></td><td>${cell}</td></tr>`;
    })
    .join("") || `<tr><td colspan="2" class="empty">No days recorded yet.</td></tr>`;

  return `<!doctype html><html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>Vyb relay — readership</title>
<style>
  :root { color-scheme: dark; }
  body { margin:0; background:#0d1117; color:#e6edf3;
         font:15px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; }
  .wrap { max-width:960px; margin:0 auto; padding:32px 20px 64px; }
  h1 { font-size:20px; margin:0 0 4px; }
  .sub { color:#8b949e; font-size:13px; margin-bottom:24px; }
  .cards { display:flex; gap:12px; flex-wrap:wrap; margin:20px 0 28px; }
  .card { background:#161b22; border:1px solid #30363d; border-radius:8px;
          padding:12px 16px; min-width:110px; }
  .card .n { font-size:26px; font-weight:700; line-height:1.1; }
  .card .l { color:#8b949e; font-size:12px; text-transform:uppercase; letter-spacing:.04em; }
  .card.h .n { color:#3fb950; } .card.b .n { color:#d29922; } .card.u .n { color:#8b949e; }
  .card.t .n { color:#58a6ff; } .card.k .n { color:#bc8cff; }
  tr.spike td { background:#1f2a17; }
  tr.spike td:first-child { box-shadow: inset 3px 0 0 #3fb950; }
  td.bar { width:110px; }
  td.bar span { display:block; height:9px; border-radius:2px;
                background:#58a6ff; opacity:.75; min-width:2px; }
  table { width:100%; border-collapse:collapse; margin:8px 0 28px; font-size:13px; }
  th,td { text-align:left; padding:7px 10px; border-bottom:1px solid #21262d; vertical-align:top; }
  th { color:#8b949e; font-weight:600; font-size:12px; text-transform:uppercase; letter-spacing:.04em; }
  td.num { text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap; }
  td.bots { color:#8b949e; }
  .nm { color:#8b949e; font-size:12px; }
  .empty { color:#8b949e; text-align:center; padding:18px; }
  h2 { font-size:14px; color:#8b949e; margin:28px 0 6px;
       text-transform:uppercase; letter-spacing:.05em; font-weight:600; }
  ul { margin:6px 0 0; padding-left:20px; color:#8b949e; font-size:13px; }
  code { color:#79c0ff; }
  footer { color:#6e7681; font-size:12px; margin-top:36px; border-top:1px solid #21262d; padding-top:14px; }
  a { color:#58a6ff; }
</style></head><body><div class="wrap">
  <h1>Vyb relay — readership</h1>
  <div class="sub">Requests counted by route, split human vs crawler.
    JSON at <a href="/stats">/stats</a> · <a href="/stats?path=/paper.pdf">/stats?path=/paper.pdf</a></div>

  <div class="cards">
    <div class="card h"><div class="n">${human}</div><div class="l">humans</div></div>
    <div class="card b"><div class="n">${bot}</div><div class="l">crawlers</div></div>
    <div class="card k"><div class="n">${tool}</div><div class="l">tools</div></div>
    <div class="card u"><div class="n">${unknown}</div><div class="l">unknown</div></div>
    <div class="card t"><div class="n">${human + bot + tool + unknown}</div><div class="l">total</div></div>
  </div>

  <h2>By route</h2>
  <table>
    <tr><th>route</th><th class="num">human</th><th class="num">bot</th>
        <th class="num">tool</th><th class="num">unk</th><th class="num">total</th>
        <th>crawlers seen</th></tr>
    ${rows}
  </table>

  <h2>By day</h2>
  <table><tr><th>day (UTC)</th><th>counts</th></tr>${dayRows}</table>
${ghSection}
  <h2>Configured routes</h2>
  <ul>${configured}</ul>

  <footer>
    ${counts.keys} request key${counts.keys === 1 ? "" : "s"} in KV ·
    counts are exact (one key per request, so concurrency can't lose updates) ·
    this page is not counted.
  </footer>
</div></body></html>`;
}
