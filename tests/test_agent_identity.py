"""agent_identity: one @ai email belongs to one node.

The store decides whether a node may use an @ai account. Two machines with the
same email are the failure case this exists for; a client that sends no uid must
keep working, so every old install is unaffected.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[1] / "src"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from opensquad import agent_identity as ai  # noqa: E402
from opensquad.system_config import syscfg  # noqa: E402


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(syscfg, "workspace_data_dir", lambda *parts: str(tmp_path / "data" / "_".join(parts)))
    return ai


def test_the_first_node_owns_the_email(store):
    assert store.bind("coder-001@ai", "machine-a-coder") == "machine-a-coder"
    assert store.uid_for("coder-001@ai") == "machine-a-coder"


def test_a_second_node_is_refused_and_told_who_owns_it(store):
    store.bind("coder-001@ai", "machine-a-coder")

    allowed, owner = store.check("coder-001@ai", "machine-b-coder")

    assert allowed is False
    assert owner == "machine-a-coder"


def test_the_owner_is_allowed(store):
    store.bind("coder-001@ai", "machine-a-coder")

    allowed, owner = store.check("coder-001@ai", "machine-a-coder")

    assert allowed is True
    assert owner == "machine-a-coder"


def test_a_second_node_cannot_take_over_the_binding(store):
    store.bind("coder-001@ai", "machine-a-coder")

    assert store.bind("coder-001@ai", "machine-b-coder") == "machine-a-coder"
    assert store.uid_for("coder-001@ai") == "machine-a-coder"


def test_without_a_uid_nothing_is_claimed_or_enforced(store):
    """Legacy agents send no uid — they must keep working, and must not bind."""
    assert store.check("coder-001@ai", "") == (True, "")
    assert store.bind("coder-001@ai", "") == ""
    assert store.uid_for("coder-001@ai") == ""
    allowed, _ = store.check("coder-001@ai", "machine-b-coder")
    assert allowed is True  # nobody claimed it, so this node may


def test_emails_are_compared_case_insensitively(store):
    store.bind("Coder-001@AI", "machine-a-coder")

    allowed, owner = store.check("coder-001@ai", "machine-a-coder")

    assert allowed is True
    assert owner == "machine-a-coder"
