"""`opensquad web` must explain a dev server that never comes up.

Reported 2026-09-30: `opensquad dev` printed "Frontend port 5173 not ready" and
fell back to the Gateway static UI, with no hint why. The cause was that this
machine has **no `node` on PATH**: npm's own `Roaming\\npm\\npm.cmd` shim falls
back to bare `node` when there is no `node.exe` beside it, so it exited instantly
— and because the child is spawned detached with `stdout/stderr=DEVNULL`, that
error had nowhere to go. The CLI then waited out the full 90-second timeout.

Diagnosing it was only half the report. Node *was* installed — on the registered
PATH, hours after the terminal that ran the command had captured its own — so
refusing to start the dev server on a machine that can plainly run one is not a
fix. The failure is now confirmed against the folders Node actually lives in
before anyone is told to reinstall anything (R4).

Four properties are worth pinning, in order of how much they cost a user:

  R1  an unrunnable npm is caught *before* spawning, and the message names the
      missing program (so there is no 90-second silence either way)
  R2  whatever the detached child does print reaches a log file, and the failure
      path tells the user where that file is
  R3  the decisions that were already right stay right: port open → nothing is
      spawned, no package.json → the existing message, dev server up → True
  R4  a `node` that is installed but missing from *this process's* PATH is found
      anyway, and the child is given a way to reach it — npm's own sibling, or
      the node folder prepended to its PATH. A stale terminal is not a broken
      machine

The port is polled by `runtime_boot._wait_port`, which uses *its own* `_port_open`
— both modules are patched here, otherwise the tests would connect to whatever
happens to be listening on 5173 on the machine running them.

Mutations verified:
  MR1  drop the `_npm_can_run` check                 → R1
  MR2  leave `stdout` at DEVNULL (no override)       → R2
  MR3  never print the log path on failure           → R2
  MR4  spawn before checking `_npm_can_run`          → R1
  MR5  skip the already-open port check              → R3
  MR6  stop at `_npm_can_run` (drop `_locate_node`)  → R4
  MR7  find node, then hand the child neither its sibling npm nor a PATH → R4
"""

from __future__ import annotations

import os
import subprocess
import tempfile

import pytest

from opensquad.cli import runtime_boot
from opensquad.cli.commands import web_cmd

ABSENT = object()


class FakePopen:
    """Stand-in for the dev-server child; exits at once unless kept alive."""

    instances: list[FakePopen] = []

    def __init__(self, cmd, cwd=None, **kwargs):
        self.cmd = list(cmd)
        self.cwd = cwd
        self.kwargs = kwargs
        self.pid = 4242
        self.alive = False
        self.returncode = 1
        FakePopen.instances.append(self)

    def poll(self):
        return None if self.alive else self.returncode


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """Start every test from "node exists only where the test puts it".

    ``_node_hint_dirs`` walks real folders and the registry, so a machine that
    happens to have Node installed would otherwise leak into the cases that are
    about there being none.
    """
    FakePopen.instances = []
    monkeypatch.setattr(web_cmd, "_node_hint_dirs", lambda: [])
    yield


@pytest.fixture
def frontend_tree(tmp_path, monkeypatch):
    """A package dir whose frontend has a package.json."""
    pkg = tmp_path / "pkg"
    front = pkg / "gateway" / "nexuschat-pro"
    front.mkdir(parents=True)
    (front / "package.json").write_text('{"name": "nexuschat-pro"}', encoding="utf-8")
    monkeypatch.setattr(web_cmd, "_package_dir", lambda: str(pkg))
    return front


@pytest.fixture
def ports_closed(monkeypatch):
    """Nothing is listening, in either module that asks."""
    monkeypatch.setattr(web_cmd, "_port_open", lambda host, port, timeout=0.4: False)
    monkeypatch.setattr(runtime_boot, "_port_open", lambda host, port, timeout=0.4: False)


@pytest.fixture
def found(monkeypatch):
    """Control what `shutil.which` resolves (npm / node on PATH or not)."""

    def _apply(mapping):
        def fake_which(name, path=None):
            value = mapping.get(name, ABSENT)
            return None if value is ABSENT or value is None else str(value)

        monkeypatch.setattr("shutil.which", fake_which)

    return _apply


@pytest.fixture
def spawn(monkeypatch):
    monkeypatch.setattr(web_cmd.subprocess, "Popen", FakePopen)
    return FakePopen


@pytest.fixture
def npm_ready(tmp_path, found):
    """npm and node both resolve; the log goes into a temp workspace."""
    npm = tmp_path / "Roaming" / "npm" / "npm.cmd"
    npm.parent.mkdir(parents=True, exist_ok=True)
    npm.write_text("@echo off\r\n", encoding="utf-8")
    found({"npm": npm, "node": tmp_path / "node.exe"})
    return npm


# ---------------------------------------------------------------- R1


