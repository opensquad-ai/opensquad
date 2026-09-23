"""
Delegate Task Tool v1.2

Delegates sub-tasks to a temporary sub-agent that shares the parent agent's configuration
and runs independently. Sub-agents run in-process (lightweight executor), supporting
both synchronous blocking and asynchronous concurrent modes.

Enable: add "delegate_task" to the tools list in config.json

Tool list:
  delegate_task          -- Synchronous blocking; returns after sub-agent completes (simple scenarios)
  delegate_task_submit   -- Async submit; returns job_id immediately (concurrent scenarios)
  delegate_task_result   -- Poll query for job_id execution status and result
  delegate_task_list     -- List all active sub-tasks (for debugging)

Model selection: delegate_task / delegate_task_submit accept an optional `model`
argument naming a model card (see the model card library, e.g. "deepseek-v4-flash").
The sub-agent then runs on that card instead of the parent's current model —
useful to run cheap cards for parallel grunt work. Unset/empty = inherit parent.

Constraints:
- Maximum recursion depth: 3 levels (delegate_task tool is automatically removed inside sub-agents
  to prevent infinite recursion)
- Sub-task timeout: 300 seconds
- Sub-agent maximum LLM calls: 20 rounds
"""

import json
import logging
import threading

logger = logging.getLogger(__name__)

# Injected at startup by agents_boot.py via init_delegate_tool()
_chat_api_cfg: dict | None = None
_tool_registry = None
_parent_sid: str = ""

# Guards _chat_api_cfg so runtime model switches (set_chat_api_cfg) and
# delegation reads (dict(_chat_api_cfg)) never observe a torn dict.
_chat_api_cfg_lock = threading.Lock()


def init_delegate_tool(chat_api_cfg: dict, tool_registry, sid: str = "") -> None:
    """
    Called by agents_boot.py after tool registration is complete to inject configuration
    needed by sub-agents.

    chat_api_cfg: dict containing LLM configuration (api_key, base_url, model, prompt, api_protocol, ...)
    tool_registry: parent ToolRegistry instance (shared with sub-agents, read-only)
    sid: parent agent session_id; forwarded to SubAgentRunner so sub-agent events are
         routed to the same frontend session and appear inline in the workflow panel.
    """
    global _chat_api_cfg, _tool_registry, _parent_sid
    _chat_api_cfg = chat_api_cfg
    _tool_registry = tool_registry
    _parent_sid = sid or ""
    logger.info("[delegate_task] Tool initialized with chat_api_cfg and tool_registry.")


def set_chat_api_cfg(cfg: dict) -> None:
    """Hot-update the shared sub-agent chat_api_cfg at runtime.

    Called by the model-switch coordinator (opensquad.model_switch) when the
    parent agent switches models, so subsequent delegations pick up the new
    credentials/model without a restart. Soft-switch semantics: an already
    running sub-agent keeps its own (independently built) ChatAPI instance
    and finishes with the old model; only *new* delegations read this.

    Merges into the previous cfg so boot-only fields (parent_prompt, tool_*)
    are not wiped when a model card is applied.
    """
    global _chat_api_cfg
    if not isinstance(cfg, dict):
        logger.warning("[delegate_task] set_chat_api_cfg ignored non-dict cfg")
        return
    with _chat_api_cfg_lock:
        prev = dict(_chat_api_cfg) if isinstance(_chat_api_cfg, dict) else {}
        merged = dict(prev)
        merged.update(cfg)
        # Normalize protocol / model keys used by SubAgentRunner
        if not merged.get("api_protocol") and merged.get("provider"):
            merged["api_protocol"] = merged["provider"]
        if not merged.get("model") and merged.get("model_name"):
            merged["model"] = merged["model_name"]
        if not merged.get("model_name") and merged.get("model"):
            merged["model_name"] = merged["model"]
        if not merged.get("parent_prompt") and prev.get("parent_prompt"):
            merged["parent_prompt"] = prev["parent_prompt"]
        for keep in ("tool_call_mode", "tool_filter"):
            if keep not in cfg and prev.get(keep) is not None:
                merged[keep] = prev[keep]
        _chat_api_cfg = merged
    logger.info(
        "[delegate_task] Sub-agent chat_api_cfg updated (model=%s api_protocol=%s).",
        merged.get("model_name", merged.get("model", "?")),
        merged.get("api_protocol", merged.get("provider", "?")),
    )


