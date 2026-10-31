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

Workflow: edit `private/studio.html`, run `python3 tools/lock.py`, commit `studio/index.html`, push. Changing the password is the same command with a new password. "Stay unlocked on this device" stores the derived key in that browser's localStorage, not the password; re-locking with a new password invalidates it.

Keep a copy of `private/` somewhere backed up. It is the only plaintext.
