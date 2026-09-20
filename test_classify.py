#!/usr/bin/env python3
"""Test the Worker's classify() logic against real-world User-Agent strings.

Runs the JS in node so we test the actual deployed code, not a re-implementation.
Checks both the bucket (which crawler) and the kind (human/bot/tool/unknown):
a curl request must NOT be reported as a crawler.
"""
import json
import subprocess

# (user-agent, expected bucket, expected kind)
CASES = [
    ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36", "human", "human"),
    ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0 Safari/537.36", "human", "human"),
    ("Mozilla/5.0 (compatible; GPTBot/1.0; +https://openai.com/gptbot)", "gptbot", "bot"),
    ("Mozilla/5.0 (compatible; ClaudeBot/1.0; +claudebot@anthropic.com)", "claudebot", "bot"),
    ("Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)", "googlebot", "bot"),
    ("Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)", "bingbot", "bot"),
    ("CCBot/2.0 (https://commoncrawl.org/faq/)", "ccbot", "bot"),
    ("Mozilla/5.0 (compatible; PerplexityBot/1.0; +https://perplexity.ai/perplexitybot)", "perplexitybot", "bot"),
    ("facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)", "facebookexternalhit", "bot"),
    ("Mozilla/5.0 (compatible; Applebot/0.1; +http://www.apple.com/go/applebot)", "applebot", "bot"),
    # Command-line clients are tools, NOT crawlers and NOT readers.
    ("curl/8.5.0", "curl", "tool"),
    ("python-requests/2.31.0", "python-requests", "tool"),
    ("Go-http-client/1.1", "go-http-client", "tool"),
    ("Wget/1.21.3", "wget", "tool"),
    ("", "unknown", "unknown"),
    ("SomeRandomThing/1.0", "unknown", "unknown"),
]

shim = r"""
const fs = require('fs');
let code = fs.readFileSync('/usr/export/rick/Projects/vyb-traffic/worker/src/index.js', 'utf8');
code = code.replace('export default {', 'const __handler = {');
code += '\nmodule.exports = { classify };\n';
const mod = { exports: {} };
new Function('module', 'exports', code)(mod, mod.exports);
const cases = %s;
console.log(JSON.stringify(cases.map(([ua]) => mod.exports.classify(ua, 'text/html'))));
""" % json.dumps([[c[0]] for c in CASES])

out = subprocess.run(["node", "-e", shim], capture_output=True, text=True)
if out.returncode != 0:
    print("node failed:\n", out.stderr)
    raise SystemExit(1)

got = json.loads(out.stdout)
bad = []
for (ua, want_bucket, want_kind), g in zip(CASES, got):
    ok_b = g["bucket"] == want_bucket
    ok_k = g["kind"] == want_kind
    mark = "ok " if (ok_b and ok_k) else "FAIL"
    if not (ok_b and ok_k):
        bad.append((ua, want_bucket, want_kind, g["bucket"], g["kind"]))
    print(f"{mark} want={want_bucket:20s}/{want_kind:7s} got={g['bucket']:20s}/{g['kind']:7s} ua={ua[:52]}")

print()
print(f"{len(CASES) - len(bad)}/{len(CASES)} passed")
if bad:
    print("failures:", bad)
    raise SystemExit(1)
