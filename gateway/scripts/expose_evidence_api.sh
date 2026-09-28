#!/usr/bin/env bash
# Give GitHub-hosted runners a path to the evidence API (and nothing else) via a
# Cloudflare quick tunnel. No Cloudflare account needed; the URL changes every run.
# For a stable setup use a named tunnel + Cloudflare Access service token instead.
set -euo pipefail
command -v cloudflared >/dev/null || { echo "Install cloudflared: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/"; exit 1; }
echo "Starting tunnel to http://localhost:8787 ... copy the https://*.trycloudflare.com URL, then run:"
echo "  gh secret set EVIDENCE_API_URL   --body https://<name>.trycloudflare.com"
echo "  gh secret set EVIDENCE_QUERY_TOKEN --body \"\$EVIDENCE_QUERY_TOKEN\""
exec cloudflared tunnel --no-autoupdate --url http://localhost:8787
