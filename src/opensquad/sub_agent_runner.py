"""
SubAgentRunner v1.1

In-process lightweight sub-agent executor.
- Independent ChatAPI instance (no state shared with parent agent)
- Limited turn loop (at most MAX_TURNS turns)
- Sub-agent tool set automatically removes delegate_task to prevent infinite recursion
- Maximum delegation depth MAX_DEPTH = 3
- Sub-task timeout TASK_TIMEOUT = 300 seconds

v1.1 additions:
- SubAgentJobManager: background async task management (submit / get_result / cleanup)
  Supports submitting multiple sub-tasks simultaneously; parent agent polls for results
  to achieve true concurrency.
"""

import asyncio
import logging
import uuid
from typing import Any

from opensquad.events import bus

logger = logging.getLogger(__name__)

MAX_TURNS = 200  # Maximum LLM calls per sub-task
MAX_DEPTH = 3  # Maximum recursive delegation depth
TASK_TIMEOUT = 300  # Sub-task total timeout (seconds)


# ---------------------------------------------------------------------------
# SubAgentJobManager -- background task management (supports concurrent sub-agents)
# ---------------------------------------------------------------------------


class _JobEntry:
    """State container for a single background sub-task."""

    __slots__ = ("_asyncio_task", "job_id", "label", "result", "status")

    def __init__(self, job_id: str, label: str):
        self.job_id = job_id
        self.label = label  # Task summary (first 60 chars of task)
        self.status = "pending"  # pending / running / done / error
        self.result: str | None = None
        self._asyncio_task: asyncio.Task | None = None