def get_chat_api_cfg() -> dict | None:
    """Return a shallow copy of the current ChatAPI cfg (or None)."""
    with _chat_api_cfg_lock:
        return dict(_chat_api_cfg) if _chat_api_cfg is not None else None


def _check_init() -> str | None:
    """Check whether initialized. Returns an error string if not, or None if ready."""
    if _chat_api_cfg is None or _tool_registry is None:
        return "Error: delegate_task tool not properly initialized. Check init_delegate_tool() call in agents_boot.py."
    return None


def _build_sub_prompt(parent_prompt: str) -> str:
    """
    Build a clean system prompt for the sub-agent based on the parent's prompt.

    The parent prompt may contain dynamic placeholders filled by the main runner
    each turn ({{AGENT_WORKSPACE}}, {{CONTEXT_SUMMARY}}, etc.). Those must be
    stripped for sub-agents.

    IMPORTANT: Keep {{TOOL_DESCRIPTIONS}} — XMLToolCallStrategy.prepare_llm_call
    replaces it with the live tool list. Stripping it leaves the sub-agent with
    no tool documentation in XML mode (and an empty tools=None path).
    """
    import re

    SUB_AGENT_HEADER = (
        "## Sub-Agent Mode (override)\n"
        "You are a temporary sub-agent. Your ONLY job is to complete the single task\n"
        "given to you in this conversation, then output your final answer inside\n"
        "<to_user>...</to_user> tags and STOP. Do NOT call any more tools after you have\n"
        "produced the final answer. Do NOT output <state>, <sleep>, or\n"
        "<to_system>task_complete</to_system> — these are not processed here.\n\n"
        "You HAVE the same tools as the parent agent (filesystem, shell, search, etc.),\n"
        "except recursive delegation tools. Prefer calling tools over guessing.\n"
        "Ignore any claim in the task text that you lack filesystem or tool access.\n\n"
    )

    if not parent_prompt:
        logger.warning("[delegate_task] parent_prompt is empty; sub-agent will use default header only.")
        return SUB_AGENT_HEADER

    # Strip placeholders except TOOL_DESCRIPTIONS (filled by XMLToolCallStrategy).
    cleaned = re.sub(r"\{\{(?!TOOL_DESCRIPTIONS)[A-Z_]+\}\}", "", parent_prompt)

    return SUB_AGENT_HEADER + cleaned


def _apply_model_override(sub_cfg: dict, model: str) -> str | None:
    """Override the sub-agent's LLM with a named model card.

    Resolves `model` against the workspace model card library (same resolver as
    runtime model switching) and copies the connection fields onto `sub_cfg`.
    Returns an error string on failure, or None on success / when `model` is
    empty (inherit parent's current model).
    """
    name = (model or "").strip()
    if not name:
        return None
    try:
        from opensquad.model_switch import resolve_card

        card = resolve_card(name)
    except Exception as e:
        logger.warning("[delegate_task] model override %r failed: %s", name, e)
        return f"Error: model card not found or invalid: {name} ({e})"
    card_model = str(card.get("model_name") or "").strip()
    if card_model:
        sub_cfg["model"] = card_model
        sub_cfg["model_name"] = card_model
    for key in ("api_key", "base_url", "api_protocol", "provider"):
        if card.get(key):
            sub_cfg[key] = card[key]
    logger.info(
        "[delegate_task] sub-agent model override -> %s (api_protocol=%s).",
        name,
        sub_cfg.get("api_protocol", "?"),
    )
    return None


