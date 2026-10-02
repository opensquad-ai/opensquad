"""remote_members: which paired machine an @ai account was created from.

The member list needs to tell a remotely-joined agent from a local one. This store
is the source of that fact; an account with no record is simply local, so every
account that existed before this feature is unaffected.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[1] / "src"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from opensquad import remote_members as rm  # noqa: E402
from opensquad.system_config import syscfg  # noqa: E402


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(syscfg, "workspace_data_dir", lambda *parts: str(tmp_path / "data" / "_".join(parts)))
    return rm


def test_a_remembered_account_is_remote_with_its_machine_name(store):
    store.remember("coder-001@ai", "peer_abc", "office-pc")

    assert store.is_remote("coder-001@ai") is True
    assert store.label("coder-001@ai") == "office-pc"


def test_an_unknown_account_is_not_remote(store):
    assert store.is_remote("local-001@ai") is False
    assert store.label("local-001@ai") == ""


def test_email_lookup_is_case_insensitive(store):
    store.remember("Coder-001@AI", "peer_abc", "office-pc")

    assert store.is_remote("coder-001@ai") is True


def test_remembering_twice_refreshes_rather_than_stacks(store):
    store.remember("coder-001@ai", "peer_abc", "old-name")
    store.remember("coder-001@ai", "peer_abc", "new-name")

    assert store.label("coder-001@ai") == "new-name"
    assert len(store.all_origins()) == 1


def test_an_empty_email_is_ignored(store):
    assert store.remember("", "peer_abc", "office-pc") == {}
    assert store.all_origins() == {}


def test_a_peer_without_a_name_is_still_remote(store):
    store.remember("coder-001@ai", "peer_abc", "")

    assert store.is_remote("coder-001@ai") is True
    assert store.label("coder-001@ai") == ""
