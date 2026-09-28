#!/usr/bin/env bash
# Demo 1 rehearsal: create two PR branches with Git AI attribution, without a real agent.
# Uses Git AI's mock_ai / mock_known_human checkpoint presets, so the PR comment
# will show tool "mock_ai". For the live demo, write the code with a real agent instead.
#   demo1/mostly-ai     -> ~85% AI  -> blocked
#   demo1/mostly-human  -> ~20% AI  -> passes
set -euo pipefail
command -v git-ai >/dev/null || { echo "Install Git AI first: https://usegitai.com"; exit 1; }
BASE="${BASE_BRANCH:-main}"
git fetch -q origin "$BASE" && git checkout -q "$BASE" && git pull -q --ff-only origin "$BASE"

git checkout -q -B demo1/mostly-ai "$BASE"
cat > app/users.py <<'PY'
from flask import Blueprint, jsonify, request

from app.db import get_db

bp = Blueprint("users", __name__)


@bp.route("/users/search")
def search_users():
    name = request.args.get("name", "")
    rows = get_db().execute(
        "SELECT id, name, email FROM users WHERE name LIKE ?", (f"%{name}%",)
    ).fetchall()
    return jsonify([{"id": r[0], "name": r[1], "email": r[2]} for r in rows])


@bp.route("/users/<int:user_id>")
def get_user(user_id: int):
    row = get_db().execute("SELECT id, name, email FROM users WHERE id = ?", (user_id,)).fetchone()
    if row is None:
        return jsonify({"error": "not found"}), 404
    return jsonify({"id": row[0], "name": row[1], "email": row[2]})
PY
git-ai checkpoint mock_ai app/users.py
printf '\n# Users API is registered in create_app() once reviewed.\n' >> app/db.py
git-ai checkpoint mock_known_human app/db.py
git add app && git commit -q -m "Add users search endpoint" -m "Assisted-by: mock_ai:unknown"
git-ai await --timeout 30 >/dev/null 2>&1 || true
git push -q -f origin demo1/mostly-ai
git push -q origin refs/notes/ai

git checkout -q -B demo1/mostly-human "$BASE"
cat > app/config.py <<'PY'
import os

MAX_PAGE_SIZE = int(os.environ.get("MAX_PAGE_SIZE", "50"))
DEFAULT_PAGE_SIZE = int(os.environ.get("DEFAULT_PAGE_SIZE", "20"))
ALLOWED_ORIGINS = [o for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o]
FEATURE_USERS_SEARCH = os.environ.get("FEATURE_USERS_SEARCH", "false") == "true"
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
REQUEST_TIMEOUT_SECONDS = float(os.environ.get("REQUEST_TIMEOUT_SECONDS", "5"))
PY
git-ai checkpoint mock_known_human app/config.py
cat > app/pagination.py <<'PY'
def clamp_page_size(requested: int, maximum: int) -> int:
    return max(1, min(int(requested), int(maximum)))
PY
git-ai checkpoint mock_ai app/pagination.py
git add app && git commit -q -m "Add config and pagination helper" -m "Assisted-by: mock_ai:unknown"
git-ai await --timeout 30 >/dev/null 2>&1 || true
git push -q -f origin demo1/mostly-human
git push -q origin refs/notes/ai
git checkout -q "$BASE"
echo "Now open the PRs:"
echo "  gh pr create --head demo1/mostly-ai    --title 'Demo 1: mostly AI (expect block)'  --body-file demo/pr_body_disclosed.md"
echo "  gh pr create --head demo1/mostly-human --title 'Demo 1: mostly human (expect pass)' --body-file demo/pr_body_disclosed.md"
