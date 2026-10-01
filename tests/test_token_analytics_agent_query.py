"""query_agent_tokens: per-agent cache-hit / cache-miss / output breakdown.

The statistics page reads this via the token_analytics plugin's
``view=agent_tokens`` dispatch. The rows in ``token_snapshots`` hold *cumulative*
counters, so the query reconstructs per-call deltas with LAG(); these tests pin
that reconstruction, the cache-miss split, the model breakdown and the
window lookback that keeps the first in-window row's delta correct.
"""

import importlib.util
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = REPO_ROOT / "src" / "plugins" / "token_analytics"


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, PLUGIN_DIR / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


query = _load("ta_query_under_test", "query.py")
storage = _load("ta_storage_under_test", "storage.py")

_INSERT = (
    "INSERT INTO token_snapshots ("
    "timestamp, agent_id, model, session_id, "
    "cumul_input_tokens, cumul_output_tokens, cumul_total_tokens, cumul_requests, "
    "cumul_cache_read_tokens, cumul_cache_creation_tokens"
    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)


def _row(ts: str, agent: str, model: str, inp: int, out: int, req: int, cache_read: int = 0):
    return (ts, agent, model, "s1", inp, out, inp + out, req, cache_read, 0)


def _make_db(tmp_path, rows) -> str:
    db_path = tmp_path / "analytics.db"
    conn = sqlite3.connect(db_path)
    conn.execute(storage._CREATE_SNAPSHOTS_TABLE)
    conn.execute(storage._CREATE_TOOL_USAGE_TABLE)
    conn.executemany(_INSERT, rows)
    conn.commit()
    conn.close()
    return str(db_path)


def test_splits_input_into_cache_hit_and_miss(tmp_path):
    db = _make_db(
        tmp_path,
        [
            _row("2026-05-01T00:00:00+00:00", "a1", "m1", 100, 10, 1, 40),
            _row("2026-05-01T01:00:00+00:00", "a1", "m1", 300, 30, 2, 140),
            _row("2026-05-01T02:00:00+00:00", "a1", "m1", 600, 60, 3, 240),
        ],
    )

    out = query.query_agent_tokens(db, time_range="all", agent_id="a1")

    # per-call deltas: input 100/200/300, output 10/20/30, cache_read 40/100/100
    assert out["summary"] == {
        "input": 600,
        "output": 60,
        "cache_read": 240,
        "cache_miss": 360,
        "total": 660,
        "requests": 3,
    }
    assert len(out["timeline"]) == 1
    bucket = out["timeline"][0]
    assert bucket["input_hit"] == 240
    assert bucket["input_miss"] == 360
    assert bucket["output"] == 60
    assert out["models"] == ["m1"]
    assert out["by_model"][0] == {
        "model": "m1",
        "input": 600,
        "output": 60,
        "cache_read": 240,
        "cache_miss": 360,
        "total": 660,
        "requests": 3,
    }


def test_tz_offset_shifts_day_buckets(tmp_path):
    db = _make_db(tmp_path, [_row("2026-05-01T23:30:00+00:00", "a1", "m1", 100, 10, 1)])

    utc = query.query_agent_tokens(db, time_range="all", agent_id="a1")
    assert [p["bucket"] for p in utc["timeline"]] == ["2026-05-01"]

    # 23:30 UTC is 07:30 the next day in UTC+8 — buckets must follow local time,
    # otherwise a Chinese viewer's "day" runs 08:00→08:00 local.
    local = query.query_agent_tokens(db, time_range="all", agent_id="a1", tz_offset_minutes=480)
    assert [p["bucket"] for p in local["timeline"]] == ["2026-05-02"]
    assert local["summary"] == utc["summary"]
    assert local["meta"]["tz_offset_minutes"] == 480


def test_clamp_tz_offset():
    clamp = query._clamp_tz_offset
    assert clamp("480") == 480
    assert clamp(-330) == -330
    assert clamp("nonsense") == 0
    assert clamp(None) == 0
    assert clamp(99999) == 1440
    assert clamp(-99999) == -1440