def test_node_missing_is_reported_before_any_spawn(frontend_tree, ports_closed, found, spawn, capsys, tmp_path):
    """The failure this file exists for: npm resolves, node does not."""
    npm = tmp_path / "Roaming" / "npm" / "npm.cmd"
    npm.parent.mkdir(parents=True)
    npm.write_text("@echo off\r\n", encoding="utf-8")
    found({"npm": npm, "node": None})

    assert web_cmd._ensure_frontend(5173) is False

    err = capsys.readouterr().err
    assert "node is not on PATH" in err
    assert str(npm) in err
    assert "usual install folders" in err, "name where node was looked for, not only where it is absent"
    assert spawn.instances == [], "npm must not be spawned when it cannot run"


def test_npm_alone_is_not_enough(frontend_tree, ports_closed, found, spawn, capsys, tmp_path):
    """Nothing executable behind the name is reported as npm missing."""
    found({"npm": None, "node": None})

    assert web_cmd._ensure_frontend(5173) is False
    err = capsys.readouterr().err
    assert "npm not found" in err
    assert spawn.instances == []


def test_node_beside_npm_counts_as_available(tmp_path, found):
    """The shim prefers `<npm dir>\\node.exe`, so no PATH entry is needed."""
    npm_dir = tmp_path / "npm"
    npm_dir.mkdir()
    (npm_dir / "node.exe").write_text("", encoding="utf-8")
    found({"node": None})

    assert web_cmd._npm_can_run(str(npm_dir / "npm.cmd")) is True


def test_node_on_path_counts_as_available(tmp_path, found):
    found({"node": tmp_path / "node.exe"})
    assert web_cmd._npm_can_run(str(tmp_path / "elsewhere" / "npm.cmd")) is True


def test_neither_node_nor_side_by_side(tmp_path, found):
    found({"node": None})
    assert web_cmd._npm_can_run(str(tmp_path / "elsewhere" / "npm.cmd")) is False


def test_resolve_npm_never_returns_a_bare_name(tmp_path, found):
    """`''` means "not installed"; a bare "npm" would only fail later, oddly."""
    found({"npm": None})
    assert web_cmd._resolve_npm() == ""


# ---------------------------------------------------------------- R2


def test_child_output_reaches_a_log_and_the_user_is_told_where(
    frontend_tree, ports_closed, npm_ready, spawn, monkeypatch, capsys, tmp_path
):
    """A detached child has no console; the log file is its only channel."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    monkeypatch.setattr("opensquad.system_config.syscfg.get_workspace", lambda: str(ws), raising=False)

    assert web_cmd._ensure_frontend(5173) is False

    log_path = ws / "data" / "logs" / "frontend.log"
    assert log_path.is_file(), "the dev server must be pointed at a real file"
    assert "vite dev" in log_path.read_text(encoding="utf-8")

    err = capsys.readouterr().err
    assert "Dev-server output:" in err
    assert str(log_path) in err


def test_spawned_child_does_not_keep_stdout_at_devnull(
    frontend_tree, ports_closed, npm_ready, spawn, monkeypatch, tmp_path
):
    """`detach_popen_kwargs()` sets DEVNULL; overriding it is the whole fix."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    monkeypatch.setattr("opensquad.system_config.syscfg.get_workspace", lambda: str(ws), raising=False)

    web_cmd._ensure_frontend(5173)

    (child,) = spawn.instances
    assert child.kwargs["stdout"] is not subprocess.DEVNULL
    assert child.kwargs["stderr"] is subprocess.STDOUT
    assert child.cwd.endswith("nexuschat-pro")


def test_log_falls_back_to_temp_when_the_workspace_is_unusable(monkeypatch, tmp_path):
    """A missing or unwritable workspace must not swallow the log too."""
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    monkeypatch.setattr("opensquad.system_config.syscfg.get_workspace", lambda: str(blocker), raising=False)

    path = web_cmd._frontend_log_path()

    assert path
    assert path.startswith(tempfile.gettempdir())


def test_failure_still_reports_when_no_log_is_writable(
    frontend_tree, ports_closed, npm_ready, spawn, monkeypatch, capsys
):
    """Without a log the only honest thing left is to say the output was lost."""
    monkeypatch.setattr(web_cmd, "_frontend_log_path", lambda: "")

    assert web_cmd._ensure_frontend(5173) is False
    err = capsys.readouterr().err
    assert "output was discarded" in err


# ---------------------------------------------------------------- R4


def _node_name() -> str:
    """What `node` is called here — the code under test branches on this, so the fixtures must.

    Hard-coding `node.exe` is why these three cases passed on Windows and failed on Linux: the
    session looked for `node`, found nothing beside npm, and reported "node is not on PATH".
    """
    return web_cmd._node_exe_names()[0]


def _npm_name() -> str:
    """What npm's launcher is called here (`npm.cmd` on Windows)."""
    return "npm.cmd" if os.name == "nt" else "npm"


