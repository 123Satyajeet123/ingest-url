#!/usr/bin/env python3
"""Both-direction check for ingest.py. Needs network, Chrome cookies, ffmpeg. Run: python3 test_ingest.py"""

import contextlib
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

import ingest

INGEST = Path(__file__).with_name("ingest.py")
CLIP = "https://www.youtube.com/watch?v=v_x9mH1P4Ng"  # 167s, auto captions, no chapters
NOCAP = "https://www.youtube.com/watch?v=pjT9nUhlwxQ"  # 68s, no captions, music only: exercises speech-to-text, not its accuracy
TALK = "https://www.youtube.com/watch?v=3rWSvrFahIY"  # 77min, 7 chapters
SPANISH = "https://www.youtube.com/watch?v=i5ui_DrtcpU"  # 282s TEDx talk, uploader captions in es
ARTICLE = "https://www.anthropic.com/engineering/building-effective-agents"
PDF = "https://arxiv.org/pdf/2210.03629"
NEURIPS_PDF = "https://proceedings.neurips.cc/paper_files/paper/2017/file/3f5ee243547dee91fbd053c1c4a845aa-Paper.pdf"
DEAD_PROXY = {**os.environ, "HTTPS_PROXY": "http://127.0.0.1:9", "HTTP_PROXY": "http://127.0.0.1:9"}


def run(*args, env=None):
    return subprocess.run([str(INGEST), *args], capture_output=True, text=True, env=env)


def fails(fn, *args):
    try:
        fn(*args)
    except ingest.IngestError as error:
        return str(error)
    raise AssertionError(f"{fn.__name__} did not fail")


def captured(fn, *args):
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        result = fn(*args)
    return result, buffer.getvalue()


tmp = Path(tempfile.mkdtemp())

# ---------- unit: guards both ways, no network ----------

assert ingest.run_or_fail(["true"]).returncode == 0
assert fails(ingest.run_or_fail, ["false"]) == "false exited 1", "empty stderr must still name the tool"
assert fails(ingest.run_or_fail, [sys.executable, "-c", "import sys; sys.exit('first\\nlast')"]) == "last"

ingest.write_nonempty(tmp / "some.md", "x")
assert (tmp / "some.md").read_text() == "x"
assert "no text extracted" in fails(ingest.write_nonempty, tmp / "empty.md", " \n")
assert not (tmp / "empty.md").exists()

assert ingest.cache_dir("https://a") == ingest.cache_dir("https://a") != ingest.cache_dir("https://b")
assert len(ingest.cache_dir("https://a").name) == 12

cache = tmp / "cache"
cache.mkdir()
assert not ingest.served_from_cache(cache, "u"), "no manifest"
(cache / "manifest.json").write_text(json.dumps({"source_url": "u", "frames_requested": False}))
assert not ingest.served_from_cache(cache, "other"), "different URL"
assert not ingest.served_from_cache(cache, "u", frames=True), "frames requested but not cached"
hit, out = captured(ingest.served_from_cache, cache, "u")
assert hit and "cached" in out and "sheets" not in out
(cache / "manifest.json").write_text(json.dumps({"source_url": "u", "frames_requested": True}))
hit, out = captured(ingest.served_from_cache, cache, "u", True)
assert hit and "then" in out and "sheets" in out

assert ingest.base_language("es-419") == "es" and ingest.base_language(None) == "en"
caps = tmp / "caps"
caps.mkdir()
assert ingest.caption_file(caps, "es") is None
for name in ("source.en.vtt", "source.es.vtt", "source.es-orig.vtt"):
    (caps / name).touch()
assert ingest.caption_file(caps, "es").name == "source.es.vtt", "own language, uploader track first"
assert ingest.caption_file(caps, "fr").name == "source.en.vtt", "English fallback"
assert ingest.caption_language(caps / "source.es-orig.vtt") == "es"

vtt = tmp / "a.vtt"
vtt.write_text(
    "WEBVTT\nKind: captions\n\n"
    "00:00:01.000 --> 00:00:03.000\n<c>hello</c>&nbsp;there\n\n"
    "00:00:02.000 --> 00:00:04.000\nhello there\n\n"
    "01:05.500 --> 01:07.000\nbye\n"
)
assert ingest.vtt_segments(vtt) == [(1, "hello there"), (65, "bye")], ingest.vtt_segments(vtt)

frames = tmp / "frames"
frames.mkdir()
raw = [frames / f"raw{n:05d}.jpg" for n in (1, 2, 3)]
for path in raw:
    path.touch()
ingest.name_frames_by_second(raw, [3.2, 3.7, 20.0])
assert sorted(p.name for p in frames.iterdir()) == ["00003.jpg", "00003b.jpg", "00020.jpg"], (
    "two cuts in one second, no clash with raw names"
)
assert ingest.frame_second(frames / "00003b.jpg") == 3
assert "reported 1 frames but 0" in fails(ingest.name_frames_by_second, [], [1.0])

