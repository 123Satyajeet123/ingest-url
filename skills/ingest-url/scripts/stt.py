#!/usr/bin/env -S uv run --quiet --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "mlx-whisper; sys_platform == 'darwin' and platform_machine == 'arm64'",
#   "faster-whisper; sys_platform != 'darwin' or platform_machine != 'arm64'",
# ]
# ///
"""On-device speech-to-text for ingest.py, kept in its own environment so captioned videos never pay for it.

stt.py <audio.wav>   prints JSON {"language": "en", "segments": [[seconds, text], ...]}
"""

import importlib
import json
import os
import sys

BACKEND = os.environ.get("INGEST_STT", "auto")  # auto | mlx-whisper | faster-whisper
MODEL = "large-v3-turbo"


def backend():
    names = [BACKEND] if BACKEND != "auto" else ["mlx-whisper", "faster-whisper"]
    for name in names:
        try:
            return name, importlib.import_module(name.replace("-", "_"))
        except ImportError:
            continue
    sys.exit("stt: no speech-to-text backend importable; install mlx-whisper (Apple Silicon) or faster-whisper")


def transcribe(wav):
    name, module = backend()
    if name == "mlx-whisper":
        result = module.transcribe(wav, path_or_hf_repo=f"mlx-community/whisper-{MODEL}")
        return result.get("language"), [(int(s["start"]), s["text"]) for s in result["segments"]]
    model = module.WhisperModel(MODEL, device="auto", compute_type="auto")
    raw, info = model.transcribe(wav)
    return info.language, [(int(s.start), s.text) for s in raw]


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    language, segments = transcribe(sys.argv[1])
    print(json.dumps({"language": language, "segments": [(t, text.strip()) for t, text in segments if text.strip()]}))


if __name__ == "__main__":
    main()
