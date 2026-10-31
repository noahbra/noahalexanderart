#!/usr/bin/env python3
"""Encrypt private/studio.html into studio/index.html for GitHub Pages.

The output is a self-contained page: an unlock form plus an AES-256-GCM
ciphertext. The key is derived in the browser from the password with
PBKDF2-SHA256. The plaintext never leaves this machine; only the ciphertext
is committed.

Usage:
  python3 tools/lock.py                       # prompts for the password
  python3 tools/lock.py --password 'phrase'   # non-interactive
  STUDIO_PASSWORD='phrase' python3 tools/lock.py

Local files referenced from the private page (img src, a href, source src,
video/audio src, link href to .pdf etc.) are inlined as data URIs before
encryption, so everything behind the password is encrypted with it.
"""
import argparse
import base64
import getpass
import mimetypes
import os
import re
import secrets
import sys
from pathlib import Path

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives import hashes
except ImportError:
    sys.exit("missing dependency: python3 -m pip install cryptography")

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "private" / "studio.html"
SHELL = ROOT / "tools" / "unlock-shell.html"
OUTPUT = ROOT / "studio" / "index.html"
ITERATIONS = 600_000
WARN_BYTES = 25 * 1024 * 1024

ATTR_RE = re.compile(r"""(?P<attr>\b(?:src|href|poster))=(?P<q>["'])(?P<url>[^"']+)(?P=q)""")


def inline_assets(html: str, base: Path) -> tuple[str, list[str]]:
    inlined: list[str] = []

    def repl(m: re.Match) -> str:
        url = m.group("url")
        if re.match(r"^(?:[a-z]+:|//|#|data:)", url, re.I):
            return m.group(0)
        path = (base / url.split("?")[0].split("#")[0]).resolve()
        if not path.is_file() or base.resolve() not in path.parents:
            return m.group(0)
        mime, _ = mimetypes.guess_type(path.name)
        mime = mime or "application/octet-stream"
        data = base64.b64encode(path.read_bytes()).decode()
        inlined.append(f"{url} ({mime}, {path.stat().st_size:,} bytes)")
        return f'{m.group("attr")}={m.group("q")}data:{mime};base64,{data}{m.group("q")}'

    return ATTR_RE.sub(repl, html), inlined


# Chrome refuses top-frame navigation to data: URLs, so inlined downloads
# (PDFs etc.) are converted to blob URLs on click.
BLOB_SHIM = """
<script>
document.addEventListener('click', function (e) {
  var a = e.target.closest && e.target.closest('a[href^="data:"]');
  if (!a) return;
  e.preventDefault();
  fetch(a.getAttribute('href')).then(function (r) { return r.blob(); }).then(function (b) {
    var u = URL.createObjectURL(b);
    if (a.hasAttribute('download')) {
      var t = document.createElement('a'); t.href = u; t.download = a.getAttribute('download') || 'file';
      document.body.appendChild(t); t.click(); t.remove();
    } else { window.open(u, '_blank', 'noopener'); }
    setTimeout(function () { URL.revokeObjectURL(u); }, 60000);
  });
});
</script>
"""


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--password", help="password (or set STUDIO_PASSWORD; otherwise prompted)")
    ap.add_argument("--source", type=Path, default=SOURCE)
    ap.add_argument("--output", type=Path, default=OUTPUT)
    args = ap.parse_args()

    if not args.source.is_file():
        sys.exit(f"source not found: {args.source}")
    if not SHELL.is_file():
        sys.exit(f"shell not found: {SHELL}")

    password = args.password or os.environ.get("STUDIO_PASSWORD")
    if not password:
        password = getpass.getpass("Password for /studio: ")
        again = getpass.getpass("Again: ")
        if password != again:
            sys.exit("passwords do not match")
    if len(password) < 8:
        sys.exit("use at least 8 characters; the only thing protecting the page is this password")

    html = args.source.read_text(encoding="utf-8")
    html, inlined = inline_assets(html, args.source.parent)
    if "</body>" in html:
        html = html.replace("</body>", BLOB_SHIM + "</body>", 1)
    else:
        html += BLOB_SHIM

    salt = secrets.token_bytes(16)
    iv = secrets.token_bytes(12)
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=ITERATIONS)
    key = kdf.derive(password.encode("utf-8"))
    ct = AESGCM(key).encrypt(iv, html.encode("utf-8"), None)

    b64 = lambda b: base64.b64encode(b).decode()
    payload = (
        '{"v":1,"kdf":"PBKDF2-SHA256","iterations":%d,"salt":"%s","iv":"%s","ct":"%s"}'
        % (ITERATIONS, b64(salt), b64(iv), b64(ct))
    )

    shell = SHELL.read_text(encoding="utf-8")
    if "__PAYLOAD__" not in shell:
        sys.exit("unlock-shell.html is missing the __PAYLOAD__ marker")
    out = shell.replace("__PAYLOAD__", payload, 1)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(out, encoding="utf-8")

    size = args.output.stat().st_size
    print(f"wrote {args.output.relative_to(ROOT)} ({size:,} bytes)")
    for item in inlined:
        print(f"  inlined {item}")
    if size > WARN_BYTES:
        print(f"WARNING: output exceeds {WARN_BYTES:,} bytes; GitHub Pages caps files at 100 MB and large pages load slowly", file=sys.stderr)
    print("plaintext stays in private/ (gitignored). Commit and push studio/index.html to deploy.")


if __name__ == "__main__":
    main()