def test_window_lookback_keeps_first_row_delta_correct(tmp_path):
    now = datetime.now(timezone.utc)
    db = _make_db(
        tmp_path,
        [
            _row((now - timedelta(hours=25)).isoformat(), "a1", "m1", 1000, 0, 1, 500),
            _row((now - timedelta(hours=2)).isoformat(), "a1", "m1", 1200, 100, 2, 700),
            _row((now - timedelta(hours=1)).isoformat(), "a1", "m1", 1500, 140, 3, 900),
        ],
    )

    out = query.query_agent_tokens(db, time_range="24h", agent_id="a1")

    # The 25h-old row is the lookback: it is excluded from the results, but the
    # older in-window row must still cost (1200-1000)=200 input, not 1200.
    assert out["summary"] == {
        "input": 500,
        "output": 140,
        "cache_read": 400,
        "cache_miss": 100,
        "total": 640,
        "requests": 2,
    }
    assert sum(p["input_hit"] for p in out["timeline"]) == 400
    assert sum(p["input_miss"] for p in out["timeline"]) == 100
    assert sum(p["output"] for p in out["timeline"]) == 140


def test_model_breakdown_and_filter(tmp_path):
    db = _make_db(
        tmp_path,
        [
            _row("2026-05-02T00:00:00+00:00", "a2", "m1", 100, 10, 1),
            _row("2026-05-02T01:00:00+00:00", "a2", "m2", 500, 50, 2),
        ],
    )

    out = query.query_agent_tokens(db, time_range="all", agent_id="a2")
    assert out["models"] == ["m2", "m1"]  # ordered by volume
    assert {m["model"]: m["total"] for m in out["by_model"]} == {"m1": 110, "m2": 440}

    filtered = query.query_agent_tokens(db, time_range="all", agent_id="a2", model="m1")
    assert filtered["models"] == ["m1"]
    assert filtered["summary"]["input"] == 100
    assert filtered["summary"]["output"] == 10
    assert filtered["summary"]["total"] == 110
    assert filtered["summary"]["requests"] == 1


def test_other_agents_are_not_counted(tmp_path):
    db = _make_db(
        tmp_path,
        [
            _row("2026-05-03T00:00:00+00:00", "a1", "m1", 100, 10, 1),
            _row("2026-05-03T00:01:00+00:00", "a3", "m1", 900, 90, 5),
        ],
    )

    out = query.query_agent_tokens(db, time_range="all", agent_id="a3")
    assert out["summary"]["requests"] == 1
    assert out["summary"]["total"] == 990


def test_missing_db_and_blank_agent_return_zeros(tmp_path):
    missing = query.query_agent_tokens(str(tmp_path / "nope.db"), "24h", "a1")
    assert missing["summary"]["total"] == 0
    assert missing["timeline"] == []

    db = _make_db(tmp_path, [_row("2026-05-04T00:00:00+00:00", "a1", "m1", 100, 10, 1)])
    blank = query.query_agent_tokens(db, "all", "")
    assert blank["summary"]["requests"] == 0
    assert blank["models"] == []


def test_query_data_dispatch_reads_workspace_db(tmp_path, monkeypatch):
    # query_data resolves <workspace>/data/plugins/token_analytics/analytics.db
    ws = tmp_path / "ws"
    db_dir = ws / "data" / "plugins" / "token_analytics"
    db_dir.mkdir(parents=True)
    _make_db(db_dir, [_row("2026-05-05T00:00:00+00:00", "a1", "m1", 200, 20, 1, 50)])

    monkeypatch.setenv("OPENSQUAD_WORKSPACE", str(ws))
    out = query.query_data(
        str(tmp_path / "install"),
        {"view": "agent_tokens", "range": "all", "agent_id": "a1", "tz_offset": "480"},
    )

    assert out["summary"]["total"] == 220
    assert out["meta"]["agent_id"] == "a1"
    assert out["meta"]["tz_offset_minutes"] == 480

    # The default (non agent_tokens) path must still be the dashboard shape.
    dashboard = query.query_data(str(tmp_path / "install"), {"range": "24h"})
    assert "by_agent" in dashboard
    assert "timeline_by_model" in dashboard


def test_database_is_not_mutated(tmp_path):
    db = _make_db(tmp_path, [_row("2026-05-06T00:00:00+00:00", "a1", "m1", 100, 10, 1, 0)])
    before = os.path.getsize(db)
    query.query_agent_tokens(db, time_range="all", agent_id="a1")

    conn = sqlite3.connect(db)
    count = conn.execute("SELECT COUNT(*) FROM token_snapshots").fetchone()[0]
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    conn.close()

    assert count == 1
    assert "_ta_deltas" not in tables  # the delta table stays connection-local
    assert os.path.getsize(db) >= before
