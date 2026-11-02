#!/usr/bin/env python3
"""Build the locked /gallery/ page from private/artwork/manifest.json.

- Applies private/gallery-config.json (exclusions, renames, medium overrides).
- Infers missing dimensions from each image's aspect ratio (marked approx.).
- Thumbnails are inlined into the page (encrypted with it by lock.py).
- Full-size images are encrypted individually into gallery/img/<id>.bin with the
  same key, and decrypted in the browser on tap.
Password: $GALLERY_PASSWORD or private/GALLERY-PASSWORD.txt. Salt: private/gallery-salt.b64.
"""
import base64, hashlib, hmac, io, json, os, re, secrets, subprocess, sys
from pathlib import Path
from PIL import Image, ImageOps
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

ROOT = Path(__file__).resolve().parent.parent
ART = ROOT / "private" / "artwork"
MANIFEST = ART / "manifest.json"
CONFIG = ROOT / "private" / "gallery-config.json"
PAGE = ROOT / "private" / "gallery.html"
OUT_DIR = ROOT / "gallery"
IMG_DIR = OUT_DIR / "img"
SALT_FILE = ROOT / "private" / "gallery-salt.b64"
ITER = 600_000
THUMB, FULL = 640, 1800
STANDARD = [(5, 7), (6, 8), (8, 8), (8, 10), (8, 12), (9, 12), (10, 10), (11, 14), (12, 12), (12, 16), (12, 18), (12, 24), (14, 18), (16, 16), (16, 20), (18, 24), (20, 24), (24, 30), (24, 36), (30, 40)]
TYPICAL_AREA = 12 * 16  # the most common size in the collection; breaks ties between same-ratio sizes
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]


def password():
    p = os.environ.get("GALLERY_PASSWORD") or ((ROOT / "private" / "GALLERY-PASSWORD.txt").read_text().strip() if (ROOT / "private" / "GALLERY-PASSWORD.txt").is_file() else None)
    if not p: sys.exit("no gallery password: set GALLERY_PASSWORD or private/GALLERY-PASSWORD.txt")
    return p


def salt():
    if SALT_FILE.is_file(): return base64.b64decode(SALT_FILE.read_text().strip())
    s = secrets.token_bytes(16); SALT_FILE.write_text(base64.b64encode(s).decode() + "\n"); print("created gallery salt"); return s


def norm_dims(d):
    if not d: return None
    m = re.match(r"\s*(\d+)\s*[x×]\s*(\d+)", d)
    return f"{m.group(1)} × {m.group(2)} in" if m else d


def infer_dims(img: Image.Image):
    w, h = img.size
    r = max(w, h) / min(w, h)
    close = [s for s in STANDARD if abs(max(s) / min(s) - r) < 0.05]
    best = min(close, key=lambda s: abs(s[0] * s[1] - TYPICAL_AREA)) if close else min(STANDARD, key=lambda s: abs(max(s) / min(s) - r))
    a, b = (min(best), max(best)) if h >= w else (max(best), min(best))
    return f"{a} × {b} in (approx.)"


def load(rel):
    im = Image.open(ART / rel); im = ImageOps.exif_transpose(im).convert("RGB"); return im


def jpeg(im, long_edge, q):
    im = im.copy(); im.thumbnail((long_edge, long_edge), Image.LANCZOS)
    buf = io.BytesIO(); im.save(buf, "JPEG", quality=q, optimize=True, progressive=True); return buf.getvalue()


