#!/bin/bash
#
# push.command — commit and push this folder to GitHub.
# Double-click in Finder (or run it from Terminal). Works out its own
# location and remote, so moving or renaming the folder does not break it.
#

cd "$(dirname "$0")" || exit 1

BOLD=$'\033[1m'; DIM=$'\033[2m'; GREEN=$'\033[32m'; RED=$'\033[31m'; YELLOW=$'\033[33m'; OFF=$'\033[0m'
NAME="$(basename "$(pwd)")"

finish() {
  echo ""
  echo "${DIM}Press any key to close this window.${OFF}"
  read -r -n 1 -s
  exit "${1:-0}"
}

echo ""
echo "${BOLD}${NAME} — push to GitHub${OFF}"
echo "${DIM}$(pwd)${OFF}"
echo ""

if ! command -v git >/dev/null 2>&1; then
  echo "${RED}git is not installed.${OFF} Run  xcode-select --install  and try again."
  finish 1
fi

if [ ! -d .git ]; then
  echo "${RED}This folder is not a git repository.${OFF}"
  echo "Expected a .git folder in:"
  echo "  $(pwd)"
  echo ""
  echo "If this is a brand-new project, run in Terminal:"
  echo "  git init && git branch -M main && git remote add origin <your-repo-url>"
  finish 1
fi

# ---- clear stale git lock files --------------------------------------------
# Git writes lock files (.git/index.lock, HEAD.lock, objects/maintenance.lock,
# etc.) while it works and removes them when done. If a git process is
# killed, or a backup/sync tool touches the folder mid-operation, a lock can
# be left behind and every later commit fails with "File exists" or
# "Another git process seems to be running."
STALE_LOCKS="$(find .git -maxdepth 4 -name '*.lock' 2>/dev/null)"
if [ -n "$STALE_LOCKS" ]; then
  if pgrep -x git >/dev/null 2>&1; then
    echo "${RED}Another git process is running.${OFF} Wait for it to finish, then try again."
    finish 1
  fi
  echo "${YELLOW}Clearing stale git lock file(s):${OFF}"
  echo "$STALE_LOCKS" | sed 's/^/  /'
  echo "$STALE_LOCKS" | tr '\n' '\0' | xargs -0 rm -f
  echo ""
fi

# ---- make sure a remote is set ---------------------------------------------
if ! git remote get-url origin >/dev/null 2>&1; then
  echo "${RED}No 'origin' remote is set for this repo.${OFF}"
  echo "Run in Terminal:  git remote add origin <your-repo-url>"
  finish 1
fi
REPO_URL="$(git remote get-url origin)"

# Remember the GitHub token in the macOS Keychain so this stops asking.
git config credential.helper osxkeychain

# ---- figure out the branch --------------------------------------------------
BRANCH="$(git symbolic-ref --short HEAD 2>/dev/null)"
if [ -z "$BRANCH" ]; then
  echo "${RED}Could not determine the current branch (detached HEAD?).${OFF}"
  finish 1
fi

# ---- commit anything new ---------------------------------------------------
if ! git add -A; then
  echo "${RED}git could not stage your changes.${OFF} Nothing was committed or pushed."
  finish 1
fi

if git diff --cached --quiet 2>/dev/null; then
  echo "${DIM}No file changes since the last commit.${OFF}"
else
  CHANGED=$(git diff --cached --name-only | wc -l | tr -d ' ')
  echo "${BOLD}$CHANGED file(s) changed:${OFF}"
  git diff --cached --name-status | head -25 | sed 's/^/  /'
  [ "$CHANGED" -gt 25 ] && echo "  ${DIM}… and $((CHANGED - 25)) more${OFF}"
  echo ""

  DEFAULT="Update $(date '+%d %b %Y, %H:%M')"
  printf "Describe the change ${DIM}[%s]${OFF}: " "$DEFAULT"
  read -r MSG
  [ -z "$MSG" ] && MSG="$DEFAULT"

  if ! git commit -q -m "$MSG"; then
    echo "${RED}Commit failed.${OFF} Nothing was pushed."
    finish 1
  fi
  echo ""
  echo "  committed: ${BOLD}$MSG${OFF}"
fi

