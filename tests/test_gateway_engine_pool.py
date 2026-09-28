"""The gateway engine must not inherit SQLAlchemy's aiosqlite default pool.

Up to SQLAlchemy 2.0.37 a file-backed ``sqlite+aiosqlite`` engine gets
``NullPool``, which rejects ``pool_size`` / ``max_overflow`` / ``pool_timeout``
with ``TypeError`` — raised while ``app.database`` is imported, so the gateway
process exits 1 and ``opensquad start`` never binds port 9555.  2.0.38 changed
the default to ``AsyncAdaptedQueuePool``, but ``sqlalchemy>=2.0.0`` is satisfied
by any 2.0.x, so a machine that already had an older one keeps it and
``pip install opensquad`` never upgrades it.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import AsyncAdaptedQueuePool, NullPool

DATABASE_PY = Path(__file__).resolve().parents[1] / "src" / "opensquad" / "gateway" / "backend" / "app" / "database.py"

TUNING_ARGS = ("pool_size", "max_overflow", "pool_timeout")


def _engine_kwargs() -> dict[str, ast.expr]:
    tree = ast.parse(DATABASE_PY.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == "create_async_engine":
                return {kw.arg: kw.value for kw in node.keywords if kw.arg}
    raise AssertionError("create_async_engine call not found in database.py")


def test_pool_class_is_pinned_explicitly():
    kwargs = _engine_kwargs()
    assert isinstance(kwargs.get("poolclass"), ast.Name), (
        "poolclass must be pinned: the aiosqlite dialect default is NullPool on "
        "SQLAlchemy <= 2.0.37, which rejects the pool tuning args"
    )
    assert kwargs["poolclass"].id == "AsyncAdaptedQueuePool"
    for name in TUNING_ARGS:
        assert name in kwargs, f"{name} belongs to the queue pool and must stay"


def test_pinned_kwargs_build_a_real_engine(tmp_path):
    kwargs = {name: ast.literal_eval(value) for name, value in _engine_kwargs().items() if name in TUNING_ARGS}
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'chat.db'}",
        poolclass=AsyncAdaptedQueuePool,
        **kwargs,
    )
    assert isinstance(engine.sync_engine.pool, AsyncAdaptedQueuePool)


def test_tuning_args_are_fatal_under_null_pool(tmp_path):
    """The premise of the pin: these args cannot survive a NullPool dialect."""
    with pytest.raises(TypeError):
        create_async_engine(
            f"sqlite+aiosqlite:///{tmp_path / 'chat.db'}",
            poolclass=NullPool,
            **{name: 8 for name in TUNING_ARGS},
        )
