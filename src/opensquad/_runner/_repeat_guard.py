"""Repeated-action guard — detect a runaway tool loop and decide what to do.

Pure decision core extracted from ``_turn_loop.py`` so the rules can be unit
tested and mutation-locked.  Two independent signals are tracked per session:

STRICT
    Same tool + same arguments + byte-identical result, for every result of the
    round.  Catches "the call itself repeats" — e.g. the 172-round ``read_file``
    loop (42 min of pure waste) that motivated the original guard.

FAILURE
    The *same failure text* keeps coming back, whatever the arguments were.
    Catches the menu-style retry that laundered the strict signal: measured
    2026-09-21 in session ``20260921_084718_9l88``, a shell died mid-command and
    the agent re-ran the same script under a fresh ``session_id`` 25 times
    (``chk`` → ``chk24``, 48 wasted rounds).  Because an argument changed every
    round the strict fingerprint reset each time and never fired, even though the
    model was handed a byte-identical ``Command aborted (...)`` 48 times.

    Rounds with no failure at all are *skipped* rather than counted as progress:
    in the incident above every failure round was preceded by a successful
    ``create_shell_session``, which is itself part of the futile strategy.  A
    *different* failure, or the same failure returning after
    ``FAILURE_STALENESS_ROUNDS`` quiet rounds, resets the streak — so a genuinely
    new problem is never blamed on an old one, and a legitimate poll that returns
    a *successful* (changing) result is never flagged at all.

    "Same failure" is decided by ``_result_formatter.failure_key`` — our own
    taxonomy (``reason``/``status`` + digit-masked message), never the rendered
    text: that text contains the ``session_id`` the retry just minted, so
    digesting it would reset the counter on every round and the signal would be
    dead on exactly the incident it exists for.
"""

from __future__ import annotations

import hashlib
from typing import Any

# Rounds of an identical call+result before the corrective hint / the abort.
STRICT_HINT_ROUNDS = 8
STRICT_ABORT_ROUNDS = 12

# Occurrences of an identical *failure* before the corrective hint / the abort.
# Lower than the strict pair on purpose: a repeated failure is a much stronger
# "you cannot proceed" signal than a repeated success, so reacting sooner is
# worth it (the incident burned 48 rounds before the user hit stop).
FAILURE_HINT_ROUNDS = 6
FAILURE_ABORT_ROUNDS = 10

# An identical failure returning after this many failure-free rounds is a new
# problem, not a stuck loop.
FAILURE_STALENESS_ROUNDS = 10

ACTION_NONE = "none"
ACTION_HINT = "hint"
ACTION_ABORT = "abort"

SIGNAL_STRICT = "strict"
SIGNAL_FAILURE = "failure"


def _digest(text: Any) -> str:
    return hashlib.md5(str(text).encode("utf-8", errors="replace"), usedforsecurity=False).hexdigest()[:8]


def round_signature(results: list[dict]) -> str:
    """STRICT fingerprint: tool + args + result digest, for every result in order."""
    return ";;".join(
        f"{r.get('name', '')}|{r.get('args_json', '')}|{_digest(r.get('result_text', ''))}" for r in results
    )


def failure_signature(results: list[dict]) -> str | None:
    """FAILURE fingerprint: digests of the failed results only, or ``None``.

    Arguments and the tool name are deliberately absent — the whole point is that
    changing them must not launder the loop.
    """
    digests = [
        _digest(r["failure_key"]) if r.get("failure_key") else _digest(r.get("result_text", ""))
        for r in results
        if r.get("failed")
    ]
    return "|".join(digests) if digests else None


def new_state() -> dict:
    """Fresh per-session counter state for both signals."""
    return {
        "count": 0,
        "last": None,
        "guarded": False,
        "fail_count": 0,
        "fail_sig": None,
        "fail_guarded": False,
        "fail_idle": 0,
    }