class SubAgentJobManager:
    """
    Background sub-agent task manager (in-process singleton).

    Usage:
        job_id = job_manager.submit(runner, task)
        info   = job_manager.get_result(job_id)
        job_manager.cleanup(job_id)   # optional; frees memory after reading result
    """

    def __init__(self):
        self._jobs: dict[str, _JobEntry] = {}
        # Sync + async runners currently executing (for abort/stop/new_session).
        self._active_runners: set[SubAgentRunner] = set()

    def register_runner(self, runner: "SubAgentRunner") -> None:
        self._active_runners.add(runner)

    def unregister_runner(self, runner: "SubAgentRunner") -> None:
        self._active_runners.discard(runner)

    def cancel_all(self, reason: str = "aborted") -> int:
        """
        Abort every in-flight sub-agent (sync + async) and cancel asyncio jobs.
        Returns the number of runners/jobs signalled.
        """
        n = 0
        for runner in list(self._active_runners):
            try:
                runner.abort(reason)
                n += 1
            except Exception:
                logger.debug("[JobManager] runner.abort failed", exc_info=True)
        for jid, entry in list(self._jobs.items()):
            if entry.status in ("done", "error", "cancelled"):
                continue
            entry.status = "cancelled"
            entry.result = f"Cancelled: {reason}"
            task = entry._asyncio_task
            if task is not None and not task.done():
                task.cancel()
                n += 1
                logger.warning(f"[JobManager] cancelled job {jid} ({reason})")
        return n

    def submit(self, runner: "SubAgentRunner", task: str) -> str:
        """
        Start a sub-agent in the background and return a job_id immediately.
        The caller must poll get_result(job_id) afterwards to retrieve the result.
        """
        job_id = uuid.uuid4().hex[:10]
        entry = _JobEntry(job_id=job_id, label=task[:60])
        entry.status = "running"
        self._jobs[job_id] = entry
        # Stamp job_id onto the runner so every streamed event nests under
        # the correct async submit fold in the UI (incl. after refresh).
        runner._job_id = job_id
        self.register_runner(runner)

        async def _run():
            try:
                entry.result = await runner.run_task(task)
                if runner.is_aborted:
                    entry.status = "cancelled"
                    entry.result = entry.result or f"Cancelled: {runner.abort_reason}"
                else:
                    entry.status = "done"
                logger.info(f"[JobManager] job {job_id} {entry.status}, result_len={len(entry.result or '')}")
            except asyncio.CancelledError:
                entry.result = "Cancelled: aborted"
                entry.status = "cancelled"
                logger.warning(f"[JobManager] job {job_id} CancelledError")
                raise
            except Exception as e:
                if runner.is_aborted:
                    entry.result = f"Cancelled: {runner.abort_reason}"
                    entry.status = "cancelled"
                else:
                    entry.result = f"Error: sub-task exception -- {e}"
                    entry.status = "error"
                    logger.error(f"[JobManager] job {job_id} error: {e}")
            finally:
                try:
                    runner._flush_thought_persist()
                except Exception:
                    pass
                self.unregister_runner(runner)

        entry._asyncio_task = asyncio.create_task(_run())
        logger.info(f"[JobManager] submitted job {job_id}: {entry.label}")
        return job_id

    def get_result(self, job_id: str) -> dict:
        """
        Query task status and result.

        Return format:
            {"job_id": ..., "status": "pending|running|done|error", "result": str|None}
        """
        entry = self._jobs.get(job_id)
        if entry is None:
            return {"job_id": job_id, "status": "not_found", "result": None}
        return {
            "job_id": entry.job_id,
            "label": entry.label,
            "status": entry.status,
            "result": entry.result,
        }

    def list_jobs(self) -> list:
        """Return a summary list of all active jobs (for debugging or agent progress overview)."""
        return [{"job_id": e.job_id, "label": e.label, "status": e.status} for e in self._jobs.values()]

    def cleanup(self, job_id: str) -> bool:
        """Free memory for a completed task. Returns True if cleanup succeeded."""
        entry = self._jobs.get(job_id)
        if entry is None:
            return False
        # If the task is still running, cancel it
        if entry._asyncio_task and not entry._asyncio_task.done():
            entry._asyncio_task.cancel()
            logger.warning(f"[JobManager] job {job_id} cancelled during cleanup")
        del self._jobs[job_id]
        return True

    def cleanup_done(self) -> int:
        """Bulk-clean all completed/errored tasks; returns the count removed."""
        done_ids = [jid for jid, e in self._jobs.items() if e.status in ("done", "error")]
        for jid in done_ids:
            del self._jobs[jid]
        return len(done_ids)


# In-process singleton -- delegate.py imports this directly
job_manager = SubAgentJobManager()


