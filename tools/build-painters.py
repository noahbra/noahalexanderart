#!/usr/bin/env python3
"""Build the locked study page The Painter's Hand at /studio/painters/.

Source (plaintext, gitignored): private/painters/
  index.html, app.js, styles.css, data/*.js, img/**/*.webp
Output (committed):             studio/painters/index.html  (unlock shell + ciphertext)
                                studio/painters/img/**/*.bin (one encrypted WebP each)

The page shares the Studio password and salt (private/PASSWORD.txt, private/salt.b64),
so one unlock on /studio/ opens it. Every reproduction is encrypted with the same key
and decrypted in the browser as it scrolls into view. Nonces are deterministic (HMAC of
the plaintext), so an unchanged image or page re-encrypts to identical bytes.

Usage: python3 tools/build-painters.py          (password from private/PASSWORD.txt or STUDIO_PASSWORD)
"""
import base64, hashlib, hmac, os, re, subprocess, sys
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "private" / "painters"
OUT = ROOT / "studio" / "painters"
IMG_OUT = OUT / "img"
ITER = 600_000
BLANK = "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"


def password() -> str:
    pw = os.environ.get("STUDIO_PASSWORD") or (ROOT / "private" / "PASSWORD.txt").read_text().strip()
    if not pw:
        sys.exit("no password: set STUDIO_PASSWORD or fill private/PASSWORD.txt")
    return pw


def salt_b64() -> str:
    return (ROOT / "private" / "salt.b64").read_text().strip()


def referenced_images() -> list[str]:
    paths = set()
    for js in sorted((SRC / "data").glob("*.js")):
        paths.update(re.findall(r'img/[A-Za-z0-9_./-]+\.webp', js.read_text(encoding="utf-8")))
    return sorted(paths)


def shim(salt: str) -> str:
    return r"""
<script>
// Reproductions are stored encrypted (img/**/*.bin). This decrypts each one with the key the
// unlock page left in storage, as the image scrolls into view or opens in the viewer.
(function () {
  var STORE = 'studio-key:' + %s;
  var keyP = null, cache = new Map();
  function key() {
    if (keyP) return keyP;
    keyP = (async function () {
      var jwk = null;
      try { jwk = sessionStorage.getItem(STORE) || localStorage.getItem(STORE); } catch (e) {}
      if (!jwk) throw new Error('nokey');
      return crypto.subtle.importKey('jwk', JSON.parse(jwk), { name: 'AES-GCM' }, false, ['decrypt']);
    })();
    return keyP;
  }
  function dec(path) {
    if (cache.has(path)) return cache.get(path);
    var p = (async function () {
      var k = await key();
      var r = await fetch(path.replace(/\.webp$/, '.bin'), { cache: 'force-cache' });
      if (!r.ok) throw new Error('http ' + r.status);
      var buf = new Uint8Array(await r.arrayBuffer());
      var pt = await crypto.subtle.decrypt({ name: 'AES-GCM', iv: buf.slice(0, 12) }, k, buf.slice(12));
      return URL.createObjectURL(new Blob([pt], { type: 'image/webp' }));
    })();
    p.catch(function () { cache.delete(path); });
    cache.set(path, p); return p;
  }
  function setSrc(img, path) {
    if (!path) return;
    if (path.indexOf('img/') !== 0) { img.src = path; return; }
    var my = (img.__dec = (img.__dec || 0) + 1);
    dec(path).then(function (url) { if (img.__dec === my) img.src = url; },
                   function () { if (img.__dec === my) img.dispatchEvent(new Event('error')); });
  }
  var io = new IntersectionObserver(function (entries) {
    entries.forEach(function (en) {
      if (!en.isIntersecting) return;
      var img = en.target; io.unobserve(img);
      var path = img.getAttribute('data-dec'); img.removeAttribute('data-dec');
      setSrc(img, path);
    });
  }, { rootMargin: '900px 0px' });
  function scan(root) {
    if (!(root instanceof Element)) return;
    if (root.matches('img[data-dec]')) io.observe(root);
    root.querySelectorAll('img[data-dec]').forEach(function (im) { io.observe(im); });
  }
  new MutationObserver(function (muts) {
    muts.forEach(function (m) { m.addedNodes.forEach(scan); });
  }).observe(document.documentElement, { childList: true, subtree: true });
  scan(document.documentElement);
  window.__painters = { setSrc: setSrc, dec: dec };
  key().catch(function () {
    var n = document.createElement('div');
    n.style.cssText = 'position:fixed;left:0;right:0;bottom:0;z-index:99;padding:12px 16px;background:#B8321F;color:#fff;font:14px system-ui;text-align:center';
    n.innerHTML = 'Your unlock expired, so the paintings cannot be decrypted. <a href="' + location.pathname + '" style="color:#fff">Reload and enter the password again.</a>';
    document.body.appendChild(n);
  });
})();
</script>
""" % repr(salt)


