"""Smoke test: ChatAPI is_image_output path against StepFun images API."""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile


async def main() -> int:
    repo_src = os.path.join(os.path.dirname(__file__), "..", "src")
    sys.path.insert(0, os.path.abspath(repo_src))

    from opensquad.chat_api import ChatAPI

    api_key = os.environ.get("STEP_API_KEY", "").strip()
    if not api_key:
        print("FAIL: set STEP_API_KEY env var (do not hardcode secrets)")
        return 2
    out_dir = tempfile.mkdtemp(prefix="opensquad_img_")
    chat = ChatAPI(
        api_key=api_key,
        base_url="https://api.stepfun.com/v1",
        model="step-image-edit-2",
        prompt="You are an image generation assistant.",
        is_image_output=True,
        is_img_model=True,
        image_size="1024x1024",
        image_steps=8,
        image_cfg_scale=1.0,
        timeout=120.0,
    )
    chat.output_media_dir = out_dir

    result = await chat.chat("a simple red apple on a white background, minimal")
    print("text:", result.get("text"))
    print("finish_reason:", result.get("finish_reason"))
    media = result.get("output_media") or []
    print("output_media:", media)
    if not media:
        print("FAIL: no output_media")
        return 1
    url = media[0].get("url", "")
    fname = url.rsplit("/", 1)[-1]
    fpath = os.path.join(out_dir, fname)
    if not os.path.isfile(fpath):
        print("FAIL: file missing", fpath)
        return 1
    size = os.path.getsize(fpath)
    print("saved:", fpath, "bytes:", size)
    if size < 1000:
        print("FAIL: file too small")
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