def main():
    cfg = json.load(open(CONFIG)); manifest = json.load(open(MANIFEST))
    pw = password(); s = salt()
    key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=s, iterations=ITER).derive(pw.encode())
    aes = AESGCM(key)
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    keep_files = set()
    works = []
    for e in manifest:
        if e["id"] in cfg["exclude_ids"]: continue
        med = (e.get("medium") or "").strip()
        if any(k in med.lower() for k in cfg["exclude_medium_keywords"]): continue
        if not e.get("images"): continue
        name = cfg["rename"].get(e["name"], e["name"])
        for pat in cfg["strip_name_patterns"]:
            if re.match(pat, name): name = "Portrait"
        med = cfg["medium_overrides"].get(e["id"], med)
        # dimensions: property, then "Dimensions: 16x20" in notes, then infer from first image
        dims = norm_dims(e.get("dimensions"))
        if not dims:
            for n in e.get("notes", []):
                m = re.search(r"Dimensions:\s*(\d+\s*[x×]\s*\d+)", n)
                if m: dims = norm_dims(m.group(1)); break
        imgs = []
        for rel in e["images"]:
            im = load(rel)
            if not dims: dims = infer_dims(im)
            iid = hashlib.sha1(rel.encode()).hexdigest()[:12]
            full = jpeg(im, FULL, 86)
            iv = hmac.new(key, full, hashlib.sha256).digest()[:12]  # deterministic: unchanged image = unchanged .bin
            (IMG_DIR / f"{iid}.bin").write_bytes(iv + aes.encrypt(iv, full, None)); keep_files.add(f"{iid}.bin")
            imgs.append({"id": iid, "thumb": "data:image/jpeg;base64," + base64.b64encode(jpeg(im, THUMB, 80)).decode(), "w": im.width, "h": im.height})
        y, mth = (e.get("date") or "0000-00")[:7].split("-")
        works.append({"id": e["id"][-8:], "title": name, "medium": med, "dims": dims, "year": int(y) if y.isdigit() else 0, "when": f"{MONTHS[int(mth) - 1]} {y}" if mth.isdigit() and 1 <= int(mth) <= 12 else y, "date": e.get("date") or "", "images": imgs, "note": "" if cfg.get("hide_notes") else " ".join(e.get("notes", []))})
    for f in IMG_DIR.iterdir():
        if f.name not in keep_files: f.unlink()
    works.sort(key=lambda w: w["date"], reverse=True)
    data = json.dumps({"salt": base64.b64encode(s).decode(), "works": works}, ensure_ascii=False)
    html = TEMPLATE.replace("__DATA__", data.replace("</", "<\\/"))
    PAGE.write_text(html, encoding="utf-8")
    print(f"gallery: {len(works)} works, {sum(len(w['images']) for w in works)} images, page {len(html):,} chars, {sum(f.stat().st_size for f in IMG_DIR.iterdir()):,} bytes of encrypted full images")
    env = dict(os.environ, STUDIO_PASSWORD=pw)
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "lock.py"), "--source", str(PAGE), "--output", str(OUT_DIR / "index.html"), "--salt-file", str(SALT_FILE), "--allow-short", "--title", "Gallery"], env=env, capture_output=True, text=True)
    print(r.stdout.strip()); 
    if r.returncode: sys.exit(r.stderr)


TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="robots" content="noindex, nofollow">
<title>Gallery</title>
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
<link rel="icon" href="/favicon.ico" sizes="32x32">
<link rel="apple-touch-icon" href="/apple-touch-icon.png">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Young+Serif&family=Karla:ital,wght@0,400;0,500;1,400&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
  :root { --bg:#ECE7DC; --fg:#221E1A; --muted:#6E655A; --rule:#CFC6B5; --frame:#F7F4EC; --frame-edge:#D9D2C2; --accent:#B8321F; --shadow:0 24px 50px -30px rgba(40,28,16,.45), 0 2px 0 rgba(255,255,255,.6) inset; --display:"Young Serif",Georgia,serif; --body:"Karla","Helvetica Neue",Arial,sans-serif; --mono:"IBM Plex Mono",Menlo,monospace; }
  @media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { --bg:#1E1B18; --fg:#EDE6D8; --muted:#A89D8D; --rule:#3B342D; --frame:#2A2521; --frame-edge:#3D362F; --accent:#E25A43; --shadow:0 30px 60px -30px rgba(0,0,0,.8); color-scheme:dark; } }
  :root[data-theme="dark"] { --bg:#1E1B18; --fg:#EDE6D8; --muted:#A89D8D; --rule:#3B342D; --frame:#2A2521; --frame-edge:#3D362F; --accent:#E25A43; --shadow:0 30px 60px -30px rgba(0,0,0,.8); color-scheme:dark; }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--fg); font-family:var(--body); font-size:17px; line-height:1.5; -webkit-font-smoothing:antialiased; }
  .wrap { max-width:1240px; margin:0 auto; padding:28px max(20px, env(safe-area-inset-right)) 80px max(20px, env(safe-area-inset-left)); }
  .rail { display:flex; justify-content:space-between; gap:16px; flex-wrap:wrap; font-family:var(--mono); font-size:12.5px; letter-spacing:.06em; text-transform:uppercase; color:var(--muted); padding-bottom:14px; border-bottom:1px solid var(--rule); }
  .rail a { color:inherit; text-decoration:none; } .rail a:hover { color:var(--fg); }
  .rail .dot { display:inline-block; width:8px; height:8px; border-radius:50%; background:var(--accent); margin-right:8px; vertical-align:1px; }
  header.hero { margin:34px 0 28px; display:flex; justify-content:space-between; align-items:flex-end; gap:24px; flex-wrap:wrap; }
  .eyebrow { font-family:var(--mono); font-size:12.5px; letter-spacing:.12em; text-transform:uppercase; color:var(--muted); margin:0 0 10px; }
  h1 { font-family:var(--display); font-weight:400; font-size:clamp(38px, 6vw, 64px); line-height:.98; letter-spacing:-.015em; margin:0; }
  h1 em { font-style:normal; color:var(--accent); }
  .lede { margin:12px 0 0; color:var(--muted); max-width:38em; }
  .count { font-family:var(--mono); font-size:12.5px; letter-spacing:.06em; text-transform:uppercase; color:var(--muted); }
  nav.years { display:flex; gap:8px; flex-wrap:wrap; margin:0 0 10px; }
  nav.years a { font-family:var(--mono); font-size:12px; letter-spacing:.04em; color:var(--muted); text-decoration:none; border:1px solid var(--rule); border-radius:999px; padding:6px 11px; background:var(--frame); }
  nav.years a:hover { color:var(--fg); border-color:var(--fg); }
  h2.year { font-family:var(--display); font-weight:400; font-size:30px; margin:38px 0 14px; display:flex; align-items:baseline; gap:14px; }
  h2.year small { font-family:var(--mono); font-size:12px; letter-spacing:.08em; text-transform:uppercase; color:var(--muted); }
  .grid { display:grid; grid-template-columns:repeat(auto-fill, minmax(260px, 1fr)); gap:22px; }
  @media (max-width:520px) { .grid { grid-template-columns:repeat(2, minmax(0,1fr)); gap:14px; } }
  figure.work { margin:0; cursor:zoom-in; }
  .mat { background:var(--frame); border:1px solid var(--frame-edge); box-shadow:var(--shadow); aspect-ratio:1/1; display:grid; place-items:center; padding:10%; position:relative; transition:transform .2s ease; }
  figure.work:hover .mat { transform:translateY(-2px); }
  .mat img { position:absolute; inset:10%; width:80%; height:80%; object-fit:contain; display:block; filter:drop-shadow(0 10px 16px rgba(0,0,0,.35)); }
  .mat .more { position:absolute; right:10px; bottom:8px; font-family:var(--mono); font-size:11px; letter-spacing:.06em; color:var(--muted); }
  figcaption { padding:10px 2px 0; }
  figcaption b { display:block; font-weight:500; font-size:16px; }
  figcaption span { display:block; font-family:var(--mono); font-size:12px; letter-spacing:.03em; color:var(--muted); margin-top:3px; }
  /* lightbox */
  .lb { position:fixed; inset:0; background:rgba(20,16,12,.94); display:none; z-index:20; color:#EDE6D8; }
  .lb.open { display:grid; grid-template-rows:1fr auto; }
  .lb .stage { display:grid; place-items:center; padding:48px 16px 8px; min-height:0; }
  .lb img { max-width:100%; max-height:calc(100vh - 150px); box-shadow:0 30px 80px -30px rgba(0,0,0,.9); opacity:0; transition:opacity .25s ease; }
  .lb img.in { opacity:1; }
  .lb .cap { text-align:center; padding:8px 16px max(24px, env(safe-area-inset-bottom)); }
  .lb .cap b { display:block; font-family:var(--display); font-weight:400; font-size:22px; }
  .lb .cap span { font-family:var(--mono); font-size:12.5px; letter-spacing:.04em; color:#A89D8D; }
  .lb .cap .note { display:block; margin-top:6px; color:#CFC6B5; font-size:14.5px; max-width:46em; margin-inline:auto; }
  .lb button { position:absolute; appearance:none; background:rgba(255,255,255,.08); border:1px solid rgba(255,255,255,.18); color:#EDE6D8; font:500 14px/1 var(--body); padding:12px 14px; border-radius:999px; cursor:pointer; }
  .lb button:hover { background:rgba(255,255,255,.16); }
  .lb .x { top:14px; right:14px; } .lb .prev { left:12px; top:50%; transform:translateY(-50%); } .lb .next { right:12px; top:50%; transform:translateY(-50%); }
  .lb .status { position:absolute; top:18px; left:50%; transform:translateX(-50%); font-family:var(--mono); font-size:12px; letter-spacing:.06em; color:#A89D8D; }
  .warn a { color:var(--accent); }
  .warn { background:var(--frame); border:1px solid var(--frame-edge); padding:14px 18px; margin:16px 0; color:var(--muted); font-size:15px; }
  footer { margin-top:48px; padding-top:14px; border-top:1px solid var(--rule); font-family:var(--mono); font-size:12px; color:var(--muted); letter-spacing:.04em; display:flex; justify-content:space-between; gap:12px; flex-wrap:wrap; }
</style>
</head>
<body>
<div class="wrap">
  <header class="rail"><span><span class="dot" aria-hidden="true"></span>Gallery · private</span><a href="/">noahalexanderart.com</a></header>
  <header class="hero">
    <div>
      <p class="eyebrow">Noah Alexander</p>
      <h1>Practice, <em>in progress.</em></h1>
      <p class="lede">Studies and paintings from the last few years, newest first. Shared with a few people; please keep it that way.</p>
    </div>
    <span class="count" id="count"></span>
  </header>
  <nav class="years" id="years"></nav>
  <div id="warn"></div>
  <div id="root"></div>
  <footer><span>Tap any painting to see it large.</span><span id="updated"></span></footer>
</div>
<div class="lb" id="lb" role="dialog" aria-modal="true" aria-label="Painting">
  <span class="status" id="lb-status"></span>
  <button class="x" id="lb-x" aria-label="Close">Close</button>
  <button class="prev" id="lb-prev" aria-label="Previous">‹</button>
  <button class="next" id="lb-next" aria-label="Next">›</button>
  <div class="stage"><img id="lb-img" alt=""></div>
  <div class="cap" id="lb-cap"></div>
</div>
<script type="application/json" id="data">__DATA__</script>
<script>
(function () {
  const D = JSON.parse(document.getElementById('data').textContent);
  const W = D.works, STORE = 'studio-key:' + D.salt;
  const $ = s => document.querySelector(s);
  const el = (t, a = {}, ...k) => { const n = document.createElement(t); for (const [x, v] of Object.entries(a)) { if (x === 'class') n.className = v; else if (x.startsWith('on')) n.addEventListener(x.slice(2), v); else n.setAttribute(x, v); } for (const c of k.flat()) if (c != null) n.append(c.nodeType ? c : document.createTextNode(c)); return n; };
  const cap = w => [w.medium, w.dims, w.when].filter(Boolean).join(' · ');

  // grid by year
  const root = $('#root'), years = [...new Set(W.map(w => w.year))].sort((a, b) => b - a);
  $('#count').textContent = `${W.length} works`;
  years.forEach(y => { $('#years').append(el('a', { href: '#y' + y }, String(y))); const ws = W.filter(w => w.year === y); root.append(el('h2', { class: 'year', id: 'y' + y }, String(y), el('small', {}, `${ws.length} work${ws.length === 1 ? '' : 's'}`))); const g = el('div', { class: 'grid' }); ws.forEach(w => g.append(card(w))); root.append(g); });
  function card(w) {
    const im = el('img', { src: w.images[0].thumb, alt: w.title, loading: 'lazy', width: w.images[0].w, height: w.images[0].h });
    const f = el('figure', { class: 'work', id: 'w-' + w.id, onclick: () => open(W.indexOf(w), 0), tabindex: 0, onkeydown: e => { if (e.key === 'Enter') open(W.indexOf(w), 0); } }, el('div', { class: 'mat' }, im, w.images.length > 1 ? el('span', { class: 'more' }, `${w.images.length} images`) : null), el('figcaption', {}, el('b', {}, w.title), el('span', {}, cap(w))));
    return f;
  }

  // key + decryption
  let keyP = null;
  async function key() {
    if (keyP) return keyP;
    keyP = (async () => {
      let jwk = null; try { jwk = sessionStorage.getItem(STORE) || localStorage.getItem(STORE); } catch (e) {}
      if (!jwk) throw new Error('nokey');
      return crypto.subtle.importKey('jwk', JSON.parse(jwk), { name: 'AES-GCM' }, false, ['decrypt']);
    })();
    return keyP;
  }
  const cache = new Map();
  async function full(id) {
    if (cache.has(id)) return cache.get(id);
    const k = await key();
    const buf = new Uint8Array(await (await fetch('img/' + id + '.bin', { cache: 'force-cache' })).arrayBuffer());
    const pt = await crypto.subtle.decrypt({ name: 'AES-GCM', iv: buf.slice(0, 12) }, k, buf.slice(12));
    const url = URL.createObjectURL(new Blob([pt], { type: 'image/jpeg' }));
    cache.set(id, url); return url;
  }
  key().catch(() => { $('#warn').append(el('div', { class: 'warn' }, 'Your unlock expired, so full-size images cannot be opened. ', el('a', { href: location.pathname }, 'Reload and enter the password again.'))); });

  // lightbox
  const lb = $('#lb'), img = $('#lb-img'), st = $('#lb-status'); let cur = -1, sub = 0, token = 0;
  async function show() {
    const w = W[cur], im = w.images[sub]; const my = ++token;
    img.classList.remove('in'); img.src = im.thumb; img.alt = w.title; st.textContent = 'Loading…';
    $('#lb-cap').innerHTML = ''; $('#lb-cap').append(el('b', {}, w.title + (w.images.length > 1 ? ` (${sub + 1} of ${w.images.length})` : '')), el('span', {}, cap(w)), w.note ? el('span', { class: 'note' }, w.note) : null);
    history.replaceState(null, '', '#w-' + w.id);
    try { const url = await full(im.id); if (my !== token) return; img.src = url; img.onload = () => img.classList.add('in'); st.textContent = ''; }
    catch (e) { if (my !== token) return; img.classList.add('in'); st.textContent = e.message === 'nokey' ? 'Reload to unlock full size' : 'Could not load full size'; }
  }
  function open(i, s) { cur = i; sub = s; lb.classList.add('open'); document.body.style.overflow = 'hidden'; show(); }
  function close() { lb.classList.remove('open'); document.body.style.overflow = ''; history.replaceState(null, '', location.pathname); }
  function step(d) { const w = W[cur]; if (sub + d >= 0 && sub + d < w.images.length) { sub += d; } else { cur = (cur + d + W.length) % W.length; sub = d > 0 ? 0 : W[cur].images.length - 1; } show(); }
  $('#lb-x').onclick = close; $('#lb-prev').onclick = () => step(-1); $('#lb-next').onclick = () => step(1);
  lb.addEventListener('click', e => { if (e.target === lb || e.target.classList.contains('stage')) close(); });
  document.addEventListener('keydown', e => { if (!lb.classList.contains('open')) return; if (e.key === 'Escape') close(); if (e.key === 'ArrowLeft') step(-1); if (e.key === 'ArrowRight') step(1); });
  let tx = null; lb.addEventListener('touchstart', e => tx = e.touches[0].clientX, { passive: true }); lb.addEventListener('touchend', e => { if (tx == null) return; const dx = e.changedTouches[0].clientX - tx; if (Math.abs(dx) > 50) step(dx < 0 ? 1 : -1); tx = null; });
  const m = /#w-([0-9a-f]+)/.exec(location.hash); if (m) { const i = W.findIndex(w => w.id === m[1]); if (i >= 0) open(i, 0); }
  $('#updated').textContent = 'Updated ' + new Date(document.lastModified).toLocaleDateString('en-US', { month: 'long', day: 'numeric', year: 'numeric' });
})();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()
