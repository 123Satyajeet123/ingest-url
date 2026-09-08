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


def run(*args):
    return subprocess.run([str(INGEST), *args], capture_output=True, text=True)


tmp = Path(tempfile.mkdtemp())

r = run("video", CLIP, tmp / "clip", "--frames")
assert r.returncode == 0, r.stderr
text = (tmp / "clip/text.md").read_text()
assert "[00:00]" in text and "[02:00]" in text, "minute buckets missing"
m = json.loads((tmp / "clip/manifest.json").read_text())
assert m["frames"] > 5 and m["sheets"] == 1 and m["transcript"] == "captions", m
assert all(len(p.stem.rstrip("b")) == 5 for p in (tmp / "clip/frames").glob("*.jpg")), "frames not named by seconds"

r = run("video", TALK, tmp / "talk")
assert r.returncode == 0, r.stderr
assert (tmp / "talk/text.md").read_text().count("\nCHAPTER ") == 7
assert not (tmp / "talk/frames").exists() and not (tmp / "talk/source.mp4").exists(), "default must not download video"

r = run("video", NOCAP, tmp / "nocap")
assert r.returncode == 0, r.stderr
assert json.loads((tmp / "nocap/manifest.json").read_text())["transcript"] == "speech-to-text"
assert "[00:00]" in (tmp / "nocap/text.md").read_text()

r = run("video", "https://www.youtube.com/watch?v=aaaaaaaaaaa", tmp / "neg-video")
assert r.returncode != 0 and "unavailable" in r.stderr and not (tmp / "neg-video/text.md").exists()

r = run("article", ARTICLE, tmp / "art")
assert r.returncode == 0 and "title: Building Effective AI Agents" in (tmp / "art/text.md").read_text()

r = run("article", "https://www.wsj.com/tech/ai", tmp / "neg-art")
assert r.returncode != 0 and not (tmp / "neg-art/text.md").exists(), "paywall must fail loudly"

r = run("pdf", PDF, tmp / "pdf")
assert r.returncode == 0, r.stderr
assert (tmp / "pdf/text.md").read_text().count("--- page ") >= 10 and any((tmp / "pdf/images").iterdir())

assert run("bogus", ARTICLE, tmp / "x").returncode != 0

print("ok", tmp)
