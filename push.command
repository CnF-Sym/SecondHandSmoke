#!/bin/bash
# push.command — Second Hand Smoke · A CnF Works Project
# Double-click to commit and push this folder to GitHub.

REPO_URL="https://github.com/CnF-Sym/SecondHandSmoke.git"
MAX_MB=50   # refuse to commit any single file larger than this

cd "$(dirname "$0")" || exit 1

echo "=============================================="
echo "  Second Hand Smoke — push to GitHub"
echo "  $(pwd)"
echo "=============================================="
echo ""

finish() {
  echo ""
  read -r -p "Press return to close..."
  exit "${1:-0}"
}

command -v git >/dev/null 2>&1 || {
  echo "git is not installed. Install Xcode Command Line Tools:"
  echo "  xcode-select --install"
  finish 1
}

# --- repo ---------------------------------------------------------------
if [ ! -d ".git" ]; then
  echo "No git repo here. Initializing..."
  git init -q
  git branch -M main
fi

# --- a .gitignore always exists, so .venv and the catalog never ship -----
if [ ! -f ".gitignore" ]; then
  echo "Creating .gitignore..."
  cat > .gitignore <<'IGNORE'
.venv/
venv/
__pycache__/
*.py[cod]
*.db
*.db-journal
*.db-wal
*.db-shm
SecondHandSmoke/
.DS_Store
._*
.vscode/
.idea/
*.log
IGNORE
fi

# If a previous run tracked the virtualenv, stop tracking it.
if git ls-files --error-unmatch .venv >/dev/null 2>&1; then
  echo "Untracking .venv (it should never be in the repo)..."
  git rm -r --cached .venv -q
fi

# --- remote -------------------------------------------------------------
if git remote get-url origin >/dev/null 2>&1; then
  CURRENT="$(git remote get-url origin)"
  if [ "$CURRENT" != "$REPO_URL" ]; then
    echo "Fixing origin: $CURRENT -> $REPO_URL"
    git remote set-url origin "$REPO_URL"
  fi
else
  echo "Adding origin remote..."
  git remote add origin "$REPO_URL"
fi

# --- what changed -------------------------------------------------------
echo ""
git status --short
echo ""

if [ -n "$(git status --porcelain)" ]; then

  # Size gate: nothing enormous slips in by accident.
  BIG="$(git status --porcelain | cut -c4- | sed 's/^.* -> //' | while read -r f; do
    f="${f%\"}"; f="${f#\"}"
    [ -f "$f" ] || continue
    SZ=$(stat -f%z "$f" 2>/dev/null || echo 0)
    [ "$SZ" -gt $((MAX_MB * 1024 * 1024)) ] && echo "  $f ($((SZ / 1024 / 1024)) MB)"
  done)"

  if [ -n "$BIG" ]; then
    echo "These files are larger than ${MAX_MB} MB:"
    echo "$BIG"
    echo ""
    read -r -p "Push them anyway? (y/N): " OK
    case "$OK" in
      y|Y) ;;
      *) echo "Stopped. Add them to .gitignore, then run this again."; finish 1 ;;
    esac
    echo ""
  fi

  read -r -p "Commit message (blank = timestamp): " MSG
  [ -z "$MSG" ] && MSG="Update $(date '+%Y-%m-%d %H:%M:%S')"

  git add -A
  git commit -m "$MSG" || { echo "Commit failed."; finish 1; }
else
  echo "Nothing to commit — working tree clean."
  echo "Checking for unpushed commits..."
fi

# --- push ---------------------------------------------------------------
BRANCH="$(git rev-parse --abbrev-ref HEAD)"
echo ""
if git rev-parse --abbrev-ref "@{u}" >/dev/null 2>&1; then
  git push
else
  git push -u origin "$BRANCH"
fi
STATUS=$?

echo ""
if [ $STATUS -eq 0 ]; then
  echo "=============================================="
  echo "  Pushed to origin/$BRANCH"
  echo "  https://github.com/CnF-Sym/SecondHandSmoke"
  echo "=============================================="
else
  echo "=============================================="
  echo "  PUSH FAILED (exit $STATUS)"
  echo ""
  echo "  Most common cause: GitHub wants a token, not"
  echo "  your password. Create one at"
  echo "  github.com/settings/tokens (scope: repo) and"
  echo "  paste it when asked for a password."
  echo "=============================================="
fi

finish $STATUS