def evaluate(state: dict, results: list[dict]) -> dict:
    """Fold this round into *state* (in place) and return the decision.

    Returns ``{"action", "signal", "rounds", "tool"}`` where ``action`` is one of
    ``ACTION_NONE`` / ``ACTION_HINT`` / ``ACTION_ABORT``.  A round with no tool
    results leaves *state* untouched — it carries no signal either way.
    """
    decision: dict = {"action": ACTION_NONE, "signal": None, "rounds": 0, "tool": ""}
    if not results:
        return decision
    tool = str(results[0].get("name", "") or "")

    # ── STRICT: identical call AND identical result ──
    sig = round_signature(results)
    if state.get("last") == sig:
        state["count"] = int(state.get("count", 0)) + 1
    else:
        state["count"] = 1
        state["guarded"] = False
        state["last"] = sig

    # ── FAILURE: identical failure text, arguments ignored ──
    fail_sig = failure_signature(results)
    if fail_sig is None:
        state["fail_idle"] = int(state.get("fail_idle", 0)) + 1
    else:
        stale = int(state.get("fail_idle", 0)) > FAILURE_STALENESS_ROUNDS
        if fail_sig == state.get("fail_sig") and not stale:
            state["fail_count"] = int(state.get("fail_count", 0)) + 1
        else:
            state["fail_count"] = 1
            state["fail_guarded"] = False
        state["fail_sig"] = fail_sig
        state["fail_idle"] = 0

    count = int(state.get("count", 0))
    fail_count = int(state.get("fail_count", 0))

    if count >= STRICT_ABORT_ROUNDS and state.get("guarded"):
        return {"action": ACTION_ABORT, "signal": SIGNAL_STRICT, "rounds": count, "tool": tool}
    if fail_count >= FAILURE_ABORT_ROUNDS and state.get("fail_guarded"):
        return {"action": ACTION_ABORT, "signal": SIGNAL_FAILURE, "rounds": fail_count, "tool": tool}
    # `>=` (not `==`) so a counter that starts mid-streak still gets its one hint
    # before the abort can fire.
    if count >= STRICT_HINT_ROUNDS and not state.get("guarded"):
        state["guarded"] = True
        return {"action": ACTION_HINT, "signal": SIGNAL_STRICT, "rounds": count, "tool": tool}
    if fail_count >= FAILURE_HINT_ROUNDS and not state.get("fail_guarded"):
        state["fail_guarded"] = True
        return {"action": ACTION_HINT, "signal": SIGNAL_FAILURE, "rounds": fail_count, "tool": tool}
    return decision


def hint_message(decision: dict) -> str:
    """Corrective text injected as a tool result (the model reads this next turn)."""
    rounds = decision.get("rounds", 0)
    tool = decision.get("tool") or "?"
    if decision.get("signal") == SIGNAL_FAILURE:
        return (
            f"[Repeated-Action Guard] 注意：你已连续 {rounds} 次拿到**完全相同的失败结果**"
            f"（最近一次来自 {tool}）。你只是改了参数，失败的实质没有变。"
            "这说明当前做法走不通，继续重试只会浪费轮次。请立即改变策略：\n"
            "1) 先读失败结果里的 partial_data / reason / working_directory，"
            "判断是命令本身的问题还是环境的问题；\n"
            "2) 换一条完全不同的路径（换工具、换工作目录、换解释器，或先确认依赖与文件是否存在），"
            "不要重放同一条命令；\n"
            "3) 若判断本轮确实无法解决，请把失败原文和你的判断告诉用户并请其决策，不要空转。"
        )
    return (
        f"[Repeated-Action Guard] 注意：你已连续 {rounds} 轮执行完全相同的工具调用"
        f"（{tool}，参数与结果均未变化）。"
        f"这通常意味着你在原地打转。请立即改变策略：\n"
        f"1) 若需继续读同一文件，请用 start_line 翻页且只读未读部分；\n"
        f"2) 若已获得足够信息，请尽快推进任务（输出结论/计划）或询问用户；\n"
        f"3) 若任务需要执行操作（如启动服务），请在 Plan 模式下调用 "
        f"agent_mode__request_switch 切换到 Build。"
    )


def abort_message(decision: dict) -> str:
    """User-facing reason for ending the turn."""
    rounds = decision.get("rounds", 0)
    tool = decision.get("tool") or "?"
    if decision.get("signal") == SIGNAL_FAILURE:
        return (
            f"[Repeated-Action Guard] 已连续 {rounds} 次拿到完全相同的失败结果"
            f"（最近一次来自 {tool}，每次只是改了参数），判定为无进展循环，已中止本轮任务。"
            f"失败原文（含 partial_data / reason）已写在工具结果里，请先看它再决定下一步。"
            f"建议：换一个思路，必要时切换模型后重试。"
        )
    return (
        f"[Repeated-Action Guard] 已连续 {rounds} 轮执行完全相同的工具调用且结果不变"
        f"（{tool}），判定为失忆循环，已中止本轮任务。"
        f"建议：切换模型、检查工具结果是否进入上下文，或分步下达指令。"
    )
