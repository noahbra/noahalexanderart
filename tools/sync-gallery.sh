#!/usr/bin/env bash
# Pull new paintings from Notion, rebuild and re-lock /gallery/, commit and push.
set -euo pipefail
cd "$(dirname "$0")/.."
python3 tools/pull-artwork.py
python3 tools/build-gallery.py
git add gallery
if git diff --cached --quiet; then echo "gallery unchanged"; exit 0; fi
git commit -qm "Gallery: sync from Notion $(date +%Y-%m-%d)"
git push
echo "pushed; GitHub Pages will serve it in about a minute"
