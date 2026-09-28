#!/usr/bin/env python3
"""
Demo 2: LLM gateway evidence report.

1. Hash every added line in the PR (same normalization as the gateway logger).
2. Ask the evidence API which of those hashes the gateway returned to the PR's
   author(s) around the time of the PR.
3. Post a sticky PR comment: gateway usage (requests, models, clients, tokens),
   matched-line share per file, and policy events (blocked / redacted prompts).
4. In enforce mode, fail when matched code is undisclosed, or above a ceiling.

Env: EVIDENCE_API_URL, EVIDENCE_QUERY_TOKEN, optional FP_HMAC_KEY
Policy: .ai-gate/policy.toml  [gateway]  (+ [disclosure] for disclosure signals)
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(__file__))
import common as c  # noqa: E402
import fingerprint as fp  # noqa: E402
from disclosure_check import detect_disclosure, gitai_ai_lines  # noqa: E402

MARKER = "ai-gate:gateway"


def query(url: str, token: str, payload: dict) -> dict:
    req = urllib.request.Request(url.rstrip("/") + "/v1/match", method="POST",
                                 data=json.dumps(payload).encode(),
                                 headers={"Authorization": f"Bearer {token}",
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def main() -> int:
    policy = {"mode": "report", "lookback_hours": 72, "max_matched_share": 0,
              "undisclosed_threshold": 20, "exclude_paths": [], "identities": {},
              **c.load_policy("gateway")}
    ctx = c.pr_context()
    base = c.merge_base(ctx["base_sha"], ctx["head_sha"])
    commits = c.pr_commits(base, ctx["head_sha"])
    added = c.added_lines(base, ctx["head_sha"], policy["exclude_paths"])

    url, token = os.environ.get("EVIDENCE_API_URL"), os.environ.get("EVIDENCE_QUERY_TOKEN")
    if not (url and token):
        msg = ("### 🛰️ LLM gateway evidence: ⚠️ Not configured\n\n"
               "Set `EVIDENCE_API_URL` and `EVIDENCE_QUERY_TOKEN` repository secrets "
               "(fork PRs never receive secrets).")
        print(msg)
        c.write_summary(msg)
        return 0

    # Who wrote this PR? PR author + GitHub-linked commit authors, mapped to gateway user ids.
    logins = {x for x in [ctx["author"], *c.pr_commit_logins(ctx["number"])] if x}
    logins |= {x.strip() for x in os.environ.get("EXTRA_GATEWAY_USERS", "").split(",") if x.strip()}
    user_ids = sorted({policy["identities"].get(lg, lg) for lg in logins})

    dates = [datetime.fromisoformat(cm["date"]) for cm in commits] or [datetime.now(timezone.utc)]
    since = (min(dates) - timedelta(hours=policy["lookback_hours"])).astimezone(timezone.utc)
    until = datetime.now(timezone.utc)

    # hash -> [(file, line)]
    index: dict[str, list[tuple[str, int]]] = {}
    total_added = 0
    for path, lines in added.items():
        for no, text in lines:
            total_added += 1
            n = fp.normalize(text)
            if n:
                index.setdefault(fp.line_hash(n), []).append((path, no))
    fingerprintable = sum(len(v) for v in index.values())

    try:
        res = query(url, token, {"user_ids": user_ids, "since": since.isoformat(),
                                 "until": until.isoformat(), "hashes": sorted(index)})
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        msg = f"### 🛰️ LLM gateway evidence: ⚠️ Evidence API unreachable\n\n`{e}`"
        print(msg)
        c.write_summary(msg)
        return 0 if policy["mode"] == "report" else 1

    matched = set(res.get("matched") or [])
    per_file: dict[str, int] = {}
    for h in matched:
        for path, _ in index.get(h, []):
            per_file[path] = per_file.get(path, 0) + 1
    matched_lines = sum(per_file.values())
    share = c.pct(matched_lines, fingerprintable)
    usage = res.get("usage") or {}
    events = res.get("policy_events") or []

    sig = detect_disclosure(ctx, commits, added, c.load_policy("disclosure"))
    # A Git AI note alone is not a disclosure (humans get notes too); AI-attributed lines are.
    gitai_ai = gitai_ai_lines(base, ctx["head_sha"]) if sig["gitai_noted_commits"] else 0
    disclosed = sig["declared"] or bool(gitai_ai)

    problems = []
    if matched_lines and share >= policy["undisclosed_threshold"] and not disclosed:
        problems.append(f"{share}% of added lines match code the gateway returned to "
                        f"{', '.join('@' + u for u in user_ids)}, but the PR does not disclose AI use")
    if policy["max_matched_share"] and share > policy["max_matched_share"]:
        problems.append(f"Gateway-matched share {share}% exceeds {policy['max_matched_share']}%")
    failing = bool(problems) and policy["mode"] == "enforce"

    status = "❌ Blocked" if failing else ("⚠️ Findings" if problems else "✅ Passed")
    md = [f"### 🛰️ LLM gateway evidence: {status}", "",
          f"**{matched_lines} of {fingerprintable} fingerprintable added lines ({share}%) match code "
          f"the gateway returned to {', '.join('@' + u for u in user_ids) or 'the author'}** "
          f"between {since:%Y-%m-%d %H:%M} and {until:%Y-%m-%d %H:%M} UTC.", "",
          f"`human {c.bar(100 - share)} gateway-matched`", "",
          "| Gateway usage in window | |", "|---|---|",
          f"| Requests | {usage.get('requests', 0)} |",
          f"| Sessions | {usage.get('sessions', 0)} |",
          f"| Models | {', '.join(f'`{k}` ×{v}' for k, v in (usage.get('models') or {}).items()) or '—'} |",
          f"| Clients | {', '.join(f'`{k}` ×{v}' for k, v in (usage.get('clients') or {}).items()) or '—'} |",
          f"| Tokens (in / out) | {usage.get('prompt_tokens', 0):,} / {usage.get('completion_tokens', 0):,} |",
          f"| Cost (USD) | {usage.get('cost', 0):.4f} |",
          f"| AI disclosed in PR | {'yes' if disclosed else 'no'} |", ""]
    if per_file:
        md += ["| File | Matched lines |", "|---|---:|"]
        md += [f"| `{p}` | {n} |" for p, n in sorted(per_file.items(), key=lambda kv: -kv[1])[:15]]
        md += [""]
    if events:
        counts: dict[str, int] = {}
        for ev in events:
            for cls in ev.get("classes") or [ev.get("reason", "policy")]:
                key = f"{ev.get('action')} · {cls}"
                counts[key] = counts.get(key, 0) + 1
        md += ["**Gateway policy events for these users in the window**", "",
               "| Action · class | Count |", "|---|---:|"]
        md += [f"| {k} | {v} |" for k, v in sorted(counts.items())] + [""]
    if problems:
        md += ["**Findings**", ""] + [f"- {p}" for p in problems] + [""]
    md += [f"<sub>Mode: `{policy['mode']}`. The gateway stores only hashes of normalized code lines, "
           "never prompts or source.</sub>"]
    body = "\n".join(md)

    print(body)
    c.write_summary(body)
    c.upsert_comment(ctx["number"], MARKER, body)
    c.set_output("matched_share", share)
    return 1 if failing else 0


if __name__ == "__main__":
    sys.exit(main())
