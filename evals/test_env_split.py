#!/usr/bin/env -S uv run --quiet --script
# /// script
# dependencies = ["pyyaml"]
# ///
"""The judge must never inherit the gateway that points the agent at a local model."""

import importlib.util
import os
from pathlib import Path


def load(env):
    os.environ.clear()
    os.environ.update(env)
    spec = importlib.util.spec_from_file_location("run", Path(__file__).parent / "run.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


base = {"PATH": os.environ["PATH"], "HOME": os.environ["HOME"], "UNRELATED": "kept"}

m = load(base)
assert m.AGENT_ENV == m.JUDGE_ENV == base, "no gateway set: agent, judge and environment must be identical"

gateway = {k: f"under-test-{k}" for k in m.GATEWAY}
m = load({**base, **gateway})
assert {k: m.AGENT_ENV[k] for k in gateway} == gateway, "agent lost the gateway"
assert not gateway.keys() & m.JUDGE_ENV.keys(), "judge inherited the model under test"
assert base == m.JUDGE_ENV, "judge lost a variable that is not part of the gateway"

print("ok")
