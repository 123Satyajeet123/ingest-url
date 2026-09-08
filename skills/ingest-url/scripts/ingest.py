#!/usr/bin/env -S uv run --quiet --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "yt-dlp[default,curl-cffi]", "pillow", "trafilatura", "pymupdf4llm",
#   "mlx-whisper; sys_platform == 'darwin' and platform_machine == 'arm64'",
#   "faster-whisper; sys_platform != 'darwin' or platform_machine != 'arm64'",
# ]
# ///
"""URL -> agent-readable files. Nonzero exit with a reason, never an empty text.md.
Output: ~/.cache/ingest-url/<hash>/ unless --out; a repeat URL is served from there.

video    text.md (front-matter, CHAPTER lines, one [mm:ss] line per minute) + manifest.json.
         Captions in the video's language, English fallback, else on-device speech-to-text.
         --frames adds source.mp4, frames/<seconds>.jpg, sheets/NN.jpg.
article  text.md. Hacker News item URLs become the comment tree.
pdf      arXiv: LaTeX source with "--- file ---" markers. Else "--- page N ---" markers + images/.
find     one block per platform (YouTube, arXiv, Semantic Scholar/OpenAlex, Hacker News, GitHub):
         date | signal | title | url. No merged ranking.
"""
import argparse, hashlib, json, os, re, subprocess, sys, tarfile, time, urllib.error, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

CACHE_ROOT = Path(os.environ.get("INGEST_CACHE", Path.home() / ".cache/ingest-url"))
COOKIE_BROWSER = os.environ.get("INGEST_BROWSER", "chrome")   # browser whose cookies yt-dlp uses
STT_BACKEND = os.environ.get("INGEST_STT", "auto")              # auto | mlx-whisper | faster-whisper
STT_MODEL = "large-v3-turbo"
SCENE_THRESHOLD = 0.35
FRAME_FLOOR_SECONDS = 10    # scene detection alone gives one frame for a motion-graphics reel
BUCKET_SECONDS = 60
COLS, ROWS, TILE_W, TILE_H = 6, 8, 320, 180
MEDIA_SUFFIXES = (".mp4", ".m4a", ".webm", ".mkv")


def die(msg):
    sys.exit(f"ingest: {msg}")


def write_nonempty(path, text):
    if not text.strip():
        die(f"no text extracted for {path.parent.name}; paywalled or client-rendered? Open it in a browser session that carries your login")
    path.write_text(text)


def cached(out, url, frames=False):
    manifest = out / "manifest.json"
    if not manifest.exists():
        return False
    m = json.loads(manifest.read_text())
    if m.get("source_url") != url or (frames and not m.get("frames_requested")):
        return False
    print(f"{out}: cached. Next: read {out / 'text.md'}" + (f", then {out / 'sheets'}" if m.get("frames_requested") else ""))
    return True


def download(url, path):
    """Browser user agent: many hosts refuse Python's default one."""
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/128 Safari/537.36"})
    with urllib.request.urlopen(request, timeout=60) as response:
        path.write_bytes(response.read())


def mmss(seconds):
    return f"{int(seconds) // 60:02d}:{int(seconds) % 60:02d}"


# ---------- video ----------

def ytdlp(args):
    base = [sys.executable, "-m", "yt_dlp", "--cookies-from-browser", COOKIE_BROWSER, "--impersonate", "chrome", "--no-progress"]
    proc = subprocess.run(base + args, capture_output=True, text=True)
    if proc.returncode:
        die(proc.stderr.strip().splitlines()[-1])


def vtt_segments(path):
    """YouTube repeats each cue two or three times as it scrolls; keep the first."""
    segments, last, t = [], "", 0
    for line in path.read_text(encoding="utf8").split("\n"):
        m = re.match(r"(\d\d):(\d\d):(\d\d)\.\d+ --> ", line)
        if m:
            t = int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3])
            continue
        if not line.strip() or line.startswith(("WEBVTT", "Kind:", "Language:")):
            continue
        text = re.sub(r"<[^>]+>", "", line).replace("&nbsp;", " ").strip()
        if text and text != last:
            segments.append((t, text))
            last = text
    return segments


def stt_backend():
    for name in ([STT_BACKEND] if STT_BACKEND != "auto" else ["mlx-whisper", "faster-whisper"]):
        try:
            return name, __import__(name.replace("-", "_"))
        except ImportError:
            continue
    die("no captions and no speech-to-text backend importable; install mlx-whisper (Apple Silicon) or faster-whisper")


