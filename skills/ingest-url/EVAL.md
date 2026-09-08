# ingest-url: criteria, vendor tests, and measured results

Method: criteria and blind predictions were written before any install. Every candidate was then
run against the same URLs and adopted per criterion, not wholesale. All numbers are from execution
on 2026-09-08 on a MacBook Pro (Apple Silicon), except the second-harness section, which used a
remote 27B model. Test URLs are in `scripts/test_ingest.py`.

## Criteria -> result

| # | criterion | result |
|---|---|---|
| C1 | video -> text.md with [mm:ss] + CHAPTER lines; unavailable video -> nonzero, no file | pass. 7/7 chapters on 3rWSvrFahIY; `aaaaaaaaaaa` exits 1 |
| C2 | frames named by seconds, verified independently | pass. Re-extracted frame at named second: pixel diff 0.9 and 2.4 vs 47 and 25 at +30s. One frame differed because the scene cut fell inside that second |
| C3 | 60-min talk in <=12 sheet reads | pass. 77-min talk = 11 sheets |
| C4 | article -> clean markdown | pass. trafilatura: 0 link-only lines of 53 |
| C5 | pdf -> page markers + images | pass. 33 pages, 5 images |
| C6 | client-rendered page | script fails as predicted; a logged-in browser tool covers it |
| C7 | loud failure, never empty output | pass after fix. trafilatura returns "" with exit 0 on paywalls; the script guards it |
| C8 | triggers on link prompts, not on others | pass. 3/3 positive fired, 0/3 negative |
| C9 | cheaper than no skill | **mixed, see below** |
| ASR | captions missing (reels, shorts) | pass on accuracy: mlx-whisper large-v3-turbo transcribed the captioned clip correctly, ~40s for 167s audio after a one-time model download. The no-caption test clip turned out to be music only and Whisper hallucinated "Thank you." on it, so the script now reports the word count and the self-check covers the path, not accuracy |

## Vendors tested (adopt per criterion)

| candidate | verdict |
|---|---|
| trafilatura | adopted for articles. 1.6s, no nav boilerplate |
| markitdown 0.1.7 | rejected. 21s on the same article, keeps nav/footer; PDF output 3.5x longer, no images |
| pymupdf4llm | adopted for PDF. page-chunked, images out |
| yt-dlp via `uv tool` with curl_cffi | adopted. brew build has no impersonation targets |
| steipete/summarize 0.21 | rejected. Printed nothing, exit 0 on the test video (silent empty) |
| claude-real-video | rejected. 2 min for a 3-min clip, every caption line tripled, 13 sheets for 3 min, no chapters, writes ~/.crv/memory.db unasked |
| mlx-whisper | adopted for no-caption media (Apple Silicon) |
| browser tool already in the harness | adopted for JS/login pages instead of adding Playwright |

## C9: with vs without the skill (claude -p, Fable 5.1, same prompts)

| task | with skill | without |
|---|---|---|
| chapters + 3-line summary | $0.91, 39s | $0.32, 16s. One yt-dlp metadata call; summary from description only |
| paper in 5 bullets (arXiv PDF) | $0.87, read the PDF | $0.32. WebFetch declined, **answered from memory** |
| article recommendation | $0.55 | $0.45, WebFetch worked |
| timestamped numbers from a 77-min talk | $0.85, 153s, correct | $0.83, 58s, correct. Subs + targeted ffmpeg frames |

Reading: this model already knows yt-dlp and ffmpeg, and YouTube did not 403 during the test, so on
shallow tasks the skill only adds overhead. The measurable wins are (1) the PDF case, where
the no-skill run silently fell back to training data, (2) a fixed output layout that parallel
subagents can share, (3) the no-captions path, which the baseline has no answer for.
The deep-talk result changed the design: whole-video frame extraction moved from default to
`--frames`, and SKILL.md now tells the agent to take the cheapest path.

## Platforms (all through the same `video` command)

| platform | result |
|---|---|
| YouTube 3-min clip, captions | pass, 32 frames with the density floor |
| YouTube 77-min talk | pass, 7 chapters, transcript only in 16s |
| Instagram reel Dc_acmis1BM | pass, 9s end to end, 126 words by speech-to-text, 6 frames. Nonexistent reel exits 1 |
| X post 1945976064758730965 | pass, 38s, 381 words from X captions, 13 frames |
| TikTok | untested: no public video URL reachable without a known handle |

The reel exposed a gap: scene detection alone gave 1 frame for a motion-graphics reel. Fix was
inside the ffmpeg select expression, one frame at least every 10s, no extra branch.

## Second harness: pi 0.83 + local Qwen (qwen3.8-27b on llama.cpp over Tailscale, 32k context)

Setup: provider entry in `~/.pi/agent/models.json` (baseUrl, api openai-completions, dummy apiKey,
compat supportsDeveloperRole=false), skill passed unchanged with `--skill <path-to-skill-dir>`.
Same six trigger prompts and three no-skill baselines as the Claude Code test. One GPU slot, so runs were sequential.

| prompt | with skill | without skill |
|---|---|---|
| chapters + summary, 77-min talk | read SKILL.md, ran script, 4 tools, 51s, correct | 19 shell calls, 551s, hand-wrote a VTT parser, grepped transcript 15x for speaker changes, **blew the 32k context, no answer** |
| arXiv PDF in 5 bullets | 9 tools, 58s, correct | 2 calls (curl + pdftotext), 27s, correct |
| article recommendation | 3 tools, 26s, correct | 2 calls (curl + regex strip), 24s, correct |
| 3 non-link prompts | skill not used, 5-7s each | n/a |

