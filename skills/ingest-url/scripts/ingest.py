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
pull     ingest what the user saved: ~/notes/inbox.txt (one URL per line, consumed once), YouTube Watch
         Later, YouTube Liked. Already-cached URLs are skipped; each item prints its own summary line.
"""

import argparse
import functools
import hashlib
import importlib
import json
import os
import re
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

CACHE_ROOT = Path(os.environ.get("INGEST_CACHE", Path.home() / ".cache/ingest-url"))
COOKIE_BROWSER = os.environ.get("INGEST_BROWSER", "chrome")
STT_BACKEND = os.environ.get("INGEST_STT", "auto")  # auto | mlx-whisper | faster-whisper
STT_MODEL = "large-v3-turbo"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/128 Safari/537.36"
OUTPUT_TEMPLATE = "source.%(ext)s"
SCENE_THRESHOLD = 0.35
MIN_SECONDS_BETWEEN_FRAMES = 10
BUCKET_SECONDS = 60
COLS, ROWS, TILE_W, TILE_H = 6, 8, 320, 180
INBOX = Path(os.environ.get("INGEST_INBOX", Path.home() / "notes/inbox.txt"))
VIDEO_HOSTS = ("youtube.com", "youtu.be", "instagram.com", "x.com", "twitter.com", "tiktok.com", "vimeo.com")
YOUTUBE_ID = re.compile(r"(?:youtu\.be/|[?&]v=)([\w-]{11})")
HN_ITEM = re.compile(r"news\.ycombinator\.com/item\?id=(\d+)")
ARXIV_ID = re.compile(r"arxiv\.org/(?:abs|pdf|src|html)/(\d{4}\.\d{4,5}(?:v\d+)?)")


class IngestError(Exception):
    """Reported to the user as one line and a nonzero exit."""


def run_or_fail(cmd):
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode:
        stderr_lines = proc.stderr.strip().splitlines()
        raise IngestError(stderr_lines[-1] if stderr_lines else f"{cmd[0]} exited {proc.returncode}")
    return proc


def fetch(url, timeout=20):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def get_json(url):
    return json.loads(fetch(url))


def xml_text(xml, tag):
    """Body of the first <tag>, whitespace collapsed; None when absent."""
    match = re.search(rf"<{tag}>\s*(.*?)\s*</{tag}>", xml, re.S)
    return " ".join(match[1].split()) if match else None


def write_nonempty(path, text):
    if not text.strip():
        raise IngestError(
            f"no text extracted for {path.parent.name}; paywalled or client-rendered? "
            "Open it in a browser session that carries your login"
        )
    path.write_text(text)


def cache_dir(url):
    return CACHE_ROOT / hashlib.sha1(url.encode()).hexdigest()[:12]


def next_step(out, with_sheets=False):
    return f"Next: read {out / 'text.md'}" + (f", then {out / 'sheets'}" if with_sheets else "")


def served_from_cache(out, url, frames=False):
    manifest_path = out / "manifest.json"
    if not manifest_path.exists():
        return False
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("source_url") != url or (frames and not manifest.get("frames_requested")):
        return False
    print(f"{out}: cached. {next_step(out, manifest.get('frames_requested'))}")
    return True


def mmss(seconds):
    return f"{int(seconds) // 60:02d}:{int(seconds) % 60:02d}"


def front_matter(title, source, author=None, published=None):
    """Field names follow Obsidian Web Clipper so vaults index the file unchanged."""
    fields = {"title": title, "source": source, "author": author, "published": published, "created": time.strftime("%Y-%m-%d")}
    return "---\n" + "".join(f"{k}: {json.dumps(v, ensure_ascii=False)}\n" for k, v in fields.items() if v) + "---\n\n"


# ---------- video ----------


def ytdlp(args):
    base = [sys.executable, "-m", "yt_dlp", "--cookies-from-browser", COOKIE_BROWSER, "--impersonate", "chrome", "--no-progress"]
    return run_or_fail(base + args).stdout


def fetch_video_info(url, out):
    ytdlp(["--write-info-json", "--skip-download", "-o", str(out / OUTPUT_TEMPLATE), url])
    return json.loads((out / "source.info.json").read_text())


def base_language(language):
    return (language or "en").split("-")[0]


def fetch_captions(url, out, lang):
    sub_langs = f"{lang},{lang}-orig,en,en-orig"
    ytdlp(
        ["--write-auto-subs", "--write-subs", "--sub-langs", sub_langs, "--skip-download", "-o", str(out / OUTPUT_TEMPLATE), url]
    )
    return caption_file(out, lang)


def caption_file(out, lang):
    """Video's own language before English, uploader tracks before auto-generated."""
    files = list(out.glob("source.*.vtt"))
    if not files:
        return None

    def preference(path):
        return (not path.name.startswith(f"source.{lang}"), "orig" in path.name, path.name)

    return min(files, key=preference)