# ---- is there anything to push? -------------------------------------------
if git rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' >/dev/null 2>&1; then
  AHEAD=$(git rev-list --count '@{upstream}..HEAD' 2>/dev/null || echo 0)
  if [ "$AHEAD" = "0" ]; then
    echo ""
    echo "${GREEN}Already up to date with GitHub. Nothing to push.${OFF}"
    finish 0
  fi
  echo ""
  echo "${DIM}$AHEAD commit(s) to push.${OFF}"
fi

# ---- push, with one auto-retry for a stale Keychain credential ------------
do_push() {
  PUSH_LOG="$(mktemp)"
  git push -u origin "$BRANCH" 2>&1 | tee "$PUSH_LOG"
  echo "${PIPESTATUS[0]}"
}

echo ""
echo "Pushing ${BOLD}$BRANCH${OFF} to GitHub…"
echo ""

PUSH_LOG="$(mktemp)"
git push -u origin "$BRANCH" 2>&1 | tee "$PUSH_LOG"
PUSH_RC=${PIPESTATUS[0]}

# GitHub's classic sign that git silently sent a stale/bad Keychain entry
# (usually an old account password from long before tokens existed) rather
# than actually asking you for anything.
if [ "$PUSH_RC" -ne 0 ] && grep -qi "Invalid username or token\|Password authentication is not supported" "$PUSH_LOG"; then
  echo ""
  echo "${YELLOW}That failure came from a stale saved credential, not something you typed.${OFF}"
  if command -v git-credential-osxkeychain >/dev/null 2>&1; then
    echo "Clearing the saved GitHub login from Keychain and asking fresh…"
    printf 'protocol=https\nhost=github.com\n\n' | git credential-osxkeychain erase
    echo ""
    rm -f "$PUSH_LOG"
    PUSH_LOG="$(mktemp)"
    git push -u origin "$BRANCH" 2>&1 | tee "$PUSH_LOG"
    PUSH_RC=${PIPESTATUS[0]}
  else
    echo "Open ${BOLD}Keychain Access${OFF}, search \"github.com\", delete the entry,"
    echo "then run this again — it'll ask for a fresh token."
  fi
fi

if [ "$PUSH_RC" -eq 0 ]; then
  echo ""
  echo "${GREEN}${BOLD}Pushed.${OFF}"
  echo "${DIM}$REPO_URL${OFF}"
  rm -f "$PUSH_LOG"
  finish 0
fi

echo ""
echo "${RED}${BOLD}Push failed.${OFF} Your work is committed locally and safe —"
echo "run this again once the problem below is sorted."
echo ""

if grep -qi "workflow.*scope\|without .workflow. scope" "$PUSH_LOG"; then
  echo "  ${BOLD}Token missing 'workflow' scope${OFF}"
  echo "  This repo has a file under .github/workflows/, and GitHub refuses"
  echo "  to let a token touch it unless the token has the 'workflow' scope."
  echo "    1. github.com/settings/tokens"
  echo "    2. Edit your existing token (don't create a new one)."
  echo "    3. Tick 'workflow' (under 'repo'), then Update token."
elif grep -qi "Invalid username or token\|authentication failed\|could not read Username\|Support for password authentication was removed" "$PUSH_LOG"; then
  echo "  ${BOLD}Asked for a username or password${OFF}"
  echo "  GitHub does not accept account passwords. Create a personal access"
  echo "  token at github.com/settings/tokens (scopes: repo, workflow) and"
  echo "  paste it when asked for the password — the username can be anything,"
  echo "  it's the token GitHub actually checks."
elif grep -qi "fetch first\|non-fast-forward\|behind" "$PUSH_LOG"; then
  echo "  ${BOLD}Rejected — the remote has commits this copy doesn't${OFF}"
  echo "  Open Terminal here and run:"
  echo "      git pull --rebase origin $BRANCH && git push"
elif grep -qi "could not resolve host\|network is unreachable" "$PUSH_LOG"; then
  echo "  ${BOLD}No internet connection.${OFF}"
else
  echo "  ${BOLD}Unrecognized failure${OFF} — the output above has the detail."
  echo "  Copy it and send it to Claude if you're not sure what to do."
fi

echo ""
echo "  (full output kept at $PUSH_LOG)"
finish 1
