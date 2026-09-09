#!/usr/bin/env -S uv run --quiet --script
# /// script
# dependencies = ["pyyaml"]
# ///
"""Run evals/*/prompt.md with and without this plugin, grade with graders/*.md, print a table.

Stand-in for `claude plugin eval` (early access). Same case format, same with/without ablation.
Usage: evals/run.py [--runs N] [--case GLOB] [--plugin-dir DIR] [--arm with|without|both] [--label TAG]
"""

import argparse
import contextlib
import fnmatch
import functools
import itertools
import json
import operator
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
SKILL_LINK = Path.home() / ".claude/skills/ingest-url"
HIDDEN_SKILL = SKILL_LINK.parents[1] / "ingest-url.hidden-during-eval"  # claude discovers every dir under skills/
TOOLS = "Bash,Read,Glob,Grep,Skill,WebFetch,ToolSearch"
GATEWAY = ("ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_MODEL", "ANTHROPIC_SMALL_FAST_MODEL")
AGENT_ENV = dict(os.environ)  # set ANTHROPIC_BASE_URL to put a local model under test
JUDGE_ENV = {k: v for k, v in os.environ.items() if k not in GATEWAY}  # the judge is never the model under test


def fail(path, message):
    raise SystemExit(f"evals: {path}: {' '.join(str(message).split())}")


def frontmatter(path):
    text = path.read_text()
    m = re.match(r"---\n(.*?)\n---\n?(.*)", text, re.S)
    if not m:
        return {}, text.strip()
    try:
        meta = yaml.safe_load(m[1]) or {}
    except yaml.YAMLError as e:
        fail(path, e)
    if not isinstance(meta, dict):
        fail(path, "frontmatter is not a mapping")
    return meta, m[2].strip()


def regex_flags(spec):
    try:
        return functools.reduce(operator.or_, (re.RegexFlag[c.upper()] for c in spec), re.NOFLAG)
    except KeyError as e:
        raise ValueError(f"unknown regex flag {e}") from None


def parse_grader(path):
    meta, body = frontmatter(path)
    grader = {"name": path.stem, "body": body, **meta}
    if grader.get("type") not in GRADE:
        fail(path, f"type must be one of {sorted(GRADE)}")
    if grader["type"] == "regex":
        grader.setdefault("match", "contains")
        if grader["match"] not in ("contains", "not_contains"):
            fail(path, "match must be contains or not_contains")
        try:
            grader["regex"] = re.compile(grader["pattern"], regex_flags(grader.get("flags", "")))
        except KeyError as e:
            fail(path, f"missing {e}")
        except (ValueError, re.error) as e:
            fail(path, e)
    return grader


def parse_case(directory):
    meta, prompt = frontmatter(directory / "prompt.md")
    return {
        "name": directory.name,
        "prompt": prompt,
        "max_turns": meta.get("max_turns", 25),
        "timeout": meta.get("timeout_seconds", 300),
        "graders": [parse_grader(p) for p in sorted((directory / "graders").glob("*.md"))],
    }


def find_cases(pattern):
    return [parse_case(p) for p in sorted(ROOT.iterdir()) if (p / "prompt.md").exists() and fnmatch.fnmatch(p.name, pattern)]


@contextlib.contextmanager
def skill_hidden():
    if not os.path.lexists(SKILL_LINK):
        yield
        return
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    SKILL_LINK.rename(HIDDEN_SKILL)
    try:
        yield
    finally:
        HIDDEN_SKILL.rename(SKILL_LINK)


def agent(prompt, plugin_dir, max_turns, timeout):
    cmd = ["claude", "-p", prompt, "--output-format", "stream-json", "--verbose", "--max-turns", str(max_turns)]
    cmd += ["--allowedTools", TOOLS]
    if plugin_dir:
        cmd += ["--plugin-dir", str(plugin_dir)]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=tempfile.mkdtemp(), env=AGENT_ENV).stdout
    except subprocess.TimeoutExpired:
        return {"answer": "", "cost": 0, "secs": timeout, "skill_used": False}
    return parse_transcript(out)


def parse_transcript(stream_json):
    events = [json.loads(line) for line in stream_json.splitlines() if line.startswith("{")]
    final = next((e for e in events if e.get("type") == "result"), {})
    tool_uses = [
        b for e in events if e.get("type") == "assistant" for b in e["message"]["content"] if b.get("type") == "tool_use"
    ]
    return {
        "answer": final.get("result", ""),
        "cost": final.get("total_cost_usd", 0),
        "secs": final.get("duration_ms", 0) / 1000,
        "skill_used": any(b["name"] == "Skill" and "ingest-url" in json.dumps(b.get("input")) for b in tool_uses),
    }


