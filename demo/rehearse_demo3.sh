#!/usr/bin/env bash
# Demo 3 rehearsal: manual AI tagging.
#   demo3/bad-tags   -> marker without reviewer, unclosed block, checkbox unticked -> fails
#   demo3/good-tags  -> complete markers, trailer, ticked checkbox                  -> passes
set -euo pipefail
BASE="${BASE_BRANCH:-main}"
HANDLE="${GITHUB_HANDLE:-$(gh api user -q .login 2>/dev/null || true)}"
: "${HANDLE:?set GITHUB_HANDLE=<your GitHub username> (or log in with gh auth login)}"
git fetch -q origin "$BASE"

git checkout -q -B demo3/bad-tags "origin/$BASE"
cat > app/slugs.py <<'PY'
import re

# @ai-generated begin tool=claude-code model=claude-sonnet-5
def slugify(text: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text.strip().lower())
    return text.strip("-")
PY
git add app/slugs.py && git commit -q -m "Add slugify helper" -m "Assisted-by: claude-code:claude-sonnet-5"
git push -q -f origin demo3/bad-tags

git checkout -q -B demo3/good-tags "origin/$BASE"
cat > app/slugs.py <<PY
import re

# @ai-generated begin tool=claude-code model=claude-sonnet-5 reviewed-by=@${HANDLE}
def slugify(text: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text.strip().lower())
    return text.strip("-")
# @ai-generated end
PY
git add app/slugs.py && git commit -q -m "Add slugify helper" -m "Assisted-by: claude-code:claude-sonnet-5"
git push -q -f origin demo3/good-tags
git checkout -q "$BASE"
echo "Open the PRs:"
echo "  gh pr create --head demo3/bad-tags  --title 'Demo 3: incomplete AI tags (expect fail)' --body 'Adds slugify.'"
echo "  gh pr create --head demo3/good-tags --title 'Demo 3: complete AI tags (expect pass)' --body-file demo/pr_body_disclosed.md"
