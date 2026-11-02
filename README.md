# noahalexanderart.com

Coming-soon page for Noah Alexander Art. Static HTML, no build step.

- `index.html`: the page. A seeded generative painting composes itself on load.
- `CNAME`: custom domain for GitHub Pages. Do not delete.

Deploy: push to `main`. GitHub Pages serves the repo root.

## /studio (password protected)

`studio/index.html` is an unlock page carrying an AES-256-GCM ciphertext. The browser derives the key from the password (PBKDF2-SHA256, 600k iterations) and decrypts in place. GitHub Pages never sees the plaintext, and neither does the public repo.

- `private/studio.html`: the real page. **Gitignored. Never commit it.** Edit it like any HTML file.
- `private/assets/`: images, PDFs, anything the page references with a relative path. Inlined into the ciphertext at lock time.
- `tools/lock.py`: encrypts `private/studio.html` into `studio/index.html`. Prompts for the password, or takes `--password` / `STUDIO_PASSWORD`.
- `tools/unlock-shell.html`: the unlock page template. Edit for styling only; keep the `__PAYLOAD__` marker.

Second locked page: `private/playbook.html` → `studio/playbook/index.html` (the interactive Oil Painter's Playbook; its source text is `private/playbook-source.md`). Lock it with `python3 tools/lock.py --source private/playbook.html --output studio/playbook/index.html`. All locked pages share the salt in `private/salt.b64`, so "stay unlocked" on one opens the others; `--new-salt` rotates it. Playbook progress lives in the browser's localStorage (key `playbook:v1`), with a sync link, JSON export and import on the page.

Workflow: edit `private/studio.html`, run `python3 tools/lock.py`, commit `studio/index.html`, push. Changing the password is the same command with a new password. "Stay unlocked on this device" stores the derived key in that browser's localStorage, not the password; re-locking with a new password invalidates it.

Keep a copy of `private/` somewhere backed up. It is the only plaintext.

## /gallery (guest password, synced from Notion)

Trusted viewers see paintings from the Notion "Artwork" database at `/gallery/`, behind a separate guest password (`private/GALLERY-PASSWORD.txt`, salt `private/gallery-salt.b64`). Thumbnails are encrypted inside the page; full-size images are encrypted one per file in `gallery/img/` and decrypted in the browser on tap.

- `tools/pull-artwork.py`: pulls pages and images from Notion into `private/artwork/` (needs a Notion integration token: `$NOTION_TOKEN`, `private/notion-token.txt`, or `~/thirdway-local/credentials/notion-token.txt`). Incremental.
- `private/gallery-config.json`: what viewers see. Excluded pages (drawings), renames (no sitter names), medium overrides. Missing dimensions are inferred from the image and marked approx.
- `tools/build-gallery.py`: config + manifest → `private/gallery.html` → locked `gallery/index.html` plus `gallery/img/*.bin`.
- `tools/sync-gallery.sh`: pull, build, commit, push. Runs daily at 7:00 under launchd (`~/Library/LaunchAgents/com.noahalexanderart.gallery-sync.plist`) from the clone at `~/noahalexanderart-sync`, because launchd jobs cannot read `~/Desktop`. Log: `~/Library/Logs/noahalexanderart-sync.log`. A failure posts a macOS notification. Run it by hand from either clone to sync sooner.
- The gallery's private files (`gallery-config.json`, `gallery-salt.b64`, `GALLERY-PASSWORD.txt`, the `artwork/` cache) live in `~/noahalexanderart-sync/private/`; the Desktop clone's `private/` holds symlinks to them, so both clones build from the same inputs.
