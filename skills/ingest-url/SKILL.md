---
name: ingest-url
description: "Turns a URL into agent-readable files on disk: transcript, text, and frames. Prefer this over WebFetch for anything WebFetch cannot read: video (YouTube, Instagram reels, X, TikTok), the body of a PDF or arXiv paper, and audio. Video becomes a timestamped transcript with chapters (captions, else on-device speech-to-text), optionally scene-change frames with contact sheets; articles become clean markdown; PDFs become markdown with page markers and figures. Use when the user shares a link to read, watch, summarize, take notes on, or research, or asks what a video, paper, post, or page says. Not for JSON APIs, deploying, or interactive browsing."
license: MIT
compatibility: "macOS (Apple Silicon for speech-to-text) or Linux with uv, ffmpeg, and a browser whose cookies yt-dlp can read (Chrome by default). Network required."
metadata:
  author: Satyajeet Das
  version: "1.1.0"
  verified: "2026-09-08"
---

# Ingest URL

One command per source type. The script writes a directory, prints a one-line summary, and on
any failure exits nonzero with the reason. It never writes empty text. Paths below are relative
to this skill's base directory.

```bash
S=<base directory>/scripts/ingest.py
$S video   <url> <out>            # text.md + manifest.json, ~15s. Captions, else speech-to-text
$S video   <url> <out> --frames   # also source.mp4, frames/SSSSS.jpg (name = seconds), sheets/NN.jpg
$S article <url> <out>            # text.md with title/date front-matter
$S pdf     <url-or-path> <out>    # text.md with "--- page N ---" markers, images/
```

Use a temporary directory for `<out>` unless the user wants the files kept. One directory per URL.

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
- `mlx-whisper unavailable`: not Apple Silicon. Use whisper.cpp or faster-whisper on the audio.
- Do not "succeed" by summarizing from the title, description, or memory. Report the failure.

Design choices and measurements: `EVAL.md`. Self-check: `python3 scripts/test_ingest.py`.