def caption_language(path):
    return base_language(path.name.split(".")[1])


def vtt_segments(path):
    """YouTube repeats each cue two or three times as it scrolls; keep the first."""
    segments, last, t = [], "", 0
    for line in path.read_text(encoding="utf8").split("\n"):
        cue = re.match(r"(?:(\d\d):)?(\d\d):(\d\d)\.\d+ --> ", line)
        if cue:
            t = int(cue[1] or 0) * 3600 + int(cue[2]) * 60 + int(cue[3])
            continue
        if not line.strip() or line.startswith(("WEBVTT", "Kind:", "Language:")):
            continue
        text = re.sub(r"<[^>]+>", "", line).replace("&nbsp;", " ").strip()
        if text and text != last:
            segments.append((t, text))
            last = text
    return segments


def download_media(url, out, with_video):
    fmt = "bv*[height<=720]+ba/b[height<=720]" if with_video else "ba/b"
    reported = ytdlp(
        ["-f", fmt, "--merge-output-format", "mp4", "-o", str(out / OUTPUT_TEMPLATE), "--print", "after_move:filepath", url]
    )
    media = Path(reported.strip().splitlines()[-1]) if reported.strip() else None
    if not media or not media.is_file():
        raise IngestError(f"download reported success but no media file appeared in {out}")
    return media


def stt_backend():
    names = [STT_BACKEND] if STT_BACKEND != "auto" else ["mlx-whisper", "faster-whisper"]
    for name in names:
        try:
            return name, importlib.import_module(name.replace("-", "_"))
        except ImportError:
            continue
    raise IngestError(
        "no captions and no speech-to-text backend importable; install mlx-whisper (Apple Silicon) or faster-whisper"
    )


def asr_segments(media):
    name, module = stt_backend()
    wav = media.with_suffix(".wav")
    run_or_fail(["ffmpeg", "-y", "-loglevel", "error", "-i", str(media), "-vn", "-ac", "1", "-ar", "16000", str(wav)])
    if name == "mlx-whisper":
        result = module.transcribe(str(wav), path_or_hf_repo=f"mlx-community/whisper-{STT_MODEL}")
        segments, language = [(int(s["start"]), s["text"]) for s in result["segments"]], result.get("language")
    else:
        model = module.WhisperModel(STT_MODEL, device="auto", compute_type="auto")
        raw, info = model.transcribe(str(wav))
        segments, language = [(int(s.start), s.text) for s in raw], info.language
    wav.unlink()
    return [(t, text.strip()) for t, text in segments if text.strip()], language


