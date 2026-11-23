#!/bin/bash
#
# push.command — commit and push this folder to GitHub over SSH.
# Double-click in Finder (or run from Terminal).
#
# Never asks for a password or token. It signs in with your SSH key. If this
# Mac doesn't have one yet, it makes one (once), copies the public half to your
# clipboard and opens GitHub's "add SSH key" page.
#
# Written by GYST. Safe to edit; GYST only checks that it never prompts.
#

cd "$(dirname "$0")" || exit 1

BOLD=$'\033[1m'; DIM=$'\033[2m'; GREEN=$'\033[32m'; RED=$'\033[31m'; YELLOW=$'\033[33m'; OFF=$'\033[0m'
NAME="$(basename "$(pwd)")"
DEFAULT_REMOTE="git@github.com:CnF-Sym/SecondHandSmoke.git"

# Git and SSH must never stop and ask for anything.
export GIT_TERMINAL_PROMPT=0
export GIT_SSH_COMMAND="ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new"

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

command -v git >/dev/null 2>&1 || { echo "${RED}git is not installed.${OFF} Run  xcode-select --install  and try again."; finish 1; }

# ---- first run: make this folder a repo ------------------------------------
if [ ! -d .git ]; then
  echo "${YELLOW}First push from this folder — setting it up.${OFF}"
  git init -q && git branch -M main
  git remote add origin "$DEFAULT_REMOTE"
  echo "  repo:   $DEFAULT_REMOTE"
  echo ""
fi

# ---- clear stale lock files ------------------------------------------------
STALE_LOCKS="$(find .git -maxdepth 4 -name '*.lock' 2>/dev/null)"
if [ -n "$STALE_LOCKS" ]; then
  if pgrep -x git >/dev/null 2>&1; then
    echo "${RED}Another git process is running.${OFF} Wait for it to finish, then try again."
    finish 1
  fi
  echo "${YELLOW}Clearing stale git lock file(s).${OFF}"
  echo "$STALE_LOCKS" | tr '\n' '\0' | xargs -0 rm -f
fi

# ---- make sure the remote is SSH, not HTTPS --------------------------------
if ! git remote get-url origin >/dev/null 2>&1; then
  git remote add origin "$DEFAULT_REMOTE"
fi
REMOTE="$(git remote get-url origin)"
if [[ "$REMOTE" =~ ^https://github.com/([^/]+)/([^/]+)$ ]]; then
  OWNER="${BASH_REMATCH[1]}"; REPO="${BASH_REMATCH[2]%.git}"
  REMOTE="git@github.com:${OWNER}/${REPO}.git"
  git remote set-url origin "$REMOTE"
  echo "${DIM}Switched the remote to SSH: $REMOTE${OFF}"
fi
# Old HTTPS logins saved by earlier scripts are no longer used.
git config --unset credential.helper 2>/dev/null || true

# ---- make sure this Mac can sign in to GitHub with a key -------------------
ssh_ok() {
  ssh -T -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 git@github.com 2>&1 | grep -q "successfully authenticated"
}

if ! ssh_ok; then
  KEY="$HOME/.ssh/id_ed25519"
  if [ ! -f "$KEY" ]; then
    echo "${YELLOW}This Mac has no SSH key for GitHub yet. Making one (one time only)…${OFF}"
    mkdir -p "$HOME/.ssh" && chmod 700 "$HOME/.ssh"
    ssh-keygen -q -t ed25519 -N "" -C "$(whoami)@$(scutil --get ComputerName 2>/dev/null || hostname) for GitHub" -f "$KEY"
  fi
  if ! grep -q "^Host github.com" "$HOME/.ssh/config" 2>/dev/null; then
    printf '\nHost github.com\n  IdentityFile ~/.ssh/id_ed25519\n  AddKeysToAgent yes\n  UseKeychain yes\n' >> "$HOME/.ssh/config"
    chmod 600 "$HOME/.ssh/config"
  fi
  if ! ssh_ok; then
    pbcopy < "$KEY.pub"
    open "https://github.com/settings/ssh/new"
    echo ""
    echo "${BOLD}One-time step:${OFF} your public SSH key is on the clipboard and GitHub's"
    echo "\"Add new SSH key\" page just opened. Give it a title (e.g. this Mac's name),"
    echo "paste into Key, click Add SSH key — then run push.command again."
    echo ""
    echo "${DIM}(That's the public half — safe to paste. Nothing secret leaves this Mac.)${OFF}"
    finish 1
  fi
fi

BRANCH="$(git symbolic-ref --short HEAD 2>/dev/null)"
[ -z "$BRANCH" ] && { echo "${RED}Could not work out the current branch.${OFF}"; finish 1; }

# ---- commit anything new ---------------------------------------------------
git add -A || { echo "${RED}git could not stage your changes.${OFF}"; finish 1; }

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
  git commit -q -m "$MSG" || { echo "${RED}Commit failed.${OFF} Nothing was pushed."; finish 1; }
  echo "  committed: ${BOLD}$MSG${OFF}"
fi

# ---- push ------------------------------------------------------------------
echo ""
echo "Pushing ${BOLD}$BRANCH${OFF}…"
PUSH_LOG="$(mktemp)"
git push -u origin "$BRANCH" 2>&1 | tee "$PUSH_LOG"
PUSH_RC=${PIPESTATUS[0]}

if [ "$PUSH_RC" -eq 0 ]; then
  echo ""
  echo "${GREEN}${BOLD}Pushed.${OFF}  ${DIM}$(git remote get-url origin)${OFF}"
  rm -f "$PUSH_LOG"
  finish 0
fi

echo ""
echo "${RED}${BOLD}Push failed.${OFF} Your work is committed locally and safe."
if grep -qi "repository not found\|does not exist" "$PUSH_LOG"; then
  echo "  The GitHub repo doesn't exist yet. Make an ${BOLD}empty${OFF} one (no README) at"
  echo "  https://github.com/new  — owner CnF-Sym, name $(basename "$(git remote get-url origin)" .git) — then run this again."
  open "https://github.com/new"
elif grep -qi "fetch first\|non-fast-forward" "$PUSH_LOG"; then
  echo "  GitHub has commits this copy doesn't. In Terminal here run:"
  echo "      git pull --rebase origin $BRANCH && git push"
elif grep -qi "permission denied\|publickey" "$PUSH_LOG"; then
  echo "  GitHub didn't accept this Mac's SSH key. Add ~/.ssh/id_ed25519.pub at"
  echo "  https://github.com/settings/keys and try again."
elif grep -qi "could not resolve host\|network is unreachable" "$PUSH_LOG"; then
  echo "  No internet connection."
else
  echo "  Unrecognized failure — the output above has the detail."
fi
rm -f "$PUSH_LOG"
finish 1

