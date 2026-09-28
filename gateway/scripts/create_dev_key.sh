#!/usr/bin/env bash
# Issue a per-developer virtual key. user_id = GitHub login, so CI can match PR authors.
# Usage: ./create_dev_key.sh <github-login> [days]
set -euo pipefail
LOGIN="${1:?usage: create_dev_key.sh <github-login> [days]}"
DAYS="${2:-30}"
URL="${LITELLM_URL:-http://localhost:4000}"
: "${LITELLM_MASTER_KEY:?export LITELLM_MASTER_KEY (from gateway/.env)}"
curl -sf "$URL/key/generate" -H "Authorization: Bearer $LITELLM_MASTER_KEY" -H "content-type: application/json" \
  -d "{\"user_id\":\"$LOGIN\",\"key_alias\":\"$LOGIN-dev\",\"duration\":\"${DAYS}d\",
       \"models\":[\"claude-sonnet-5\",\"claude-opus-5-5\",\"claude-haiku-4-5-20251001\",\"demo-mock\"],
       \"metadata\":{\"github_login\":\"$LOGIN\",\"purpose\":\"ai-code-gate-demo\"}}" \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["key"])'
