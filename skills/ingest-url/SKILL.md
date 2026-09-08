---
name: ingest-url
description: "Turns a URL into agent-readable files on disk: transcript, text, and frames. Prefer this over WebFetch for anything WebFetch cannot read: video (YouTube, Instagram reels, X, TikTok), the body of a PDF or arXiv paper, and audio. Video becomes a timestamped transcript with chapters (captions, else on-device speech-to-text), optionally scene-change frames with contact sheets; articles become clean markdown; PDFs become markdown with page markers and figures. Use when the user shares a link to read, watch, summarize, take notes on, or research, or asks what a video, paper, post, or page says. Not for JSON APIs, deploying, or interactive browsing."
license: MIT
compatibility: "macOS or Linux with uv, ffmpeg, and a browser whose cookies yt-dlp can read (Chrome by default). Speech-to-text runs on-device: mlx-whisper on Apple Silicon, faster-whisper elsewhere. Network required."
metadata:
  author: Satyajeet Das
  version: "1.3.0"
  verified: "2026-09-08"
---

# Ingest URL

One command per source type. The script writes a directory, prints a one-line summary ending in
what to read next, and on any failure exits nonzero with the reason. It never writes empty text.
Paths below are relative to this skill's base directory.

```bash
S=<base directory>/scripts/ingest.py
$S video   <url> [<url> ...]          # text.md + manifest.json per URL, ~15s each. Captions, else speech-to-text
$S video   <url> --frames             # also source.mp4, frames/SSSSS.jpg (name = seconds), sheets/NN.jpg
$S article <url> [<url> ...]          # text.md with title/date front-matter
$S pdf     <url-or-path> [<url> ...]  # arXiv: LaTeX source with "--- file ---" markers; else "--- page N ---" + images/
```

Output goes to `~/.cache/ingest-url/<hash of url>/` and a URL already ingested returns instantly
as `cached`. Pass `--out <dir>` only when the user wants the files somewhere specific. The summary
line names the directory and, for video, lists the chapters. `text.md` starts with front-matter
(title, source, author, published) that Obsidian and graph tools read as is.

## Reading what you ingested

1. Map first. Video: the chapter list is already in the summary line. Paper: `grep -n '^--- \|^#\|\\\\section' text.md`.
2. Targeted question: `grep -n -i <term> text.md`, then read a window around each hit (two minutes
   of `[mm:ss]` lines, or one section). Answer with quoted lines and their timestamps or pages.
   Do not read the whole file for a fact; focused context is more reliable than full context.
3. Whole-source summary: read `text.md` end to end, quote the lines you will rely on first, then
   synthesize. For "every number" or "all recommendations", summarize per chapter or section and merge.
4. Pull a frame only when the transcript points at something visual ("as you can see", "this
   chart", a figure that is not spoken). One `ffmpeg -ss` frame beats a sheet survey.
5. Every number you cite must be grep-able in `text.md` or visible in a named frame. If it is not
   there, say so; do not fill the gap from memory.
6. As a subagent, return quotes with timestamps plus the directory path, not the transcript.

## Pick the cheapest path that answers the question

- Title, chapters, duration only: `yt-dlp --dump-json <url>`. No ingest needed.
- What was said: `video` (default). Read `text.md`: `CHAPTER mm:ss title` lines, then one
  `[mm:ss]` line per minute. Cite these timestamps.
- One slide or moment at a known time: download once, then grab that frame directly.
  `ffmpeg -ss 33:15 -i source.mp4 -frames:v 1 -vf scale=960:-1 slide.jpg`
- Survey every slide or demo in a talk: `--frames`. Read `sheets/NN.jpg` first (48 frames
  per sheet, each labelled mm:ss, about 10 sheets per hour), then open only the
  `frames/SSSSS.jpg` you need. Whole-video frames are the expensive path; take it last.

## When the script says no

- `no text extracted` or `fetch failed` on an article: paywall or client-rendered page. Open it
  with a browser tool that carries the user's login. Same for X posts and Instagram captions.
- yt-dlp 403/429: the script already uses browser cookies and Chrome impersonation. If it still
  fails, the site changed: run `uv tool upgrade yt-dlp`, retry once, then report.
- `no speech-to-text backend importable`: run the script through `uv run` so its inline
  dependencies install (mlx-whisper on Apple Silicon, faster-whisper elsewhere).
- Do not "succeed" by summarizing from the title, description, or memory. Report the failure.

Design choices and measurements: `EVAL.md`. Self-check: `python3 scripts/test_ingest.py`.
