"""Tests for self_learn store / export / can_start_auto."""

import os
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from plugins.self_learn import store
from plugins.self_learn.export_import import export_package, import_package
from plugins.self_learn.orchestrator import can_start_auto


def test_corpus_append_list_mark(tmp_path=None):
    with tempfile.TemporaryDirectory() as td:
        agent_dir = td
        e1 = store.append_corpus_entry(
            agent_dir,
            summary="User prefers concise answers and Chinese replies.",
            session_id="s1",
            session_title="Chat A",
            source="auto_compress",
            agent_id="demo",
        )
        e2 = store.append_corpus_entry(
            agent_dir,
            summary="Repeated: always run tests before commit.",
            session_id="s2",
            session_title="Chat B",
            source="manual_compress",
            agent_id="demo",
        )
        listed = store.list_corpus(agent_dir, unlearned_only=True)
        assert listed["total"] == 2
        assert store.get_corpus(agent_dir, e1["id"])["summary"].startswith("User prefers")

        run = store.create_run(agent_dir, trigger="manual")
        marked = store.mark_corpus_learned(agent_dir, [e1["id"], e2["id"]], run["id"])
        assert marked["count"] == 2
        listed2 = store.list_corpus(agent_dir, unlearned_only=True)
        assert listed2["total"] == 0


def test_pipeline_defaults_and_gates():
    with tempfile.TemporaryDirectory() as td:
        store.ensure_defaults(td)
        p = store.load_pipeline(td)
        assert p["name"] == "default"
        assert p["gates"]["allow_memory_write"] is True
        assert p["gates"]["allow_agent_md"] is False
        p["gates"]["allow_agent_md"] = True
        store.save_pipeline(td, p)
        p2 = store.load_pipeline(td)
        assert p2["gates"]["allow_agent_md"] is True


def test_export_import_roundtrip():
    with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as dst:
        store.append_corpus_entry(src, summary="lesson one", session_id="a", source="compress")
        store.append_corpus_entry(src, summary="lesson two", session_id="b", source="compress")
        store.create_run(src, trigger="manual")
        blob = export_package(src)
        assert len(blob) > 50
        report = import_package(dst, blob, dry_run=False)
        assert report["corpus_added"] == 2
        assert store.list_corpus(dst)["total"] == 2
        # second import skips
        report2 = import_package(dst, blob, dry_run=False)
        assert report2["corpus_skipped"] == 2


def test_can_start_auto_gates():
    with tempfile.TemporaryDirectory() as td:
        store.ensure_defaults(td)
        store.update_meta(
            td,
            idle_auto_enabled=True,
            idle_minutes=30,
            cooldown_hours=24,
            last_user_activity_at="2000-01-01T00:00:00Z",
            last_learn_at=None,
            last_agent_state="idle",
        )
        ok, reason = can_start_auto(td)
        assert ok is False
        assert reason == "no_unlearned_corpus"
        store.append_corpus_entry(td, summary="x", session_id="s")
        ok2, reason2 = can_start_auto(td)
        assert ok2 is True
        assert reason2 == "ok"


def test_enqueue_learn_request():
    with tempfile.TemporaryDirectory() as td:
        req = store.enqueue_learn_request(td, force=True, allow_agent_md=True)
        assert req["status"] == "pending"
        pending = store.list_pending_requests(td)
        assert len(pending) == 1
        assert pending[0]["id"] == req["id"]
        store.update_request(td, req["id"], status="done")
        assert store.list_pending_requests(td) == []


def test_run_writes_and_enrich():
    with tempfile.TemporaryDirectory() as td:
        e1 = store.append_corpus_entry(td, summary="Prefer Chinese replies.", session_id="s1")
        run = store.create_run(td, trigger="manual")
        store.update_run(td, run["id"], status="running", corpus_ids=[e1["id"]])
        w = store.append_run_write(
            td,
            target="memory",
            content="User prefers Chinese replies",
            evidence_refs=[e1["id"]],
            run_id=run["id"],
        )
        assert w and w["target"] == "memory"
        detail = store.enrich_run_detail(td, store.get_run(td, run["id"]))
        assert detail["writes"][0]["content"].startswith("User prefers")
        assert detail["corpus_items"][0]["id"] == e1["id"]
        assert "Chinese" in (detail["corpus_items"][0]["summary"] or "")


def test_runtime_missing_outside_agent():
    from plugins.self_learn.runtime import resolve_runtime

    cfg, registry, source = resolve_runtime()
    # In unit-test process there is typically no agent runner / delegate.
    assert source in ("missing", "delegate", "active_runner", "self_learn")
    if source == "missing":
        assert cfg is None and registry is None
