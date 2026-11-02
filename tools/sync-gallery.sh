#!/bin/bash
# Pull new paintings from Notion, rebuild and re-lock /gallery/, commit and push.
# Runs by hand from any clone, and daily under launchd (com.noahalexanderart.gallery-sync)
# from ~/noahalexanderart-sync, a clone outside ~/Desktop because launchd jobs cannot read Desktop.
set -euo pipefail
export PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin
export GIT_TERMINAL_PROMPT=0
cd "$(dirname "$0")/.."
log() { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }
fail() {
  log "FAILED at line $1"
  osascript -e 'display notification "See ~/Library/Logs/noahalexanderart-sync.log" with title "Gallery sync failed"' >/dev/null 2>&1 || true
  exit 1
}
trap 'fail $LINENO' ERR

log "sync start in $PWD"
git pull --rebase --quiet
python3 tools/pull-artwork.py
python3 tools/build-gallery.py
git add gallery
if git diff --cached --quiet; then log "gallery unchanged"; exit 0; fi
git commit -qm "Gallery: sync from Notion $(date +%Y-%m-%d)"
# gh holds the GitHub token in the keychain; the keychain helper alone has nothing for github.com.
git -c credential.helper='!gh auth git-credential' push --quiet
log "pushed; GitHub Pages will serve it in about a minute"