def judge(rubric, answer):
    prompt = (
        f"You are grading an AI answer against a rubric. Every numbered criterion must hold.\n\nRUBRIC:\n{rubric}\n\n"
        f'ANSWER:\n{answer}\n\nReply with JSON only: {{"pass": true|false, "reason": "<one sentence>"}}'
    )
    cmd = ["claude", "-p", prompt, "--model", "haiku", "--output-format", "json", "--max-turns", "1"]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=120, env=JUDGE_ENV).stdout
    try:
        text = json.loads(out).get("result", "")
        verdict = json.loads(text[text.index("{") : text.rindex("}") + 1])  # judges like to wrap JSON in a code fence
        return bool(verdict.get("pass")), verdict.get("reason", "")
    except (json.JSONDecodeError, AttributeError, ValueError):
        return False, f"judge returned no JSON: {out[:120]!r}"


def grade_tool_used(grader, run):
    return grader.get("min", 1) <= int(run["skill_used"]) <= grader.get("max", 10**9), ""


def grade_regex(grader, run):
    hit = grader["regex"].search(run["answer"]) is not None
    return hit == (grader["match"] == "contains"), ""


def grade_llm(grader, run):
    return judge(grader["body"], run["answer"])


GRADE = {"tool_used": grade_tool_used, "regex": grade_regex, "llm": grade_llm}


def score(case, arm, run):
    applicable = [g for g in case["graders"] if arm == "with" or not g.get("withOnly")]
    verdicts = {g["name"]: GRADE[g["type"]](g, run) for g in applicable}
    counted = [verdicts[g["name"]][0] for g in applicable if g.get("scored", True)]
    return {
        "score": sum(counted) / max(len(counted), 1),
        "graders": {name: ok for name, (ok, _) in verdicts.items()},
        "reasons": {name: why for name, (_, why) in verdicts.items() if why},
    }


def evaluate(case, arm, plugin_dir):
    run = agent(case["prompt"], plugin_dir, case["max_turns"], case["timeout"])
    return {**score(case, arm, run), **run}


def report_line(row):
    verdicts = " ".join(f"{name}={'P' if ok else 'F'}" for name, ok in row["graders"].items())
    head = f"{row['case']:26} {row['arm']:8} run{row['run']} score={row['score']:.2f}"
    print(f"{head} ${row['cost']:.2f} {row['secs']:.0f}s skill={row['skill_used']} {verdicts}", flush=True)


def mean(values):
    values = list(values)
    return sum(values) / len(values) if values else float("nan")


def summarize(results, out):
    print("\ncase                        with   without  delta")
    for name in sorted({r["case"] for r in results}):
        with_, without = (
            mean(r["score"] for r in results if r["case"] == name and r["arm"] == arm) for arm in ("with", "without")
        )
        print(f"{name:26} {with_:5.2f}  {without:5.2f}   {with_ - without:+.2f}")
    print(f"total cost ${sum(r['cost'] for r in results):.2f}  results: {out}")


def results_path(label):
    out = ROOT / "results" / f"{time.strftime('%Y%m%d-%H%M%S')}{'-' + label if label else ''}.json"
    out.parent.mkdir(exist_ok=True)
    return out


def parse_args():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--runs", type=int, default=1, help="repetitions per case and arm")
    ap.add_argument("--case", default="*", help="glob over case directory names")
    ap.add_argument("--plugin-dir", type=Path, default=ROOT.parent)
    ap.add_argument("--arm", choices=("with", "without", "both"), default="both")
    ap.add_argument("--label", default="", help="tag for the results filename, e.g. the SKILL.md version under test")
    return ap.parse_args()


def main():
    args = parse_args()
    cases = find_cases(args.case)
    if not cases:
        print(f"no cases match {args.case!r}")
        return
    arms = [(arm, plugin) for arm, plugin in (("with", args.plugin_dir), ("without", None)) if args.arm in (arm, "both")]
    out = results_path(args.label)
    results = []
    with skill_hidden():
        for case, (arm, plugin), i in itertools.product(cases, arms, range(1, args.runs + 1)):
            row = {"case": case["name"], "arm": arm, "run": i, **evaluate(case, arm, plugin)}
            results.append(row)
            out.write_text(json.dumps(results, indent=1))
            report_line(row)
    summarize(results, out)


if __name__ == "__main__":
    main()
