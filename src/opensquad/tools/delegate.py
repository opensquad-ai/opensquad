"""
Delegate Task Tool v1.1

Delegates sub-tasks to a temporary sub-agent that shares the parent agent's configuration
and runs independently. Sub-agents run in-process (lightweight executor), supporting
both synchronous blocking and asynchronous concurrent modes.

Enable: add "delegate_task" to the tools list in config.json

Tool list:
  delegate_task          -- Synchronous blocking; returns after sub-agent completes (simple scenarios)
  delegate_task_submit   -- Async submit; returns job_id immediately (concurrent scenarios)
  delegate_task_result   -- Poll query for job_id execution status and result
  delegate_task_list     -- List all active sub-tasks (for debugging)

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
    """
    global _chat_api_cfg
    if not isinstance(cfg, dict):
        logger.warning("[delegate_task] set_chat_api_cfg ignored non-dict cfg")
        return
    with _chat_api_cfg_lock:
        _chat_api_cfg = cfg
    logger.info(
        "[delegate_task] Sub-agent chat_api_cfg updated (model=%s).",
        cfg.get("model_name", cfg.get("model", "?")),
    )


def _check_init() -> str | None:
    """Check whether initialized. Returns an error string if not, or None if ready."""
    if _chat_api_cfg is None or _tool_registry is None:
        return "Error: delegate_task tool not properly initialized. Check init_delegate_tool() call in agents_boot.py."
    return None


def _build_sub_prompt(parent_prompt: str) -> str:
    """
    Build a clean system prompt for the sub-agent based on the parent's prompt.

    The parent prompt comes from thought_xml.md / base_xml.md and may contain
    unreplaced dynamic placeholders ({{TOOL_DESCRIPTIONS}}, {{AGENT_WORKSPACE}},
    {{AGENT_PROFILE}}, {{CONTEXT_SUMMARY}}, etc.) that are normally filled in by
    context_base.py on every turn of the main runner loop.  These placeholders are
    *never* injected for sub-agents, so they must be stripped before being passed to
    the LLM, otherwise the model sees literal "{{...}}" tokens and behaves erratically
    (calling tools in a loop without ever producing a final answer).

    In addition, sub-agents should NOT use the full parent-agent state machine
    (working/idle state, <to_system>task_complete</to_system>, sleep tags, etc.).
    A concise override header is prepended to remind the sub-agent of its sole duty.
    """
    import re

    SUB_AGENT_HEADER = (
        "## Sub-Agent Mode (override)\n"
        "You are a temporary sub-agent. Your ONLY job is to complete the single task\n"
        "given to you in this conversation, then output your final answer inside\n"
        "<to_user>...</to_user> tags and STOP. Do NOT call any more tools after you have\n"
        "produced the final answer. Do NOT output <state>, <sleep>, or\n"
        "<to_system>task_complete</to_system> — these are not processed here.\n\n"
    )

    if not parent_prompt:
        logger.warning("[delegate_task] parent_prompt is empty; sub-agent will use default header only.")
        return SUB_AGENT_HEADER

    # Strip all {{PLACEHOLDER}} tokens that context_base.py normally fills in.
    # This covers: {{TOOL_DESCRIPTIONS}}, {{AGENT_WORKSPACE}}, {{AGENT_PROFILE}},
    # {{CONTEXT_SUMMARY}}, {{TEAM_COLLAB_CARDS}}, {{SKILLS_INSTRUCTIONS}},
    # {{MCP_GUIDE}}, {{MCP_CURRENT_STATE}}, and any future additions.
    cleaned = re.sub(r"\{\{[A-Z_]+\}\}", "", parent_prompt)

    return SUB_AGENT_HEADER + cleaned


def _build_runner(depth: int, task_preview: str):
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
    # Build a cleaned prompt: strip unreplaced {{PLACEHOLDER}} tokens from the parent
    # prompt and prepend a sub-agent-specific header.  Without this, placeholders like
    # {{TOOL_DESCRIPTIONS}} appear literally in the LLM context and cause the model to
    # loop through tool calls indefinitely instead of producing a final answer.
    sub_cfg["prompt"] = _build_sub_prompt(parent_prompt)

    # Dynamically resolve the current session_id so sub-agent events are routed to the
    # frontend session that triggered this tool call (session_manager is always up-to-date).
    current_sid = _parent_sid  # fall back to boot-time value if session_manager unavailable
    try:
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


async def delegate_task(task: str, context: str = "", depth: int = 0) -> str:
    """
    [Sub-task Delegation - Sync] Delegate a sub-task to a temporary sub-agent and block until
    it completes, then return the result.

    Suitable for a single sub-task or scenarios that do not require concurrency. For running
    multiple independent sub-tasks simultaneously, use delegate_task_submit + delegate_task_result.

    Args:
        task: Sub-task description (detailed goal, constraints, and expected output format).
        context: Optional supplementary context (background information, relevant data snippets).
        depth: Current delegation depth (managed automatically; do not set manually).

    Returns:
        Text result after the sub-agent completes.
    """
    err = _check_init()
    if err:
        return err

    # Guard: LLM sometimes wraps both fields into the task argument as a dict
    if isinstance(task, dict):
        context = task.get("context", context)
        task = task.get("task", "")

    runner, err = _build_runner(depth, task[:80])
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


async def delegate_task_submit(task: str, context: str = "", depth: int = 0) -> str:
    """
    [Sub-task Delegation - Async Submit] Start a sub-agent in the background to execute a task
    and return a job_id immediately.

    Suitable for scenarios that need multiple independent sub-tasks running simultaneously:
      1. Call delegate_task_submit multiple times to submit all sub-tasks (each returns a job_id immediately)
      2. Use delegate_task_result(job_id) to poll each task's status
      3. Aggregate results after all are done

    Args:
        task: Sub-task description (detailed goal, constraints, and expected output format).
        context: Optional supplementary context.
        depth: Current delegation depth (managed automatically).

    Returns:
        JSON string in the format: {"job_id": "...", "status": "running", "label": "..."}
    """
    err = _check_init()
    if err:
        return err

    # Guard: LLM sometimes wraps both fields into the task argument as a dict
    if isinstance(task, dict):
        context = task.get("context", context)
        task = task.get("task", "")

    runner, err = _build_runner(depth, task[:80])
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