assert ingest.arxiv_id("https://arxiv.org/abs/2210.03629v2") == "2210.03629v2"
assert ingest.arxiv_id("https://arxiv.org/pdf/2210.03629") == "2210.03629"
assert ingest.arxiv_id("https://example.com/2210.03629") is None

tgz = tmp / "source.tar.gz"
with tarfile.open(tgz, "w:gz") as tar:
    for name, text in (
        ("main.tex", "\\begin{document}"),
        ("sub/b.tex", "b"),
        ("../escape.tex", "evil"),
        ("/abs.tex", "evil"),
        ("notes.txt", "no"),
    ):
        info = tarfile.TarInfo(name)
        info.size = len(text)
        tar.addfile(info, io.BytesIO(text.encode()))
    link = tarfile.TarInfo("link.tex")
    link.type = tarfile.SYMTYPE
    link.linkname = "/etc/passwd"
    tar.addfile(link)
    folder = tarfile.TarInfo("dir.tex")
    folder.type = tarfile.DIRTYPE
    tar.addfile(folder)
tex = ingest.latex_files(tgz)
assert set(tex) == {"main.tex", "sub/b.tex"}, tex
assert ingest.latex_markdown({"z.tex": "\\begin{document}", "a.tex": "x"}).startswith("\n\n--- file z.tex ---"), "main file leads"
assert ingest.latex_markdown({"b.tex": "x", "a.tex": "y"}).startswith("\n\n--- file a.tex ---"), "no main: alphabetical"

assert ingest.xml_text("<a>\n  x  y \n</a>", "a") == "x y" and ingest.xml_text("<a>x</a>", "b") is None

ingest.STT_BACKEND = "no-such-backend"
assert "no speech-to-text backend" in fails(ingest.stt_backend)
ingest.STT_BACKEND = "auto"

assert ingest.strip_html("<p>a<p>b <i>c</i>") == "a\n\nb c"
assert ingest.hn_comment_lines(
    {"children": [{"author": "x", "text": "hi", "children": [{"author": "y", "text": "yo"}]}, {"text": None}]}
) == ["- **x**: hi", "  - **y**: yo"]


def boom(query, limit):
    raise RuntimeError("down")


ingest.FINDERS["boom"] = boom
_, out = captured(ingest.find, "q", ["boom"], 1)
assert out.startswith("## boom (failed: RuntimeError: down)"), out
del ingest.FINDERS["boom"]

# ---------- live ----------

r = run("video", CLIP, "--out", tmp / "clip", "--frames")
assert r.returncode == 0, r.stderr
text = (tmp / "clip/text.md").read_text()
assert "[00:00]" in text and "[02:00]" in text, "minute buckets missing"
m = json.loads((tmp / "clip/manifest.json").read_text())
assert m["frames"] > 5 and m["sheets"] == 1 and m["transcript"] == "captions", m
assert all(len(p.stem.rstrip("b")) == 5 for p in (tmp / "clip/frames").glob("*.jpg")), "frames not named by seconds"

r = run("video", TALK, "--out", tmp / "talk")
assert r.returncode == 0, r.stderr
assert "76:07 Closing Remarks" in r.stdout, "summary must list chapters"
assert (tmp / "talk/text.md").read_text().count("\nCHAPTER ") == 7
assert not (tmp / "talk/frames").exists() and not (tmp / "talk/source.mp4").exists(), "default must not download video"

r = run("video", SPANISH, "--out", tmp / "es")
assert r.returncode == 0, r.stderr
assert json.loads((tmp / "es/manifest.json").read_text())["language"] == "es", "must pick the video's own language"
assert (tmp / "es/text.md").read_text().startswith('---\ntitle: "Cómo'), "front-matter must lead and keep unicode"

r = run("video", CLIP, "--out", tmp / "clip")
assert r.returncode == 0 and "cached" in r.stdout, "same URL and no new request must be served from cache"

r = run("video", NOCAP, "--out", tmp / "nocap")
assert r.returncode == 0, r.stderr
assert json.loads((tmp / "nocap/manifest.json").read_text())["transcript"] == "speech-to-text"
assert "[00:00]" in (tmp / "nocap/text.md").read_text()

r = run("video", "https://www.youtube.com/watch?v=aaaaaaaaaaa", "--out", tmp / "neg-video")
assert r.returncode != 0 and r.stderr.startswith("ingest:") and "unavailable" in r.stderr, r.stderr
assert not (tmp / "neg-video/text.md").exists()

r = run("article", ARTICLE, "--out", tmp / "art")
assert r.returncode == 0 and "title: Building Effective AI Agents" in (tmp / "art/text.md").read_text()