def assemble(salt: str) -> str:
    frag = (SRC / "index.html").read_text(encoding="utf-8")
    frag = re.sub(r'<meta name="claude:viewport-fit-sides"[^>]*>\s*', "", frag)
    frag = frag.replace('<link rel="stylesheet" href="styles.css">', "<style>\n" + (SRC / "styles.css").read_text(encoding="utf-8") + "\n</style>")
    cut = frag.index('<div id="app"')
    head = frag[:cut].strip()
    body_html = frag[cut:]

    app = (SRC / "app.js").read_text(encoding="utf-8")
    before = app
    app = app.replace(
        """return '<img ' + loading + 'decoding="async" src="' + imgURL(file, w) + '" alt="' + esc(alt || "") + '" ' + extra + ">";""",
        """return '<img ' + loading + 'decoding="async" src="%s" data-dec="' + imgURL(file, w) + '" alt="' + esc(alt || "") + '" ' + extra + ">";""" % BLANK)
    app = app.replace("    V.img.src = url;\n", "    window.__painters.setSrc(V.img, url);\n")
    if app.count("data-dec") != 1 or "__painters.setSrc(V.img" not in app:
        sys.exit("app.js patch points not found; the app changed shape")
    for pat in (r'\.src\s*=', r'src="\' \+ imgURL'):
        leftovers = [m.start() for m in re.finditer(pat, app)]
        if leftovers:
            sys.exit(f"unpatched image assignment in app.js for pattern {pat!r}")

    scripts = []
    for name in re.findall(r'<script charset="utf-8" src="([^"]+)"></script>', body_html):
        if name == "app.js":
            scripts.append(shim(salt))
            scripts.append("<script>\n" + app + "\n</script>")
        else:
            scripts.append("<script>\n" + (SRC / name).read_text(encoding="utf-8") + "\n</script>")
    body_html = re.sub(r'(?:<script charset="utf-8" src="[^"]+"></script>\s*)+', "", body_html).rstrip()

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="robots" content="noindex, nofollow">
<meta name="referrer" content="no-referrer">
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
<link rel="icon" href="/favicon.ico" sizes="32x32">
<link rel="apple-touch-icon" href="/apple-touch-icon.png">
{head}
<style>
body {{ margin: 0; font-family: system-ui, sans-serif; font-size: 14px; }}
img {{ max-width: 100%; }}
[hidden] {{ display: none !important; }}
</style>
</head>
<body>
{body_html}
{"".join(scripts)}
</body>
</html>
"""


def main() -> None:
    if not SRC.is_dir():
        sys.exit(f"source not found: {SRC}")
    pw = password()
    salt = salt_b64()
    key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=base64.b64decode(salt), iterations=ITER).derive(pw.encode())
    aes = AESGCM(key)

    refs = referenced_images()
    missing = [p for p in refs if not (SRC / p).is_file()]
    if missing:
        sys.exit(f"{len(missing)} referenced images missing from {SRC}: {missing[:5]}")
    keep = set()
    total = 0
    for rel in refs:
        data = (SRC / rel).read_bytes()
        iv = hmac.new(key, data, hashlib.sha256).digest()[:12]
        out = OUT / re.sub(r"\.webp$", ".bin", rel)
        out.parent.mkdir(parents=True, exist_ok=True)
        blob = iv + aes.encrypt(iv, data, None)
        if not out.is_file() or out.read_bytes() != blob:
            out.write_bytes(blob)
        keep.add(out)
        total += len(blob)
    stale = [p for p in IMG_OUT.rglob("*.bin") if p not in keep]
    for p in stale:
        p.unlink()

    build = SRC / "build.html"
    build.write_text(assemble(salt), encoding="utf-8")
    subprocess.run([sys.executable, str(ROOT / "tools" / "lock.py"), "--source", str(build), "--output", str(OUT / "index.html"),
                    "--title", "The Painter's Hand", "--password", pw], check=True)
    print(f"painters: {len(refs)} images encrypted ({total:,} bytes), {len(stale)} stale removed, page {build.stat().st_size:,} bytes plaintext")


if __name__ == "__main__":
    main()