class SubAgentRunner:
    """
    Lightweight sub-agent executor.

    Usage:
        runner = SubAgentRunner(chat_api_cfg, tool_registry, delegation_depth=1)
        result = await runner.run_task(task_description)
    """

    def __init__(
        self,
        chat_api_cfg: dict,
        tool_registry,
        delegation_depth: int = 1,
        sid: str | None = None,
        sub_task_label: str = "",
        job_id: str | None = None,
    ):
        """
        chat_api_cfg: dict containing all parameters needed to instantiate ChatAPI;
            built by delegate.py from the parent agent config.
            Required: api_key, base_url, model, prompt, api_protocol
            Optional: token_max, temperature, timeout, is_img_model, is_audio_model, is_video_model,
                      use_file_api, file_api_size_threshold
        tool_registry: Parent ToolRegistry instance (read-only shared; sub-agent does not modify registry)
        delegation_depth: Current delegation depth (starting from 1)
        sid: Parent agent session_id; when set, sub-agent events are emitted to this session
             so the parent's workflow panel shows sub-agent progress in real time.
        sub_task_label: Short label (first ~60 chars of task) used in frontend events.
        job_id: Async job id (delegate_task_submit); stamped on every event for UI nesting.
        """
        self.chat_api_cfg = chat_api_cfg
        self.tool_registry = tool_registry
        self.delegation_depth = delegation_depth
        self._chat_api = None
        self._sid = sid
        self._sub_task_label = sub_task_label or ""
        self._job_id = job_id or ""
        # Live thought chunks are still streamed; persistence coalesces to one
        # event per thought phase so refresh does not explode into fragments.
        self._thought_persist_buf: list[str] = []
        self._aborted = False
        self._abort_reason = ""

    @property
    def is_aborted(self) -> bool:
        return self._aborted

    @property
    def abort_reason(self) -> str:
        return self._abort_reason or "aborted"

    def abort(self, reason: str = "aborted") -> None:
        """Signal this runner to stop emitting and exit its turn loop ASAP."""
        self._aborted = True
        self._abort_reason = reason or "aborted"
        logger.info(f"[SubAgentRunner] abort requested depth={self.delegation_depth} reason={self._abort_reason}")

    def _should_stop(self) -> bool:
        if self._aborted:
            return True
        try:
            from opensquad.input_hub import input_hub

            if input_hub is not None and getattr(input_hub, "is_stop_requested", None):
                if input_hub.is_stop_requested():
                    self.abort("stop_task")
                    return True
        except Exception:
            pass
        return False

    def _tag_payload(self, data) -> dict:
        """Normalize emit payload and always stamp sub-agent metadata."""
        if isinstance(data, str):
            payload = {"text": data}
        elif isinstance(data, dict):
            payload = dict(data)
        else:
            payload = {"text": str(data)}
        payload["sub_agent"] = True
        payload["sub_task_label"] = self._sub_task_label
        if self._job_id:
            payload["job_id"] = self._job_id
        return payload

    def _flush_thought_persist(self) -> None:
        """Persist coalesced thought text (one row per thought phase)."""
        if not self._thought_persist_buf:
            return
        text = "".join(self._thought_persist_buf)
        self._thought_persist_buf.clear()
        if text.strip():
            self._persist_sub_event("thought", self._tag_payload(text))

    def _persist_sub_event(self, etype: str, data: dict) -> None:
        """Best-effort persist so refresh/history_sync can rebuild the nest."""
        try:
            from opensquad import session_manager as _sm_module

            sm = getattr(_sm_module, "session_manager", None)
            if sm is not None and hasattr(sm, "add_event"):
                sm.add_event(etype, dict(data))
        except Exception:
            logger.debug("[SubAgentRunner] persist sub event skipped", exc_info=True)

    def _emit_sub_sync(self, etype: str, data) -> None:
        """Sync bus emit used by ChatAPI stream callbacks (must stay tagged)."""
        if self._aborted:
            return
        payload = self._tag_payload(data)
        if self._sid:
            bus.emit(etype, {"sid": self._sid, "data": payload})
        else:
            bus.emit(etype, payload)
        # Thought: stream live, but coalesce on disk. Other events flush any
        # pending thought phase first so order stays correct on refresh.
        if etype == "thought":
            text = payload.get("text", "") if isinstance(payload, dict) else str(data)
            if text:
                self._thought_persist_buf.append(str(text))
        else:
            self._flush_thought_persist()
            self._persist_sub_event(etype, payload)

    async def _emit_sub(self, etype: str, data: dict):
        """Emit a frontend event under the parent agent's session_id, tagged as sub-agent."""
        if self._aborted:
            return
        payload = self._tag_payload(data)
        if self._sid:
            await bus.emit_async(etype, {"sid": self._sid, "data": payload})
        else:
            # Still emit so local/dev UIs can see activity even without sid.
            await bus.emit_async(etype, payload)
        if etype == "thought":
            text = payload.get("text", "") if isinstance(payload, dict) else ""
            if text:
                self._thought_persist_buf.append(str(text))
        else:
            self._flush_thought_persist()
            self._persist_sub_event(etype, payload)

    def _build_chat_api(self):
        """Instantiate the appropriate ChatAPI subclass based on api_protocol."""
        cfg = self.chat_api_cfg
        provider = cfg.get("api_protocol", "openai")

        # Sub-agent system prompt: concise version focused on completing a single task
        prompt = cfg.get(
            "prompt",
            "You are a sub-agent focused on completing a single task. Execute the task and return a concise natural-language result when done; do not call any tools after finishing.",
        )

        from opensquad.xml_parser import StreamingTagParser

        # Route XML <thought>/<think> chunks through the tagged sub-agent path so
        # the frontend can nest them under the parent delegate_task fold.
        stream_parser = StreamingTagParser(
            {
                "thought": lambda x: self._emit_sub_sync("thought", x),
                "think": lambda x: self._emit_sub_sync("thought", x),
            }
        )

        common_kwargs = dict(
            api_key=cfg.get("api_key", ""),
            base_url=cfg.get("base_url", ""),
            model=cfg.get("model", "") or cfg.get("model_name", ""),
            prompt=prompt,
            stream_parser=stream_parser,
            token_max=cfg.get("token_max", 32000),
            temperature=cfg.get("temperature", 0.3),
            timeout=cfg.get("timeout", 60.0),
            is_img_model=cfg.get("is_img_model", False) or cfg.get("is_image", False),
            is_audio_model=cfg.get("is_audio_model", False),
            is_video_model=cfg.get("is_video_model", False) or cfg.get("is_video", False),
            use_file_api=cfg.get("use_file_api", False),
            file_api_size_threshold=cfg.get("file_api_size_threshold", 4 * 1024 * 1024),
        )

        if provider in ("claude", "anthropic"):
            from opensquad.claude_api import ClaudeAPI

            api = ClaudeAPI(
                **common_kwargs,
                is_think=cfg.get("is_think", False),
                thinking_budget_tokens=cfg.get("thinking_budget_tokens", 10000),
                reasoning_effort=cfg.get("reasoning_effort", "high"),
            )
        elif provider in ("google", "gemini"):
            from opensquad.google_api import GoogleAPI

            api = GoogleAPI(**common_kwargs)
        else:
            from opensquad.chat_api import ChatAPI

            api = ChatAPI(
                **common_kwargs,
                is_think=cfg.get("is_think", False),
                reasoning_effort=cfg.get("reasoning_effort", "high"),
            )

        # Native reasoning_content / stream emits go through ChatAPI._emit_with_sid.
        # Without this, those thoughts hit the parent session untagged and break
        # delegate nesting (and hide subsequent sub tool_calls as orphans).
        api._sid_provider = lambda: self._sid
        api._emit_with_sid = lambda etype, data: self._emit_sub_sync(etype, data)
        return api

    def _get_sub_registry(self):
        """
        Return a filtered view of the tool registry with delegate_task removed.
        Does not modify the parent registry; wraps it in a thin proxy instead.
        """
        return _FilteredRegistry(self.tool_registry, exclude={"delegate_task"})

    async def run_task(self, task: str) -> str:
        """
        Execute a sub-task and return the final text result.

        task: Natural-language task description
        Returns: Final LLM text output (forced return after MAX_TURNS turns)
        """
        # Depth check
        if self.delegation_depth > MAX_DEPTH:
            return f"Error: Sub-agent delegation depth exceeds limit {MAX_DEPTH}; execution refused."

        logger.info(f"[SubAgentRunner] depth={self.delegation_depth} starting task (len={len(task)}): {task[:100]}...")
        job_manager.register_runner(self)
        try:
            result = await asyncio.wait_for(self._execute(task), timeout=TASK_TIMEOUT)
            logger.info(f"[SubAgentRunner] depth={self.delegation_depth} task completed, result_len={len(result)}")
            return result
        except asyncio.CancelledError:
            self.abort("cancelled")
            logger.warning(f"[SubAgentRunner] depth={self.delegation_depth} task cancelled")
            return f"Cancelled: {self.abort_reason}"
        except asyncio.TimeoutError:
            logger.error(f"[SubAgentRunner] depth={self.delegation_depth} task timed out after {TASK_TIMEOUT}s")
            return f"Error: Sub-task timed out ({TASK_TIMEOUT}s); try splitting into smaller tasks."
        except Exception as e:
            if self._aborted:
                return f"Cancelled: {self.abort_reason}"
            logger.exception(f"[SubAgentRunner] depth={self.delegation_depth} task failed: {e}")
            return f"Error: Sub-task execution failed -- {e}"
        finally:
            self._flush_thought_persist()
            job_manager.unregister_runner(self)

    async def _execute(self, task: str) -> str:
        """Internal execution loop (no timeout wrapper)."""
        import json as _json
        from datetime import datetime as _dt

        # Notify frontend that sub-agent has started
        await self._emit_sub("info", {"message": f"[Sub-Agent] Starting: {self._sub_task_label or task[:80]}"})

        # Build an independent ChatAPI instance (reuse if already injected externally, e.g. for mock testing)
        if self._chat_api is None:
            self._chat_api = self._build_chat_api()

        # Get the filtered tool registry
        sub_registry = self._get_sub_registry()

        # Extract fake config to select tool call strategy
        fake_config = {
            "model": {
                "tool_call_mode": self.chat_api_cfg.get("tool_call_mode", "auto"),
                "tool_filter": self.chat_api_cfg.get("tool_filter", "all"),
                "api_protocol": self.chat_api_cfg.get("api_protocol", "openai"),
                "model_name": self.chat_api_cfg.get("model", ""),
            }
        }

        # Select strategy using the fake config
        from opensquad.tool_call_strategy import ToolCallStrategySelector

        self.tool_call_strategy = ToolCallStrategySelector.select(fake_config, sub_registry)

        # Prepare LLM call parameters (this handles both XML and Native FC mode)
        llm_params = self.tool_call_strategy.prepare_llm_call(self._chat_api.get_system_prompt())
        new_sys = llm_params.get("system_prompt")
        if new_sys:
            self._chat_api.update_system_prompt(new_sys)
        current_tools = llm_params.get("tools")
        current_tool_choice = llm_params.get("tool_choice", "auto")

        # First-turn input: task description (tool docs are no longer manually injected if Native FC is active)
        # Note: In Native FC, the tool docs are sent via tools parameter. In XML mode, they are injected into system_prompt.
        current_input = task

        last_text = ""

        for turn in range(1, MAX_TURNS + 1):
            if self._should_stop():
                logger.info(
                    f"[SubAgentRunner] depth={self.delegation_depth} stopping at turn={turn} ({self.abort_reason})"
                )
                return f"Cancelled: {self.abort_reason}"

            logger.debug(f"[SubAgentRunner] depth={self.delegation_depth} turn={turn}")

            # Call LLM (async chat())
            try:
                ai_response = await asyncio.wait_for(
                    self._chat_api.chat(
                        current_input,
                        tools=current_tools,
                        tool_choice=current_tool_choice,
                        tool_call_strategy=self.tool_call_strategy,
                    ),
                    timeout=120.0,
                )
            except asyncio.TimeoutError:
                logger.error(f"[SubAgentRunner] LLM call timed out at turn {turn}")
                return "Error: LLM call timed out"
            except Exception as e:
                logger.error(f"[SubAgentRunner] LLM call failed at turn {turn}: {e}")
                return f"Error: LLM call failed -- {e}"

            if not ai_response:
                logger.warning(f"[SubAgentRunner] Empty LLM response at turn {turn}")
                break

            tool_data_from_api = None
            # chat() now returns a dict {"text": ..., "tool_data": ...}; unwrap it
            if isinstance(ai_response, dict):
                response_text = ai_response.get("text", "") or ""
                tool_data_from_api = ai_response.get("tool_data")
                ai_response = response_text

            # Parse response
            from opensquad.runner import ResponseParser

            # Prioritize tool_data from API strategy (Native FC mode)
            if tool_data_from_api:
                tool_calls = tool_data_from_api  # List[(name, args)]
                logger.info(f"[SubAgentRunner] [OK] Using tool_data from Native FC strategy: {len(tool_calls)} tool(s)")
            else:
                xml_result = ResponseParser.parse_tool_call(ai_response)
                tool_calls = [xml_result] if xml_result else None

            if tool_calls:
                # Execute all tool calls sequentially
                all_results = []
                for call_index, (t_name, t_args) in enumerate(tool_calls):
                    if self._should_stop():
                        return f"Cancelled: {self.abort_reason}"
                    logger.info(f"[SubAgentRunner] turn={turn} tool_call #{call_index}: {t_name}")

                    # Build call_id consistent with main runner format
                    call_id = f"sub_{_dt.now().strftime('%M%S')}_{t_name}_{call_index}"
                    t_args_json = _json.dumps(t_args, ensure_ascii=False, indent=2) if t_args else "{}"

                    # Emit tool_call event to frontend (under parent session)
                    await self._emit_sub(
                        "tool_call",
                        {
                            "id": call_id,
                            "name": t_name,
                            "args": t_args_json,
                        },
                    )

                    # Execute tool (bind call_id so shell Jobs can stream to the CMD panel)
                    try:
                        from opensquad.tools.system import reset_tool_call_context, set_tool_call_context

                        _ctx_token = set_tool_call_context(
                            sid=getattr(self, "_sid", "") or "",
                            call_id=call_id,
                            tool_name=t_name,
                        )
                        try:
                            tool_result = await sub_registry.call(t_name, t_args)
                        finally:
                            reset_tool_call_context(_ctx_token)
                    except Exception as e:
                        tool_result = f"Error: tool {t_name} execution failed -- {e}"

                    # Emit tool_result event to frontend
                    await self._emit_sub(
                        "tool_result",
                        {
                            "id": call_id,
                            "name": t_name,
                            "args": t_args_json,
                            "result": tool_result,
                        },
                    )

                    all_results.append(f'[tool_result name="{t_name}"]\n{tool_result}\n[/tool_result]')

                # Combine all tool results as next-turn input
                current_input = "\n\n".join(all_results)
                last_text = ""
            else:
                # No tool call -> extract text, end this turn
                from opensquad.runner import ResponseParser as RP

                user_msg = RP.parse_to_user(ai_response) if hasattr(RP, "parse_to_user") else _extract_text(ai_response)
                if not user_msg:
                    user_msg = _extract_text(ai_response)
                last_text = user_msg.strip()
                logger.info(f"[SubAgentRunner] depth={self.delegation_depth} final answer at turn={turn}")
                break

        if not last_text:
            last_text = f"[Sub-agent produced no final text output within {MAX_TURNS} turns]"

        # Notify frontend that sub-agent has finished + surface the final answer
        # (parent tool_result also carries this text; panel shows it as Result).
        await self._emit_sub(
            "info",
            {
                "event": "sub_agent_result",
                "message": f"[Sub-Agent] Done: {self._sub_task_label or task[:80]}",
                "text": last_text,
                "result": last_text,
            },
        )

        return last_text


