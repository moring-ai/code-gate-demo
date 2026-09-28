"""
Demo 4: Org AI usage policy as a LiteLLM pre-call guardrail.

Checks every request before it reaches the model provider:
  1. Approved models          (data["model"])
  2. Approved clients/tools   (User-Agent prefix, e.g. "claude-cli/" = Claude Code)
  3. Data classes             (secrets, PII, classification labels, customer IDs)
     each with an action: block | redact | log

mode: monitor  -> never blocks or redacts, only records events (start here)
mode: enforce  -> applies the actions

Scans every message, including tool results. That matters for coding agents:
when an agent reads `.env` or a customer export, that content is sent to the
model in the next request. Prefer `redact` for secrets: agents resend the whole
conversation, so a `block` would fail every later request in that session.

Policy file: org_policy.yml (path via ORG_POLICY_PATH)
"""
from __future__ import annotations

import json
import os
import re
import threading
import urllib.request
from datetime import datetime, timezone
from typing import Any

import yaml
from fastapi import HTTPException
from litellm.integrations.custom_guardrail import CustomGuardrail

POLICY_PATH = os.environ.get("ORG_POLICY_PATH", os.path.join(os.path.dirname(__file__), "org_policy.yml"))
EVIDENCE_URL = os.environ.get("EVIDENCE_API_URL", "")
EVIDENCE_TOKEN = os.environ.get("EVIDENCE_INGEST_TOKEN", "")


def _luhn(number: str) -> bool:
    digits = [int(d) for d in re.sub(r"\D", "", number)]
    if len(digits) < 13:
        return False
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


VALIDATORS = {"luhn": _luhn}


def _emit(event: dict) -> None:
    """Fire-and-forget event to the evidence API; never blocks the request path."""
    print(json.dumps({"org_policy_event": event}), flush=True)
    if not (EVIDENCE_URL and EVIDENCE_TOKEN):
        return

    def _post() -> None:
        try:
            req = urllib.request.Request(EVIDENCE_URL.rstrip("/") + "/v1/events", method="POST",
                                         data=json.dumps(event).encode(),
                                         headers={"Authorization": f"Bearer {EVIDENCE_TOKEN}",
                                                  "Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=3).read()
        except Exception as e:  # noqa: BLE001
            print(f"org-policy: evidence post failed: {e}", flush=True)

    threading.Thread(target=_post, daemon=True).start()


class OrgPolicyGuardrail(CustomGuardrail):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        with open(POLICY_PATH) as fh:
            self.policy = yaml.safe_load(fh) or {}
        self.mode = os.environ.get("ORG_POLICY_MODE", self.policy.get("mode", "monitor"))
        self.models = set(self.policy.get("approved_models") or [])
        self.clients = tuple(self.policy.get("approved_clients") or [])
        self.scan_system = bool(self.policy.get("scan_system_prompt", False))
        self.classes = []
        for dc in self.policy.get("data_classes") or []:
            self.classes.append({
                "id": dc["id"], "action": dc.get("action", "log"),
                "re": re.compile(dc["pattern"]), "group": int(dc.get("group", 0)),
                "validator": VALIDATORS.get(dc.get("validator", "")),
            })

    # ----------------------------------------------------------- helpers
    def _findings(self, text: str) -> list[tuple[dict, re.Match]]:
        hits = []
        for dc in self.classes:
            for m in dc["re"].finditer(text):
                if dc["validator"] and not dc["validator"](m.group(0)):
                    continue
                hits.append((dc, m))
        return hits

    def _walk(self, obj: Any, found: dict[str, str], redact: bool) -> Any:
        """Return obj with redactions applied (if redact); record class->action in found."""
        if isinstance(obj, str):
            hits = self._findings(obj)
            if not hits:
                return obj
            for dc, _ in hits:
                found[dc["id"]] = dc["action"]
            if not redact:
                return obj
            out = obj
            for dc in self.classes:
                if dc["action"] != "redact":
                    continue
                def _sub(m: re.Match, dc: dict = dc) -> str:
                    if dc["validator"] and not dc["validator"](m.group(0)):
                        return m.group(0)
                    g = dc["group"]
                    if g and m.group(g):
                        s, e = m.span(g)
                        return m.group(0)[: s - m.start()] + f"[REDACTED:{dc['id']}]" + m.group(0)[e - m.start():]
                    return f"[REDACTED:{dc['id']}]"
                out = dc["re"].sub(_sub, out)
            return out
        if isinstance(obj, list):
            return [self._walk(v, found, redact) for v in obj]
        if isinstance(obj, dict):
            return {k: self._walk(v, found, redact) for k, v in obj.items()}
        return obj

    def _deny(self, base: dict, reason: str, classes: list[str] | None = None) -> None:
        _emit({**base, "action": "block" if self.mode == "enforce" else "would_block",
               "reason": reason, "classes": classes or []})
        if self.mode == "enforce":
            raise HTTPException(status_code=403, detail={
                "error": f"Blocked by org AI policy: {reason}",
                "policy": "org-ai-policy", "classes": classes or []})

    # ----------------------------------------------------------- hook
    async def async_pre_call_hook(self, user_api_key_dict, cache, data: dict, call_type):  # noqa: ANN001
        md = data.get("litellm_metadata") or data.get("metadata") or {}
        headers = (data.get("proxy_server_request") or {}).get("headers") or {}
        client = md.get("user_agent") or headers.get("user-agent") or ""
        model = data.get("model") or ""
        base = {"kind": "policy", "ts": datetime.now(timezone.utc).isoformat(),
                "user_id": getattr(user_api_key_dict, "user_id", None),
                "key_alias": getattr(user_api_key_dict, "key_alias", None),
                "model": model, "client": client[:80], "mode": self.mode}

        if self.models and model not in self.models:
            self._deny(base, f"model '{model}' is not on the approved list", ["unapproved_model"])
        if self.clients and not client.startswith(self.clients):
            self._deny(base, f"client '{client[:40] or 'unknown'}' is not an approved AI tool",
                       ["unapproved_client"])

        fields = ["messages"] + (["system"] if self.scan_system else [])
        found: dict[str, str] = {}
        for f in fields:
            if f in data:
                self._walk(data[f], found, redact=False)
        if not found:
            return data

        blocked = sorted(k for k, a in found.items() if a == "block")
        redacted = sorted(k for k, a in found.items() if a == "redact")
        logged = sorted(k for k, a in found.items() if a == "log")
        if blocked:
            self._deny(base, "prompt contains restricted data", blocked)
        if redacted:
            _emit({**base, "action": "redact" if self.mode == "enforce" else "would_redact",
                   "classes": redacted, "reason": "sensitive values removed before sending"})
            if self.mode == "enforce":
                for f in fields:
                    if f in data:
                        data[f] = self._walk(data[f], {}, redact=True)
        if logged:
            _emit({**base, "action": "log", "classes": logged, "reason": "sensitive data observed"})
        return data
