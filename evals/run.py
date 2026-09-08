#!/usr/bin/env -S uv run --quiet --script
# /// script
# dependencies = ["pyyaml"]
# ///
"""Run evals/*/prompt.md with and without this plugin, grade with graders/*.md, print a table.

Stand-in for `claude plugin eval` (early access). Same case format, same with/without ablation.
Usage: evals/run.py [--runs N] [--case GLOB] [--plugin-dir DIR]
"""
import argparse, fnmatch, json, re, signal, subprocess, sys, time, tempfile
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parent
SKILL_LINK = Path.home() / ".claude/skills/ingest-url"   # moved out of the skills dir for the whole run so both arms start clean
TOOLS = "Bash,Read,Glob,Grep,Skill,WebFetch,ToolSearch"


def frontmatter(path):
    text = path.read_text()
    m = re.match(r"---\n(.*?)\n---\n?(.*)", text, re.S)
    return (yaml.safe_load(m[1]) or {}, m[2].strip()) if m else ({}, text.strip())


def agent(prompt, plugin_dir, max_turns, timeout):
    cmd = ["claude", "-p", prompt, "--output-format", "stream-json", "--verbose", "--max-turns", str(max_turns), "--allowedTools", TOOLS]
    if plugin_dir:
        cmd += ["--plugin-dir", str(plugin_dir)]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=tempfile.mkdtemp()).stdout
    except subprocess.TimeoutExpired:
        return {"answer": "", "cost": 0, "secs": timeout, "skill_used": False}
    events = [json.loads(l) for l in out.splitlines() if l.startswith("{")]
    final = next((e for e in events if e.get("type") == "result"), {})
    tool_uses = [b for e in events if e.get("type") == "assistant" for b in e["message"]["content"] if b.get("type") == "tool_use"]
    skill_used = any(b["name"] == "Skill" and "ingest-url" in json.dumps(b.get("input")) for b in tool_uses)
    return {"answer": final.get("result", ""), "cost": final.get("total_cost_usd", 0), "secs": final.get("duration_ms", 0) / 1000,
            "turns": final.get("num_turns"), "skill_used": skill_used}


def judge(rubric, answer):
    prompt = (f"You are grading an AI answer against a rubric. Every numbered criterion must hold.\n\nRUBRIC:\n{rubric}\n\n"
              f"ANSWER:\n{answer}\n\nReply with JSON only: {{\"pass\": true|false, \"reason\": \"<one sentence>\"}}")
    out = subprocess.run(["claude", "-p", prompt, "--model", "haiku", "--output-format", "json", "--max-turns", "1"],
                         capture_output=True, text=True, timeout=120).stdout
    try:
        text = json.loads(out).get("result", "")
        verdict = json.loads(text[text.index("{"):text.rindex("}") + 1])   # judges like to wrap JSON in a code fence
        return bool(verdict.get("pass")), verdict.get("reason", "")
    except (json.JSONDecodeError, AttributeError, ValueError):
        return False, f"judge returned no JSON: {out[:120]!r}"


def grade(grader_path, run):
    meta, body = frontmatter(grader_path)
    kind = meta["type"]
    if kind == "tool_used":
        n = 1 if run["skill_used"] else 0
        ok = meta.get("min", 1) <= n <= meta.get("max", 10**9)
        return ok, ""
    if kind == "regex":
        hit = re.search(meta["pattern"], run["answer"], re.I if "i" in meta.get("flags", "") else 0) is not None
        return (hit if meta.get("match", "contains") == "contains" else not hit), ""
    if kind == "llm":
        return judge(body, run["answer"])
    raise ValueError(kind)


def main():
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--case", default="*")
    ap.add_argument("--plugin-dir", type=Path, default=ROOT.parent)
    ap.add_argument("--arm", choices=("with", "without", "both"), default="both")
    ap.add_argument("--label", default="", help="tag for the results filename, e.g. the SKILL.md version under test")
    a = ap.parse_args()
    cases = sorted(p for p in ROOT.iterdir() if (p / "prompt.md").exists() and fnmatch.fnmatch(p.name, a.case))
    for case in cases:                                   # parse everything before spending money
        frontmatter(case / "prompt.md")
        for g in (case / "graders").glob("*.md"):
            frontmatter(g)
    out = ROOT / "results" / f"{time.strftime('%Y%m%d-%H%M%S')}{'-' + a.label if a.label else ''}.json"
    out.parent.mkdir(exist_ok=True)
    hidden = None
    if SKILL_LINK.exists() or SKILL_LINK.is_symlink():
        hidden = Path(tempfile.gettempdir()) / "ingest-url.hidden-during-eval"
        SKILL_LINK.rename(hidden)
    results = []
    try:
        for case in cases:
            meta, prompt = frontmatter(case / "prompt.md")
            graders = sorted((case / "graders").glob("*.md"))
            for arm, plugin in (("with", a.plugin_dir), ("without", None)):
                if a.arm != "both" and arm != a.arm:
                    continue
                for i in range(a.runs):
                    run = agent(prompt, plugin, meta.get("max_turns", 25), meta.get("timeout_seconds", 300))
                    scored = {}
                    for g in graders:
                        gm, _ = frontmatter(g)
                        if arm == "without" and gm.get("withOnly"):
                            continue
                        scored[g.stem] = grade(g, run)
                    counted = {k: v for k, v in scored.items() if not frontmatter(case / "graders" / f"{k}.md")[0].get("scored", True) is False}
                    score = sum(ok for ok, _ in counted.values()) / max(len(counted), 1)
                    results.append({"case": case.name, "arm": arm, "run": i + 1, "score": score, "graders": {k: v[0] for k, v in scored.items()},
                                    "reasons": {k: v[1] for k, v in scored.items() if v[1]}, "answer": run["answer"],
                                    **{k: run[k] for k in ("cost", "secs", "skill_used")}})
                    out.write_text(json.dumps(results, indent=1))
                    print(f"{case.name:26} {arm:8} run{i+1} score={score:.2f} ${run['cost']:.2f} {run['secs']:.0f}s skill={run['skill_used']} "
                          + " ".join(f"{k}={'P' if v[0] else 'F'}" for k, v in scored.items()), flush=True)
    finally:
        if hidden:
            hidden.rename(SKILL_LINK)
    print("\ncase                        with   without  delta")
    for name in sorted({r["case"] for r in results}):
        w = [r["score"] for r in results if r["case"] == name and r["arm"] == "with"]
        wo = [r["score"] for r in results if r["case"] == name and r["arm"] == "without"]
        mw = sum(w) / len(w) if w else float("nan")
        mwo = sum(wo) / len(wo) if wo else float("nan")
        print(f"{name:26} {mw:5.2f}  {mwo:5.2f}   {mw - mwo:+.2f}")
    print(f"total cost ${sum(r['cost'] for r in results):.2f}  results: {out}")


if __name__ == "__main__":
    main()
