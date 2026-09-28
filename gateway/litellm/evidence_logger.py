"""
Demo 2: LiteLLM success callback that records *evidence* of AI-generated code.

For every successful model response it extracts candidate code (fenced blocks in
text, and string arguments of tool calls such as Claude Code's Write/Edit), hashes
each normalized line, and sends ONLY the hashes plus usage metadata to the
evidence API. No prompts or source code leave the gateway.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.environ.get("FP_MODULE_DIR", "/app/tools"))
sys.path.insert(0, os.path.dirname(__file__))
import fingerprint as fp  # noqa: E402
from litellm.integrations.custom_logger import CustomLogger  # noqa: E402

EVIDENCE_URL = os.environ.get("EVIDENCE_API_URL", "")
EVIDENCE_TOKEN = os.environ.get("EVIDENCE_INGEST_TOKEN", "")


def _post(event: dict) -> None:
    req = urllib.request.Request(EVIDENCE_URL.rstrip("/") + "/v1/events", method="POST",
                                 data=json.dumps(event).encode(),
                                 headers={"Authorization": f"Bearer {EVIDENCE_TOKEN}",
                                          "Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=5).read()


class EvidenceLogger(CustomLogger):
    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):  # noqa: ANN001
        try:
            slo = kwargs.get("standard_logging_object") or {}
            md = slo.get("metadata") or {}
            hashes, n_lines = fp.fingerprint_response(slo.get("response"))
            h2, n2 = fp.fingerprint_response(response_obj)
            hashes |= h2
            n_lines = max(n_lines, n2)
            headers = md.get("headers") or {}
            event = {
                "kind": "generation",
                "ts": datetime.fromtimestamp(slo.get("startTime") or datetime.now().timestamp(),
                                             tz=timezone.utc).isoformat(),
                "user_id": md.get("user_api_key_user_id"),
                "key_alias": md.get("user_api_key_alias"),
                "model": slo.get("model_group") or slo.get("model"),
                "client": (md.get("user_agent") or headers.get("user-agent") or "")[:80],
                "session_id": headers.get("x-claude-code-session-id") or slo.get("session_id"),
                "request_id": slo.get("id"),
                "tags": [t for t in (slo.get("request_tags") or []) if not t.startswith("User-Agent")],
                "prompt_tokens": slo.get("prompt_tokens") or 0,
                "completion_tokens": slo.get("completion_tokens") or 0,
                "cost": slo.get("response_cost") or 0,
                "code_lines": n_lines,
                "hashes": sorted(hashes),
            }
            if EVIDENCE_URL and EVIDENCE_TOKEN:
                await asyncio.to_thread(_post, event)
            print(json.dumps({"evidence": {k: v for k, v in event.items() if k != "hashes"},
                              "hash_count": len(hashes)}), flush=True)
        except Exception as e:  # noqa: BLE001 - logging must never break the request path
            print(f"evidence_logger error: {e}", flush=True)


evidence_logger = EvidenceLogger()
