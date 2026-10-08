"""群折叠 — GET /groups 带上 folded，PUT /groups/{id} 读写的是当前用户那一行。

折叠是 **per-user** 偏好（存 user_group_settings.folded，不是 groups 行），所以
两件事必须成立：列表按调用者自己的 settings 返回；写入时键是 `current_user.id`。
后者尤其容易悄悄坏掉 —— 直接把字段写到 Group 上也能"跑通"，但会把一个人的折叠
变成所有人的折叠。

`update_group` 还有一处只在"该用户还没有 settings 行"时才走到的分支（新建一行），
以及一个 **不能靠 re-query 拿值** 的响应拼装（刚 `db.add` 的行还没 flush，re-query
查不到）—— 所以这两条路径各配了一个用例。
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

_BACKEND_DIR = Path(__file__).resolve().parents[1] / "src" / "opensquad" / "gateway" / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from opensquad.gateway.backend.app import api as gw  # noqa: E402


class _Scalars:
    def __init__(self, values):
        self._values = values

    def all(self):
        return self._values


class _Result:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def scalars(self):
        return _Scalars(self._value)


class _DB:
    """按顺序吐出预置结果；只记录 add / commit。"""

    def __init__(self, results):
        self._results = list(results)
        self.added: list = []
        self.commits = 0

    async def execute(self, *args, **kwargs):
        assert self._results, "more queries than the test prepared"
        return _Result(self._results.pop(0))

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1


def _handler(path: str, method: str):
    for route in gw.router.routes:
        if getattr(route, "path", "") == path and method in (getattr(route, "methods", set()) or set()):
            return route.endpoint
    raise AssertionError(f"{method} {path} not found")


@pytest.fixture(autouse=True)
def _no_launcher_probe(monkeypatch):
    """`_build_email_agent_id_map` 会去 :9600 探真 launcher —— 测试里不碰网络。"""

    async def empty():
        return {}

    monkeypatch.setattr(gw, "_build_email_agent_id_map", empty)


def _group(**over):
    base = dict(
        id="g1",
        name="开发协作组",
        avatar=None,
        description="d",
        is_private=False,
        notification_sound_enabled=True,
        created_by="7",
        created_at=datetime(2026, 10, 6, 12, 0, 0),
    )
    base.update(over)
    return SimpleNamespace(**base)


def _settings(folded=False, **over):
    base = dict(
        user_id="7",
        group_id="g1",
        unread_count=3,
        has_unread_mention=False,
        notification_enabled=True,
        folded=folded,
    )
    base.update(over)
    return SimpleNamespace(**base)


def _put(db, body):
    return asyncio.run(
        _handler("/groups/{group_id}", "PUT")(
            group_id="g1",
            group_update=gw.GroupUpdate(**body),
            current_user=SimpleNamespace(id="7", name="ss"),
            db=db,
        )
    )


def test_update_writes_folded_to_the_callers_own_settings_row():
    settings = _settings(folded=False)
    # 1 group, 2 settings(one in the folded block, one for the response), 1 members
    db = _DB([_group(), settings, settings, []])

    res = _put(db, {"folded": True})

    assert settings.folded is True
    assert db.added == []  # 已有 settings 行 → 只改它，不再插一行
    assert db.commits == 1
    assert res.folded is True


def test_update_creates_the_settings_row_when_the_member_has_none():
    # 没有 settings 行（None），且 re-query 也查不到 → 响应必须用请求值兜底，
    # 否则刚 add 的那行还没 flush，用户看到的是"折叠了但状态没变"。
    db = _DB([_group(), None, None, []])

    res = _put(db, {"folded": True})

    assert len(db.added) == 1
    row = db.added[0]
    # 按类名而不是 isinstance：api.py 是 `from app.models import ...`（backend 在
    # sys.path 上），和本文件 `opensquad.gateway.backend.app.models` 是**两个**类对象。
    assert type(row).__name__ == "UserGroupSettings"
    assert (row.user_id, row.group_id, row.folded) == ("7", "g1", True)
    assert res.folded is True


def test_update_without_the_folded_key_leaves_it_alone():
    settings = _settings(folded=True)
    db = _DB([_group(), settings, []])

    res = _put(db, {"name": "改名"})

    assert settings.folded is True  # 没被 None 覆盖成 False
    assert db.added == []
    assert res.folded is True


def test_update_can_unfold():
    settings = _settings(folded=True)
    db = _DB([_group(), settings, settings, []])

    res = _put(db, {"folded": False})

    assert settings.folded is False
    assert res.folded is False


def test_the_group_list_carries_the_callers_folded_flag():
    db = _DB([[_group()], [_settings(folded=True)], []])

    items = asyncio.run(
        _handler("/groups", "GET")(
            current_user=SimpleNamespace(id="7"),
            db=db,
        )
    )

    assert len(items) == 1
    assert items[0].folded is True
    assert items[0].unread_count == 3


def test_a_group_without_settings_row_defaults_to_unfolded():
    db = _DB([[_group()], [], []])

    items = asyncio.run(
        _handler("/groups", "GET")(
            current_user=SimpleNamespace(id="7"),
            db=db,
        )
    )

    assert items[0].folded is False