def transcript_markdown(info, segments):
    buckets = {}
    for t, text in segments:
        buckets.setdefault(t // BUCKET_SECONDS * BUCKET_SECONDS, []).append(text)
    date = info.get("upload_date")
    published = f"{date[:4]}-{date[4:6]}-{date[6:]}" if date else None
    lines = [front_matter(info["title"], info.get("webpage_url"), info.get("channel"), published) + f"# {info['title']}", ""]
    lines += [f"CHAPTER {mmss(c['start_time'])} {c['title']}" for c in info.get("chapters") or []]
    lines.append("")
    lines += [f"[{mmss(t)}] {' '.join(texts)}" for t, texts in sorted(buckets.items())]
    return "\n".join(lines) + "\n"


def scene_frames(media, frames_dir):
    """frames/<seconds>.jpg; showinfo's pts_time is the clock."""
    frames_dir.mkdir(exist_ok=True)
    select = f"gt(scene,{SCENE_THRESHOLD})+isnan(prev_selected_t)+gte(t-prev_selected_t,{MIN_SECONDS_BETWEEN_FRAMES})"
    filters = f"select='{select}',showinfo,scale=960:-1"
    proc = run_or_fail(
        ["ffmpeg", "-hide_banner", "-i", str(media), "-vf", filters, "-fps_mode", "vfr", str(frames_dir / "raw%05d.jpg")]
    )
    seconds = [float(m) for m in re.findall(r"pts_time:\s*([\d.]+)", proc.stderr)]
    name_frames_by_second(sorted(frames_dir.glob("raw*.jpg")), seconds)
    return sorted(frames_dir.glob("*.jpg"))


def name_frames_by_second(raw_frames, seconds):
    """A second holding several cuts gets b, bb, ... suffixes."""
    if len(seconds) != len(raw_frames):
        raise IngestError(f"showinfo reported {len(seconds)} frames but {len(raw_frames)} were written")
    for raw, t in zip(raw_frames, seconds, strict=True):
        target = raw.with_name(f"{int(t):05d}.jpg")
        while target.exists():
            target = target.with_name(target.stem + "b.jpg")
        raw.rename(target)


def frame_second(frame):
    return int(frame.stem.rstrip("b"))


def contact_sheets(frames, sheets_dir):
    from PIL import Image, ImageDraw

    sheets_dir.mkdir(exist_ok=True)
    per_sheet = COLS * ROWS
    for n, start in enumerate(range(0, len(frames), per_sheet)):
        sheet = Image.new("RGB", (COLS * TILE_W, ROWS * TILE_H), "black")
        draw = ImageDraw.Draw(sheet)
        for j, frame in enumerate(frames[start : start + per_sheet]):
            x, y = (j % COLS) * TILE_W, (j // COLS) * TILE_H
            sheet.paste(Image.open(frame).resize((TILE_W, TILE_H)), (x, y))
            draw.rectangle([x, y, x + 70, y + 16], fill="black")
            draw.text((x + 3, y + 2), mmss(frame_second(frame)), fill="yellow")
        sheet.save(sheets_dir / f"{n:02d}.jpg", quality=80)
    return sorted(sheets_dir.glob("*.jpg"))


def print_video_summary(out, manifest, frames):
    chapters = manifest["chapters"] or []
    print(
        f"{out}: {manifest['duration']}s, {manifest['words']} words from {manifest['transcript']} ({manifest['language']}), "
        f"{len(chapters)} chapters, {manifest.get('frames', 0)} frames, {manifest.get('sheets', 0)} sheets. "
        + next_step(out, frames)
    )
    for c in chapters:
        print(f"  {mmss(c['start_time'])} {c['title']}")


def video(url, out, frames=False):
    if served_from_cache(out, url, frames):
        return
    out.mkdir(parents=True, exist_ok=True)
    info = fetch_video_info(url, out)
    captions = fetch_captions(url, out, base_language(info.get("language")))
    media = download_media(url, out, with_video=frames) if frames or not captions else None
    if captions:
        segments, language = vtt_segments(captions), caption_language(captions)
    else:
        segments, language = asr_segments(media)
    write_nonempty(out / "text.md", transcript_markdown(info, segments))
    manifest = {k: info.get(k) for k in ("id", "title", "channel", "upload_date", "duration", "chapters", "webpage_url")}
    manifest |= {"source_url": url, "frames_requested": frames, "transcript": "captions" if captions else "speech-to-text"}
    manifest |= {"language": language, "words": sum(len(text.split()) for _, text in segments)}
    if frames:
        frame_files = scene_frames(media, out / "frames")
        manifest |= {"frames": len(frame_files), "sheets": len(contact_sheets(frame_files, out / "sheets"))}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1))
    print_video_summary(out, manifest, frames)


