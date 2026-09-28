"""Shared helpers for the AI code gate CI scripts. Stdlib only (Python 3.11+)."""
from __future__ import annotations

import fnmatch
import json
import os
import re
import subprocess
import sys
import tomllib
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(os.environ.get("GITHUB_WORKSPACE", ".")).resolve()


# ------------------------------------------------------------------ shell / git
def run(cmd: list[str], check: bool = True, cwd: Path | None = None) -> str:
    res = subprocess.run(cmd, cwd=cwd or ROOT, capture_output=True, text=True)
    if check and res.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} failed ({res.returncode}): {res.stderr.strip()}")
    return res.stdout


def merge_base(base: str, head: str) -> str:
    return run(["git", "merge-base", base, head]).strip()


def pr_commits(base: str, head: str) -> list[dict[str, str]]:
    fmt = "%H%x1f%an%x1f%ae%x1f%aI%x1f%s%x1f%B%x1e"
    out = run(["git", "log", "--no-merges", f"--format={fmt}", f"{base}..{head}"])
    commits = []
    for rec in out.split("\x1e"):
        rec = rec.strip("\n")
        if not rec:
            continue
        sha, an, ae, date, subject, body = (rec.split("\x1f") + [""] * 6)[:6]
        commits.append({"sha": sha, "author": an, "email": ae, "date": date,
                        "subject": subject, "body": body})
    return commits


_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def added_lines(base: str, head: str, exclude: list[str] | None = None) -> dict[str, list[tuple[int, str]]]:
    """Map file -> [(line_no, text)] for lines added between base and head."""
    exclude = exclude or []
    out = run(["git", "diff", "--no-color", "--no-ext-diff", "-U0", f"{base}...{head}"])
    files: dict[str, list[tuple[int, str]]] = {}
    current: str | None = None
    line_no = 0
    for raw in out.splitlines():
        if raw.startswith("+++ "):
            path = raw[4:]
            current = None if path == "/dev/null" else path[2:] if path.startswith("b/") else path
            if current and any(fnmatch.fnmatch(current, pat) for pat in exclude):
                current = None
            continue
        m = _HUNK.match(raw)
        if m:
            line_no = int(m.group(1))
            continue
        if current and raw.startswith("+") and not raw.startswith("+++"):
            files.setdefault(current, []).append((line_no, raw[1:]))
            line_no += 1
    return files


# ------------------------------------------------------------------ policy
def load_policy(section: str) -> dict[str, Any]:
    path = ROOT / ".ai-gate" / "policy.toml"
    if not path.exists():
        return {}
    with path.open("rb") as fh:
        return tomllib.load(fh).get(section, {})


# ------------------------------------------------------------------ PR context
def pr_context() -> dict[str, Any]:
    """PR metadata from the GitHub event, with env overrides for local runs."""
    ctx: dict[str, Any] = {"number": None, "base_sha": None, "head_sha": None,
                           "author": None, "labels": [], "body": "", "head_ref": ""}
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if event_path and Path(event_path).exists():
        ev = json.loads(Path(event_path).read_text())
        pr = ev.get("pull_request") or {}
        ctx.update(
            number=pr.get("number"),
            base_sha=(pr.get("base") or {}).get("sha"),
            head_sha=(pr.get("head") or {}).get("sha"),
            head_ref=(pr.get("head") or {}).get("ref", ""),
            author=(pr.get("user") or {}).get("login"),
            labels=[lbl["name"] for lbl in pr.get("labels") or []],
            body=pr.get("body") or "",
        )
    # Local / manual overrides
    ctx["base_sha"] = os.environ.get("BASE_SHA", ctx["base_sha"]) or "origin/main"
    ctx["head_sha"] = os.environ.get("HEAD_SHA", ctx["head_sha"]) or "HEAD"
    ctx["author"] = os.environ.get("PR_AUTHOR", ctx["author"])
    if os.environ.get("PR_BODY_FILE"):
        ctx["body"] = Path(os.environ["PR_BODY_FILE"]).read_text()
    if os.environ.get("PR_LABELS"):
        ctx["labels"] = [s.strip() for s in os.environ["PR_LABELS"].split(",") if s.strip()]
    return ctx


# ------------------------------------------------------------------ GitHub API
def gh_api(method: str, path: str, body: Any = None) -> Any:
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        return None
    url = os.environ.get("GITHUB_API_URL", "https://api.github.com") + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        # Fork PRs get a read-only token: report, don't crash the gate.
        warn(f"GitHub API {method} {path} -> HTTP {e.code}: {e.read()[:200]!r}")
        return None


def repo() -> str | None:
    return os.environ.get("GITHUB_REPOSITORY")


def upsert_comment(pr_number: int | None, marker: str, body: str) -> None:
    """Create or update a single 'sticky' comment identified by a hidden marker."""
    if not (pr_number and repo()):
        return
    tag = f"<!-- {marker} -->"
    full = f"{tag}\n{body}"
    comments = gh_api("GET", f"/repos/{repo()}/issues/{pr_number}/comments?per_page=100") or []
    for c in comments:
        if tag in (c.get("body") or ""):
            gh_api("PATCH", f"/repos/{repo()}/issues/comments/{c['id']}", {"body": full})
            return
    gh_api("POST", f"/repos/{repo()}/issues/{pr_number}/comments", {"body": full})


def set_label(pr_number: int | None, label: str, present: bool) -> None:
    if not (pr_number and repo()):
        return
    if present:
        gh_api("POST", f"/repos/{repo()}/issues/{pr_number}/labels", {"labels": [label]})
    else:
        gh_api("DELETE", f"/repos/{repo()}/issues/{pr_number}/labels/{label}")


def pr_commit_logins(pr_number: int | None) -> set[str]:
    if not (pr_number and repo()):
        return set()
    commits = gh_api("GET", f"/repos/{repo()}/pulls/{pr_number}/commits?per_page=100") or []
    return {(c.get("author") or {}).get("login") for c in commits if (c.get("author") or {}).get("login")}


# ------------------------------------------------------------------ output
def write_summary(markdown: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(markdown + "\n")


def set_output(name: str, value: Any) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(f"{name}={value}\n")


def warn(msg: str) -> None:
    print(f"::warning::{msg}" if os.environ.get("GITHUB_ACTIONS") else f"WARNING: {msg}", file=sys.stderr)


def bar(pct: float, width: int = 20) -> str:
    filled = max(0, min(width, round(pct / 100 * width)))
    return "█" * filled + "░" * (width - filled)


def pct(part: float, whole: float) -> float:
    return round(100.0 * part / whole, 1) if whole else 0.0
