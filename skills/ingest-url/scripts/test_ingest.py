#!/usr/bin/env python3
"""Both-direction check for ingest.py. Needs network, Chrome cookies, ffmpeg. Run: python3 test_ingest.py"""
import json, subprocess, tempfile
from pathlib import Path

INGEST = Path(__file__).with_name("ingest.py")
CLIP = "https://www.youtube.com/watch?v=v_x9mH1P4Ng"          # 167s, auto captions, no chapters
NOCAP = "https://www.youtube.com/watch?v=pjT9nUhlwxQ"         # 68s, no captions, music only -> exercises the speech-to-text path, not its accuracy
TALK = "https://www.youtube.com/watch?v=3rWSvrFahIY"          # 77min, 7 chapters
ARTICLE = "https://www.anthropic.com/engineering/building-effective-agents"
PDF = "https://arxiv.org/pdf/2210.03629"
SPANISH = "https://www.youtube.com/watch?v=i5ui_DrtcpU"       # 282s TEDx talk, uploader captions in es


def run(*args):
    return subprocess.run([str(INGEST), *args], capture_output=True, text=True)


tmp = Path(tempfile.mkdtemp())

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
assert r.returncode != 0 and "unavailable" in r.stderr and not (tmp / "neg-video/text.md").exists()

r = run("article", ARTICLE, "--out", tmp / "art")
assert r.returncode == 0 and "title: Building Effective AI Agents" in (tmp / "art/text.md").read_text()

r = run("article", "https://www.wsj.com/tech/ai", "--out", tmp / "neg-art")
assert r.returncode != 0 and not (tmp / "neg-art/text.md").exists(), "paywall must fail loudly"

r = run("pdf", PDF, "--out", tmp / "arx")
assert r.returncode == 0, r.stderr
arx = (tmp / "arx/text.md").read_text()
assert arx.count("--- file ") >= 5 and "0.84" in arx and 'title: "ReAct' in arx, "arXiv must come from LaTeX source with real title"

r = run("pdf", "https://proceedings.neurips.cc/paper_files/paper/2017/file/3f5ee243547dee91fbd053c1c4a845aa-Paper.pdf", "--out", tmp / "pdf")
assert r.returncode == 0, r.stderr
assert (tmp / "pdf/text.md").read_text().count("--- page ") >= 5, "non-arXiv PDF must take the page path"

r = run("find", "Scaling Self-Play with Self-Guidance", "--sources", "youtube,arxiv,hn", "--limit", "3")
assert r.returncode == 0, r.stderr
assert "arxiv.org/pdf/2604.20209" in r.stdout and "youtube.com/watch" in r.stdout, "find must surface the paper and its talks"
assert "## hn" in r.stdout, "a platform with no hits still reports itself"
assert run("find", "x", "--sources", "tiktok").returncode != 0, "unknown source must be refused"

assert run("bogus", ARTICLE).returncode != 0
assert run("article", ARTICLE, PDF, "--out", tmp / "x").returncode != 0, "--out with several URLs must be refused"

print("ok", tmp)
