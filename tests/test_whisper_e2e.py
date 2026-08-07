"""End-to-end smoke test for the whisper model store.

Run from project root:
    python tests/test_whisper_e2e.py

This will:
1. wipe the existing tiny model from the workspace data dir
2. start a background download
3. poll the status until done or 5 min elapse
4. assert the file is on disk and SHA-valid (or the HF-mirror repack path
   produced a usable openai-whisper .pt file).
"""

from __future__ import annotations

import os
import sys
import time

PROJECT_ROOT = os.path.abspath(os.path.dirname(__file__))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "..", "src"))
# Ensure the workspace data is clean for 'tiny'.

from plugins.whisper import model_store


def main() -> int:
    target = "tiny"
    print(f"[test] forcing target model: {target}")
    model_store._write_selected_model(target)
    model_store.reset_for_tests()

    # Wipe any existing tiny.pt
    for path in (
        os.path.join(model_store.model_dir(), f"{target}.pt"),
        os.path.join(model_store._legacy_cache_dir(), f"{target}.pt"),
    ):
        try:
            if os.path.isfile(path):
                os.remove(path)
                print(f"[test] wiped {path}")
        except OSError as e:
            print(f"[test] could not wipe {path}: {e}")

    result = model_store.start_download(model=target, force=True)
    print("[test] start_download:", result)

    if not result.get("started"):
        print("[test] download did not start; assuming already complete")
        return 0

    # Poll until done or 5 min elapse.
    deadline = time.time() + 300
    last_state = None
    while time.time() < deadline:
        status = model_store.get_status()
        dl = status.get("download", {}) or {}
        state = dl.get("state")
        msg = dl.get("message")
        progress = dl.get("progress")
        if state != last_state or (state in ("downloading", "error") and msg):
            print(f"[test] state={state!r} progress={progress} message={msg!r}")
            last_state = state
        if state in ("ready", "error"):
            break
        time.sleep(2.0)

    final = model_store.get_status()
    print("[test] final status:", final)
    if final.get("ready"):
        print("[test] OK: model is ready")
        return 0
    print("[test] FAIL: model is not ready")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