# ---------- article ----------


def strip_html(html):
    return re.sub(r"<[^>]+>", "", (html or "").replace("<p>", "\n\n")).strip()


def hn_comment_lines(node, depth=0):
    lines = []
    for child in node.get("children") or []:
        if child.get("text"):
            lines.append(f"{'  ' * depth}- **{child.get('author')}**: {strip_html(child['text'])}")
        lines += hn_comment_lines(child, depth + 1)
    return lines


def hn_thread(item_id):
    """(markdown, comment count)."""
    data = get_json(f"https://hn.algolia.com/api/v1/items/{item_id}")
    title = data.get("title") or ""
    head = front_matter(
        title or f"HN {item_id}",
        f"https://news.ycombinator.com/item?id={item_id}",
        data.get("author"),
        (data.get("created_at") or "")[:10],
    )
    comments = hn_comment_lines(data)
    lines = [head + f"# {title}", "", data.get("url") or "", "", strip_html(data.get("text")), "", *comments]
    return "\n".join(lines) + "\n", len(comments)


def hn_article(item_id, url, out):
    text, comments = hn_thread(item_id)
    write_nonempty(out / "text.md", text)
    (out / "manifest.json").write_text(json.dumps({"source_url": url, "comments": comments}, indent=1))
    print(f"{out}: HN thread, {comments} comments. {next_step(out)}")


def web_article(url, out):
    import trafilatura

    html = trafilatura.fetch_url(url)
    if not html:
        raise IngestError(f"fetch failed for {url}")
    text = trafilatura.extract(html, url=url, output_format="markdown", with_metadata=True, include_links=False) or ""
    write_nonempty(out / "text.md", text)
    (out / "manifest.json").write_text(json.dumps({"source_url": url, "lines": len(text.splitlines())}, indent=1))
    print(f"{out}: {len(text.splitlines())} lines. {next_step(out)}")


def article(url, out):
    if served_from_cache(out, url):
        return
    out.mkdir(parents=True, exist_ok=True)
    hn = HN_ITEM.search(url)
    if hn:
        hn_article(hn[1], url, out)
    else:
        web_article(url, out)


# ---------- pdf ----------


def arxiv_id(url):
    match = ARXIV_ID.search(url)
    return match[1] if match else None


def arxiv_metadata(aid):
    try:
        entry = fetch(f"https://export.arxiv.org/api/query?id_list={aid}").decode().split("<entry>", 1)[1]
        return {
            "title": xml_text(entry, "title"),
            "author": xml_text(entry, "name"),
            "published": xml_text(entry, "published")[:10],
        }
    except (urllib.error.URLError, IndexError, TypeError, OSError):
        return {}


def is_tex_member(member):
    """Regular .tex files at plain relative paths; links, directories and escaping names are dropped."""
    name = Path(member.name)
    return member.isfile() and name.suffix == ".tex" and not name.is_absolute() and ".." not in name.parts


def latex_files(tgz):
    with tarfile.open(tgz) as tar:
        return {m.name: tar.extractfile(m).read().decode("utf8", "replace") for m in tar.getmembers() if is_tex_member(m)}


def latex_markdown(tex):
    main = next((name for name, text in tex.items() if "\\begin{document}" in text), sorted(tex)[0])
    ordered = [main, *sorted(name for name in tex if name != main)]
    return "".join(f"\n\n--- file {name} ---\n\n{tex[name]}" for name in ordered)


