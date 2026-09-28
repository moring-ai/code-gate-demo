#!/usr/bin/env python3
"""
Demo 1: Git AI attribution gate.

Reads Git AI authorship notes (refs/notes/ai) for the PR's commits, computes the
share of added lines written by AI, posts a sticky PR comment, and exits 1 when
the share exceeds the policy threshold (default 50%) so a required status check
blocks the merge.

Policy: .ai-gate/policy.toml  [gitai]
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(__file__))
import common as c  # noqa: E402

GIT_AI = os.environ.get("GIT_AI_BIN", "git-ai")
MARKER = "ai-gate:gitai"


def stats(rev: str) -> dict | None:
    res = subprocess.run([GIT_AI, "stats", rev, "--json"], cwd=c.ROOT,
                         capture_output=True, text=True, env={**os.environ, "CI": "true"})
    if res.returncode != 0:
        c.warn(f"git-ai stats {rev} failed: {res.stderr.strip()[:300]}")
        return None
    for line in res.stdout.splitlines():
        line = line.strip()
        if line.startswith("{"):
            return json.loads(line)
    return None


def has_note(sha: str) -> bool:
    return subprocess.run(["git", "notes", "--ref=ai", "show", sha], cwd=c.ROOT,
                          capture_output=True).returncode == 0


def main() -> int:
    policy = {"max_ai_share": 50, "fail_on_no_attribution": False,
              "override_label": "ai-reviewed", **c.load_policy("gitai")}
    ctx = c.pr_context()
    head = ctx["head_sha"]
    base = c.merge_base(ctx["base_sha"], head)
    commits = c.pr_commits(base, head)

    # Range stats (net diff across the PR). Needs HEAD == PR head; see workflow.
    rng = stats(f"{base}..{head}")
    if rng and "range_stats" in rng:
        totals, authorship = rng["range_stats"], rng.get("authorship_stats", {})
    else:  # Fallback: sum per-commit stats
        totals = {"ai_additions": 0, "human_additions": 0, "unknown_additions": 0,
                  "git_diff_added_lines": 0, "tool_model_breakdown": {}}
        authorship = {}
        for cm in commits:
            s = stats(cm["sha"]) or {}
            for k in ("ai_additions", "human_additions", "unknown_additions", "git_diff_added_lines"):
                totals[k] += s.get(k, 0)
            for tm, v in (s.get("tool_model_breakdown") or {}).items():
                totals["tool_model_breakdown"].setdefault(tm, {"ai_additions": 0})
                totals["tool_model_breakdown"][tm]["ai_additions"] += v.get("ai_additions", 0)

    rows = []
    for cm in commits:
        s = stats(cm["sha"]) or {}
        rows.append({**cm, "ai": s.get("ai_additions", 0), "human": s.get("human_additions", 0),
                     "unknown": s.get("unknown_additions", 0), "noted": has_note(cm["sha"])})

    added = totals.get("git_diff_added_lines", 0)
    ai = totals.get("ai_additions", 0)
    human = totals.get("human_additions", 0)
    unknown = totals.get("unknown_additions", 0)
    share = c.pct(ai, added)
    noted = sum(r["noted"] for r in rows)
    override = policy["override_label"] in ctx["labels"]

    if noted == 0:
        verdict = "fail" if policy["fail_on_no_attribution"] else "warn"
        headline = "No Git AI attribution found for this PR"
    elif share > policy["max_ai_share"]:
        verdict = "override" if override else "fail"
        headline = f"{share}% of added lines were written by AI (threshold {policy['max_ai_share']}%)"
    else:
        verdict = "pass"
        headline = f"{share}% of added lines were written by AI (threshold {policy['max_ai_share']}%)"

    icon = {"pass": "✅ Passed", "fail": "❌ Blocked", "warn": "⚠️ Warning",
            "override": "🟡 Passed with security override"}[verdict]
    md = [f"### 🤖 AI attribution gate (Git AI): {icon}", "", f"**{headline}**", ""]
    if noted:
        md += [f"`human {c.bar(100 - share)} ai`", "",
               "| Attribution | Added lines | Share |", "|---|---:|---:|",
               f"| AI (agent-reported) | {ai} | {share}% |",
               f"| Human (known) | {human} | {c.pct(human, added)}% |",
               f"| Unknown (no attestation) | {unknown} | {c.pct(unknown, added)}% |",
               f"| **Total added** | **{added}** | |", ""]
        tm = totals.get("tool_model_breakdown") or {}
        if tm:
            md += ["**AI lines by tool and model**", "", "| Tool::model | AI lines |", "|---|---:|"]
            md += [f"| `{k}` | {v.get('ai_additions', 0)} |" for k, v in sorted(tm.items())]
            md += [""]
    md += ["<details><summary>Per-commit breakdown</summary>", "",
           "| Commit | Subject | AI | Human | Unknown | Git AI note |", "|---|---|---:|---:|---:|:---:|"]
    for r in rows:
        md.append(f"| `{r['sha'][:7]}` | {r['subject'][:60]} | {r['ai']} | {r['human']} | "
                  f"{r['unknown']} | {'✔' if r['noted'] else '✘'} |")
    md += ["", "</details>", ""]
    missing = authorship.get("commits_without_authorship") or [r["sha"] for r in rows if not r["noted"]]
    if missing:
        md += [f"⚠️ {len(missing)} commit(s) have no Git AI note. Their lines count as *unknown*. "
               "Install Git AI locally and push notes with `git push origin refs/notes/ai`.", ""]
    if verdict == "fail" and noted:
        md += ["**To merge:** reduce the AI-written share, or have a security reviewer review the "
               f"AI-generated code and apply the `{policy['override_label']}` label.", ""]
    md += ["<sub>Source: Git AI line-level attribution in `refs/notes/ai`. Agents report which lines "
           "they wrote; nothing is guessed from code style.</sub>"]
    body = "\n".join(md)

    print(body)
    c.write_summary(body)
    c.upsert_comment(ctx["number"], MARKER, body)
    if ai > 0:
        c.set_label(ctx["number"], "ai-assisted", True)
    c.set_output("ai_share", share)
    c.set_output("verdict", verdict)
    with open(c.ROOT / "ai-gate-gitai.json", "w") as fh:
        json.dump({"verdict": verdict, "ai_share": share, "totals": totals, "commits": rows}, fh, indent=2)
    return 1 if verdict == "fail" else 0


if __name__ == "__main__":
    sys.exit(main())