def asr_segments(media):
    name, module = stt_backend()
    wav = media.with_suffix(".wav")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(media), "-vn", "-ac", "1", "-ar", "16000", str(wav)], check=True)
    if name == "mlx-whisper":
        result = module.transcribe(str(wav), path_or_hf_repo=f"mlx-community/whisper-{STT_MODEL}")
        segments, language = result["segments"], result.get("language")
        segments = [(int(s["start"]), s["text"]) for s in segments]
    else:
        model = module.WhisperModel(STT_MODEL, device="auto", compute_type="auto")
        raw, info = model.transcribe(str(wav))
        segments, language = [(int(s.start), s.text) for s in raw], info.language
    wav.unlink()
    return [(t, text.strip()) for t, text in segments if text.strip()], language


def front_matter(title, source, author=None, published=None):
    """Field names follow Obsidian Web Clipper so vaults index the file unchanged."""
    fields = {"title": title, "source": source, "author": author, "published": published,
              "created": time.strftime("%Y-%m-%d")}
    return "---\n" + "".join(f"{k}: {json.dumps(v, ensure_ascii=False)}\n" for k, v in fields.items() if v) + "---\n\n"


def transcript_markdown(info, segments):
    buckets = {}
    for t, text in segments:
        buckets.setdefault(t // BUCKET_SECONDS * BUCKET_SECONDS, []).append(text)
    date = info.get("upload_date")
    lines = [front_matter(info["title"], info.get("webpage_url"), info.get("channel"),
                          f"{date[:4]}-{date[4:6]}-{date[6:]}" if date else None) + f"# {info['title']}", ""]
    lines += [f"CHAPTER {mmss(c['start_time'])} {c['title']}" for c in info.get("chapters") or []]
    lines.append("")
    lines += [f"[{mmss(t)}] {' '.join(texts)}" for t, texts in sorted(buckets.items())]
    return "\n".join(lines) + "\n"


def scene_frames(media, frames_dir):
    """Frames named by second. showinfo's pts_time is the clock; -frame_pts counts in stream timebase."""
    frames_dir.mkdir(exist_ok=True)
    select = f"gt(scene,{SCENE_THRESHOLD})+isnan(prev_selected_t)+gte(t-prev_selected_t,{FRAME_FLOOR_SECONDS})"
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(media), "-vf", f"select='{select}',showinfo,scale=960:-1",
         "-fps_mode", "vfr", str(frames_dir / "%05d.jpg")], capture_output=True, text=True)
    if proc.returncode:
        die(proc.stderr.strip().splitlines()[-1])
    times = [float(m) for m in re.findall(r"pts_time:\s*([\d.]+)", proc.stderr)]
    files = sorted(frames_dir.glob("[0-9]*.jpg"))
    if len(times) != len(files):
        die(f"showinfo reported {len(times)} frames but {len(files)} were written")
    for f, t in zip(files, times):
        target = frames_dir / f"{int(t):05d}.jpg"
        while target.exists():
            target = target.with_name(target.stem + "b.jpg")
        f.rename(target)
    return sorted(frames_dir.glob("*.jpg"))


