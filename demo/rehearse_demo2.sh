#!/usr/bin/env bash
# Demo 2 rehearsal: get code from the gateway (demo-mock model, no tokens spent),
# commit it, and push a PR branch. The gateway logs hashes of the returned lines;
# the gateway-evidence check then finds them in the PR.
#   ./demo/rehearse_demo2.sh undisclosed   -> no AI disclosure -> blocked
#   ./demo/rehearse_demo2.sh disclosed     -> Assisted-by trailer + checkbox -> passes
# Needs: LITELLM_URL (default http://localhost:4000), DEV_KEY (from create_dev_key.sh)
set -euo pipefail
VARIANT="${1:-undisclosed}"
URL="${LITELLM_URL:-http://localhost:4000}"
: "${DEV_KEY:?export DEV_KEY=<your virtual key from gateway/scripts/create_dev_key.sh>}"
BASE="${BASE_BRANCH:-main}"
BRANCH="demo2/${VARIANT}"

resp=$(curl -sf "$URL/v1/messages" -H "Authorization: Bearer $DEV_KEY" -H "content-type: application/json" \
  -d '{"model":"demo-mock","max_tokens":400,"messages":[{"role":"user","content":"Write a Flask endpoint that searches users by name."}]}')
code=$(printf '%s' "$resp" | python3 -c 'import json,re,sys
t="".join(b.get("text","") for b in json.load(sys.stdin)["content"] if b.get("type")=="text")
print(re.search(r"```[^\n]*\n(.*?)```", t, re.S).group(1))')

git fetch -q origin "$BASE" && git checkout -q -B "$BRANCH" "origin/$BASE"
{ printf 'from flask import Blueprint, jsonify, request\n\nfrom app.db import get_db\n\nbp = Blueprint("search", __name__)\n\n\n'
  printf '%s\n' "$code"; } > app/search.py
git add app/search.py
if [[ "$VARIANT" == "disclosed" ]]; then
  git commit -q -m "Add user search endpoint" -m "Assisted-by: demo-mock:gateway"
else
  git commit -q -m "Add user search endpoint"
fi
git push -q -f origin "$BRANCH"
git checkout -q "$BASE"
echo "Pushed $BRANCH. Open the PR:"
if [[ "$VARIANT" == "disclosed" ]]; then
  echo "  gh pr create --head $BRANCH --title 'Demo 2: gateway code, disclosed' --body-file demo/pr_body_disclosed.md"
else
  echo "  gh pr create --head $BRANCH --title 'Demo 2: gateway code, undisclosed' --body 'Adds search.'"
fi