class _FilteredRegistry:
    """
    Thin wrapper around the parent ToolRegistry that filters out specified tool names on calls.
    Only implements the interfaces needed by SubAgentRunner: call() and get_tool_docs().
    """

    def __init__(self, parent_registry, exclude: set):
        self._parent = parent_registry
        self._exclude = exclude

    def _is_excluded(self, name: str) -> bool:
        """
        Check whether a fully-qualified tool name belongs to an excluded namespace.
        name may be "delegate_task.delegate_task" (full name) or "delegate_task" (namespace).
        """
        # Full name format: ns.fn
        ns = name.split(".", 1)[0] if "." in name else name
        return ns in self._exclude

    async def call(self, name: str, args: Any) -> str:
        if self._is_excluded(name):
            return f"Error: tool '{name}' is disabled in sub-agent (prevents infinite recursion)."
        return await self._parent.call(name, args)

    def get_all_tool_names(self) -> list:
        """Return filtered tool name list (filtered by namespace)."""
        try:
            all_names = self._parent.get_all_tool_names()
        except AttributeError:
            # Compatibility with older registry versions
            try:
                all_names = list(self._parent._tools.keys())
            except Exception:
                return []
        return [n for n in all_names if not self._is_excluded(n)]

    def get_tool_docs(self, name: str) -> str | None:
        """Proxy tool documentation lookup to the parent registry."""
        if self._is_excluded(name):
            return None
        try:
            return self._parent.get_tool_docs(name)
        except Exception:
            return None

    def generate_tool_descriptions(self) -> str:
        """Proxy for XMLToolCallStrategy: generate text-based tool descriptions (filtered)."""
        try:
            full_desc = self._parent.generate_tool_descriptions()
        except Exception:
            return ""
        # Filter out excluded namespaces from the text description
        # Each namespace block starts with a line like "### namespace" or "- namespace.func"
        # Simple approach: delegate to parent and remove excluded blocks
        if not self._exclude:
            return full_desc
        lines = full_desc.split("\n")
        filtered = []
        skip = False
        for line in lines:
            # Check if this line starts a new namespace block
            is_ns_header = False
            for ns in self._exclude:
                if line.strip().startswith(f"### {ns}") or line.strip().startswith(f"## {ns}"):
                    is_ns_header = True
                    skip = True
                    break
            if not is_ns_header and skip:
                # Check if we hit a new non-excluded namespace header
                if line.strip().startswith("### ") or line.strip().startswith("## "):
                    skip = False
            if not skip:
                filtered.append(line)
        return "\n".join(filtered)

    def generate_openai_tools(self, tool_filter: str = "all") -> list:
        """Proxy for NativeToolCallStrategy: generate OpenAI Tools schema (filtered)."""
        try:
            all_tools = self._parent.generate_openai_tools(tool_filter=tool_filter)
        except Exception:
            return []
        # Filter out tools whose name starts with an excluded namespace
        return [t for t in all_tools if not self._is_excluded(t.get("function", {}).get("name", ""))]