def contact_sheets(frames, sheets_dir):
    from PIL import Image, ImageDraw
    sheets_dir.mkdir(exist_ok=True)
    per_sheet = COLS * ROWS
    for n, start in enumerate(range(0, len(frames), per_sheet)):
        sheet = Image.new("RGB", (COLS * TILE_W, ROWS * TILE_H), "black")
        draw = ImageDraw.Draw(sheet)
        for j, f in enumerate(frames[start:start + per_sheet]):
            x, y = (j % COLS) * TILE_W, (j // COLS) * TILE_H
            sheet.paste(Image.open(f).resize((TILE_W, TILE_H)), (x, y))
            draw.rectangle([x, y, x + 70, y + 16], fill="black")
            draw.text((x + 3, y + 2), mmss(int(f.stem.rstrip("b"))), fill="yellow")
        sheet.save(sheets_dir / f"{n:02d}.jpg", quality=80)
    return sorted(sheets_dir.glob("*.jpg"))


def download_media(url, out, with_video):
    fmt = "bv*[height<=720]+ba/b[height<=720]" if with_video else "ba/b"
    ytdlp(["-f", fmt, "--merge-output-format", "mp4", "-o", str(out / "source.%(ext)s"), url])
    media = [p for p in out.iterdir() if p.stem == "source" and p.suffix in MEDIA_SUFFIXES]
    if not media:
        die(f"download reported success but no media file appeared in {out}")
    return media[0]


def caption_file(out, language):
    """Video's own language before English, uploader tracks before auto-generated."""
    files = list(out.glob("source.*.vtt"))
    if not files:
        return None
    lang = (language or "en").split("-")[0]
    return min(files, key=lambda p: (not p.name.startswith(f"source.{lang}"), "orig" in p.name, p.name))


def video(url, out, frames=False):
    if cached(out, url, frames):
        return
    out.mkdir(parents=True, exist_ok=True)
    ytdlp(["--write-info-json", "--skip-download", "-o", str(out / "source.%(ext)s"), url])
    info = json.loads((out / "source.info.json").read_text())
    language = info.get("language")
    lang = (language or "en").split("-")[0]
    ytdlp(["--write-auto-subs", "--write-subs", "--sub-langs", f"{lang},{lang}-orig,en,en-orig", "--skip-download",
           "-o", str(out / "source.%(ext)s"), url])
    captions = caption_file(out, language)
    media = download_media(url, out, with_video=frames) if frames or not captions else None
    if captions:
        segments = vtt_segments(captions)
        language = captions.name.split(".")[1].split("-")[0]
    else:
        segments, language = asr_segments(media)
    write_nonempty(out / "text.md", transcript_markdown(info, segments))
    manifest = {k: info.get(k) for k in ("id", "title", "channel", "upload_date", "duration", "chapters", "webpage_url")}
    manifest |= {"source_url": url, "frames_requested": frames}
    manifest["transcript"] = "captions" if captions else "speech-to-text"
    manifest["language"] = language
    manifest["words"] = sum(len(t.split()) for _, t in segments)
    if frames:
        manifest["frames"] = len(scene_frames(media, out / "frames"))
        manifest["sheets"] = len(contact_sheets(sorted((out / "frames").glob("*.jpg")), out / "sheets"))
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1))
    print(f"{out}: {manifest.get('duration')}s, {manifest['words']} words from {manifest['transcript']} ({language}), "
          f"{len(manifest.get('chapters') or [])} chapters, {manifest.get('frames', 0)} frames, {manifest.get('sheets', 0)} sheets. "
          f"Next: read {out / 'text.md'}" + (f", then {out / 'sheets'}" if frames else ""))
    for c in manifest.get("chapters") or []:
        print(f"  {mmss(c['start_time'])} {c['title']}")


# ---------- article ----------

def hn_thread(item_id):
    data = get_json(f"https://hn.algolia.com/api/v1/items/{item_id}")
    strip = lambda html: re.sub(r"<[^>]+>", "", (html or "").replace("<p>", "\n\n")).strip()
    lines = [front_matter(data.get("title") or f"HN {item_id}", f"https://news.ycombinator.com/item?id={item_id}",
                          data.get("author"), (data.get("created_at") or "")[:10]),
             f"# {data.get('title') or ''}", "", data.get("url") or "", "", strip(data.get("text")), ""]
    def walk(node, depth):
        for child in node.get("children") or []:
            if child.get("text"):
                lines.append(f"{'  ' * depth}- **{child.get('author')}**: {strip(child['text'])}")
            walk(child, depth + 1)
    walk(data, 0)
    return "\n".join(lines) + "\n"


def article(url, out):
    import trafilatura
    if cached(out, url):
        return
    out.mkdir(parents=True, exist_ok=True)
    hn = re.search(r"news\.ycombinator\.com/item\?id=(\d+)", url)
    if hn:
        text = hn_thread(hn[1])
        comments = text.count("\n- **")
        write_nonempty(out / "text.md", text)
        (out / "manifest.json").write_text(json.dumps({"source_url": url, "comments": comments}, indent=1))
        print(f"{out}: HN thread, {comments} comments. Next: read {out / 'text.md'}")
        return
    html = trafilatura.fetch_url(url)
    if not html:
        die(f"fetch failed for {url}")
    text = trafilatura.extract(html, url=url, output_format="markdown", with_metadata=True, include_links=False) or ""
    write_nonempty(out / "text.md", text)
    (out / "manifest.json").write_text(json.dumps({"source_url": url, "lines": len(text.splitlines())}, indent=1))
    print(f"{out}: {len(text.splitlines())} lines. Next: read {out / 'text.md'}")


