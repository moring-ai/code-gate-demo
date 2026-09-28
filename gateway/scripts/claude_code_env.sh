#!/usr/bin/env bash
# Point Claude Code at the gateway for this shell:   source gateway/scripts/claude_code_env.sh <virtual-key>
# (Put the same values in ~/.claude/settings.json "env" to make it permanent; run /status in Claude Code to confirm.)
export ANTHROPIC_BASE_URL="${LITELLM_URL:-http://localhost:4000}"
export ANTHROPIC_AUTH_TOKEN="${1:?usage: source claude_code_env.sh <virtual-key>}"
unset ANTHROPIC_API_KEY
# Pin model ids to names the gateway approves (see litellm/config.yaml).
export ANTHROPIC_DEFAULT_SONNET_MODEL=claude-sonnet-5
export ANTHROPIC_DEFAULT_OPUS_MODEL=claude-opus-5-5
export ANTHROPIC_DEFAULT_HAIKU_MODEL=claude-haiku-4-5-20251001
export ANTHROPIC_CUSTOM_HEADERS="x-litellm-tags: repo:ai-code-gate-demo"
echo "Claude Code -> $ANTHROPIC_BASE_URL (key ${ANTHROPIC_AUTH_TOKEN:0:8}…)"
