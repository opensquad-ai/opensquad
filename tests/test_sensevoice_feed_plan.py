"""The SenseVoice ONNX feed must follow the *graph's own* input names.

Regression for a hardcoded feed of ``speech`` / ``speech_lengths`` / ``textnorm``
against graphs that declare ``x`` / ``x_length`` / ``text_norm``. Every
transcription failed inside onnxruntime with::

    Required inputs (['x', 'x_length', 'text_norm']) are missing from input feed
    (['speech', 'speech_lengths', 'language', 'textnorm'])

(`language` alone happened to match, which is why only three were reported.)

These tests exercise the pure planning helpers, so they need neither the ~240 MB
graph nor onnxruntime/librosa/soundfile installed — `inference.py` imports those
at their call sites for exactly this reason.
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path

import numpy as np
import pytest

_MODULE_PATH = Path(__file__).resolve().parents[1] / "src" / "plugins" / "sensevoice" / "inference.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("sensevoice_inference_under_test", _MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


inference = _load_module()

# The two spellings seen in the wild.
MODELSCOPE_INPUTS = ["x", "x_length", "language", "text_norm"]
FUNASR_INPUTS = ["speech", "speech_lengths", "language", "textnorm"]


def _feat(frames: int = 34, dims: int = 560) -> np.ndarray:
    return np.zeros((1, frames, dims), dtype=np.float32)


@pytest.mark.parametrize(
    ("declared", "expected"),
    [
        (MODELSCOPE_INPUTS, {"x": "x", "x_length": "x_length", "language": "language", "text_norm": "text_norm"}),
        (FUNASR_INPUTS, {"x": "speech", "x_length": "speech_lengths", "language": "language", "text_norm": "textnorm"}),
    ],
)
def test_plan_uses_the_graph_spelling(declared, expected):
    assert inference.resolve_feed_plan(declared) == expected


def test_feed_keys_are_the_graph_names_not_our_names():
    """The old bug in one assertion: keys must never be our internal spelling."""
    plan = inference.resolve_feed_plan(MODELSCOPE_INPUTS)
    feed = inference.build_input_feed(plan, _feat())
    assert sorted(feed) == ["language", "text_norm", "x", "x_length"]
    assert not {"speech", "speech_lengths", "textnorm"} & set(feed)


def test_feed_is_dtype_correct():
    plan = inference.resolve_feed_plan(MODELSCOPE_INPUTS)
    feed = inference.build_input_feed(plan, _feat())
    assert feed["x"].dtype == np.float32
    assert feed["x"].shape == (1, 34, 560)
    for name in ("x_length", "language", "text_norm"):
        assert feed[name].dtype == np.int32, name
        assert feed[name].shape == (1,), name


def test_length_tensor_counts_frames_of_a_batched_tensor():
    plan = inference.resolve_feed_plan(MODELSCOPE_INPUTS)
    assert int(inference.build_input_feed(plan, _feat(frames=57))["x_length"][0]) == 57


def test_length_tensor_also_accepts_an_unbatched_tensor():
    plan = inference.resolve_feed_plan(MODELSCOPE_INPUTS)
    assert int(inference.build_input_feed(plan, _feat().reshape(34, 560))["x_length"][0]) == 34


def test_language_and_textnorm_values():
    plan = inference.resolve_feed_plan(MODELSCOPE_INPUTS)
    feed = inference.build_input_feed(plan, _feat(), language="zh")
    assert int(feed["language"][0]) == inference.LANG_MAP["zh"]
    assert int(feed["text_norm"][0]) == 1
    # Unknown language falls back to auto (0) instead of raising mid-request.
    assert int(inference.build_input_feed(plan, _feat(), language="klingon")["language"][0]) == 0


def test_optional_inputs_are_dropped_when_the_graph_does_not_declare_them():
    """Some exports bake the language/ITN prompt in and drop those inputs."""
    plan = inference.resolve_feed_plan(["x", "x_length"])
    assert plan == {"x": "x", "x_length": "x_length"}
    assert sorted(inference.build_input_feed(plan, _feat())) == ["x", "x_length"]


def test_feed_keys_follow_the_graph_for_the_other_export_too():
    """Kills a regression to keying the feed by our own logical names."""
    plan = inference.resolve_feed_plan(FUNASR_INPUTS)
    feed = inference.build_input_feed(plan, _feat())
    assert sorted(feed) == ["language", "speech", "speech_lengths", "textnorm"]


def test_infer_feeds_the_graph_names():
    """`infer` must go through the plan — a hardcoded dict here is the 502.

    Built via ``object.__new__`` so the test needs no onnxruntime and no model:
    the session is a stand-in that records what it was handed.
    """
    engine = object.__new__(inference.SenseVoiceONNX)
    engine.outputs = ["logits"]
    engine.feed_plan = inference.resolve_feed_plan(MODELSCOPE_INPUTS)
    engine.preprocess = lambda _path: _feat()

    seen: dict[str, object] = {}

    class RecordingSession:
        def run(self, outputs, feed):
            seen["outputs"] = outputs
            seen["feed"] = feed
            return [np.zeros((1, 34, 25055), dtype=np.float32)]

    engine.session = RecordingSession()
    engine.infer("ignored.wav", language="zh")

    assert seen["outputs"] == ["logits"]
    assert sorted(seen["feed"]) == ["language", "text_norm", "x", "x_length"]
    assert not {"speech", "speech_lengths", "textnorm"} & set(seen["feed"])


@pytest.mark.parametrize("declared", [[], ["speech"], ["x"], ["logits"], ["feats", "lens"]])
def test_missing_essential_input_fails_at_load_time(declared):
    """Fail once when the model loads, not on every transcription request."""
    with pytest.raises(RuntimeError) as excinfo:
        inference.resolve_feed_plan(declared)
    assert "missing required input" in str(excinfo.value)


def test_unfillable_extra_input_is_flagged(caplog):
    with caplog.at_level(logging.WARNING, logger="sensevoice_inference_under_test"):
        plan = inference.resolve_feed_plan([*MODELSCOPE_INPUTS, "speaker_embedding"])
    assert "x" in plan
    assert "speaker_embedding" in caplog.text


def test_essentials_are_the_two_audio_tensors():
    assert inference._ESSENTIAL_INPUTS == ("x", "x_length")
    assert set(inference._ESSENTIAL_INPUTS) <= set(inference._INPUT_ALIASES)
