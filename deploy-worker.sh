#!/bin/bash
# Deploy the vybfly-paper-relay Worker to Cloudflare.
#
# Auth: reads the token from the Hermes vault at deploy time. The token is
# never printed and never lands in a file or a command line.
set -euo pipefail

DIR="/usr/export/rick/Projects/vyb-traffic/worker"
cd "$DIR"

echo "== checking for the Cloudflare token in the Hermes vault =="
if ! hermes vault list 2>/dev/null | grep -qi cloudflare; then
  echo "ERROR: no Cloudflare item in the Hermes vault."
  echo "Add one first:  hermes vault add"
  echo "  kind:  token / api_key"
  echo "  label: cloudflare"
  echo "  value: <your Cloudflare API token with 'Workers KV Storage: Edit' + 'Workers Scripts: Edit'>"
  exit 1
fi

echo "== installing wrangler (local to this project) =="
if [ ! -x node_modules/.bin/wrangler ]; then
  npm install --silent
fi

WRANGLER="$DIR/node_modules/.bin/wrangler"

echo "== resolving the KV namespace id =="
NS_ID=$(hermes vault get cloudflare >/dev/null 2>&1 || true)
# Create the namespace if the toml still holds the placeholder.
if grep -q "REPLACE_WITH_KV_NAMESPACE_ID" wrangler.toml; then
  echo "creating KV namespace VYBFLY_STATS ..."
  OUT=$("$WRANGLER" kv namespace create VYBFLY_STATS 2>&1) || { echo "$OUT"; exit 1; }
  echo "$OUT"
  ID=$(echo "$OUT" | grep -oE 'id = "[0-9a-f]{32}"' | grep -oE '[0-9a-f]{32}' | head -1)
  if [ -z "$ID" ]; then echo "could not parse namespace id from output above"; exit 1; fi
  sed -i "s/REPLACE_WITH_KV_NAMESPACE_ID/$ID/" wrangler.toml
  echo "wrote namespace id into wrangler.toml"
fi

echo "== deploying =="
"$WRANGLER" deploy

echo
echo "Deployed. Test it with:"
echo "  curl -sI https://vybfly-paper-relay.<your-subdomain>.workers.dev/paper.pdf"
echo "  curl -s  https://vybfly-paper-relay.<your-subdomain>.workers.dev/stats | jq"