# ---------- pdf ----------

def arxiv_id(url):
    m = re.search(r"arxiv\.org/(?:abs|pdf|src|html)/(\d{4}\.\d{4,5}(?:v\d+)?)", url)
    return m[1] if m else None


def arxiv_metadata(aid):
    try:
        xml = urllib.request.urlopen(f"https://export.arxiv.org/api/query?id_list={aid}", timeout=20).read().decode()
        entry = xml.split("<entry>", 1)[1]
        pick = lambda tag: re.search(rf"<{tag}>\s*(.*?)\s*</{tag}>", entry, re.S)
        return {"title": " ".join(pick("title")[1].split()), "author": pick("name")[1], "published": pick("published")[1][:10]}
    except (urllib.error.URLError, IndexError, TypeError, OSError):
        return {}


def arxiv_source(aid, out):
    """None for PDF-only submissions."""
    tgz = out / "source.tar.gz"
    try:
        download(f"https://arxiv.org/src/{aid}", tgz)
        with tarfile.open(tgz) as tar:
            tex = {m.name: tar.extractfile(m).read().decode("utf8", "replace") for m in tar.getmembers() if m.name.endswith(".tex")}
    except (urllib.error.URLError, tarfile.TarError, OSError):
        return None
    if not tex:
        return None
    main = next((n for n, t in tex.items() if "\\begin{document}" in t), sorted(tex)[0])
    rest = sorted(n for n in tex if n != main)
    return "".join(f"\n\n--- file {n} ---\n\n{tex[n]}" for n in [main] + rest)


def pdf(source, out):
    import pymupdf4llm
    if cached(out, source):
        return
    out.mkdir(parents=True, exist_ok=True)
    aid = arxiv_id(source)
    tex = arxiv_source(aid, out) if aid else None
    if tex:
        meta = arxiv_metadata(aid)
        write_nonempty(out / "text.md", front_matter(meta.get("title", aid), f"https://arxiv.org/abs/{aid}", meta.get("author"), meta.get("published")) + tex)
        (out / "manifest.json").write_text(json.dumps({"source_url": source, "format": "latex", "files": tex.count("--- file ")}, indent=1))
        print(f"{out}: arXiv LaTeX source, {tex.count('--- file ')} files. Next: read {out / 'text.md'}; grep '^\\\\section' for the map")
        return
    path = Path(source)
    if source.startswith("http"):
        path = out / "source.pdf"
        try:
            download(f"https://arxiv.org/pdf/{aid}" if aid else source, path)
        except (urllib.error.URLError, OSError) as error:
            die(f"download failed for {source}: {error}")
    pages = pymupdf4llm.to_markdown(str(path), page_chunks=True, write_images=True, image_path=str(out / "images"))
    text = "".join(f"\n\n--- page {n} ---\n\n{p['text']}" for n, p in enumerate(pages, 1))
    write_nonempty(out / "text.md", front_matter(path.stem, source) + text)
    images = len(list((out / "images").glob("*")))
    (out / "manifest.json").write_text(json.dumps({"source_url": source, "format": "pdf", "pages": len(pages), "images": images}, indent=1))
    print(f"{out}: {len(pages)} pages, {images} images. Next: read {out / 'text.md'}; grep '^--- page\\|^#' for the map")


# ---------- find ----------

def get_json(url):
    request = urllib.request.Request(url, headers={"User-Agent": "ingest-url (https://github.com/123Satyajeet123/ingest-url)"})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read())


def find_youtube(query, limit):
    proc = subprocess.run([sys.executable, "-m", "yt_dlp", f"ytsearch{limit}:{query}", "--flat-playlist", "--no-warnings",
                           "--print", "%(upload_date,release_date|)s\t%(view_count|0)s views\t%(title)s\t%(url)s"],
                          capture_output=True, text=True)
    return [line.split("\t") for line in proc.stdout.splitlines() if line.count("\t") == 3]


def find_arxiv(query, limit):
    q = urllib.parse.quote(f"all:{query}")
    xml = urllib.request.urlopen(f"https://export.arxiv.org/api/query?search_query={q}&sortBy=relevance&max_results={limit}", timeout=20).read().decode()
    rows = []
    for entry in xml.split("<entry>")[1:]:
        pick = lambda tag: " ".join(re.search(rf"<{tag}>\s*(.*?)\s*</{tag}>", entry, re.S)[1].split())
        rows.append([pick("published")[:10], "", pick("title"), pick("id").replace("/abs/", "/pdf/")])
    return rows


