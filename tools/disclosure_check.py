#!/usr/bin/env python3
"""
Demo 3: AI disclosure check (manual tagging).

Conventions this check enforces
  1. Commit trailer      Assisted-by: claude-code:claude-sonnet-5
  2. In-code markers     # @ai-generated begin tool=claude-code model=claude-sonnet-5 reviewed-by=@alice
                         ...code...
                         # @ai-generated end
                         (or a single "# @ai-generated file tool=... reviewed-by=..." header)
  3. PR template         - [x] This PR contains AI-generated code
                         - [x] I have reviewed all AI-generated code line by line

Rules
  - Markers must be well-formed: begin/end balanced, required fields present.
  - If AI is declared anywhere, the "I have reviewed" checkbox must be ticked.
  - If Git AI notes show AI-written lines but nothing is declared -> undisclosed AI.

Policy: .ai-gate/policy.toml  [disclosure]
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(__file__))
import common as c  # noqa: E402

MARKER = "ai-gate:disclosure"
MARK_RE = re.compile(r"@ai-generated\s+(begin|end|file)\b(.*)$", re.IGNORECASE)
KV_RE = re.compile(r"([\w-]+)=(\S+)")
CHECK_AI = re.compile(r"-\s*\[[xX]\]\s*This PR contains AI-generated code")
CHECK_REVIEWED = re.compile(r"-\s*\[[xX]\]\s*I have reviewed all AI-generated code")
BOT_HINTS = ("[bot]", "copilot", "devin", "claude", "codex", "cursor")
PLACEHOLDER_RE = re.compile(r"(?i)^@?(replace[-_]?me|todo|tbd|fixme|your[-_]?handle|your[-_]?name|"
                            r"username|user|reviewer|human|someone|x{2,}|none|n/?a|unknown)$")
HANDLE_RE = re.compile(r"^@[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
_user_exists: dict[str, bool] = {}


def reviewer_problem(handle: str) -> str | None:
    """Reject placeholders, malformed handles and (in CI) accounts that don't exist."""
    if PLACEHOLDER_RE.match(handle):
        return "is a placeholder, not a person"
    if not HANDLE_RE.match(handle):
        return "is not a GitHub handle (expected @username)"
    if os.environ.get("GITHUB_TOKEN"):
        name = handle[1:].lower()
        if name not in _user_exists:
            _user_exists[name] = c.gh_api("GET", f"/users/{name}") is not None
        if not _user_exists[name]:
            return "is not an existing GitHub account"
    return None


def detect_disclosure(ctx: dict, commits: list[dict], added: dict, policy: dict) -> dict:
    """Collect every declared-AI signal. Shared with gateway_report.py."""
    trailers = [t.lower() for t in policy.get("trailers", [])]
    sig: dict = {"trailer_commits": [], "markers": [], "checkbox_ai": False,
                 "checkbox_reviewed": False, "agent_branch": False, "bot_commits": [],
                 "gitai_noted_commits": []}
    for cm in commits:
        body_lines = [ln.strip().lower() for ln in cm["body"].splitlines()]
        if any(ln.startswith(t) for ln in body_lines for t in trailers):
            sig["trailer_commits"].append(cm["sha"][:7])
        who = f"{cm['author']} {cm['email']}".lower()
        if any(h in who for h in BOT_HINTS):
            sig["bot_commits"].append(cm["sha"][:7])
        if subprocess.run(["git", "notes", "--ref=ai", "show", cm["sha"]], cwd=c.ROOT,
                          capture_output=True).returncode == 0:
            sig["gitai_noted_commits"].append(cm["sha"][:7])
    for path, lines in added.items():
        for no, text in lines:
            m = MARK_RE.search(text)
            if m:
                sig["markers"].append({"file": path, "line": no, "kind": m.group(1).lower(),
                                       "fields": dict(KV_RE.findall(m.group(2)))})
    body = ctx.get("body") or ""
    sig["checkbox_ai"] = bool(CHECK_AI.search(body))
    sig["checkbox_reviewed"] = bool(CHECK_REVIEWED.search(body))
    sig["agent_branch"] = any((ctx.get("head_ref") or "").startswith(p)
                              for p in policy.get("agent_branch_prefixes", []))
    sig["declared"] = bool(sig["trailer_commits"] or sig["markers"] or sig["checkbox_ai"])
    return sig


