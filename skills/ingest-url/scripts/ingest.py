#!/usr/bin/env -S uv run --quiet --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "yt-dlp[default,curl-cffi]", "pillow", "trafilatura", "pymupdf4llm",
#   "mlx-whisper; sys_platform == 'darwin' and platform_machine == 'arm64'",
#   "faster-whisper; sys_platform != 'darwin' or platform_machine != 'arm64'",
# ]
# ///
"""Turn one URL into agent-readable files in OUT. Fails loudly: nonzero exit with a reason, never an empty text.md.
OUT defaults to a content-addressed directory under the cache root, so a URL ingested once is free the next time.

video    text.md ([mm:ss] transcript lines, CHAPTER lines) + manifest.json. Captions in the video's own language
         (English as fallback), else on-device speech-to-text in any language.
         --frames adds source.mp4, frames/<seconds>.jpg and sheets/NN.jpg contact sheets.
article  text.md with title/date front-matter.
pdf      text.md with "--- page N ---" markers + images/. Accepts a URL or a local path.
"""
import argparse, hashlib, json, os, re, subprocess, sys, urllib.request
from pathlib import Path

CACHE_ROOT = Path(os.environ.get("INGEST_CACHE", Path.home() / ".cache/ingest-url"))
COOKIE_BROWSER = os.environ.get("INGEST_BROWSER", "chrome")   # browser whose cookies yt-dlp uses
STT_BACKEND = os.environ.get("INGEST_STT", "auto")              # auto | mlx-whisper | faster-whisper
STT_MODEL = "large-v3-turbo"
SCENE_THRESHOLD = 0.35
FRAME_FLOOR_SECONDS = 10    # at least one frame every N seconds, so reels and slow fades still get sampled
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
    """True when OUT already holds a finished ingest of this URL with at least what was asked for."""
    manifest = out / "manifest.json"
    if not manifest.exists():
        return False
    m = json.loads(manifest.read_text())
    if m.get("source_url") != url or (frames and not m.get("frames_requested")):
        return False
    print(f"{out}: cached. Next: read {out / 'text.md'}" + (f", then {out / 'sheets'}" if m.get("frames_requested") else ""))
    return True


def mmss(seconds):
    return f"{int(seconds) // 60:02d}:{int(seconds) % 60:02d}"


# ---------- video ----------

def ytdlp(args):
    base = [sys.executable, "-m", "yt_dlp", "--cookies-from-browser", COOKIE_BROWSER, "--impersonate", "chrome", "--no-progress"]
    proc = subprocess.run(base + args, capture_output=True, text=True)
    if proc.returncode:
        die(proc.stderr.strip().splitlines()[-1])


def vtt_segments(path):
    """(seconds, text) per caption cue, with the rolling duplicates YouTube emits removed."""
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
    """mlx-whisper on Apple Silicon, faster-whisper (CPU or CUDA) everywhere else; INGEST_STT overrides."""
    for name in ([STT_BACKEND] if STT_BACKEND != "auto" else ["mlx-whisper", "faster-whisper"]):
        try:
            return name, __import__(name.replace("-", "_"))
        except ImportError:
            continue
    die("no captions and no speech-to-text backend importable; install mlx-whisper (Apple Silicon) or faster-whisper")


def asr_segments(media):
    """(seconds, text, language) from on-device speech-to-text, for platforms without captions."""
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


def transcript_markdown(info, segments):
    buckets = {}
    for t, text in segments:
        buckets.setdefault(t // BUCKET_SECONDS * BUCKET_SECONDS, []).append(text)
    lines = [f"# {info['title']}", ""]
    lines += [f"CHAPTER {mmss(c['start_time'])} {c['title']}" for c in info.get("chapters") or []]
    lines.append("")
    lines += [f"[{mmss(t)}] {' '.join(texts)}" for t, texts in sorted(buckets.items())]
    return "\n".join(lines) + "\n"


def scene_frames(media, frames_dir):
    """Scene-change frames named by their timestamp in seconds. showinfo's pts_time is the reliable clock;
    -frame_pts numbers frames in the stream timebase, not seconds."""
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
    """Best caption track: the video's own language first, English second, uploader tracks before auto-generated."""
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


# ---------- article ----------

def article(url, out):
    import trafilatura
    if cached(out, url):
        return
    out.mkdir(parents=True, exist_ok=True)
    html = trafilatura.fetch_url(url)
    if not html:
        die(f"fetch failed for {url}")
    text = trafilatura.extract(html, url=url, output_format="markdown", with_metadata=True, include_links=False) or ""
    write_nonempty(out / "text.md", text)
    (out / "manifest.json").write_text(json.dumps({"source_url": url, "lines": len(text.splitlines())}, indent=1))
    print(f"{out}: {len(text.splitlines())} lines. Next: read {out / 'text.md'}")


# ---------- pdf ----------

def pdf(source, out):
    import pymupdf4llm
    if cached(out, source):
        return
    out.mkdir(parents=True, exist_ok=True)
    path = Path(source)
    if source.startswith("http"):
        path = out / "source.pdf"
        urllib.request.urlretrieve(source, path)
    pages = pymupdf4llm.to_markdown(str(path), page_chunks=True, write_images=True, image_path=str(out / "images"))
    text = "".join(f"\n\n--- page {n} ---\n\n{p['text']}" for n, p in enumerate(pages, 1))
    write_nonempty(out / "text.md", text)
    images = len(list((out / "images").glob("*")))
    (out / "manifest.json").write_text(json.dumps({"source_url": source, "pages": len(pages), "images": images}, indent=1))
    print(f"{out}: {len(pages)} pages, {images} images. Next: read {out / 'text.md'}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("kind", choices=("video", "article", "pdf"))
    parser.add_argument("url")
    parser.add_argument("out", type=Path, nargs="?", help="default: <cache root>/<12-char hash of url>")
    parser.add_argument("--frames", action="store_true", help="video only: extract scene frames and contact sheets")
    a = parser.parse_args()
    if a.out is None:
        a.out = CACHE_ROOT / hashlib.sha1(a.url.encode()).hexdigest()[:12]
    if a.frames and a.kind != "video":
        parser.error("--frames applies to video only")
    {"video": lambda: video(a.url, a.out, frames=a.frames), "article": lambda: article(a.url, a.out), "pdf": lambda: pdf(a.url, a.out)}[a.kind]()