def _npm_shim(tmp_path):
    """npm as an earlier install left it: a shim with no node beside it."""
    npm = tmp_path / "Roaming" / "npm" / _npm_name()
    npm.parent.mkdir(parents=True, exist_ok=True)
    npm.write_text("@echo off\r\n", encoding="utf-8")
    return npm


def test_node_off_this_path_is_still_found_and_used(
    frontend_tree, ports_closed, found, spawn, monkeypatch, capsys, tmp_path
):
    """The regression behind the second report: node installed, PATH stale."""
    npm = _npm_shim(tmp_path)
    found({"npm": npm, "node": None})
    node_dir = tmp_path / "ai" / "nodejs24"
    node_dir.mkdir(parents=True)
    (node_dir / _node_name()).write_text("", encoding="utf-8")
    monkeypatch.setattr(web_cmd, "_node_hint_dirs", lambda: [str(node_dir)])

    web_cmd._ensure_frontend(5173)

    assert "node is not on PATH" not in capsys.readouterr().err
    (child,) = spawn.instances
    assert child.kwargs["env"]["PATH"].split(os.pathsep)[0] == str(node_dir)


def test_a_node_install_uses_its_own_npm_before_patching_path(
    frontend_tree, ports_closed, found, spawn, monkeypatch, tmp_path
):
    """`<node dir>\\npm.cmd` prefers the node beside it — nothing else needed."""
    npm = _npm_shim(tmp_path)
    found({"npm": npm, "node": None})
    node_dir = tmp_path / "nodejs"
    node_dir.mkdir()
    (node_dir / _node_name()).write_text("", encoding="utf-8")
    sibling = node_dir / _npm_name()
    sibling.write_text("@echo off\r\n", encoding="utf-8")
    monkeypatch.setattr(web_cmd, "_node_hint_dirs", lambda: [str(node_dir)])

    web_cmd._ensure_frontend(5173)

    (child,) = spawn.instances
    assert child.cmd[0] == str(sibling), "the found node brings its own npm along"
    assert "env" not in child.kwargs, "a self-sufficient npm needs no PATH override"


def test_finding_no_node_anywhere_still_spawns_nothing(frontend_tree, ports_closed, found, spawn, capsys, tmp_path):
    """Widening the search must not turn "cannot run npm" into an attempt."""
    npm = _npm_shim(tmp_path)
    found({"npm": npm, "node": None})

    assert web_cmd._ensure_frontend(5173) is False
    assert "node is not on PATH" in capsys.readouterr().err
    assert spawn.instances == []


def test_locate_node_prefers_the_shim_folder_over_path(tmp_path, found):
    node_dir = tmp_path / "npm"
    node_dir.mkdir()
    beside = node_dir / _node_name()
    beside.write_text("", encoding="utf-8")
    found({"node": tmp_path / "elsewhere" / _node_name()})

    assert web_cmd._locate_node(str(node_dir / _npm_name())) == str(beside)


def test_locate_node_returns_empty_when_there_is_none(tmp_path, found):
    found({"node": None})
    assert web_cmd._locate_node(str(tmp_path / "npm.cmd")) == ""


# ---------------------------------------------------------------- R3


def test_already_running_dev_server_skips_the_spawn(frontend_tree, found, spawn, monkeypatch):
    monkeypatch.setattr(web_cmd, "_port_open", lambda host, port, timeout=0.4: True)
    found({"npm": None, "node": None})  # must not even get as far as asking

    assert web_cmd._ensure_frontend(5173) is True
    assert spawn.instances == []


def test_missing_package_json_keeps_its_message(tmp_path, monkeypatch, ports_closed, found, spawn, capsys):
    empty = tmp_path / "other"
    (empty / "gateway" / "nexuschat-pro").mkdir(parents=True)
    monkeypatch.setattr(web_cmd, "_package_dir", lambda: str(empty))
    found({"npm": tmp_path / "npm.cmd", "node": tmp_path / "node.exe"})

    assert web_cmd._ensure_frontend(5173) is False
    assert "package.json not found" in capsys.readouterr().err
    assert spawn.instances == []


def test_dev_server_that_opens_the_port_is_accepted(frontend_tree, npm_ready, found, monkeypatch, capsys):
    """The success path: port closed at first, listening once the child is up."""
    FakePopen.instances = []

    def alive_child(cmd, cwd=None, **kwargs):
        child = FakePopen(cmd, cwd, **kwargs)
        child.alive = True
        return child

    monkeypatch.setattr(web_cmd.subprocess, "Popen", alive_child)
    monkeypatch.setattr(web_cmd, "_port_open", lambda host, port, timeout=0.4: False)

    asks = {"n": 0}

    def poll_once(host, port, timeout=0.4):
        asks["n"] += 1
        return asks["n"] >= 1  # the daemon-side poll sees it on its first check

    monkeypatch.setattr(runtime_boot, "_port_open", poll_once)

    assert web_cmd._ensure_frontend(5173) is True
    assert "Dev-server output" not in capsys.readouterr().err