def arxiv_latex(aid, out):
    """None for PDF-only submissions."""
    tgz = out / "source.tar.gz"
    try:
        tgz.write_bytes(fetch(f"https://arxiv.org/src/{aid}", timeout=60))
        tex = latex_files(tgz)
    except (urllib.error.URLError, tarfile.TarError, OSError):
        return None
    return latex_markdown(tex) if tex else None


def write_arxiv_latex(aid, source, tex, out):
    meta = arxiv_metadata(aid)
    head = front_matter(meta.get("title") or aid, f"https://arxiv.org/abs/{aid}", meta.get("author"), meta.get("published"))
    write_nonempty(out / "text.md", head + tex)
    files = tex.count("--- file ")
    (out / "manifest.json").write_text(json.dumps({"source_url": source, "format": "latex", "files": files}, indent=1))
    print(f"{out}: arXiv LaTeX source, {files} files. {next_step(out)}; grep '^\\\\section' for the map")


def local_or_downloaded_pdf(source, aid, out):
    if not source.startswith(("http://", "https://")):
        path = Path(source)
        if not path.is_file():
            raise IngestError(f"no such file: {source}")
        return path
    path = out / "source.pdf"
    try:
        path.write_bytes(fetch(f"https://arxiv.org/pdf/{aid}" if aid else source, timeout=60))
    except (urllib.error.URLError, OSError) as error:
        raise IngestError(f"download failed for {source}: {error}") from error
    return path


def write_pdf_pages(source, aid, out):
    import pymupdf4llm

    path = local_or_downloaded_pdf(source, aid, out)
    pages = pymupdf4llm.to_markdown(str(path), page_chunks=True, write_images=True, image_path=str(out / "images"))
    text = "".join(f"\n\n--- page {n} ---\n\n{p['text']}" for n, p in enumerate(pages, 1))
    write_nonempty(out / "text.md", front_matter(path.stem, source) + text)
    images = len(list((out / "images").glob("*")))
    manifest = {"source_url": source, "format": "pdf", "pages": len(pages), "images": images}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1))
    print(f"{out}: {len(pages)} pages, {images} images. {next_step(out)}; grep '^--- page\\|^#' for the map")


def pdf(source, out):
    if served_from_cache(out, source):
        return
    out.mkdir(parents=True, exist_ok=True)
    aid = arxiv_id(source)
    tex = arxiv_latex(aid, out) if aid else None
    if tex:
        write_arxiv_latex(aid, source, tex, out)
    else:
        write_pdf_pages(source, aid, out)


# ---------- find ----------


def find_youtube(query, limit):
    template = "%(upload_date,release_date|)s\t%(view_count|0)s views\t%(title)s\t%(url)s"
    proc = run_or_fail(
        [sys.executable, "-m", "yt_dlp", f"ytsearch{limit}:{query}", "--flat-playlist", "--no-warnings", "--print", template]
    )
    return [line.split("\t") for line in proc.stdout.splitlines() if line.count("\t") == 3]


def find_arxiv(query, limit):
    q = urllib.parse.quote(f"all:{query}")
    xml = fetch(f"https://export.arxiv.org/api/query?search_query={q}&sortBy=relevance&max_results={limit}").decode()
    return [
        [xml_text(entry, "published")[:10], "", xml_text(entry, "title"), xml_text(entry, "id").replace("/abs/", "/pdf/")]
        for entry in xml.split("<entry>")[1:]
    ]


def paper_url(paper):
    if (paper.get("externalIds") or {}).get("ArXiv"):
        return f"https://arxiv.org/pdf/{paper['externalIds']['ArXiv']}"
    return (paper.get("openAccessPdf") or {}).get("url") or f"https://www.semanticscholar.org/paper/{paper['paperId']}"


