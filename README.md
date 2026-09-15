# ingest-url

Your agent cannot watch a video. Paste a YouTube link into Claude Code and WebFetch returns the page footer. Paste a reel and it gets the caption. Paste an arXiv PDF and it answers from memory.

ingest-url is an [Agent Skill](https://agentskills.io) that gives the agent the content itself: a timestamped transcript with chapters, frames when a slide matters, the paper's LaTeX, the thread's comments. Cached, keyless, and it fails out loud instead of returning nothing.

<img width="1280" height="831" alt="PHOTO-2026-09-09-15-58-36" src="https://github.com/user-attachments/assets/e8a3d3a0-4854-4523-b536-4fc80e16f4e3" />

## Install

```bash
npx skills add 123Satyajeet123/ingest-url                                               # any Agent Skills client
claude plugin marketplace add 123Satyajeet123/ingest-url && /plugin install ingest-url   # Claude Code
pi install git:github.com/123Satyajeet123/ingest-url                                    # pi
```

Needs `uv` and `ffmpeg`. Nothing else to run: the first use installs its own environment in about
ten seconds, and the Claude Code plugin pre-warms it in the background when a session starts.

## What the agent does with it

You paste a link or ask a question; the agent picks the path.

| you say | the agent gets |
|---|---|
| a YouTube, reel, X, or TikTok link | `text.md`: chapters, one `[mm:ss]` line per minute; captions in the video's language, else on-device speech-to-text |
| "what is on the slide at 33:15" | scene frames named by second, plus contact sheets, only when asked |
| an article or blog post | clean markdown |
| a Hacker News item | the comment tree |
| an arXiv link | the LaTeX source with title and author |
| any other PDF | page-marked markdown with figures |
| "find talks and papers on X" | candidate URLs from YouTube, arXiv, Semantic Scholar, Hacker News, GitHub |
| nothing, on a schedule | `pull`: what you saved on your phone, in YouTube, or kept open in the browser, ingested and filed |

Output lives in `~/.cache/ingest-url/<hash>/`; a repeat URL costs 40 ms. `text.md` opens with
Obsidian-style front-matter so vaults and graph tools index it unchanged.

## What happens the first time, and when

- **First use** installs the core environment, about 10 s and 115 MB. Automatic.
- **First video without captions** installs the speech-to-text environment, about 2 GB, on that
  call only. `skills/ingest-url/scripts/setup --stt` does it ahead of time if you prefer.
- **First PDF that is not arXiv** installs the PDF environment, about 190 MB, on that call only.
- **Browser cookies** are never read unless a site blocks the plain request; then yt-dlp reads
  them from Chrome, which on macOS asks the Keychain once. `INGEST_BROWSER` picks the browser.
- **Permission prompts** in Claude Code stop if you allow the script once:

```json
{ "permissions": { "allow": ["Bash(/path/to/skills/ingest-url/scripts/ingest.py:*)", "Read", "Grep"] } }
```

## Commands, for when you call it yourself

```bash
ingest.py video   <url> [<url> ...] [--frames]
ingest.py article <url> [<url> ...]
ingest.py pdf     <url-or-path> [<url> ...]
ingest.py find    <topic words> [--sources youtube,arxiv,papers,hn,github] [--limit 8]
ingest.py pull    [--sources inbox,browser,ytwatchlater,ytliked] [--limit 10]
```

## Measured

With and without the skill on the same prompts, two runs per cell, Claude Code, WebFetch allowed in both arms:

| task | with | without |
|---|---|---|
| figure spoken inside a 77-min talk | 1.00 | 0.50 |
| reel with no captions | 1.00 | 0.75 |
| chapters of a talk | 1.00 | 1.00 |
| number only in a PDF body | 1.00 | 1.00 |

It wins where the answer is in audio or on a slide and breaks even where the model already knows
a shortcut. Same trigger behaviour on pi with a local 27B model, which without the skill ran out
of context on the chapter question. Numbers, vendor comparisons, and the eval runner:
`skills/ingest-url/EVAL.md` and `evals/`.

```bash
python3 skills/ingest-url/scripts/test_ingest.py   # every path, both directions, live URLs, about a minute
```

MIT