def _get_tool_docs(registry) -> str:
    """Extract brief documentation for all tools from the registry, for injection into the sub-agent's first message."""
    try:
        names = registry.get_all_tool_names()
    except Exception:
        return ""

    lines = []
    for name in names:
        doc = registry.get_tool_docs(name)
        if doc:
            # Use only the first line of the description
            first_line = doc.strip().split("\n")[0][:120]
            lines.append(f"- {name}: {first_line}")
        else:
            lines.append(f"- {name}")
    return "\n".join(lines)


def _extract_text(response: str) -> str:
    """
    Extract plain text from an LLM response (strips XML tag blocks).
    Aligned with the _remove_all_tags logic in runner.py but lighter weight.
    """
    if not response:
        return ""
    import re

    text = response
    # Remove thought/plan/think/tool_call/tool_result blocks
    silent_blocks = [
        "thought",
        "plan",
        "think",
        "tool_call",
        "tool_result",
        "to_system",
        "state",
        "wake",
        "sleep",
        "title",
        "option",
        "arguments",
    ]
    for tag in silent_blocks:
        text = re.sub(rf"<{tag}\b[^>]*>.*?</{tag}>", "", text, flags=re.DOTALL | re.IGNORECASE)
    # Keep to_user content
    text = re.sub(r"<to_user\b[^>]*>(.*?)</to_user>", r"\1", text, flags=re.DOTALL | re.IGNORECASE)
    # Remove remaining tags
    text = re.sub(r"<[^>]+>", "", text)
    return text.strip()
