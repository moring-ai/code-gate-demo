#!/usr/bin/env bash
# Demo 4 smoke test: sends allowed and non-compliant requests through the gateway.
# Usage: LITELLM_URL=http://localhost:4000 KEY=sk-... ./policy_smoke_test.sh
set -uo pipefail
URL="${LITELLM_URL:-http://localhost:4000}"
KEY="${KEY:-${LITELLM_MASTER_KEY:?set KEY or LITELLM_MASTER_KEY}}"
pass=0; fail=0

check() {  # check <name> <expected_http> <json> [extra curl args...]
  local name="$1" want="$2" body="$3"; shift 3
  local code
  code=$(curl -s -o /tmp/smoke.out -w "%{http_code}" "$URL/v1/messages" \
    -H "Authorization: Bearer $KEY" -H "content-type: application/json" "$@" -d "$body")
  if [[ "$code" == "$want" ]]; then pass=$((pass+1)); mark="PASS"; else fail=$((fail+1)); mark="FAIL"; fi
  printf '%-4s %-38s http %s  %s\n' "$mark" "$name" "$code" "$(head -c 110 /tmp/smoke.out | tr '\n' ' ')"
}
msg() { printf '{"model":"%s","max_tokens":50,"messages":[{"role":"user","content":"%s"}]}' "$1" "$2"; }

check "allowed: approved model + client"   200 "$(msg demo-mock 'write search_users')"
check "allowed: Claude Code user agent"    200 "$(msg demo-mock 'hello')" -H "user-agent: claude-cli/2.1.210 (external, cli)"
check "block: unapproved model"            403 "$(msg unapproved-model 'hi')"
check "block: unapproved client"           403 "$(msg demo-mock 'hi')" -H "user-agent: python-requests/2.32"
check "block: US SSN in prompt"            403 "$(msg demo-mock 'customer ssn is 123-45-6789')"
check "block: card number (Luhn valid)"    403 "$(msg demo-mock 'card 4111 1111 1111 1111')"
check "allow: 13-digit timestamp, bad Luhn" 200 "$(msg demo-mock 'ts 1790556942000 id 4111111111111112')"
check "block: CONFIDENTIAL label"          403 "$(msg demo-mock 'CONFIDENTIAL: Q3 board deck')"
check "block: customer id"                 403 "$(msg demo-mock 'refund CUST-12345678')"
check "redact (allowed): AWS key, password" 200 "$(msg demo-mock 'key AKIAIOSFODNN7EXAMPLE password = hunter2hunter2')"
check "block: private key in tool_result"  403 '{"model":"demo-mock","max_tokens":50,"messages":[{"role":"user","content":"read it"},{"role":"assistant","content":[{"type":"tool_use","id":"t1","name":"Read","input":{"file_path":"id_rsa"}}]},{"role":"user","content":[{"type":"tool_result","tool_use_id":"t1","content":"-----BEGIN OPENSSH PRIVATE KEY-----\nabc"}]}]}'
echo "---- $pass passed, $fail failed"
[[ $fail -eq 0 ]]
