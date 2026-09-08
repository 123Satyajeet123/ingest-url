# ingest-url

Your agent cannot watch a video. Paste a YouTube link into Claude Code and WebFetch returns the page footer. Paste a reel and it gets the caption. Paste an arXiv PDF and it answers from memory. So people transcribe elsewhere and paste the text back in.

ingest-url is an [Agent Skill](https://agentskills.io) that gives the agent the talk itself: a timestamped transcript with chapters, frames when the speaker points at a slide, the paper's LaTeX, the thread's comments. One command, cached, and it fails out loud instead of returning nothing.

![demo](docs/demo.gif)

| input | output |
|---|---|
| YouTube, Instagram reel, X post, any yt-dlp site | `text.md`: chapters, one `[mm:ss]` line per minute. Captions in the video's language, else on-device speech-to-text. `--frames` adds scene frames named by second and contact sheets |
| article, blog post | markdown, no navigation boilerplate |
| Hacker News item | the comment tree |
| arXiv | the LaTeX source |
| other PDF | markdown with `--- page N ---` markers and extracted figures |
| a topic | `find`: URLs from YouTube, arXiv, Semantic Scholar or OpenAlex, Hacker News, GitHub, one block per platform |

Every path exits nonzero with a reason instead of writing an empty file. Output is cached under `~/.cache/ingest-url/<hash>/`; a repeat URL costs 40 ms. `text.md` opens with Obsidian Web Clipper front-matter.

## Install

```bash
claude plugin marketplace add 123Satyajeet123/ingest-url && /plugin install ingest-url   # Claude Code
npx skills add 123Satyajeet123/ingest-url                                               # any Agent Skills client
pi install git:github.com/123Satyajeet123/ingest-url                                    # pi
```

Needs `uv`, `ffmpeg`, and a browser whose cookies yt-dlp can read (`INGEST_BROWSER`, default chrome). Python dependencies, including the speech-to-text backend for the machine (mlx-whisper on Apple Silicon, faster-whisper elsewhere), install on first run.

## Use

```bash
ingest.py video   <url> [<url> ...] [--frames]
ingest.py article <url> [<url> ...]
ingest.py pdf     <url-or-path> [<url> ...]
ingest.py find    <topic words> [--sources youtube,arxiv,papers,hn,github] [--limit 8]
```

SKILL.md tells the agent which path is cheapest for which question and how to read the result: map first, grep a window for a fact, whole read for a summary, one frame when the speaker points at a slide.

## Measured

With and without the skill, two runs per cell, Claude Code, WebFetch allowed in both arms:

| task | with | without |
|---|---|---|
| figure spoken inside a 77-min talk | 1.00 | 0.50 |
| reel with no captions | 1.00 | 0.75 |
| chapters of a talk | 1.00 | 1.00 |
| number only in a PDF body | 1.00 | 1.00 |

Same trigger behaviour on pi with a local 27B Qwen, where the unaided model ran out of context on the chapter question. Details, vendor comparisons, and the eval runner are in `skills/ingest-url/EVAL.md` and `evals/`.

```bash
python3 skills/ingest-url/scripts/test_ingest.py   # every path, both directions, live URLs, about a minute
```

MIT
