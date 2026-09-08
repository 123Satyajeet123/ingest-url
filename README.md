# ingest-url

An [Agent Skill](https://agentskills.io) that turns a video, reel, X post, PDF, or article into
files an agent can grep and cite by timestamp. Captions first, on-device speech-to-text second,
frames last, and a nonzero exit instead of a made-up summary. Ingested URLs are cached on disk,
so the second agent, session, or subagent that needs the same link pays nothing. Ships with
with/without evals that show where it pays for itself and where it does not.
Works in Claude Code, pi, and any agent that implements the Agent Skills spec.

| source | what the agent gets |
|---|---|
| YouTube, Instagram reels, X, TikTok, any site yt-dlp supports | `text.md` with one `[mm:ss]` line per minute and `CHAPTER` lines; captions when the platform has them, on-device speech-to-text when it does not. Optional scene frames named by second, plus contact sheets so a 1-hour talk is 10 images to skim |
| articles, blog posts | clean markdown with title and date, no navigation boilerplate |
| PDFs, arXiv | arXiv: the LaTeX source, so equations and tables survive; other PDFs: markdown with `--- page N ---` markers and extracted figures |

Every path fails loudly: nonzero exit with a reason, never an empty file. An agent that gets
empty text will summarize from memory and call it done. This script does not let it.

## Why

Agents already know yt-dlp and ffmpeg. What they rediscover every time, at a cost of a dozen
tool calls, is the set of things that go wrong:

- YouTube returns 403 without browser cookies and Chrome impersonation.
- ffmpeg's `-frame_pts` names frames in the stream timebase, not seconds; the only reliable clock is `showinfo`.
- Many ffmpeg builds lack `drawtext`, so labelled contact sheets need Pillow.
- YouTube captions repeat every line two or three times as rolling cues.
- Article extractors return an empty string with exit 0 on paywalls.
- Scene detection alone yields one frame for a motion-graphics reel.
- Chapters are in the video metadata; a small model that does not know this will grep a transcript until it runs out of context.

The skill is those fixes, once, plus instructions on which path is cheapest for which question.
The measurements behind each decision, including a with/without-skill comparison on Claude Code
and on pi with a local 27B model, are in [skills/ingest-url/EVAL.md](skills/ingest-url/EVAL.md).

## Install

```bash
# Claude Code
claude plugin marketplace add 123Satyajeet123/ingest-url
/plugin install ingest-url

# Any Agent Skills client (Cursor, Copilot, Gemini CLI, Codex, ...)
npx skills add 123Satyajeet123/ingest-url

# pi
pi install git:github.com/123Satyajeet123/ingest-url
```

Or copy `skills/ingest-url` into your agent's skills directory.

## Requirements

`uv`, `ffmpeg`, and a browser whose cookies yt-dlp can read (Chrome by default; set
`INGEST_BROWSER=firefox` or similar to change). Python dependencies install themselves on first
run from the script's inline metadata, including the right speech-to-text backend for the
machine: mlx-whisper on Apple Silicon, faster-whisper (CPU or CUDA) everywhere else. Both use
Whisper large-v3-turbo and produced identical transcripts in testing. `INGEST_STT` forces one.

## Output layout

Output lands in `~/.cache/ingest-url/<hash of url>/` unless `--out` is given, and a URL already
ingested is served from there in milliseconds. Several URLs can be passed in one call. `INGEST_CACHE`
moves the root. The front-matter uses Obsidian Web Clipper's field names, so a vault or a graph
tool indexes the files with no glue.

```
<out>/
  text.md          front-matter (title, source, author, published), then transcript with CHAPTER and [mm:ss] lines, or article/PDF markdown
  manifest.json    id, title, channel, date, duration, chapters, language, transcript source, word count
  frames/00083.jpg scene frame at 1:23 (with --frames)
  sheets/00.jpg    48 labelled frames per sheet (with --frames)
  images/          figures extracted from a PDF
```

## Self-check

```bash
python3 skills/ingest-url/scripts/test_ingest.py
```

Runs every path in both directions against live URLs: a working link must yield text, a
paywalled or missing one must fail. About a minute.

## Reading, not just fetching

SKILL.md carries a short reading protocol: map first (chapters or section headings), grep and read
a window for a targeted fact, whole read with quotes-first for a summary, one extracted frame when
the speaker points at a slide, and never a number that is not grep-able in the source. Each step
traces to a measured result, either published context-rot work or this skill's own eval.

## Eval

`evals/` holds five cases in the `claude plugin eval` format: a non-link task that must not
trigger the skill, a number that exists only in a PDF body, a reel with no captions, a chapter
list, and a figure spoken inside a 77-minute talk. Each has a skill-fired indicator, a regex on
the ground truth, and a rubric graded by a judge model.

```bash
claude plugin eval .                 # when enabled on your account
evals/run.py --runs 2                # same cases, same with/without ablation, works today
```

Measured result on Claude Code: the skill wins where the answer lives in audio or on a slide
(1.00 with, 0.50 to 0.75 without) and breaks even on tasks the model already knows how to do
from metadata. Numbers and the description A/B are in EVAL.md.

## Limits

Windows is untested. TikTok could not be tested from the author's network (the site is blocked
in India at the TLS level), though yt-dlp supports it. Captions are taken in the video's own
language with English as the fallback; speech-to-text detects the language itself.

## License

MIT