r = run("article", "https://news.ycombinator.com/item?id=49023019", "--out", tmp / "hn")
assert r.returncode == 0 and (tmp / "hn/text.md").read_text().count("\n- **") > 20, "HN item must come back as a comment tree"
assert json.loads((tmp / "hn/manifest.json").read_text())["comments"] > 20

r = run("article", "https://www.wsj.com/tech/ai", "--out", tmp / "neg-art")
assert r.returncode != 0 and r.stderr.startswith("ingest:") and not (tmp / "neg-art/text.md").exists(), "paywall must fail loudly"

r = run("pdf", PDF, "--out", tmp / "arx")
assert r.returncode == 0, r.stderr
arx = (tmp / "arx/text.md").read_text()
assert arx.count("--- file ") >= 5 and "0.84" in arx and 'title: "ReAct' in arx, (
    "arXiv must come from LaTeX source with real title"
)

r = run("pdf", NEURIPS_PDF, "--out", tmp / "pdf")
assert r.returncode == 0, r.stderr
assert (tmp / "pdf/text.md").read_text().count("--- page ") >= 5, "non-arXiv PDF must take the page path"

r = run("pdf", str(tmp / "pdf/source.pdf"), "--out", tmp / "local")
assert r.returncode == 0 and (tmp / "local/text.md").read_text().count("--- page ") >= 5, "local PDF path"
r = run("pdf", str(tmp / "missing.pdf"), "--out", tmp / "neg-local")
assert r.returncode != 0 and r.stderr.startswith("ingest: no such file"), r.stderr

r = run("find", "Scaling Self-Play with Self-Guidance", "--sources", "youtube,arxiv,hn", "--limit", "3")
assert r.returncode == 0, r.stderr
assert "arxiv.org/pdf/2604.20209" in r.stdout and "youtube.com/watch" in r.stdout, "find must surface the paper and its talks"
assert "## hn" in r.stdout, "a platform with no hits still reports itself"
r = run("find", "x", "--sources", "youtube", "--limit", "1", env=DEAD_PROXY)
assert r.returncode == 0 and "## youtube (failed: IngestError" in r.stdout, r.stdout + r.stderr
assert run("find", "x", "--sources", "tiktok").returncode != 0, "unknown source must be refused"

assert run("bogus", ARTICLE).returncode != 0
assert run("article", ARTICLE, PDF, "--out", tmp / "x").returncode != 0, "--out with several URLs must be refused"
assert run("article", ARTICLE, "--frames").returncode == 2, "--frames outside video must be refused"

# ---- pull: canonical keys, routing, consume-once inbox, failure isolation ----
canon = ingest.canonical_url
assert canon("https://youtu.be/v_x9mH1P4Ng?t=5") == canon("https://www.youtube.com/watch?v=v_x9mH1P4Ng&list=WL")
assert canon("https://x.com/a/status/1?utm_source=t") == "https://x.com/a/status/1"
assert canon("https://site.com/p?a=1&b=2") == "https://site.com/p?a=1&b=2", "non-tracking params must survive"
for url, kind in (
    ("https://arxiv.org/abs/2604.20209", "pdf"),
    ("https://x.com/a/status/1", "video"),
    ("https://news.ycombinator.com/item?id=1", "article"),
    ("https://notyoutube.com/x", "article"),
):
    assert ingest.kind_for(url) == kind, (url, ingest.kind_for(url))

inbox = tmp / "inbox.txt"
inbox.write_text(f"https://youtu.be/v_x9mH1P4Ng?t=5\n{ARTICLE}?utm_source=x\nhttps://example.invalid/nothing\n\n")
env = {**os.environ, "INGEST_INBOX": str(inbox), "INGEST_CACHE": str(tmp / "pullcache")}
r = subprocess.run([str(INGEST), "pull", "--sources", "inbox"], capture_output=True, text=True, env=env)
assert r.returncode == 0 and "2 new, 1 failed" in r.stdout, r.stdout + r.stderr
assert "example.invalid" in r.stdout, "a failing URL must be reported on its own line"
assert inbox.read_text() == "", "inbox must be empty after consumption"
done = (tmp / "inbox.done").read_text()
assert done.count("ingested") == 2 and done.count("failed") == 1, done
inbox.write_text("https://www.youtube.com/watch?v=v_x9mH1P4Ng\n")
r = subprocess.run([str(INGEST), "pull", "--sources", "inbox"], capture_output=True, text=True, env=env)
assert "0 new, 0 failed, 1 cached" in r.stdout, "a URL variant already ingested must be served from cache"
assert subprocess.run([str(INGEST), "pull", "--sources", "nope"], capture_output=True).returncode != 0


print("ok", tmp)