Reading: trigger behaviour is identical to Claude Code (3/3 fire, 0/3 false positives) with a 27B
local model. The skill's value on a small model is not speed on easy tasks, it is the video case:
the model did not know chapters are in yt-dlp metadata and could not fit its own exploration in
32k tokens. The skill turns that into 4 tool calls. No extra harness work was needed: pi already
implements the Agent Skills spec and reads Claude Code skill folders.

Operational notes: llama.cpp keeps generating after a client is killed, so a killed run blocks the
single slot for minutes; poll `/slots` before each run. pi buffers `--mode json` output when piped
and loses it if killed, so give runs a generous cap rather than a tight one.

## Plugin eval: with vs without, and description A/B (Claude Code, Fable 5.1)

`evals/` holds five cases in the `claude plugin eval` format (that command was still gated on this
account, so `evals/run.py` drives the same files: fresh `claude -p` session per run, plugin loaded
via `--plugin-dir` in the with arm and absent in the without arm, regex graders in code, rubric
graders judged by Haiku). Two runs per cell. WebFetch was allowed in every run, which the earlier
trigger test had not done; that omission had flattered the fire rate.

| case | v1 description, with | v2 description, with | without |
|---|---|---|---|
| non-link shell task (must not fire) | 1.00, fired 0/2 | 1.00, fired 0/2 | 1.00 |
| number only in a PDF body | 1.00, fired 0/2, $0.46 | 1.00, fired 1/2, $0.51 | 1.00, $0.45 |
| Instagram reel, no captions | 1.00, fired 2/2, $0.52 | 1.00, fired 2/2, $0.51 | 0.75, $0.51 |
| chapters of a 77-min talk | 1.00, fired 2/2, $0.95 | 1.00, fired 2/2, $0.95 | 1.00, $0.40 |
| timestamped figures inside the talk | 1.00, fired 2/2, $0.90 | 1.00, fired 2/2, $1.05 | 0.50, $0.52 |

Reading:
- The skill wins where the answer lives in audio or on a slide: the reel (unaided runs leaned on
  the caption half the time) and the in-talk figures (unaided runs found the spoken 60% but never
  the 67.1% that is only on a slide). Both cases: 1.00 with, 0.50 to 0.75 without.
- It does not win on chapters or on a PDF number for this model, which knows yt-dlp metadata and
  curl plus pdftotext on its own; there the skill costs about 2x for the same answer.
- v1's description lost to WebFetch on PDF links every time. v2 names WebFetch and states what it
  cannot read; fire rate on the PDF case went from 0/2 to 1/2 with no change elsewhere. Small
  sample, so recorded as a direction, not a result.
- Two eval bugs cost a full run each and are worth knowing: Haiku wraps its JSON verdict in a code
  fence, so the parser must find the first `{`; and YAML rejects `\.` inside double-quoted regex
  patterns, so grader patterns use single quotes. The runner now validates every case file before
  spending money and writes results after each run.
- Total spend for all eval runs, including the two wasted ones: about $17.

## Platform and language agnosticism (1.2.0)

| change | evidence |
|---|---|
| speech-to-text backend chosen by platform marker in the script's inline dependencies | faster-whisper 1.2.1 `large-v3-turbo` on CPU: 24s for the 50s reel, 2m40s for the 167s clip; transcript byte-identical to mlx-whisper on the reel |
| captions in the video's own language, English fallback | Spanish TEDx talk (i5ui_DrtcpU): picked the uploader's `es` track over `es-orig` auto and over English, 711 words, manifest language `es` |
| content-addressed cache, default output dir | second call 0.04s vs 6.8s; asking for `--frames` after a transcript-only run re-ingests instead of serving the cache |
| TikTok | not testable here: TLS handshake to tiktok.com fails from plain curl too (blocked in India). yt-dlp has the extractor |

## Search: vendor test and the `find` subcommand (1.4.0)

Need, from the session mining: about 5 explicit "search X and YouTube, then watch" prompts and 69
strict discovery prompts in two months; general web discovery already served by the harness's
WebSearch (873 calls). Criteria S1-S8 were written before testing (search-eval/CRITERIA.md).

| candidate | verdict |
|---|---|
| mvanhorn/last30days-skill (61.6k stars, 59 MB, 2307-line SKILL.md) | rejected. Keyless, `--days 3650`: 0 YouTube videos on both test queries where `yt-dlp ytsearch` returned the exact talks in 1s; paper hits were adjacent (AlphaZero, STP) not the target paper arXiv's own relevance search ranks first; 20-23s per query |
| per-platform skills (hermes youtube-content, deepmind arxiv, vm0 hackernews) | not adopted: three trigger surfaces for one chain |
| nothing (agent types the one-liners) | seen in logs; loses on consistency and parallelism |
| own `find` subcommand | adopted. 5 platforms in parallel, 1.7s, per-platform blocks, a failed platform reports its error and the rest still answer |

Known weak spots: Semantic Scholar's shared pool throttles unpredictably (OpenAlex fallback
ranks poorly); yt-dlp flat search omits upload dates; no free X or Instagram search exists.

## Not covered yet
- Windows.
- TikTok end to end (network-blocked where this was built).
