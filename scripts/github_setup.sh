#!/usr/bin/env bash
# One-shot: verify the name is free, commit launch-readiness files, create the
# GitHub repo, and push. Safe to re-run. Edit REPO below if "keeptrue" is taken.
#
#   bash scripts/github_setup.sh
set -euo pipefail

REPO="keeptrue"
VISIBILITY="public"   # change to "private" if you want to soft-launch first

cd "$(dirname "$0")/.."

echo "==> Checking name availability"
OWNER="$(gh api user --jq .login)"
if gh repo view "$OWNER/$REPO" >/dev/null 2>&1; then
  echo "  ! GitHub repo $OWNER/$REPO already exists — will reuse it."
  REPO_EXISTS=1
else
  echo "  ok: github.com/$OWNER/$REPO is free"
  REPO_EXISTS=0
fi
if curl -sf "https://pypi.org/pypi/$REPO/json" >/dev/null 2>&1; then
  echo "  ! PyPI name '$REPO' is TAKEN — pick another before publishing to PyPI."
else
  echo "  ok: PyPI name '$REPO' looks free"
fi

echo "==> Running tests before anything leaves this machine"
pytest -q

echo "==> Committing any pending changes"
if [[ -n "$(git status --porcelain)" ]]; then
  git add -A
  git commit -q -m "Repo launch-readiness: CI, release workflow, badges, docs"
  echo "  committed."
else
  echo "  nothing to commit."
fi

echo "==> Creating + pushing the repo"
if [[ "$REPO_EXISTS" -eq 0 ]]; then
  gh repo create "$REPO" --"$VISIBILITY" --source=. --remote=origin \
    --description "Measure whether your coding agent actually follows the rules in AGENTS.md / CLAUDE.md." \
    --push
else
  git remote get-url origin >/dev/null 2>&1 || git remote add origin "https://github.com/$OWNER/$REPO.git"
  git push -u origin main
fi

echo
echo "Done. Next:"
echo "  • Repo:  https://github.com/$OWNER/$REPO"
echo "  • Add topics: gh repo edit $OWNER/$REPO --add-topic coding-agents,agents-md,claude-code,llm-eval"
echo "  • Publish to PyPI: configure trusted publishing, then: git tag v0.1.0 && git push --tags"
