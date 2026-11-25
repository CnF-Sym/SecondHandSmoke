#!/bin/zsh
# Publish GitHub Release.command — puts the signed DMG up as a GitHub Release.
#
# Run it after Build Second Hand Smoke.command, Make Second Hand Smoke DMG.command
# and push.command. It uploads only what Make DMG made: the notarized DMG and its
# .sha256. Nothing is rebuilt.
#
# Uses GitHub's own command-line tool (gh). Signing in to gh opens your browser
# and shows a one-time code. There is no password or token to type, here or
# anywhere else.
#
# The tag is pushed over SSH, the same way push.command pushes.

cd "${0:A:h}"
export GIT_SSH_COMMAND="ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new"

BOLD=$'\e[1m'; RED=$'\e[31m'; GREEN=$'\e[32m'; DIM=$'\e[2m'; OFF=$'\e[0m'
finish() { echo ""; echo "Press return to close this window."; read; exit "${1:-0}"; }
stop() { echo ""; echo "${RED}$*${OFF}"; echo "Nothing was published."; finish 1; }

VERSION="$(tr -d '[:space:]' < VERSION)"
TAG="v$VERSION"
DMG="dist/SecondHandSmoke-$VERSION.dmg"
SUM="$DMG.sha256"
NOTES="RELEASE_NOTES_v$VERSION.md"

echo "${BOLD}=== Publish Second Hand Smoke $VERSION ===${OFF}"

# 1. The files exist, the checksum matches, and Apple's staple is on the DMG.
[ -f "$DMG" ] || stop "No $DMG. Run Make Second Hand Smoke DMG.command first."
[ -f "$SUM" ] || stop "No $SUM. Run Make Second Hand Smoke DMG.command again."
[ -f "$NOTES" ] || stop "No $NOTES. Write the release notes before publishing."
(cd dist && shasum -a 256 -c "$(basename "$SUM")" >/dev/null 2>&1) \
  || stop "The DMG doesn't match its .sha256. Make the DMG again."
xcrun stapler validate "$DMG" >/dev/null 2>&1 \
  || stop "The DMG isn't notarized and stapled. Make the DMG again."
spctl -a -t open --context context:primary-signature "$DMG" >/dev/null 2>&1 \
  || stop "macOS wouldn't open this DMG as signed and notarized."
echo "${GREEN}✓${OFF} DMG is notarized, stapled and matches its checksum"

# 2. Everything is committed and pushed, so the tag points at the code people
#    can actually read.
[ -z "$(git status --porcelain)" ] \
  || stop "There are changes that aren't committed. Run push.command first."
BRANCH="$(git rev-parse --abbrev-ref HEAD)"
git fetch -q origin "$BRANCH" || stop "Couldn't reach GitHub over SSH."
[ "$(git rev-parse HEAD)" = "$(git rev-parse "origin/$BRANCH")" ] \
  || stop "This Mac and GitHub don't have the same commits. Run push.command first."
echo "${GREEN}✓${OFF} Code is committed and pushed ($BRANCH)"

# 3. GitHub's command-line tool, signed in through the browser.
if ! command -v gh >/dev/null 2>&1; then
  if command -v brew >/dev/null 2>&1; then
    echo ""
    echo "This needs GitHub's command-line tool (gh). It isn't installed."
    printf "Install it with Homebrew now? (y/N) "; read -r answer
    [[ "$answer" == [yY]* ]] || stop "gh is needed to publish."
    brew install gh || stop "Homebrew couldn't install gh."
  else
    stop "This needs GitHub's command-line tool. Install it from https://cli.github.com and run this again."
  fi
fi
if ! gh auth status -h github.com >/dev/null 2>&1; then
  echo ""
  echo "Sign in to GitHub. Your browser will open and ask for a one-time code."
  echo "${DIM}When it asks about git protocol, pick SSH. Skip uploading an SSH key; yours is already on GitHub.${OFF}"
  gh auth login -h github.com -p ssh -w || stop "GitHub sign-in didn't finish."
fi
REPO="$(gh repo view --json nameWithOwner -q .nameWithOwner)" || stop "gh can't see this repository."
echo "${GREEN}✓${OFF} Signed in to GitHub ($REPO)"

# 4. A release on a private repository can't be downloaded by anyone else.
if [ "$(gh repo view --json visibility -q .visibility)" != "PUBLIC" ]; then
  echo ""
  echo "${BOLD}$REPO is private.${OFF} Nobody else can download a release from it."
  echo "Making it public shows all the code and its full history to anyone."
  printf "Make it public now? Type ${BOLD}public${OFF} to confirm: "; read -r answer
  [ "$answer" = "public" ] || stop "The repository is still private."
  gh repo edit "$REPO" --visibility public --accept-visibility-change-consequences \
    || stop "GitHub didn't change the visibility."
  echo "${GREEN}✓${OFF} $REPO is public"
fi

# 5. Labels the issue forms use. Already there is fine.
gh label create verification --color B60205 --description "It said a card was safe and it wasn't" >/dev/null 2>&1
gh label create critical --color B60205 --description "Stop everything" >/dev/null 2>&1
gh label create "camera coverage" --color 1D76DB --description "Camera or recorder not detected right" >/dev/null 2>&1

# 6. Never replace a release that's already out. A new build gets a new version.
if gh release view "$TAG" >/dev/null 2>&1; then
  stop "Release $TAG already exists. Bump VERSION, build, make the DMG, push, then publish."
fi

echo ""
echo "About to publish ${BOLD}Second Hand Smoke $VERSION (beta)${OFF} to $REPO:"
echo "  $DMG"
echo "  $SUM"
printf "Publish? (y/N) "; read -r answer
[[ "$answer" == [yY]* ]] || stop "Cancelled."

# 7. Tag the pushed commit, then make the release from that tag.
if ! git rev-parse -q --verify "refs/tags/$TAG" >/dev/null; then
  git tag -a "$TAG" -m "Second Hand Smoke $VERSION" || stop "Couldn't create tag $TAG."
fi
git push -q origin "refs/tags/$TAG" || stop "Couldn't push tag $TAG to GitHub."

# Not marked as a pre-release on purpose: GitHub's /releases/latest link skips
# pre-releases, and that's the link the website uses.
gh release create "$TAG" "$DMG" "$SUM" \
  --repo "$REPO" \
  --title "Second Hand Smoke $VERSION (beta)" \
  --notes-file "$NOTES" \
  --verify-tag --latest \
  || stop "GitHub didn't create the release. The tag $TAG was pushed; run this again once the problem is fixed."

echo ""
echo "${GREEN}${BOLD}Published.${OFF}"
echo ""
echo "Link for your website (always the newest release):"
echo "  ${BOLD}https://github.com/$REPO/releases/latest${OFF}"
echo ""
echo "Direct download of this version:"
echo "  https://github.com/$REPO/releases/download/$TAG/$(basename "$DMG")"
finish 0
