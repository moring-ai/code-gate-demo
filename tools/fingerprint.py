"""
Line fingerprinting shared by the gateway (evidence logger) and CI (gateway report).

Design goals
- The gateway never stores raw code: only short hashes of normalized lines.
- CI hashes the PR's added lines the same way and asks "which of these did the
  gateway return to this developer recently?"
- Stdlib only, so it can be mounted into the LiteLLM container and run in CI.

Normalization collapses whitespace so re-indentation or formatter changes still
match. Trivial lines (brackets, `else:`, `pass`, very short lines) are skipped
because they would match by coincidence.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
from typing import Any, Iterable

MIN_LINE_CHARS = int(os.environ.get("FP_MIN_LINE_CHARS", "10"))
# Optional shared secret. If set on both gateway and CI, hashes become HMACs,
# so the evidence store cannot be brute-forced for common lines.
_HMAC_KEY = os.environ.get("FP_HMAC_KEY", "").encode()

_WS = re.compile(r"\s+")
_TRIVIAL = re.compile(
    r"^(?:[\s\{\}\[\]\(\);,:.]*|else:?|try:|finally:|pass|break|continue|return|"
    r"end|fi|done|\*/|/\*|//|#|\"\"\"|''')$",
    re.IGNORECASE,
)
_FENCE = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)
# Tool-call argument keys whose values are NOT new code (e.g. the text being replaced).
_SKIP_KEYS = {"old_string", "old_str", "file_path", "path", "pattern", "description", "url"}


def normalize(line: str) -> str | None:
    s = _WS.sub(" ", line.strip())
    if len(s) < MIN_LINE_CHARS or _TRIVIAL.match(s):
        return None
    return s


def line_hash(normalized: str) -> str:
    data = normalized.encode("utf-8", "replace")
    if _HMAC_KEY:
        return hmac.new(_HMAC_KEY, data, hashlib.sha256).hexdigest()[:16]
    return hashlib.sha256(data).hexdigest()[:16]


def hash_lines(lines: Iterable[str]) -> set[str]:
    out: set[str] = set()
    for ln in lines:
        n = normalize(ln)
        if n:
            out.add(line_hash(n))
    return out


# --------------------------------------------------------------------------
# Extract candidate code from LLM responses (Anthropic or OpenAI shapes)
# --------------------------------------------------------------------------
def _walk_strings(obj: Any, parent_key: str = "") -> Iterable[str]:
    if isinstance(obj, str):
        if parent_key not in _SKIP_KEYS and ("\n" in obj or len(obj) > 40):
            yield obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield from _walk_strings(v, k)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_strings(v, parent_key)


def _code_from_text(text: str) -> list[str]:
    blocks = _FENCE.findall(text or "")
    return blocks


def extract_code_blobs(response: Any) -> list[str]:
    """Return strings that are likely code the model produced.

    - Fenced code blocks in text content
    - All string arguments of tool calls (Write/Edit/Bash etc.), except keys
      that carry pre-existing content such as `old_string`
    """
    blobs: list[str] = []
    if response is None:
        return blobs
    if hasattr(response, "model_dump"):
        response = response.model_dump()
    if isinstance(response, str):
        try:
            response = json.loads(response)
        except ValueError:
            return _code_from_text(response)
    if not isinstance(response, dict):
        return blobs

    # Anthropic Messages format
    for block in response.get("content") or []:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            blobs += _code_from_text(block.get("text", ""))
        elif btype in ("tool_use", "server_tool_use"):
            blobs += list(_walk_strings(block.get("input") or {}))

    # OpenAI chat.completions format
    for choice in response.get("choices") or []:
        msg = (choice or {}).get("message") or {}
        content = msg.get("content")
        if isinstance(content, str):
            blobs += _code_from_text(content)
        for tc in msg.get("tool_calls") or []:
            args = ((tc or {}).get("function") or {}).get("arguments")
            if isinstance(args, str):
                try:
                    blobs += list(_walk_strings(json.loads(args)))
                except ValueError:
                    blobs.append(args)
    return blobs


def fingerprint_response(response: Any) -> tuple[set[str], int]:
    """Return (set of line hashes, number of code lines seen)."""
    hashes: set[str] = set()
    n_lines = 0
    for blob in extract_code_blobs(response):
        lines = blob.splitlines()
        n_lines += len(lines)
        hashes |= hash_lines(lines)
    return hashes, n_lines