def find_papers(query, limit):
    """Semantic Scholar throttles anonymous callers; OpenAlex is the fallback."""
    try:
        data = get_json("https://api.semanticscholar.org/graph/v1/paper/search?" + urllib.parse.urlencode(
            {"query": query, "limit": limit, "fields": "title,year,citationCount,externalIds,openAccessPdf"}))
        return [[str(p.get("year") or ""), f"{p.get('citationCount', 0)} citations", p["title"],
                 f"https://arxiv.org/pdf/{p['externalIds']['ArXiv']}" if (p.get("externalIds") or {}).get("ArXiv")
                 else (p.get("openAccessPdf") or {}).get("url") or f"https://www.semanticscholar.org/paper/{p['paperId']}"]
                for p in data.get("data", [])]
    except (urllib.error.URLError, OSError, KeyError):
        data = get_json("https://api.openalex.org/works?" + urllib.parse.urlencode(
            {"search": query, "per-page": limit, "select": "title,publication_year,cited_by_count,open_access,doi"}))
        return [[str(w.get("publication_year") or ""), f"{w.get('cited_by_count', 0)} citations", w["title"],
                 (w.get("open_access") or {}).get("oa_url") or w.get("doi") or ""] for w in data.get("results", [])]


def find_hn(query, limit):
    data = get_json("https://hn.algolia.com/api/v1/search?" + urllib.parse.urlencode({"query": query, "tags": "story", "hitsPerPage": limit}))
    return [[h["created_at"][:10], f"{h.get('points', 0)} points, {h.get('num_comments', 0)} comments", h["title"],
             h.get("url") or f"https://news.ycombinator.com/item?id={h['objectID']}"] for h in data.get("hits", [])]


def find_github(query, limit):
    data = get_json("https://api.github.com/search/repositories?" + urllib.parse.urlencode({"q": query, "sort": "stars", "per_page": limit}))
    return [[r["pushed_at"][:10], f"{r['stargazers_count']} stars", r["full_name"] + (f": {r['description']}" if r.get("description") else ""), r["html_url"]]
            for r in data.get("items", [])]


FINDERS = {"youtube": find_youtube, "arxiv": find_arxiv, "papers": find_papers, "hn": find_hn, "github": find_github}


def find(query, sources, limit):
    def run(name):
        try:
            return name, FINDERS[name](query, limit), None
        except Exception as error:
            return name, [], f"{type(error).__name__}: {str(error)[:120]}"
    with ThreadPoolExecutor(len(sources)) as pool:
        for name, rows, error in pool.map(run, sources):
            print(f"## {name}" + (f" (failed: {error})" if error else f" ({len(rows)})"))
            for date, signal, title, url in rows:
                print(f"{date or '----------':10} | {signal:>24} | {title[:90]} | {url}")
            print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("kind", choices=("video", "article", "pdf", "find"))
    parser.add_argument("urls", nargs="+", metavar="URL|QUERY")
    parser.add_argument("--sources", default=",".join(FINDERS), help="find only: comma list of " + ",".join(FINDERS))
    parser.add_argument("--limit", type=int, default=8, help="find only: results per platform")
    parser.add_argument("--out", type=Path, help="output directory (single URL only); default: <cache root>/<12-char hash of url>")
    parser.add_argument("--frames", action="store_true", help="video only: extract scene frames and contact sheets")
    a = parser.parse_args()
    if a.kind == "find":
        unknown = set(a.sources.split(",")) - set(FINDERS)
        if unknown:
            parser.error(f"unknown sources: {', '.join(sorted(unknown))}")
        find(" ".join(a.urls), a.sources.split(","), a.limit)
        sys.exit(0)
    if a.frames and a.kind != "video":
        parser.error("--frames applies to video only")
    if a.out and len(a.urls) > 1:
        parser.error("--out takes a single URL")
    run = {"video": lambda u, o: video(u, o, frames=a.frames), "article": article, "pdf": pdf}[a.kind]
    for url in a.urls:
        run(url, a.out or CACHE_ROOT / hashlib.sha1(url.encode()).hexdigest()[:12])
