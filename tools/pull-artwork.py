#!/usr/bin/env python3
"""Pull the Artwork database from Notion into private/artwork/.

Needs a Notion internal integration token with read access, connected to the
Artwork database. Token lookup order: $NOTION_TOKEN, private/notion-token.txt,
~/thirdway-local/credentials/notion-token.txt.

Writes private/artwork/manifest.json and downloads each page's images into
private/artwork/<page-id>/. Re-runs are incremental: pages whose
last_edited_time is unchanged and whose images already exist are skipped.
"""
import json, os, re, sys, time, urllib.request, urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "private" / "artwork"
MANIFEST = OUT / "manifest.json"
DATABASE_ID = "9bd7a67181084ef08ba12d99f122a1a5"
API = "https://api.notion.com/v1"
VERSION = "2022-06-28"


def token() -> str:
    for cand in [os.environ.get("NOTION_TOKEN"),
                 (ROOT / "private" / "notion-token.txt"),
                 Path.home() / "thirdway-local" / "credentials" / "notion-token.txt"]:
        if isinstance(cand, str) and cand:
            return cand.strip()
        if isinstance(cand, Path) and cand.is_file():
            return cand.read_text().strip()
    sys.exit("no Notion token: set NOTION_TOKEN or save it to private/notion-token.txt")


TOKEN = None


def api(method, path, body=None):
    req = urllib.request.Request(API + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {TOKEN}", "Notion-Version": VERSION, "Content-Type": "application/json"})
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(float(e.headers.get("Retry-After", "2"))); continue
            sys.exit(f"Notion API {e.code} on {path}: {e.read().decode()[:300]}")
    sys.exit("Notion API: too many retries")


def plain(rich): return "".join(t.get("plain_text", "") for t in rich or [])


def query_all():
    rows, cursor = [], None
    while True:
        body = {"page_size": 100, "sorts": [{"property": "Date", "direction": "descending"}]}
        if cursor: body["start_cursor"] = cursor
        res = api("POST", f"/databases/{DATABASE_ID}/query", body)
        rows += res["results"]
        if not res.get("has_more"): return rows
        cursor = res["next_cursor"]


def blocks(page_id):
    out, cursor = [], None
    while True:
        res = api("GET", f"/blocks/{page_id}/children?page_size=100" + (f"&start_cursor={cursor}" if cursor else ""))
        out += res["results"]
        if not res.get("has_more"): return out
        cursor = res["next_cursor"]


def download(url, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "curl/8"})
    with urllib.request.urlopen(req, timeout=120) as r, open(dest, "wb") as f:
        f.write(r.read())


def main():
    global TOKEN
    TOKEN = token()
    old = {e["id"]: e for e in json.load(open(MANIFEST))} if MANIFEST.is_file() else {}
    manifest, downloaded, skipped = [], 0, 0
    for page in query_all():
        pid = page["id"].replace("-", "")
        props = page["properties"]
        entry = {
            "id": pid,
            "name": plain(props.get("Name", {}).get("title")),
            "medium": plain(props.get("Medium", {}).get("rich_text")),
            "dimensions": (props.get("Dimensions", {}).get("select") or {}).get("name"),
            "date": (props.get("Date", {}).get("date") or {}).get("start"),
            "tags": [t["name"] for t in props.get("Tags", {}).get("multi_select", [])],
            "last_edited": page["last_edited_time"],
            "images": [], "notes": [],
        }
        prev = old.get(pid)
        if prev and prev.get("last_edited") == entry["last_edited"] and all((OUT / p).is_file() for p in prev.get("images", [])):
            manifest.append(prev); skipped += 1; continue
        for b in blocks(page["id"]):
            t = b["type"]
            if t == "image":
                src = b["image"].get("file") or b["image"].get("external") or {}
                url = src.get("url")
                if not url: continue
                name = re.sub(r"[^A-Za-z0-9._-]", "_", url.split("?")[0].rsplit("/", 1)[-1]) or "image"
                rel = f"{pid}/{b['id'].replace('-', '')[:8]}-{name}"
                dest = OUT / rel
                if not dest.is_file():
                    download(url, dest); downloaded += 1
                entry["images"].append(rel)
            elif t in ("paragraph", "quote", "callout", "bulleted_list_item", "numbered_list_item"):
                txt = plain(b[t].get("rich_text")).strip()
                if txt: entry["notes"].append(txt)
        manifest.append(entry)
        print(f"pulled {entry['name']!r} ({len(entry['images'])} image{'s' if len(entry['images']) != 1 else ''})")
    OUT.mkdir(parents=True, exist_ok=True)
    json.dump(manifest, open(MANIFEST, "w"), indent=1, ensure_ascii=False)
    print(f"manifest: {len(manifest)} pages, {downloaded} images downloaded, {skipped} pages unchanged")


if __name__ == "__main__":
    main()