def validate_markers(markers: list[dict], added: dict, require_reviewer: bool) -> tuple[list[str], int]:
    """Return (problems, declared_line_count)."""
    problems: list[str] = []
    declared = 0
    by_file: dict[str, list[dict]] = {}
    for mk in markers:
        by_file.setdefault(mk["file"], []).append(mk)
    for path, mks in by_file.items():
        open_at: int | None = None
        for mk in sorted(mks, key=lambda m: m["line"]):
            loc = f"`{path}:{mk['line']}`"
            if mk["kind"] in ("begin", "file"):
                if "tool" not in mk["fields"]:
                    problems.append(f"{loc} marker is missing `tool=`")
                if require_reviewer and "reviewed-by" not in mk["fields"]:
                    problems.append(f"{loc} marker is missing `reviewed-by=@<human>`")
                elif require_reviewer:
                    who = mk["fields"]["reviewed-by"]
                    why = reviewer_problem(who)
                    if why:
                        problems.append(f"{loc} reviewer `{who}` {why}")
            if mk["kind"] == "file":
                declared += len(added.get(path, []))
            elif mk["kind"] == "begin":
                if open_at is not None:
                    problems.append(f"{loc} nested `begin` (previous begin at line {open_at})")
                open_at = mk["line"]
            elif mk["kind"] == "end":
                if open_at is None:
                    problems.append(f"{loc} `end` without matching `begin`")
                else:
                    declared += sum(1 for no, _ in added.get(path, []) if open_at < no < mk["line"])
                    open_at = None
        if open_at is not None:
            problems.append(f"`{path}:{open_at}` `begin` marker is never closed")
    return problems, declared


def gitai_ai_lines(base: str, head: str) -> int | None:
    exe = os.environ.get("GIT_AI_BIN", "git-ai")
    if not shutil.which(exe):
        return None
    res = subprocess.run([exe, "stats", f"{base}..{head}", "--json"], cwd=c.ROOT,
                         capture_output=True, text=True, env={**os.environ, "CI": "true"})
    try:
        data = json.loads(next(ln for ln in res.stdout.splitlines() if ln.startswith("{")))
        return (data.get("range_stats") or data).get("ai_additions", 0)
    except (StopIteration, ValueError):
        return None


def main() -> int:
    policy = {"trailers": ["Assisted-by"], "require_reviewed_by": True,
              "require_review_checkbox": True, "agent_branch_prefixes": [], "exclude_paths": [],
              **c.load_policy("disclosure")}
    ctx = c.pr_context()
    base = c.merge_base(ctx["base_sha"], ctx["head_sha"])
    commits = c.pr_commits(base, ctx["head_sha"])
    added = c.added_lines(base, ctx["head_sha"], policy["exclude_paths"])
    total_added = sum(len(v) for v in added.values())

    sig = detect_disclosure(ctx, commits, added, policy)
    problems, declared_lines = validate_markers(sig["markers"], added, policy["require_reviewed_by"])
    ai_lines = gitai_ai_lines(base, ctx["head_sha"]) if sig["gitai_noted_commits"] else 0

    implied = sig["agent_branch"] or bool(sig["bot_commits"]) or bool(ai_lines)
    if (sig["declared"] or implied) and policy["require_review_checkbox"] and not sig["checkbox_reviewed"]:
        problems.append("AI involvement detected but the PR checkbox "
                        "“I have reviewed all AI-generated code” is not ticked")
    if implied and not sig["declared"]:
        why = []
        if ai_lines:
            why.append(f"Git AI attributes {ai_lines} added line(s) to AI")
        if sig["agent_branch"]:
            why.append(f"branch `{ctx['head_ref']}` uses an agent prefix")
        if sig["bot_commits"]:
            why.append(f"commits {', '.join(sig['bot_commits'])} look agent-authored")
        problems.append("Undisclosed AI: " + "; ".join(why) +
                        ". Add an `Assisted-by:` trailer, `@ai-generated` markers, or tick the PR checkbox.")

    verdict = "fail" if problems else "pass"
    yes, no = "✅", "—"
    md = [f"### 🏷️ AI disclosure check: {'❌ Failed' if problems else '✅ Passed'}", "",
          "| Signal | Found |", "|---|---|",
          f"| `Assisted-by` / co-author trailers | {', '.join(sig['trailer_commits']) or no} |",
          f"| `@ai-generated` markers | {len(sig['markers']) or no} "
          f"({declared_lines} of {total_added} added lines inside markers) |",
          f"| PR checkbox: contains AI code | {yes if sig['checkbox_ai'] else no} |",
          f"| PR checkbox: reviewed all AI code | {yes if sig['checkbox_reviewed'] else no} |",
          f"| Git AI notes | {len(sig['gitai_noted_commits'])} commit(s)"
          f"{f', {ai_lines} AI lines' if ai_lines else ''} |",
          f"| Agent branch / bot commits | {yes if (sig['agent_branch'] or sig['bot_commits']) else no} |", ""]
    if problems:
        md += ["**Problems**", ""] + [f"- {p}" for p in problems] + [""]
    md += ["<sub>Conventions: see `AGENTS.md`. Markers need `tool=` and `reviewed-by=@<human>`.</sub>"]
    body = "\n".join(md)

    print(body)
    c.write_summary(body)
    c.upsert_comment(ctx["number"], MARKER, body)
    if sig["declared"]:
        c.set_label(ctx["number"], "ai-assisted", True)
    c.set_output("verdict", verdict)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
