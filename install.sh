#!/usr/bin/env bash
# Installs the inbox-cleanup skill for Claude Code.
#   curl -fsSL https://raw.githubusercontent.com/OllieKnight31/inbox-cleanup-skill/main/install.sh | bash
set -euo pipefail

REPO="${INBOX_CLEANUP_REPO:-https://github.com/OllieKnight31/inbox-cleanup-skill}"
DEST="${INBOX_CLEANUP_DEST:-$HOME/.claude/skills/inbox-cleanup}"

command -v git >/dev/null || { echo "git is required."; exit 1; }
command -v python3 >/dev/null || { echo "python3 is required."; exit 1; }

if [ -d "$DEST/.git" ]; then
  echo "Updating $DEST"
  git -C "$DEST" pull --ff-only --quiet
elif [ -e "$DEST" ]; then
  echo "$DEST already exists and is not a git checkout. Move it away and run this again."
  exit 1
else
  echo "Installing to $DEST"
  mkdir -p "$(dirname "$DEST")"
  git clone --quiet "$REPO" "$DEST"
fi

if [ "${INBOX_CLEANUP_SKIP_PIP:-0}" != "1" ]; then
  python3 -m pip install --quiet -r "$DEST/requirements.txt" \
    || python3 -m pip install --quiet --user -r "$DEST/requirements.txt" \
    || echo "Could not install Python packages automatically. Run: pip install -r $DEST/requirements.txt"
fi

echo
echo "Installed. Try it on a made up inbox:"
echo "  python3 $DEST/scripts/cleanup.py demo"
echo "Then connect an account: see $DEST/references/setup.md"
echo "In Claude Code, say: clean my inbox"