def find_papers(query, limit):
    """Semantic Scholar throttles anonymous callers; OpenAlex is the fallback."""
    try:
        params = {"query": query, "limit": limit, "fields": "title,year,citationCount,externalIds,openAccessPdf"}
        data = get_json("https://api.semanticscholar.org/graph/v1/paper/search?" + urllib.parse.urlencode(params))
        return [
            [str(p.get("year") or ""), f"{p.get('citationCount', 0)} citations", p["title"], paper_url(p)]
            for p in data.get("data", [])
        ]
    except (urllib.error.URLError, OSError, KeyError):
        params = {"search": query, "per-page": limit, "select": "title,publication_year,cited_by_count,open_access,doi"}
        data = get_json("https://api.openalex.org/works?" + urllib.parse.urlencode(params))
        return [
            [
                str(w.get("publication_year") or ""),
                f"{w.get('cited_by_count', 0)} citations",
                w["title"],
                (w.get("open_access") or {}).get("oa_url") or w.get("doi") or "",
            ]
            for w in data.get("results", [])
        ]


def find_hn(query, limit):
    data = get_json(
        "https://hn.algolia.com/api/v1/search?" + urllib.parse.urlencode({"query": query, "tags": "story", "hitsPerPage": limit})
    )
    return [
        [
            h["created_at"][:10],
            f"{h.get('points', 0)} points, {h.get('num_comments', 0)} comments",
            h["title"],
            h.get("url") or f"https://news.ycombinator.com/item?id={h['objectID']}",
        ]
        for h in data.get("hits", [])
    ]


def find_github(query, limit):
    data = get_json(
        "https://api.github.com/search/repositories?" + urllib.parse.urlencode({"q": query, "sort": "stars", "per_page": limit})
    )
    return [
        [
            r["pushed_at"][:10],
            f"{r['stargazers_count']} stars",
            r["full_name"] + (f": {r['description']}" if r.get("description") else ""),
            r["html_url"],
        ]
        for r in data.get("items", [])
    ]


FINDERS = {"youtube": find_youtube, "arxiv": find_arxiv, "papers": find_papers, "hn": find_hn, "github": find_github}


def find_one(name, query, limit):
    """(name, rows, error); a platform failure is reported, never fatal."""
    try:
        return name, FINDERS[name](query, limit), None
    except Exception as error:
        return name, [], f"{type(error).__name__}: {str(error)[:120]}"


def find(query, sources, limit):
    with ThreadPoolExecutor(len(sources)) as pool:
        results = pool.map(functools.partial(find_one, query=query, limit=limit), sources)
    for name, rows, error in results:
        print(f"## {name}" + (f" (failed: {error})" if error else f" ({len(rows)})"))
        for date, signal, title, url in rows:
            print(f"{date or '----------':10} | {signal:>24} | {title[:90]} | {url}")
        print()


# ---------- pull ----------


def canonical_url(url):
    """One cache key per thing: YouTube variants collapse to watch?v=ID; tracking params and fragments go."""
    url = url.strip()
    video_id = YOUTUBE_ID.search(url)
    if video_id and any(host in url for host in ("youtube.com", "youtu.be")):
        return f"https://www.youtube.com/watch?v={video_id[1]}"
    parts = urllib.parse.urlsplit(url)
    query = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query) if not k.startswith("utm_")]
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/") or "/", urllib.parse.urlencode(query), ""))


def kind_for(url):
    host = urllib.parse.urlsplit(url).netloc.removeprefix("www.")
    if arxiv_id(url) or url.lower().endswith(".pdf"):
        return "pdf"
    if any(host == h or host.endswith("." + h) for h in VIDEO_HOSTS):
        return "video"
    return "article"


def youtube_playlist(list_id, limit):
    proc = run_or_fail(
        [
            sys.executable,
            "-m",
            "yt_dlp",
            "--cookies-from-browser",
            COOKIE_BROWSER,
            "--flat-playlist",
            "--no-warnings",
            "--playlist-items",
            f"1-{limit}",
            "--print",
            "%(url)s",
            f"https://www.youtube.com/playlist?list={list_id}",
        ]
    )
    return proc.stdout.split()