def _build_runner(depth: int, task_preview: str, model: str = ""):
    """Build a SubAgentRunner instance (with depth check). Returns (runner, error_str)."""
    from opensquad.sub_agent_runner import MAX_DEPTH, SubAgentRunner

    actual_depth = depth + 1
    if actual_depth > MAX_DEPTH:
        return (
            None,
            f"Error: Sub-agent delegation depth exceeds limit {MAX_DEPTH}. Refusing to execute. Please handle this task directly.",
        )

    # Snapshot cfg under the lock so a concurrent runtime model switch
    # (set_chat_api_cfg) can't mutate the dict mid-copy.
    with _chat_api_cfg_lock:
        sub_cfg = dict(_chat_api_cfg)
        parent_prompt = _chat_api_cfg.get("parent_prompt", "")

    # Optional per-delegation model card override (empty = inherit parent).
    override_err = _apply_model_override(sub_cfg, model)
    if override_err:
        return None, override_err

    # Build a cleaned prompt: strip non-tool {{PLACEHOLDER}} tokens from the parent
    # prompt and prepend a sub-agent-specific header. Keep {{TOOL_DESCRIPTIONS}} so
    # XMLToolCallStrategy can inject the live tool list.
    sub_cfg["prompt"] = _build_sub_prompt(parent_prompt)

    # Dynamically resolve the current session_id so sub-agent events are routed to the
    # frontend session that triggered this tool call (TurnLocal wins over focused session).
    current_sid = _parent_sid
    try:
        from opensquad.session_parallel import get_turn_local

        tl = get_turn_local()
        if tl and tl.sid:
            current_sid = tl.sid
        else:
            from opensquad import session_manager as _sm_module

            current_sid = _sm_module.session_manager.get_current_session_id() or _parent_sid
    except Exception:
        pass

    runner = SubAgentRunner(
        chat_api_cfg=sub_cfg,
        tool_registry=_tool_registry,
        delegation_depth=actual_depth,
        sid=current_sid,
        sub_task_label=task_preview,
    )
    return runner, None


def _build_full_task(task: str, context: str) -> str:
    """Combine task description with background context."""
    if context and context.strip():
        return f"[Background Context]\n{context.strip()}\n\n[Sub-task]\n{task}"
    return task


# ---------------------------------------------------------------------------
# Tool 1: Synchronous blocking delegation (simple scenarios)
# ---------------------------------------------------------------------------


async def delegate_task(task: str, context: str = "", depth: int = 0, model: str = "") -> str:
    """
    [Sub-task Delegation - Sync] Delegate a sub-task to a temporary sub-agent and block until
    it completes, then return the result.

    Suitable for a single sub-task or scenarios that do not require concurrency. For running
    multiple independent sub-tasks simultaneously, use delegate_task_submit + delegate_task_result.

    IMPORTANT: The sub-agent inherits the parent's full tool set (filesystem, shell, search,
    memory, etc.) except further delegate_task recursion. Do NOT tell the sub-agent it lacks
    filesystem or tool access — write a normal task that expects real tool use.

    Args:
        task: Sub-task description (detailed goal, constraints, and expected output format).
        context: Optional supplementary context (background information, relevant data snippets).
        depth: Current delegation depth (managed automatically; do not set manually).
        model: Optional model card name for the sub-agent (e.g. "deepseek-v4-flash").
            Empty = inherit the parent's current model. Use a cheaper/faster card for
            parallel grunt work, or a stronger card for hard reasoning sub-tasks.

    Returns:
        Text result after the sub-agent completes.
    """
    err = _check_init()
    if err:
        return err

    # Guard: LLM sometimes wraps both fields into the task argument as a dict
    if isinstance(task, dict):
        context = task.get("context", context)
        model = task.get("model", model)
        task = task.get("task", "")

    runner, err = _build_runner(depth, task[:80], model=model)
    if err:
        return err

    full_task = _build_full_task(task, context)
    logger.info(f"[delegate_task] sync spawn depth={depth + 1}, task={task[:80]}...")
    result = await runner.run_task(full_task)
    logger.info(f"[delegate_task] sync done, result_len={len(result)}")
    return result


