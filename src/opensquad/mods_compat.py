"""Static compatibility scanner for Claude Code **mods**.

A mod is a plugin directory whose behaviour lives in JavaScript hooks:

    my-mod/
    ├── .claude-plugin/plugin.json     # identity (name / version / description / author)
    └── hooks/
        ├── hooks.json                 # {"modules": ["./guard.mjs"]}
        └── guard.mjs                  # export function register(on, options)

The API a mod may use (events, ``$`` members, elements, budgets) is Claude Code's,
and this host serves only a **subset** of it.  The subset — and the reason for every
`degraded`/`refused` row — is decided in ``docs/claude-code-mods-compat-v0.md``.

This module answers one question without executing any JavaScript: *given what this
mod's source references, could it run here?*  It is the offline gate the design doc
asks for (``claude plugin validate``'s equivalent), and it feeds the Mods page in
System Settings.

Verdicts
--------
``runnable``  every event and ``$`` member it references is served.
``partial``   at least one ``degraded`` reference, none refused — it will run with
              the documented differences.
``blocked``   at least one ``refused`` reference (or a TypeScript module that this
              host will not transpile).  ``blocked_by`` names the gap.
``unknown``   no hooks were recognised in the source (dynamic registration, or a
              module we could not read).

Honesty rules (they matter more than the verdict itself):

* ``blocked``/``partial`` always carry the exact list that caused it, so the UI can
  name what is missing instead of saying "unsupported".
* The verdict is about **compatibility, not availability**.  A ``runnable`` mod still
  cannot run until the mod host ships — callers must render that separately (the API
  returns ``host.available = False`` today).

Everything here is pure file reading: no network, no subprocess, no imports of the
mod's code.  Paths are injected so tests never touch the real workspace.

NOTE: the tables below are the machine-readable form of the design doc's §3/§4.
Change them together.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

# ── Verdicts ───────────────────────────────────────────────────────────────

SERVED = "served"
DEGRADED = "degraded"
REFUSED = "refused"

_RANK = {SERVED: 0, DEGRADED: 1, REFUSED: 2}

# ── Mods 事件表 (docs/claude-code-mods-compat-v0.md §3) ─────────────────────
# name -> (verdict, our seam / why)

_EVENT_TABLE: dict[str, tuple[str, str]] = {
    # served
    "tool.call": (SERVED, "on_before_tool / on_after_tool / on_tool_error — 可观察、可 deny、可改写结果"),
    "turn.start": (SERVED, "on_task_start"),
    "turn.complete": (SERVED, "on_task_complete"),
    "command.run": (SERVED, "斜杠命令体系 (cli/slash_commands.py)"),
    # degraded
    "prompt.submit": (DEGRADED, "on_message_received — 粒度是整条消息，无 context 块与 origin"),
    "session.start": (DEGRADED, "宿主启动时发一次 — 我们是 agent 级不是 session 级，一个 agent 承载多会话"),
    "session.end": (DEGRADED, "宿主销毁时发一次（fire-and-forget）— 同上，粒度是 agent 而非会话"),
    "turn.step": (DEGRADED, "每轮工具调用后发一次；不是 Claude Code 的 async generator 流式语义"),
    "command.describe": (DEGRADED, "静态描述可给，动态钩子无对应"),
    "ui.render": (DEGRADED, "只开少数具名插槽（见 §5 插槽清单）"),
    "telemetry.log": (DEGRADED, "EventBus — 字段集合不同"),
    "telemetry.mark": (DEGRADED, "EventBus — 字段集合不同"),
    "tool.register": (DEGRADED, "能力以 $.tool.register 形式提供，事件形式不给"),
    "skill.prompt": (DEGRADED, "skills 体系 — 语义相近字段不同"),
    "config.set": (DEGRADED, "syscfg + config_schema — 不是事件钩子"),
    "config.describe": (DEGRADED, "syscfg + config_schema — 不是事件钩子"),
}

# refusals that are worth naming (a missing key falls back to _DEFAULT_REFUSED_EVENT)
_EVENT_REFUSAL_REASONS: dict[str, str] = {
    "prompt.edit": "50ms 热路径 + 我们无行内编辑器协议",
    "prompt.compose": "没有可组合的提示词管线",
    "prompt.section": "没有可组合的提示词管线",
    "prompt.context": "没有可组合的提示词管线",
    "prompt.attachment": "没有可组合的提示词管线",
    "prompt.fill": "没有可组合的提示词管线",
    "prompt.suggest": "没有可组合的提示词管线",
    "tool.check": "工具清单由 ToolRegistry 静态决定，无逐次权限判定回调",
    "tool.describe": "工具清单由 ToolRegistry 静态决定",
    "tool.list": "工具清单由 ToolRegistry 静态决定",
    "agent.offer": "无 agent 编排协议",
    "agent.spawn": "无 agent 编排协议",
    "plugin.register": "引擎级扩展点，v0 不开",
    "engine.create": "引擎级扩展点，v0 不开",
    "attribution.text": "无对应概念",
    "ui.press": "需要输入接管（铁律 2）",
    "ui.input": "需要输入接管（铁律 2）",
    "ui.select": "需要输入接管（铁律 2）",
    "ui.focus": "需要焦点管理（铁律 2）",
    "ui.scroll": "需要滚动接管（铁律 2）",
    "ui.message": "需要输入接管（铁律 2）",
    "ui.close": "需要输入接管",
    "ui.fault": "无对应概念",
    "session.compact": "会话生命周期不在我们的 hook 面上",
    "session.receive": "会话生命周期不在我们的 hook 面上",
    "session.send": "会话生命周期不在我们的 hook 面上",
    "session.append": "会话生命周期不在我们的 hook 面上",
    "session.attach": "会话生命周期不在我们的 hook 面上",
    "session.detach": "会话生命周期不在我们的 hook 面上",
    "session.measure": "会话生命周期不在我们的 hook 面上",
}
_DEFAULT_REFUSED_EVENT = "v0 未收录此事件（矩阵外一律拒绝，加载时报告）"

# ── $ 成员表 (docs/claude-code-mods-compat-v0.md §4) ────────────────────────
# "namespace.method" -> (verdict, note)

_DOLLAR_TABLE: dict[str, tuple[str, str]] = {
    # served — plugin / command
    "plugin.name": (SERVED, ""),
    "plugin.root": (SERVED, ""),
    "command.register": (SERVED, "写入 slash_commands 的运行时注册表；CLI/TUI 的补全与帮助立刻可见"),
    "command.list": (SERVED, "合并静态 COMMANDS 与 mod 注册项，并标注 source（quick-buttons 靠这个筛）"),
    "command.run": (
        SERVED,
        "宿主派发 `command.run` 给所有注册了该命令的 mod，返回其 verdict；用户侧入口见 `mod_command` 命令帧",
    ),
    # served — session facts (read-only)
    "session.id": (DEGRADED, "只给当前会话 id（由 hook 上下文带到宿主），没有 CC 的历史会话概念"),
    "session.cwd": (SERVED, "workspace 根目录（当前 agent 的工作区）"),
    "session.root": (DEGRADED, "未接入：与 cwd 同值（我们没有项目根/仓库根的分层）"),
    "session.model": (DEGRADED, "未接入：hook 上下文不带模型，需要 runner→插件的管道"),
    "session.turns": (DEGRADED, "未接入：同上；轮次计数在 runner 里，未下发给插件"),
    # Measured: the two real mods that ask for this (usage-meter, cache-panel)
    # want Claude's **account** rate limits, and reach for Anthropic's usage API
    # with an OAuth handle. There is no equivalent here — a `{rateLimits: []}`
    # would render an empty meter, i.e. a fake.
    "session.usage": (REFUSED, "CC 的 rateLimits 是账户概念（Anthropic 套餐窗口）；本机没有对应物，给空值等于假数据"),
    # served — per-plugin KV, session memory, timers
    "store.get": (SERVED, "4 MiB/插件"),
    "store.set": (SERVED, "4 MiB/插件"),
    "store.delete": (SERVED, "4 MiB/插件"),
    "store.keys": (SERVED, "4 MiB/插件"),
    # Scope differs from Claude Code: the host is per-agent, not per-session, so
    # this state lives as long as the agent does.
    "state.get": (DEGRADED, "宿主进程内的 per-mod KV；作用域是 agent 而非会话"),
    "state.set": (DEGRADED, "同上：随 agent 生命周期，不是随会话"),
    "state.update": (DEGRADED, "同上：随 agent 生命周期，不是随会话"),
    "state.atom": (SERVED, "订阅/派生语义未实现——用它的 TS mod 走 SDK shim"),
    "state.read": (SERVED, "订阅语义未实现（dsh：渲染期读到的 slot 会被订阅）"),
    "state.derive": (SERVED, "未实现"),
    "state.memberOf": (SERVED, "未实现"),
    "clock.now": (SERVED, ""),
    "clock.sleep": (SERVED, "定时器随插件/会话销毁"),
    "clock.after": (SERVED, "定时器随插件/会话销毁"),
    "clock.every": (SERVED, "定时器随插件/会话销毁"),
    # degraded
    "session.messages": (DEGRADED, "上限沿用 4096 条；脱敏策略待定"),
    "session.version": (DEGRADED, "报告本宿主版本，非 Claude Code 版本"),
    "session.repo": (DEGRADED, "只给 workspace 根，不给远端 / 分支 / 脏状态"),
    "session.surfaces": (DEGRADED, "只含 §5 开放插槽的子集"),
    "fs.read": (DEGRADED, "4 MiB/次；需闸门"),
    "fs.write": (
        DEGRADED,
        "默认拒绝，逐个 mod 授权（workspace 内、单次 4 MiB）。**这不是沙箱**：mod 可以用 node:fs 直接写，授权只是 API 契约",
    ),
    "fs.list": (DEGRADED, "只列一层、上限 500 条；workspace 收敛（符号链接指向外部会被拒）"),
    "fs.exists": (DEGRADED, "需闸门"),
    "fs.stat": (DEGRADED, "workspace 收敛；目录 size 记 0（不递归统计）"),
    "process.run": (DEGRADED, "默认拒绝，逐个 mod 授权（默认 30s / 上限 10min；argv 数组，不走 shell）"),
    "http.fetch": (DEGRADED, "默认拒绝，逐个 mod 授权 + 域名白名单；单次响应 4 MiB"),
    "env.get": (DEGRADED, "给宿主进程的环境变量（不需授权，读不到就返回 undefined）"),
    "env.set": (DEGRADED, "默认拒绝，逐个 mod 授权；改的是 agent 进程的环境，同一 agent 内的其它插件也能看到"),
    "tool.register": (DEGRADED, "经 ToolRegistry，level 用 core/extended/hidden"),
    "tool.call": (DEGRADED, "$ 侧走 ToolRegistry，但没有 next / deny 语义，返回契约也不同"),
    "tool.list": (DEGRADED, "只列已注册的工具名，不给逐次权限判定"),
    "prompt.submit": (DEGRADED, "提交的消息必须标 origin，否则与真人消息混淆"),
    "turn.abort": (DEGRADED, "粒度待确认"),
    "telemetry.log": (DEGRADED, "落入宿主日志；不写我们的 token / 事件统计"),
    "telemetry.mark": (DEGRADED, "落入宿主日志；不写我们的 token / 事件统计"),
    "ui.resolve": (
        DEGRADED,
        "元素工厂的来源（`const { Box, Text } = $.ui.resolve(e)`）；只给白名单 5 种元素，其余返回 undefined",
    ),
    "ui.invalidate": (DEGRADED, "只记一次重绘请求；宿主按自己的节奏重画，不保证即时"),
    "ui.log": (DEGRADED, "只写入宿主日志，不产生 Agent Web / TUI 的界面提示"),
    "ui.status": (DEGRADED, "StatusBar 未开（v0 只开 AbovePrompt），状态栏不会变化"),
    "ui.toast": (DEGRADED, "只写入宿主日志，不弹提示 —— mod 不报错，但用户看不到"),
    "ui.notice": (DEGRADED, "只写入宿主日志，无界面提示条"),
    "ui.open": (
        DEGRADED,
        "真放置：宿主记下 id，宿主按 id 渲染 Pane、界面为它开一个 mod tab；pane 由 mod 自己画，宿主不接管焦点/滚动",
    ),
    "ui.close": (REFUSED, "能开不能关：宿主会放置 pane，但不替 mod 关掉用户的 tab（关 tab 属于布局状态机）"),
    # refused — v0 明确不做
    "fs.ancestors": (REFUSED, "不提供"),
    "process.spawn": (REFUSED, "常驻子进程不给"),
    "prompt.read": (REFUSED, "无 compose 管线"),
    "prompt.fill": (REFUSED, "无 compose 管线"),
    "prompt.suggest": (REFUSED, "无 compose 管线"),
    "prompt.compose": (REFUSED, "无 compose 管线"),
    "session.compact": (REFUSED, "会话写入是宿主特权"),
    "session.send": (REFUSED, "会话写入是宿主特权"),
    "session.append": (REFUSED, "会话写入是宿主特权"),
    "session.authorize": (REFUSED, "授权是宿主特权（铁律 1）"),
    "ui.blit": (REFUSED, "逐帧绘制接管"),
    "ui.panes": (REFUSED, "焦点/输入接管"),
    "ui.focus": (REFUSED, "焦点/输入接管"),
    "ui.scroll": (REFUSED, "焦点/输入接管"),
    "ui.copy": (REFUSED, "需要选择/输入接管"),
    "ui.selection": (REFUSED, "需要选择/输入接管"),
    "ui.ask": (REFUSED, "需要输入接管（铁律 2）"),
    "settings.read": (REFUSED, "读配置=读密钥面，v0 不开"),
    "model.complete": (REFUSED, "v1 候选：成本/配额/审计未定"),
    "model.fork": (REFUSED, "v1 候选：成本/配额/审计未定"),
    "model.classify": (REFUSED, "v1 候选"),
    "agent.register": (REFUSED, "v1 候选：需编排与配额先定"),
    "agent.spawn": (REFUSED, "v1 候选：需编排与配额先定"),
    "agent.list": (REFUSED, "v1 候选"),
    "config.list": (REFUSED, "v0 不开"),
    "config.set": (REFUSED, "v0 不开"),
    "mcp.call": (REFUSED, "v0 不开（宿主已有 mcp_query，将来可能 degraded）"),
    "mcp.connect": (REFUSED, "v0 不开"),
    "audio.play": (REFUSED, "v0 不打"),
    "audio.speak": (REFUSED, "v0 不打"),
}

# Namespaces whose unlisted members fall back to this verdict.
_DOLLAR_NAMESPACE_DEFAULT: dict[str, tuple[str, str]] = {
    "plugin": (SERVED, ""),
    "state": (SERVED, "会话内存 KV"),
    "store": (SERVED, "4 MiB/插件"),
    "clock": (SERVED, "定时器随插件/会话销毁"),
    "command": (SERVED, ""),
    "session": (REFUSED, "未收录的会话成员一律拒绝"),
    "ui": (REFUSED, "未收录的 ui 成员一律拒绝"),
    "fs": (REFUSED, "未收录的 fs 成员一律拒绝"),
    "model": (REFUSED, "v1 候选"),
    "agent": (REFUSED, "v1 候选"),
    "config": (REFUSED, "v0 不开"),
    "settings": (REFUSED, "v0 不开"),
    "mcp": (REFUSED, "v0 不开"),
    "audio": (REFUSED, "v0 不打"),
}
_DOLLAR_UNKNOWN_NAMESPACE = (REFUSED, "未知命名空间（矩阵外一律拒绝）")

# ── Source scanning ────────────────────────────────────────────────────────

# on('tool.call', ...) / on("session.start", ...) — the event name is the first
# string literal of every registration call.
_ON_CALL_RE = re.compile(r"""\bon\s*\(\s*(['"])(?P<event>[A-Za-z][\w.]*)\1""")
# $.namespace.member — also matches $.namespace.member(...)
_DOLLAR_RE = re.compile(r"""\$\.(?P<ns>[a-z]+)\.(?P<member>[A-Za-z_]\w*)""")
# `component: 'AbovePrompt'` — the slot a `ui.render` handler draws into.
_COMPONENT_RE = re.compile(r"""component\s*:\s*['\"]([A-Za-z][A-Za-z0-9]*)['\"]""")

_CATCH_RE = re.compile(r"""\.\s*catch\s*\(""")


def _read_json(path: str) -> dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def event_verdict(event: str) -> tuple[str, str]:
    """Verdict for one mods event name."""
    hit = _EVENT_TABLE.get(event)
    if hit is not None:
        return hit
    return (REFUSED, _EVENT_REFUSAL_REASONS.get(event, _DEFAULT_REFUSED_EVENT))


def dollar_verdict(namespace: str, member: str) -> tuple[str, str]:
    """Verdict for one ``$`` member (``namespace.member``)."""
    hit = _DOLLAR_TABLE.get(f"{namespace}.{member}")
    if hit is not None:
        return hit
    return _DOLLAR_NAMESPACE_DEFAULT.get(namespace, _DOLLAR_UNKNOWN_NAMESPACE)


def _scan_source(text: str) -> tuple[set[str], set[tuple[str, str]]]:
    events = {m.group("event") for m in _ON_CALL_RE.finditer(text)}
    dollars = {(m.group("ns"), m.group("member")) for m in _DOLLAR_RE.finditer(text)}
    return events, dollars


def _module_paths(mod_dir: str, hooks: dict[str, Any]) -> tuple[list[str], list[str], list[str]]:
    """Resolve ``hooks.json``'s ``modules`` entries.

    Returns ``(found_paths, declared_missing, declared_tsx)``.

    ``.ts`` runs as-is on Node 24 (native type stripping); ``.tsx`` needs a JSX
    transform, which the host does at load time with a vendored sucrase. Both are
    therefore *loadable*; ``declared_tsx`` is only reported so the card can say
    the module is transpiled on the way in.

    ``hooks.json`` lives in ``hooks/`` and its ``modules`` entries are relative to
    that directory (``{"modules": ["./guard.mjs"]}`` → ``hooks/guard.mjs``).
    """
    modules = hooks.get("modules")
    if not isinstance(modules, list):
        modules = []
    hooks_dir = os.path.join(mod_dir, "hooks")
    paths: list[str] = []
    missing: list[str] = []
    declared_ts: list[str] = []
    for entry in modules:
        if not isinstance(entry, str) or not entry.strip():
            continue
        rel = entry.strip()
        if rel.lower().endswith(".tsx"):
            declared_ts.append(rel)
        for base in (hooks_dir, mod_dir):
            candidate = os.path.normpath(os.path.join(base, rel))
            if os.path.isfile(candidate):
                paths.append(candidate)
                break
        else:
            missing.append(rel)
    return paths, missing, declared_ts


def scan_mod(mod_dir: str) -> dict[str, Any]:
    """Statically scan one mod directory.  Never executes the mod's code."""
    mod_dir = os.path.abspath(mod_dir)
    name = os.path.basename(mod_dir)

    manifest = _read_json(os.path.join(mod_dir, ".claude-plugin", "plugin.json"))
    hooks = _read_json(os.path.join(mod_dir, "hooks", "hooks.json"))
    display_name = str(manifest.get("name") or name)
    hooks_path = os.path.join(mod_dir, "hooks", "hooks.json")

    module_paths, unreadable, declared_ts = _module_paths(mod_dir, hooks)

    used_events: set[str] = set()
    used_dollars: set[tuple[str, str]] = set()
    drawn_components: set[str] = set()
    has_ts_module = bool(declared_ts)
    has_catch = False
    notes: list[str] = []

    if not os.path.isfile(hooks_path):
        notes.append("没有 hooks/hooks.json —— 不是 mod 包（或清单缺失）")
    for path in module_paths:
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:
            unreadable.append(os.path.basename(path))
            continue
        events, dollars = _scan_source(text)
        used_events |= events
        used_dollars |= dollars
        # Which slots it draws into: `on('ui.render', { component: 'ToolUse' })`.
        # Only a literal can be seen statically; an empty set therefore means
        # "unknown", never "none" — `effect_summary` treats it as such.
        drawn_components |= set(_COMPONENT_RE.findall(text))
        if _CATCH_RE.search(text):
            has_catch = True
    for rel in unreadable:
        notes.append(f"清单声明的模块不存在：{rel}")

    # ── verdict ──
    blocked_by: list[dict[str, str]] = []
    degraded_by: list[dict[str, str]] = []
    for event in sorted(used_events):
        verdict, why = event_verdict(event)
        if verdict == REFUSED:
            blocked_by.append({"kind": "event", "name": event, "why": why})
        elif verdict == DEGRADED:
            degraded_by.append({"kind": "event", "name": event, "why": why})
    for ns, member in sorted(used_dollars):
        verdict, why = dollar_verdict(ns, member)
        full = f"{ns}.{member}"
        if verdict == REFUSED:
            blocked_by.append({"kind": "dollar", "name": f"$.{full}", "why": why})
        elif verdict == DEGRADED:
            degraded_by.append({"kind": "dollar", "name": f"$.{full}", "why": why})

    if has_ts_module:
        # Not a gap: the host transpiles JSX at load time. Say so, so the card is
        # not silent about a build step happening on the way in.
        notes.append("含 JSX 模块，加载时转译（vendored sucrase，只做语法变换不执行代码）")

    used_anything = bool(used_events or used_dollars)
    if blocked_by:
        verdict = "blocked"
    elif not used_anything:
        verdict = "unknown"
        if not notes:
            notes.append("未识别到事件或 $ 调用（可能是动态注册，或模块读不到）")
    elif degraded_by:
        verdict = "partial"
    else:
        verdict = "runnable"

    return {
        "name": display_name,
        "dir_name": name,
        "version": str(manifest.get("version") or ""),
        "description": str(manifest.get("description") or ""),
        "author": _author_name(manifest.get("author")),
        "dir": mod_dir,
        "has_manifest": bool(manifest),
        "has_hooks": os.path.isfile(hooks_path),
        "modules": [os.path.relpath(p, mod_dir).replace(os.sep, "/") for p in module_paths],
        "verdict": verdict,
        "used_events": sorted(used_events),
        "used_dollar": [f"$.{ns}.{m}" for ns, m in sorted(used_dollars)],
        "drawn_components": sorted(drawn_components),
        "blocked_by": blocked_by,
        "degraded_by": degraded_by,
        "has_catch": has_catch,
        "notes": notes,
    }


def _author_name(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return str(value.get("name") or "")
    return ""


# ── Workspace-level discovery & state ──────────────────────────────────────


def mods_root() -> str:
    """``<workspace>/mods`` — where installed mods live (sibling of plugins/skills)."""
    from opensquad.system_config import syscfg

    return os.path.join(syscfg.get_workspace(), "mods")


def mods_state_root() -> str:
    """``<workspace>/data/mods`` — per-mod host state, kept out of the vendor manifest."""
    from opensquad.system_config import syscfg

    return syscfg.workspace_data_dir("mods")


def mod_state_path(dir_name: str, state_root: str | None = None) -> str:
    base = state_root or mods_state_root()
    return os.path.join(base, dir_name, "state.json")


# ── Per-mod permissions ────────────────────────────────────────────────────
#
# The four capabilities that can leave the agent's own sandbox-shaped world:
# writing files, running processes, fetching URLs, mutating the environment.
# They are **default-deny** and granted per mod.
#
# This is an API contract, *not* a sandbox: a mod runs as ordinary Node inside the
# host process, so it can reach `node:fs` / `node:child_process` / `fetch` without
# asking `$` at all (measured — see docs/mods-bridge-m1-plan.md §3.4). The value of
# default-deny is that a mod gets a definite answer and the user gets a record of
# what it declared; the honest boundary is install-time consent.

GATED_CAPABILITIES: tuple[str, ...] = ("fs.write", "process.run", "http.fetch", "env.set")

PERMISSION_BLURB: dict[str, str] = {
    "fs.write": "写文件（限 workspace 内，单次 4 MiB）",
    "process.run": "执行命令（默认 30s，上限 10min）",
    "http.fetch": "发起网络请求（单次响应 4 MiB；需域名白名单）",
    "env.set": "修改宿主进程的环境变量（会影响同一 agent 内的其它插件）",
}


def mod_permissions_path(dir_name: str, state_root: str | None = None) -> str:
    base = state_root or mods_state_root()
    return os.path.join(base, dir_name, "permissions.json")


def read_mod_permissions(dir_name: str, state_root: str | None = None) -> dict[str, Any]:
    data = _read_json(mod_permissions_path(dir_name, state_root))
    granted = data.get("granted")
    domains = data.get("domains")
    return {
        "granted": [g for g in (granted if isinstance(granted, list) else []) if g in GATED_CAPABILITIES],
        "domains": [str(d) for d in (domains if isinstance(domains, list) else [])],
    }


def write_mod_permissions(
    dir_name: str, *, granted: list[str], domains: list[str] | None = None, state_root: str | None = None
) -> dict[str, Any]:
    payload = {
        "granted": [g for g in granted if g in GATED_CAPABILITIES],
        "domains": [str(d).strip().lower() for d in (domains or []) if str(d).strip()],
    }
    path = mod_permissions_path(dir_name, state_root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
    return payload


def read_mod_state(dir_name: str, state_root: str | None = None) -> dict[str, Any]:
    state = _read_json(mod_state_path(dir_name, state_root))
    return {"enabled": bool(state.get("enabled", False))}


def write_mod_state(dir_name: str, *, enabled: bool, state_root: str | None = None) -> dict[str, Any]:
    path = mod_state_path(dir_name, state_root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = {"enabled": bool(enabled)}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
    return payload


def discover_mods(root: str | None = None, state_root: str | None = None) -> list[dict[str, Any]]:
    """Scan every directory under the mods root, adding host state.

    Both roots are injectable so tests stay off the real workspace.
    """
    root = root or mods_root()
    out: list[dict[str, Any]] = []
    if not os.path.isdir(root):
        return out
    for entry in sorted(os.listdir(root)):
        if entry.startswith((".", "_")):
            continue
        path = os.path.join(root, entry)
        if not os.path.isdir(path):
            continue
        info = scan_mod(path)
        info["state"] = read_mod_state(entry, state_root)
        out.append(info)
    return out


# What the host actually serves today — one place, so the page cannot drift from
# the runtime (`tests/test_mods_wired_surface.py` cross-checks the `$` members
# against host.mjs, and `tests/test_mods_host_deny_e2e.py` exercises them live).
WIRED: dict[str, tuple[str, ...]] = {
    "events": ("tool.call", "turn.start", "turn.step", "turn.complete", "session.start", "session.end"),
    "dollar": (
        "$.plugin.name",
        "$.plugin.root",
        "$.clock.now",
        "$.clock.sleep",
        "$.clock.after",
        "$.clock.every",
        "$.ui.log",
        "$.ui.notice",
        "$.ui.toast",
        "$.telemetry.log",
        "$.telemetry.mark",
        "$.session.cwd",
        "$.session.id",
        "$.command.register",
        "$.command.list",
        "$.command.run",
        "$.state.get",
        "$.state.set",
        "$.state.delete",
        "$.state.keys",
        "$.state.update",
        "$.fs.list",
        "$.fs.stat",
        "$.fs.write",
        "$.process.run",
        "$.http.fetch",
        "$.env.get",
        "$.env.set",
        "$.fs.read",
        "$.fs.exists",
        "$.store.get",
        "$.store.set",
        "$.store.delete",
        "$.store.keys",
        "$.ui.resolve",
        "$.ui.invalidate",
    ),
    # Wired slots. Elements are whitelisted, and `Button.onPress` never crosses
    # the wire (the host owns the invocation). `Pane` needs real pane placement
    # on the frontend, so `$.ui.open` still answers `{isPlaced: false}` and mods
    # take their documented fallback.
    "slots": ("AbovePrompt", "AssistantMessage", "Pane"),
}

_DISPLAY_EVENTS = ("ui.render", "ui.close", "ui.press", "ui.input", "ui.select", "ui.focus", "ui.scroll")

# ── Render tree validation (铁律 3) ────────────────────────────────────────
#
# A mod hands the host a *declaration*, never a handle: `Button({onPress})` is
# hoisted to an action id host-side, so what reaches Python is a plain JSON tree.
# Validation is a whitelist with hard caps; an unknown element or attribute is
# **dropped with a diagnostic** rather than failing the tree (dsh's permissive
# policy), because one stray property must not blank a whole surface.

RENDER_MAX_NODES = 200
RENDER_MAX_DEPTH = 12
RENDER_MAX_TEXT = 10_000  # reference: Code / Markdown cap

# Structural attributes accepted on every element: `key` is a list hint for the
# renderer, not a capability, and real mods set it on nearly every node (dropping
# it produced a diagnostic per node — found by the real-mod smoke).
_STRUCTURAL_ATTRS = frozenset({"key"})

RENDER_ELEMENTS: dict[str, frozenset[str]] = {
    "Text": frozenset({"color", "bold", "italic", "underline", "dimColor", "inverse", "wrap"}),
    "Box": frozenset(
        {
            # Presentational only. The list grew from real mods hitting it
            # (`flexWrap`/`columnGap` were dropped from every node of usage-meter).
            "flexDirection",
            "flexWrap",
            "alignItems",
            "padding",
            "paddingX",
            "paddingY",
            "gap",
            "rowGap",
            "columnGap",
            "borderStyle",
            "borderColor",
            "width",
            "height",
            "justifyContent",
            "marginTop",
            "marginBottom",
        }
    ),
    "Button": frozenset({"label", "hotkey", "action", "plain", "dimColor"}),
    "Markdown": frozenset({"key"}),
    "Code": frozenset({"language", "key"}),
}

# Refused *with a reason*, so the drop is explainable and the gap is nameable.
REFUSED_ELEMENTS: dict[str, str] = {
    "Client": "等于让 mod 加载任意前端模块",
    "Input": "输入接管（铁律 1/2）",
    "Select": "输入接管（铁律 1/2）",
    "Svg": "内容与尺寸未受校验",
    "Raster": "内容与尺寸未受校验",
    "Image": "内容与尺寸未受校验",
}


def validate_element_tree(node: Any) -> dict[str, Any]:
    """Whitelist + caps over one element tree.

    Returns ``{"node": cleaned_or_None, "dropped": [diagnostic, ...]}``. Every
    diagnostic names what was dropped and why, so a blank surface is explainable
    instead of mysterious.
    """
    dropped: list[str] = []
    budget = {"nodes": 0}

    def walk(item: Any, depth: int) -> Any:
        if depth > RENDER_MAX_DEPTH:
            dropped.append(f"超过最大深度 {RENDER_MAX_DEPTH}，整棵子树丢弃")
            return None
        if isinstance(item, str):
            return item[:RENDER_MAX_TEXT]
        if not isinstance(item, dict):
            dropped.append(f"非元素节点（{type(item).__name__}）已丢弃")
            return None
        kind = str(item.get("type") or "")
        if kind in REFUSED_ELEMENTS:
            dropped.append(f"{kind} 被拒：{REFUSED_ELEMENTS[kind]}")
            return None
        allowed = RENDER_ELEMENTS.get(kind)
        if allowed is None:
            dropped.append(f"未知元素 {kind!r} 已丢弃")
            return None
        budget["nodes"] += 1
        if budget["nodes"] > RENDER_MAX_NODES:
            dropped.append(f"节点数超过上限 {RENDER_MAX_NODES}，其余丢弃")
            return None

        raw_props = item.get("props")
        props: dict[str, Any] = {}
        for key, value in (raw_props if isinstance(raw_props, dict) else {}).items():
            if key in allowed or key in _STRUCTURAL_ATTRS:
                props[key] = value
            else:
                dropped.append(f"{kind}.{key} 不在白名单，已丢弃")

        clean: dict[str, Any] = {"type": kind, "props": props}
        children = item.get("children")
        if children is not None:
            seq = children if isinstance(children, list) else [children]
            kept = [c for c in (walk(child, depth + 1) for child in seq) if c is not None]
            if kept:
                clean["children"] = kept
        return clean

    return {"node": walk(node, 0), "dropped": dropped}


def effect_summary(info: dict[str, Any]) -> dict[str, Any]:
    """What this mod would actually do here — as data; the page owns the wording.

    Loading is not effect. Three outcomes hide behind "loaded":

    * ``not_loadable`` — nothing to load (TypeScript source);
    * ``no_effect``    — none of its entry points fire (every event it hooks is
      one we never emit — a `ui.render`-only mod can never start);
    * ``invisible``    — its logic runs, but every slot it draws into is one we
      do not mount, so the user sees nothing;
    * ``works``        — something it declares is served and it is not purely a
      drawing.

    ``invisible`` and ``no_effect`` both mean "you see nothing", for different
    reasons — and the reason decides what to build next.
    """
    modules = list(info.get("modules") or [])
    used_events = list(info.get("used_events") or [])
    used_dollar = list(info.get("used_dollar") or [])
    served_events = [e for e in used_events if e in WIRED["events"]]
    served_dollar = [d for d in used_dollar if d in WIRED["dollar"]]
    draws = any(e in _DISPLAY_EVENTS for e in used_events) or any(
        d.startswith("$.ui.") and d != "$.ui.log" for d in used_dollar
    )

    if not modules:
        kind = "not_loadable"
    elif not served_events:
        kind = "no_effect"
    elif draws and _draws_nowhere_we_mount(info):
        kind = "invisible"
    else:
        kind = "works"
    return {"kind": kind, "events": served_events, "dollar": served_dollar, "draws": draws}


def _draws_nowhere_we_mount(info: dict[str, Any]) -> bool:
    """True when nothing this mod draws can reach the screen.

    Two ways to know that: the host mounts no slot at all, or the mod's drawn
    components were all read off its source and none of them is mounted. An
    unreadable component list is **not** evidence of anything — a mod that draws
    through a computed matcher must keep whatever verdict its served events
    earned, so it falls back to the host-level question.
    """
    wired = set(WIRED["slots"])
    if not wired:
        return True
    drawn = set(info.get("drawn_components") or [])
    return bool(drawn) and not (drawn & wired)


def load_plan(info: dict[str, Any]) -> dict[str, Any]:
    """What a mod contributes if loaded now, and what stays inert.

    Policy: **per-contribution loading**. A mod is loadable even when it declares
    capabilities this host refuses, because a refused contribution is inert by
    construction — we never emit its events, and we never provide its ``$``
    members. So refusing them costs the mod *those contributions* instead of the
    whole mod, which is the difference between loading 0 and 3 of the real market
    mods we sampled (docs/mods-bridge-m0.md §9).

    ``inert`` is the blocked list verbatim, so the loader and the page name the
    same gaps with the same reasons. The one thing that is still fatal is having
    no loadable module at all (TypeScript sources — v0 never transpiles).
    """
    modules = list(info.get("modules") or [])
    inert = list(info.get("blocked_by") or [])
    effect = effect_summary(info)
    if not modules:
        return {
            "loadable": False,
            "modules": [],
            "inert": inert,
            "effect": effect,
            "reason": "没有可加载的模块（hooks.json 里没有能解析到的 modules 条目）",
        }
    return {"loadable": True, "modules": modules, "inert": inert, "effect": effect, "reason": ""}


def matrix_summary() -> dict[str, Any]:
    """The host's own capability table, for the UI to render with zero mods present.

    A mods page that shows only an empty list teaches nothing; this is the "what
    will work here" panel, and it is also the honest answer when a mod is blocked:
    the reason strings come from the very same table.
    """
    events: dict[str, list[str]] = {SERVED: [], DEGRADED: [], REFUSED: []}
    for name, (verdict, _why) in _EVENT_TABLE.items():
        events[verdict].append(name)
    refused_known = sorted(_EVENT_REFUSAL_REASONS)
    events[REFUSED] = sorted(set(events[REFUSED]) | set(refused_known))

    dollars: dict[str, list[str]] = {SERVED: [], DEGRADED: [], REFUSED: []}
    for key, (verdict, _why) in _DOLLAR_TABLE.items():
        dollars[verdict].append(f"$.{key}")

    return {
        "source": "docs/claude-code-mods-compat-v0.md",
        "upstream": "Claude Code mods reference (v2.1.287 line)",
        "events": {k: sorted(v) for k, v in events.items()},
        "dollar": {k: sorted(v) for k, v in dollars.items()},
        "elements_open": ["Text", "Box", "Button", "Markdown", "Code"],
        "elements_refused": ["Client", "Svg", "Raster", "Image", "Input", "Select"],
        "slots_open": ["AbovePrompt", "ToolUse", "AssistantMessage", "StatusBar", "Spinner", "Pane", "Sidebar"],
        "slots_never": "审批/权限/邀请/危险确认/Git 确认/系统设置本体",
    }


# ── Host observation journal ───────────────────────────────────────────────
#
# The host runs inside the *agent* process (one per agent), so the gateway cannot
# probe it: all it can do is read what each agent last observed. Every agent
# therefore leaves one small JSON record, and this is the honest granularity —
# "last observed", never "live". Staleness comes from the timestamp, because a
# killed agent leaves its record behind.
#
# This exists to keep fail-open honest: a degradation that nobody can see is a
# silent bypass (docs/mods-bridge-m0.md §3.1).

HOST_STALE_SECONDS = 120

# Matrix §1.5: `$.store` is capped at 4 MiB per mod.
MOD_STORE_LIMIT = 4 * 1024 * 1024


def mod_store_dir() -> str:
    return os.path.join(mods_state_root(), "store")


def mod_store_path(mod_dir_name: str) -> str:
    safe = "".join(c if (c.isalnum() or c in "-_.") else "_" for c in (mod_dir_name or "unknown"))
    return os.path.join(mod_store_dir(), f"{safe or 'unknown'}.json")


def read_mod_store(mod_dir_name: str) -> dict[str, Any]:
    data = _read_json(mod_store_path(mod_dir_name))
    return data if isinstance(data, dict) else {}


def write_mod_store(mod_dir_name: str, data: dict[str, Any]) -> None:
    """Persist one mod's KV blob, enforcing the per-mod cap (raises on overflow)."""
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    if len(payload.encode("utf-8")) > MOD_STORE_LIMIT:
        raise ValueError(f"$.store 超过 {MOD_STORE_LIMIT} 字节上限")
    path = mod_store_path(mod_dir_name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(payload)


def host_journal_dir() -> str:
    return os.path.join(mods_state_root(), "_host")


def host_journal_path(agent_id: str) -> str:
    safe = "".join(c if (c.isalnum() or c in "-_.") else "_" for c in (agent_id or "unknown"))
    return os.path.join(host_journal_dir(), f"{safe or 'unknown'}.json")


def write_host_status(
    agent_id: str,
    *,
    state: str,
    node: str = "",
    pid: int | None = None,
    mods: int = 0,
    inert: int = 0,
    last_failure: str = "",
) -> None:
    """Record what the host in *agent_id* just observed (sync: small bounded JSON)."""
    import datetime

    now = datetime.datetime.now(datetime.timezone.utc)
    payload = {
        "agent_id": agent_id or "",
        "state": state,
        "node": node,
        "pid": pid,
        "mods": mods,
        "inert": inert,
        "last_failure": last_failure,
        "updated_at": now.isoformat(),
        "updated_at_ts": now.timestamp(),
    }
    path = host_journal_path(agent_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)


def read_host_observations() -> list[dict[str, Any]]:
    directory = host_journal_dir()
    if not os.path.isdir(directory):
        return []
    out: list[dict[str, Any]] = []
    for entry in sorted(os.listdir(directory)):
        if not entry.endswith(".json"):
            continue
        record = _read_json(os.path.join(directory, entry))
        if record:
            out.append(record)
    return out


def host_status() -> dict[str, Any]:
    """Whether mods can actually *run* here.  Compatibility ≠ availability.

    ``available`` answers exactly one question — can a host process start at all
    (is there a node runtime).  It is **not** a promise that any given mod does
    something: M0 wires one event (``tool.call``) plus a minimal ``$``, so each
    mod's own verdict from §3/§4 still decides whether registering has an effect.
    ``scope`` says out loud what is wired, so the page cannot imply more, and
    ``observed`` is the *last reported* state of the per-agent hosts.
    """
    import time

    node = _node_hint()
    now = time.time()
    running = degraded = stale = 0
    last_failure = ""
    observed_at = 0.0
    for record in read_host_observations():
        ts = float(record.get("updated_at_ts") or 0)
        observed_at = max(observed_at, ts)
        if ts and (now - ts) > HOST_STALE_SECONDS:
            stale += 1
            continue
        if record.get("state") == "degraded":
            degraded += 1
            if not last_failure:
                last_failure = str(record.get("last_failure") or "")
        elif record.get("state") == "running":
            running += 1
    return {
        "available": bool(node),
        "reason": "" if node else "未找到 Node 运行时——mod 宿主无法启动（安装 Node.js 或设置 OPENSQUAD_NODE）",
        # Derived from WIRED so the page cannot promise more than the runtime does.
        "scope": (
            "事件："
            + " / ".join(WIRED["events"])
            + f" · $：{len(WIRED['dollar'])} 个成员（plugin / clock / ui.log / telemetry / session.cwd / fs.read / fs.exists）"
            + f" · 渲染插槽：{len(WIRED['slots'])}"
        ),
        "wired": {"events": list(WIRED["events"]), "dollar": list(WIRED["dollar"])},
        "node_runtime": node,
        "observed": {"running": running, "degraded": degraded, "stale": stale},
        "last_failure": last_failure,
        "observed_at_ts": observed_at,
    }


def _node_hint() -> str:
    import shutil

    explicit = (os.environ.get("OPENSQUAD_NODE") or "").strip()
    if explicit and os.path.isfile(explicit):
        return explicit
    home = (os.environ.get("OPENSQUAD_NODE_HOME") or "").strip()
    if home:
        exe = os.path.join(home, "node.exe" if os.name == "nt" else "node")
        if os.path.isfile(exe):
            return exe
    return shutil.which("node") or ""