def inbox_urls(limit):
    if not INBOX.exists():
        return []
    return [line.strip() for line in INBOX.read_text().splitlines() if line.strip()][:limit]


PULLERS = {
    "inbox": inbox_urls,
    "ytwatchlater": functools.partial(youtube_playlist, "WL"),
    "ytliked": functools.partial(youtube_playlist, "LL"),
}


def consume_inbox(handled):
    """Move handled lines from the inbox to inbox.done with a timestamp and outcome; keep the rest."""
    if not INBOX.exists() or not handled:
        return
    remaining = [line for line in INBOX.read_text().splitlines() if line.strip() and line.strip() not in handled]
    stamp = time.strftime("%Y-%m-%d %H:%M")
    with INBOX.with_name("inbox.done").open("a") as done:
        done.writelines(f"{stamp} {outcome} {line}\n" for line, outcome in handled.items())
    INBOX.write_text("".join(line + "\n" for line in remaining))


def pull(sources, limit):
    counts = {"ingested": 0, "failed": 0, "cached": 0}
    inbox_outcomes = {}
    for source in sources:
        for raw in PULLERS[source](limit):
            outcome = pull_one(canonical_url(raw))
            counts[outcome] += 1
            if source == "inbox":
                inbox_outcomes[raw] = outcome
    consume_inbox(inbox_outcomes)
    summary = ", ".join(
        f"{n} {label}" for label, n in (("new", counts["ingested"]), ("failed", counts["failed"]), ("cached", counts["cached"]))
    )
    print(f"pull: {summary}, sources {','.join(sources)}")


def pull_one(url):
    if (cache_dir(url) / "manifest.json").exists():
        return "cached"
    try:
        ingest_one(url)
    except IngestError as error:
        print(f"ingest: {url}: {error}")
        return "failed"
    return "ingested"


def ingest_one(url):
    {"video": video, "article": article, "pdf": pdf}[kind_for(url)](url, cache_dir(url))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("kind", choices=("video", "article", "pdf", "find", "pull"))
    parser.add_argument("urls", nargs="*", metavar="URL|QUERY")
    parser.add_argument("--sources", help=f"find: subset of {','.join(FINDERS)}; pull: subset of {','.join(PULLERS)}")
    parser.add_argument("--limit", type=int, default=8, help="find: results per platform; pull: items per source")
    parser.add_argument(
        "--out", type=Path, help="output directory (single URL only); default: <cache root>/<12-char hash of url>"
    )
    parser.add_argument("--frames", action="store_true", help="video only: extract scene frames and contact sheets")
    args = parser.parse_args()
    if args.kind in ("find", "pull"):
        known = FINDERS if args.kind == "find" else PULLERS
        sources = args.sources.split(",") if args.sources else list(known)
        if unknown := set(sources) - set(known):
            parser.error(f"unknown sources: {', '.join(sorted(unknown))}")
        if args.kind == "find" and not args.urls:
            parser.error("find needs a query")
        try:
            find(" ".join(args.urls), sources, args.limit) if args.kind == "find" else pull(sources, args.limit)
        except IngestError as error:
            sys.exit(f"ingest: {error}")
        return
    if not args.urls:
        parser.error(f"{args.kind} needs at least one URL")
    if args.frames and args.kind != "video":
        parser.error("--frames applies to video only")
    if args.out and len(args.urls) > 1:
        parser.error("--out takes a single URL")
    ingest = {"video": functools.partial(video, frames=args.frames), "article": article, "pdf": pdf}[args.kind]
    try:
        for url in args.urls:
            ingest(url, args.out or cache_dir(url))
    except IngestError as error:
        sys.exit(f"ingest: {error}")


if __name__ == "__main__":
    main()