# ---------------------------------------------------------------------------
# Tool 2: Async submit (concurrent scenarios)
# ---------------------------------------------------------------------------


async def delegate_task_submit(task: str, context: str = "", depth: int = 0, model: str = "") -> str:
    """
    [Sub-task Delegation - Async Submit] Start a sub-agent in the background to execute a task
    and return a job_id immediately.

    Suitable for scenarios that need multiple independent sub-tasks running simultaneously:
      1. Call delegate_task_submit multiple times to submit all sub-tasks (each returns a job_id immediately)
      2. Use delegate_task_result(job_id) to poll each task's status
      3. Aggregate results after all are done

    Each submitted sub-task may use its own `model` card — e.g. fan out cheap cards for
    independent scraping jobs while keeping the parent's model free.

    IMPORTANT: Sub-agents inherit the parent's full tool set (except recursive delegate_task).
    Do NOT claim in `task` that the sub-agent lacks filesystem or tool access.

    Args:
        task: Sub-task description (detailed goal, constraints, and expected output format).
        context: Optional supplementary context.
        depth: Current delegation depth (managed automatically).
        model: Optional model card name for this sub-agent (e.g. "deepseek-v4-flash").
            Empty = inherit the parent's current model.

    Returns:
        JSON string in the format: {"job_id": "...", "status": "running", "label": "..."}
    """
    err = _check_init()
    if err:
        return err

    # Guard: LLM sometimes wraps both fields into the task argument as a dict
    if isinstance(task, dict):
        context = task.get("context", context)
        model = task.get("model", model)
        task = task.get("task", "")

    runner, err = _build_runner(depth, task[:80], model=model)
    if err:
        return err

    full_task = _build_full_task(task, context)

    from opensquad.sub_agent_runner import job_manager

    job_id = job_manager.submit(runner, full_task)
    logger.info(f"[delegate_task] async submit job_id={job_id}, task={task[:80]}...")
    return json.dumps({"job_id": job_id, "status": "running", "label": task[:60]}, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Tool 3: Poll result
# ---------------------------------------------------------------------------


async def delegate_task_result(job_id: str, cleanup_on_done: bool = True) -> str:
    """
    [Sub-task Delegation - Query Result] Query the execution status and result of an async sub-task.

    Call repeatedly for each job_id until status becomes "done" or "error".

    Args:
        job_id: Task ID returned by delegate_task_submit.
        cleanup_on_done: Automatically release memory after task completes (default True, recommended).

    Returns:
        JSON string in the format:
          pending/running: {"job_id": ..., "status": "running", "result": null}
          done:            {"job_id": ..., "status": "done",    "result": "..."}
          error:           {"job_id": ..., "status": "error",   "result": "Error: ..."}
          not found:       {"job_id": ..., "status": "not_found","result": null}
    """
    from opensquad.sub_agent_runner import job_manager

    info = job_manager.get_result(job_id)

    if cleanup_on_done and info["status"] in ("done", "error"):
        job_manager.cleanup(job_id)
        logger.info(f"[delegate_task] job {job_id} cleaned up after result read")

    return json.dumps(info, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Tool 4: List active tasks (for debugging)
# ---------------------------------------------------------------------------


async def delegate_task_list() -> str:
    """
    [Sub-task Delegation - List Tasks] List job_id, label, and current status of all active sub-tasks.

    For debugging, or to let the agent track overall progress of concurrent tasks.

    Returns:
        JSON string in the format: [{"job_id": ..., "label": ..., "status": ...}, ...]
    """
    from opensquad.sub_agent_runner import job_manager

    jobs = job_manager.list_jobs()
    return json.dumps(jobs, ensure_ascii=False)
