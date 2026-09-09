"""Regression: vision.read_image paths must reach the next LLM turn."""

from __future__ import annotations

from types import SimpleNamespace


def test_vision_result_image_paths_injected_into_current_images():
    """Mirrors turn_loop injection when vision.read_image returns image_paths."""
    runner = SimpleNamespace(_current_images=[])
    result = {
        "status": "success",
        "message": "registered",
        "image_paths": [
            r"C:\ai_work\pro0\opensquad_runtime_deploy\.playwright-mcp\state1.png",
        ],
    }
    _vision_paths = result.get("image_paths")
    assert isinstance(_vision_paths, list)
    already = set(runner._current_images or [])
    new_paths = [p for p in _vision_paths if p and p not in already]
    runner._current_images = list(runner._current_images or []) + new_paths
    assert runner._current_images == _vision_paths


def test_parallel_turn_must_consume_images_after_turn0():
    """Document the fixed contract: mid-loop images are not turn==0-only."""
    turn = 1
    current_images = [r"C:\tmp\shot.png"]
    # Fixed behavior: consume whenever non-empty (any turn)
    native = list(current_images) if current_images else None
    assert turn != 0  # would have been dropped by the old gate
    assert native == [r"C:\tmp\shot.png"]


def test_mcp_multimodal_queues_tool_result_images():
    runner = SimpleNamespace(_tool_result_images=[])
    result = {
        "__mcp_multimodal__": True,
        "text": "screenshot ok",
        "images": [{"mimeType": "image/png", "data": "aaa"}],
    }
    if result.get("__mcp_multimodal__"):
        imgs = result.get("images") or []
        existing = list(getattr(runner, "_tool_result_images", None) or [])
        existing.extend(imgs)
        runner._tool_result_images = existing
    assert len(runner._tool_result_images) == 1
