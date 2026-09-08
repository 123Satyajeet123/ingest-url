# ingest-url: criteria, vendor tests, measured results

Criteria and blind predictions were written before any install; candidates were run against the
same URLs and adopted per criterion. Numbers are from 2026-09-08/09 on a MacBook Pro (Apple Silicon)
unless stated. Test URLs: `scripts/test_ingest.py`. Eval cases and runner: `evals/`.

## Criteria

| # | criterion | result |
|---|---|---|
| C1 | video -> [mm:ss] + CHAPTER lines; unavailable video -> nonzero, no file | pass. 7/7 chapters; bad id exits 1 |
| C2 | frames named by second, verified independently | pass. Re-extracted frame at the named second: pixel diff 0.9 and 2.4 vs 47 and 25 at +30s |
| C3 | 60-min talk in <=12 sheet reads | pass. 77-min talk = 11 sheets |
| C4 | article -> clean markdown | pass. 0 link-only lines of 53 |
| C5 | pdf -> page markers + images | pass. 33 pages, 5 images |
| C6 | client-rendered page | fails as predicted; logged-in browser tool covers it |
| C7 | loud failure, never empty output | pass after fix: trafilatura returns "" with exit 0 on paywalls |
| C8 | fires on link prompts, silent otherwise | pass. 3/3 and 0/3 |
| C9 | cheaper than no skill | mixed, see below |
| STT | media without captions | pass. Whisper large-v3-turbo: 40s for 167s audio on mlx, byte-identical on faster-whisper. On a music-only clip Whisper wrote "Thank you."; the summary line now reports word count |

## Vendors

| candidate | verdict |
|---|---|
| trafilatura | adopted for articles. 1.6s, no nav boilerplate |
| markitdown 0.1.7 | rejected. 21s, keeps nav/footer; PDF output 3.5x longer, no images |
| pymupdf4llm | adopted for PDF |
| yt-dlp via `uv tool` with curl_cffi | adopted; brew build has no impersonation |
| steipete/summarize 0.21 | rejected. Printed nothing, exit 0 |
| claude-real-video | rejected. 2 min for a 3-min clip, tripled caption lines, 13 sheets, writes ~/.crv unasked |
| mlx-whisper / faster-whisper | adopted per platform |
| mvanhorn/last30days-skill (61.6k stars, 59 MB) | rejected for search. 0 YouTube results on two queries where `yt-dlp ytsearch` found the exact talks; adjacent papers, not the target |
| harness browser tool | adopted for JS/login pages |

## Platforms

| platform | result |
|---|---|
| YouTube, 3-min clip | pass, 32 frames |
| YouTube, 77-min talk | pass, 7 chapters, transcript in 16s |
| Instagram reel | pass, 9s, 126 words by speech-to-text, 6 frames; missing reel exits 1 |
| X post | pass, 38s, 381 words from X captions, 13 frames |
| Spanish TEDx talk | pass, picks the uploader's `es` track over auto and over English |
| Hacker News item | pass, 47-comment tree |
| arXiv | pass, LaTeX source, 48 files, 1.4s |
| TikTok | untestable here: TLS handshake fails from plain curl (blocked in India) |

## With vs without the skill, Claude Code (Fable 5.1), 2 runs per cell, WebFetch allowed

| case | with | without |
|---|---|---|
| figure spoken inside a 77-min talk | 1.00, fired 2/2, $0.90 | 0.50, $0.52 (found the spoken 60%, never the 67.1% on the slide) |
| reel with no captions | 1.00, fired 2/2, $0.52 | 0.75, $0.51 (leaned on the caption half the time) |
| chapters of the talk | 1.00, fired 2/2, $0.95 | 1.00, $0.40 |
| number only in a PDF body | 1.00, fired 1/2, $0.51 | 1.00, $0.45 |
| non-link shell task | fired 0/2 | |

The skill wins where the answer is in audio or on a slide and breaks even where the model already
knows the metadata path. The description was revised once: naming WebFetch and what it cannot read
moved the PDF fire rate from 0/2 to 1/2 (n=2). Eval spend including two wasted runs: $17.

Trigger test of the 1.4.0 description: 3/3 find or read prompts fired, 0/3 negatives fired
(including "search the web for the latest news"). "Find talks and papers on X" cost $1.06 and gave a
timestamped watch order; "what are people saying about X" fanned out subagents to $7.00 and hit the
turn cap. SKILL.md now says three sources, one pass.

## Second harness: pi 0.83 + local Qwen 27B (llama.cpp over Tailscale, 32k context)

Skill passed unchanged with `--skill`. Same prompts.

| prompt | with | without |
|---|---|---|
| chapters + summary, 77-min talk | 4 tools, 51s, correct | 19 shell calls, 551s, hand-wrote a VTT parser, ran out of context, no answer |
| arXiv PDF in 5 bullets | 9 tools, 58s, correct | 2 calls, 27s, correct |
| article recommendation | 3 tools, 26s, correct | 2 calls, 24s, correct |
| 3 non-link prompts | not fired | |

Notes: llama.cpp keeps generating after a client disconnects, so a killed run blocks the single slot
for minutes. pi buffers `--mode json` output when piped and loses it if killed.

OpenCode: reads `~/.claude/skills` natively; its default request sent `max_tokens: 32000` against
a 32k context and llama.cpp closed the socket. Fixed with a model `limit` in the config. Not yet run
end to end because the rig went down.

## Eval tooling

`claude plugin eval` was gated on this account, so `evals/run.py` drives the same case files.
Two bugs each cost a run: Haiku wraps its JSON verdict in a code fence; YAML rejects `\.` in
double-quoted regex. The runner validates cases before spending and writes results after each run.

## Not covered
Windows. TikTok end to end.
