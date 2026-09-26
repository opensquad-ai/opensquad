"""ASR audio conversion must not require a system ffmpeg.

The browser hands the built-in ASR webm/opus (that is what ``MediaRecorder``
produces), while SenseVoice and Whisper both need 16 kHz PCM. So the ffmpeg
lookup sits on the *normal* path of every microphone feature, and the Agent
Python that runs the plugin services has no ffmpeg on PATH.

What this file locks:

  R1  the shared resolver in ``plugins/_service_runtime.py`` checks the
      ``OPENSQUAD_FFMPEG`` override, then PATH, then the ``imageio-ffmpeg``
      wheel's bundled static build — and raises naming every location tried;
  R2  both ASR plugins *declare* ``imageio-ffmpeg`` in ``dependencies.pip``.
      That declaration is the whole mechanism: the launcher installs declared
      deps into the Agent Python on service start
      (``process_manager._install_dependencies``), which is how the resolver's
      last branch can succeed on a machine with no system ffmpeg;
  R3  the SenseVoice service converts through that resolver and never falls
      back to a bare ``shutil.which("ffmpeg")`` — the exact line that produced
      ``RuntimeError: ffmpeg not found on PATH`` for every webm upload;
  R4  the agent-side converter (``audio/stepfun_asr._ffmpeg_to_wav``) follows
      the same order and stays degraded (returns None, original bytes) instead
      of raising when no ffmpeg exists anywhere.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import types
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_RUNTIME_HELPER = _ROOT / "src" / "plugins" / "_service_runtime.py"
_SENSEVOICE_SERVICE = _ROOT / "src" / "plugins" / "sensevoice" / "service" / "service.py"

_FAKE_EXE = types.SimpleNamespace(get_ffmpeg_exe=lambda: "/from/imageio_ffmpeg/ffmpeg-7.1.exe")


def _load_runtime_helper():
    """Load ``plugins/_service_runtime.py`` by path (services import it loosely)."""
    spec = importlib.util.spec_from_file_location("_service_runtime_under_test", _RUNTIME_HELPER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def helper(monkeypatch):
    """Resolver module with a clean environment: no override, no ffmpeg on PATH."""
    monkeypatch.delenv("OPENSQUAD_FFMPEG", raising=False)
    monkeypatch.setattr("shutil.which", lambda _name: None)
    monkeypatch.setitem(sys.modules, "imageio_ffmpeg", None)  # import raises → no bundled build
    return _load_runtime_helper()


def _touch(path: Path) -> str:
    path.write_bytes(b"\x00")
    return str(path)


# ── R1 ─────────────────────────────────────────────────────────────────────


def test_override_wins_over_path_and_bundled_build(helper, tmp_path, monkeypatch):
    override = _touch(tmp_path / "custom-ffmpeg.exe")
    on_path = _touch(tmp_path / "path-ffmpeg.exe")
    monkeypatch.setenv("OPENSQUAD_FFMPEG", override)
    monkeypatch.setattr("shutil.which", lambda _name: on_path)
    monkeypatch.setitem(sys.modules, "imageio_ffmpeg", _FAKE_EXE)

    assert helper.ffmpeg_executable() == override


def test_falls_back_to_path_when_no_override(helper, tmp_path, monkeypatch):
    on_path = _touch(tmp_path / "path-ffmpeg.exe")
    monkeypatch.setattr("shutil.which", lambda name: on_path if name == "ffmpeg" else None)
    monkeypatch.setitem(sys.modules, "imageio_ffmpeg", _FAKE_EXE)

    assert helper.ffmpeg_executable() == on_path


def test_falls_back_to_the_imageio_ffmpeg_bundled_build(helper, tmp_path, monkeypatch):
    """The branch that makes this work with no system ffmpeg installed."""
    bundled = _touch(tmp_path / "ffmpeg-win-x86_64-v7.1.exe")
    monkeypatch.setitem(
        sys.modules,
        "imageio_ffmpeg",
        types.SimpleNamespace(get_ffmpeg_exe=lambda: str(bundled)),
    )

    assert helper.ffmpeg_executable() == str(bundled)


def test_import_error_from_imageio_ffmpeg_is_not_fatal(helper, monkeypatch):
    """A broken/absent wheel must read as 'not there', not as a crash."""

    def _boom(**_kwargs):
        raise RuntimeError("no bundled binary for this platform")

    monkeypatch.setitem(sys.modules, "imageio_ffmpeg", types.SimpleNamespace(get_ffmpeg_exe=_boom))

    with pytest.raises(RuntimeError, match="OPENSQUAD_FFMPEG"):
        helper.ffmpeg_executable()


def test_error_names_every_location_it_tried(helper):
    with pytest.raises(RuntimeError) as err:
        helper.ffmpeg_executable()

    message = str(err.value)
    assert "OPENSQUAD_FFMPEG" in message
    assert "imageio-ffmpeg" in message


def test_expose_ffmpeg_on_path_prepends_without_duplicating(helper, tmp_path, monkeypatch):
    bundled = _touch(tmp_path / "ffmpeg.exe")
    monkeypatch.setitem(sys.modules, "imageio_ffmpeg", types.SimpleNamespace(get_ffmpeg_exe=lambda: str(bundled)))
    monkeypatch.setenv("PATH", "C:\\windows")

    assert helper.expose_ffmpeg_on_path() == str(bundled)
    assert str(tmp_path) in os.environ["PATH"]

    # Second call must not stack the same directory again.
    helper.expose_ffmpeg_on_path()
    assert os.environ["PATH"].split(os.pathsep).count(str(tmp_path)) == 1


def test_expose_ffmpeg_on_path_is_none_when_nothing_resolves(helper):
    assert helper.expose_ffmpeg_on_path() is None


# ── R2 ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("plugin", ["sensevoice", "whisper"])
def test_asr_plugin_declares_the_bundled_ffmpeg_dependency(plugin):
    manifest = json.loads((_ROOT / "src" / "plugins" / plugin / "plugin.json").read_text(encoding="utf-8"))
    pip_deps = manifest["dependencies"]["pip"]

    assert "imageio-ffmpeg" in pip_deps, (
        f"{plugin}/plugin.json must declare imageio-ffmpeg — the launcher installs declared "
        "deps into the Agent Python, which is the only ffmpeg the service can reach"
    )


def test_import_name_is_the_one_the_runtime_checks():
    """process_manager maps the pip name through pkg_import_map before probing."""
    mapping = json.loads((_ROOT / "src" / "opensquad" / "launcher" / "pkg_import_map.json").read_text(encoding="utf-8"))

    assert mapping["imageio-ffmpeg"] == "imageio_ffmpeg"


# ── R3 ─────────────────────────────────────────────────────────────────────


def _service_source() -> str:
    return _SENSEVOICE_SERVICE.read_text(encoding="utf-8")


def test_sensevoice_service_uses_the_shared_resolver():
    source = _service_source()

    assert "from plugins._service_runtime import ffmpeg_executable" in source
    assert "_runtime_ffmpeg()" in source


def test_sensevoice_service_has_no_bare_ffmpeg_lookup():
    """`shutil.which("ffmpeg")` is what broke every webm upload."""
    source = _service_source()

    assert 'shutil.which("ffmpeg")' not in source
    assert "shutil.which('ffmpeg')" not in source


# ── R4 ─────────────────────────────────────────────────────────────────────


@pytest.fixture
def stepfun(monkeypatch):
    from opensquad.audio import stepfun_asr

    monkeypatch.delenv("OPENSQUAD_FFMPEG", raising=False)
    return stepfun_asr


def test_agent_side_resolver_falls_back_to_bundled_build(stepfun, tmp_path, monkeypatch):
    bundled = _touch(tmp_path / "ffmpeg.exe")
    monkeypatch.setattr("shutil.which", lambda _name: None)
    monkeypatch.setitem(sys.modules, "imageio_ffmpeg", types.SimpleNamespace(get_ffmpeg_exe=lambda: str(bundled)))

    assert stepfun._resolve_ffmpeg() == str(bundled)


def test_agent_side_conversion_degrades_instead_of_raising(stepfun, monkeypatch):
    monkeypatch.setattr(stepfun, "_resolve_ffmpeg", lambda: None)

    assert stepfun._ffmpeg_to_wav("whatever.webm") is None
