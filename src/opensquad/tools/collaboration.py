# -*- coding: utf-8 -*-
"""
Collaboration Tools v4.1

Multi-agent collaboration lifecycle management:
- Start/join/end collaboration sessions
- Load/unload collab cards into agent prompts
- Query team status and group rosters
- Structured task assignment (v4.1): assign_task, add_subtask, update_task_progress

v4.1 changes:
- Added assign_task() for PM to assign tasks to specific workers with structured subtasks
- Added add_subtask() for adding subtasks to existing assignments
- Added update_task_progress() for workers to update progress without touching Markdown
- Workers no longer need to parse/rewrite Markdown — just call update_task_progress(subtask_id, status)
"""
import os
import json
import logging
import threading
from typing import Dict, Any, List, Optional
from datetime import datetime

logger = logging.getLogger(__name__)

# Serialize read-modify-write sequences on collab_board items.
# collab_board._LOCK only protects single file I/O, not cross-function
# sequences like: list_items() -> modify extra -> upsert_item().
# Without this, concurrent agents updating subtasks can overwrite each other.
_collab_rw_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def _project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def _collab_cards_dir() -> str:
    return os.path.join(_project_root(), "collab_cards")

def _agents_dir() -> str:
    return os.path.join(_project_root(), "agents")


# ---------------------------------------------------------------------------
# Collab card discovery & parsing (reuses skill_loader parser)
# ---------------------------------------------------------------------------

def list_collab_cards() -> Dict[str, Any]:
    """
    List all available collaboration cards.
    Each card defines a collaboration pattern (e.g., software development, research).

    Returns a list of cards with name, description, suggested_roles, and tags.
    """
    cards_dir = _collab_cards_dir()
    if not os.path.isdir(cards_dir):
        return {"status": "success", "cards": []}

    results = []
    for fname in sorted(os.listdir(cards_dir)):
        if not fname.endswith(".md"):
            continue
        card_name = fname[:-3]
        fpath = os.path.join(cards_dir, fname)
        try:
            from ..skill_loader import parse_skill_md
            fm, _ = parse_skill_md(fpath)
            results.append({
                "name": card_name,
                "display_name": fm.get("name", card_name),
                "description": fm.get("description", ""),
                "suggested_roles": fm.get("suggested_roles", []),
                "tags": fm.get("tags", ""),
            })
        except Exception as e:
            logger.warning(f"[Collab] Failed to parse collab card {card_name}: {e}")
            results.append({"name": card_name, "description": "(parse error)", "suggested_roles": []})

    return {"status": "success", "cards": results}


# ---------------------------------------------------------------------------
# Collaboration lifecycle
# ---------------------------------------------------------------------------

def start_collaboration(card: str, members: Optional[List[str]] = None,
                        group_id: str = "",
                        project_name: str = "",
                        project_description: str = "") -> Dict[str, Any]:
    """
    [PM only] Start a collaboration session.

    This will:
    1. Load the collab card into YOUR prompt (persistent for the duration)
    2. Optionally send a group chat message @mentioning suggested members to join

    The collab card's suggested_roles are advisory -- you decide who to invite and how
    to assign work. After starting, use group chat to discuss and assign tasks.
    Each member (including yourself) should communicate and track tasks
    via group chat.

    Args:
        card: Collab card name (filename without .md under collab_cards/, e.g. "software_dev_team")
        members: Optional list of agent directory names to invite (e.g. ["coder", "qa"]).
                 If not provided, the invitation step is skipped -- you can decide later via group chat.
        group_id: ID or name of the group to send the invitation to.
                  Must be a group that all invited members have joined.
                  Required if members is provided.
        project_name: Human-readable project name
        project_description: Brief description of the project goal
    """
    # 1. Validate collab card exists
    card_file = os.path.join(_collab_cards_dir(), f"{card}.md")
    if not os.path.exists(card_file):
        return {"status": "error", "message": f"Collab card '{card}' not found in {_collab_cards_dir()}"}

    # 2. Load collab card into own prompt via skill_loader
    # NOTE: import the module itself, not `_loaded_skills` by value.
    # skill_loader.add_skill_from_file() rebinds `_loaded_skills` to a new list,
    # so a previously imported list reference would become stale and private marking would fail.
    from .. import skill_loader as _skill_loader
    result = _skill_loader.add_skill_from_file(card_file, f"collab_{card}")
    if not result.get("success"):
        return {"status": "error", "message": f"Failed to load collab card: {result.get('error')}"}

    # Mark the loaded skill as private so it gets full injection
    for s in _skill_loader.get_loaded_skills():
        if s.name == f"collab_{card}":
            s.is_private = True
            break

    # 3. Create collaboration task id (6-char alnum) for board tracking
    task_rec = None
    try:
        from ..input_hub import input_hub
        from ..collab_board import create_task
        agent_dir = input_hub.agent_dir or ""
        creator = os.path.basename(agent_dir) if agent_dir else "unknown_agent"
        task_rec = create_task(task_name=project_name or card, created_by=creator)
    except Exception as e:
        logger.warning(f"[Collab] Failed to create task id: {e}")

    # 4. Optionally send group chat invitation to members
    im_result = None
    if not members:
        im_result = "No members specified; decide who to invite via group chat"
    elif not group_id:
        im_result = "No group_id provided; notify members manually"
    else:
        from ..input_hub import input_hub
        agent_dir = input_hub.agent_dir
        current_agent = os.path.basename(agent_dir) if agent_dir else "unknown"

        # Build @mention list
        mention_parts = []
        for m in members:
            config_path = os.path.join(_agents_dir(), m, "config.json")
            agent_name = m
            if os.path.exists(config_path):
                from opensquad.json_cache import load_json_cached
                cfg = load_json_cached(config_path)
                agent_name = cfg.get("agent_name", m) if cfg else m
            mention_parts.append(f"@{agent_name}")

        mentions_str = " ".join(mention_parts)
        _task_id = task_rec.get("task_id") if isinstance(task_rec, dict) else "(pending)"
        invite_msg = (
            f"{mentions_str}\n"
            f"[Collaboration Started] {project_name or 'New Project'}\n"
            f"Task ID: {_task_id}\n"
            f"Collab Card: {card}\n"
            f"{project_description or ''}\n"
            f"You're invited to join -- consider calling: join_collaboration(card=\"{card}\")\n"
            f"All board updates/reads must include collab_id=\"{_task_id}\""
        )

        try:
            from ..bridge import bridge
            if bridge and bridge.token:
                # Resolve group name -> ID if a name was passed instead of an ID
                target = group_id
                groups = bridge.list_groups_api()
                if not any(g.get("id") == group_id for g in groups if isinstance(g, dict)):
                    for g in groups:
                        if isinstance(g, dict) and g.get("name") == group_id:
                            target = g.get("id", group_id)
                            break
                bridge.send_message(invite_msg, target_id=target, target_type="group")
                im_result = f"Invitation sent to group {target}"

                # Store group info in task metadata for later use (e.g. assign_task notifications)
                if isinstance(task_rec, dict) and task_rec.get("task_id"):
                    try:
                        from ..collab_board import update_task as _cb_update_task
                        _cb_update_task(
                            task_id=task_rec["task_id"],
                            extra={"group_id": target, "group_name": group_id},
                        )
                    except Exception:
                        pass
            else:
                im_result = "Bridge not connected; notify members manually"
        except Exception as e:
            logger.warning(f"[Collab] Failed to send group invitation: {e}")
            im_result = f"Failed to send invitation: {e}"

    # 5. Auto-inject collaboration board protocol as runtime guidance
    try:
        from ..input_hub import input_hub
        input_hub.push(
            "[Collab Board Protocol] Mandatory: use collaboration module board APIs for all board updates. "
            "PM must update Requirements + Plan + Assignment/Progress via collaboration.board_update; "
            "Worker must read assigned tasks via collaboration.board_list/board_list_my_tasks and frequently update progress with [ ]/[>]/[x]. "
            "Board stores only latest snapshots, not full history.",
            source="system"
        )
    except Exception:
        pass

    return {
        "status": "success",
        "message": f"Collaboration started with collab card '{card}'",
        "card_loaded": True,
        "task": task_rec,
        "invitation": im_result,
        "members": members or [],
        "next_steps": (
            "1. Review the collab card now loaded in your prompt\n"
            "2. ⚠️ Check skill library: use `agent_setup.list_skills()` to see if any existing skills match this task — activate them before proceeding\n"
            "3. ⚠️ Activate Task Watch: call `task_watch.start(description, check_interval=120)` to enable active supervision for your collaboration task. This prevents stalls and keeps you on track. Use `task_watch.update(progress)` after each sub-task, and `task_watch.complete(summary)` when done.\n"
            "4. Use task.task_id as collab_id for ALL board operations\n"
            "5. MUST use collaboration.board_update to write Requirements Zone (goals/scope/constraints/acceptance)\n"
            "6. MUST use collaboration.board_update to write Plan Zone (architecture/workflow/module boundaries/risks)\n"
            "7. ⚠️ MUST use collaboration.assign_task (NOT board_update) to assign tasks to each worker — call it once PER worker with a unique item_key\n"
            "8. Discuss assignment with team via @mention in group chat\n"
            "9. Continuously monitor progress via board_list and worker updates"
        ),
        "task_assignment_guide": {
            "description": "Use this guide to write properly structured task assignments on the collaboration board.",
            "rule": "Use assign_task() to assign tasks to workers. Each call creates one task entry under the target worker's agent_id with structured subtasks.",
            "pm_example": (
                "# Example: PM assigns auth module to coder using assign_task (structured API)\n"
                'assign_task(\n'
                '    collab_id="a8K2pQ",\n'
                '    worker_id="coder",\n'
                '    task_name="用户认证模块",\n'
                '    description="实现完整的用户认证功能",\n'
                '    file_scope="src/auth/",\n'
                '    dependencies="none",\n'
                '    deadline="2h",\n'
                '    acceptance_criteria="单元测试全部通过，错误处理完善",\n'
                '    subtasks=[\n'
                '        {"title": "登录API接口", "description": "POST /api/login, 参数验证username/password, 返回JWT token"},\n'
                '        {"title": "注册API接口", "description": "POST /api/register, bcrypt加密, 邮箱验证"},\n'
                '        {"title": "Token刷新", "description": "POST /api/token/refresh, access_token 15min, refresh_token 7days"},\n'
                '    ],\n'
                '    item_key="task_coder_auth"\n'
                ')\n\n'
                "# Returns: {subtask_ids: {'登录API接口': 'st_task_coder_auth_1', ...}}\n\n"
                "# Assign another task to qa_agent:\n"
                'assign_task(\n'
                '    collab_id="a8K2pQ",\n'
                '    worker_id="qa",\n'
                '    task_name="认证模块测试",\n'
                '    file_scope="tests/",\n'
                '    dependencies="task_coder_auth",\n'
                '    deadline="1h",\n'
                '    acceptance_criteria="测试覆盖率 > 80%",\n'
                '    subtasks=[\n'
                '        {"title": "编写登录API单元测试", "description": "正常登录、错误密码、空字段等场景"},\n'
                '        {"title": "编写注册API集成测试", "description": "重复注册检测、密码强度验证"},\n'
                '    ],\n'
                '    item_key="task_qa_test"\n'
                ')'
            ),
            "worker_example": (
                "# Example: Worker(coder) updates subtask progress using update_task_progress (NO Markdown needed!)\n"
                'update_task_progress(\n'
                '    collab_id="a8K2pQ",\n'
                '    item_key="task_coder_auth",\n'
                '    subtask_id="st_task_coder_auth_1",\n'
                '    status="done",\n'
                '    progress=100,\n'
                '    note="API已实现并通过本地测试"\n'
                ')\n\n'
                "# Batch update multiple subtasks:\n"
                'batch_update_tasks(\n'
                '    collab_id="a8K2pQ",\n'
                '    item_key="task_coder_auth",\n'
                '    updates=[\n'
                '        {"subtask_id": "st_task_coder_auth_1", "status": "done", "progress": 100},\n'
                '        {"subtask_id": "st_task_coder_auth_2", "status": "doing", "progress": 50},\n'
                '    ]\n'
                ')'
            ),
            "key_rules": (
                "1. PM uses assign_task() — structured parameters, no Markdown writing needed\n"
                "2. Workers use update_task_progress() — just pass subtask_id and status, no content rewriting\n"
                "3. Each subtask gets a unique ID (st_{item_key}_{index}) returned by assign_task\n"
                "4. Workers find their tasks and subtask IDs via board_list_my_tasks()\n"
                "5. Overall progress is auto-calculated from subtask statuses"
            ),
        },
    }


def join_collaboration(card: str, collab_id: str = "") -> Dict[str, Any]:
    """
    [Worker] Join an active collaboration session.

    This loads the collab card into your prompt so you understand
    the workflow, roles, and communication patterns for the duration.

    After joining, read PM's messages in group chat for your task assignments.

    Args:
        card: Collab card name (as specified by PM in the invitation)
    """
    # 1. Validate collab card
    card_file = os.path.join(_collab_cards_dir(), f"{card}.md")
    if not os.path.exists(card_file):
        return {"status": "error", "message": f"Collab card '{card}' not found in {_collab_cards_dir()}"}

    # 2. Load collab card into own prompt
    from .. import skill_loader as _skill_loader
    result = _skill_loader.add_skill_from_file(card_file, f"collab_{card}")
    if not result.get("success"):
        return {"status": "error", "message": f"Failed to load collab card: {result.get('error')}"}

    # Mark as private for full injection
    for s in _skill_loader.get_loaded_skills():
        if s.name == f"collab_{card}":
            s.is_private = True
            break

    # Auto-inject collaboration board protocol as runtime guidance
    try:
        from ..input_hub import input_hub
        input_hub.push(
            "[Collab Board Protocol] Worker MUST: 1) Call board_list_my_tasks() to find assigned tasks AFTER joining. "
            "2) Use update_task_progress(item_key, subtask_id, status) to update progress — NO Markdown rewriting needed. "
            "3) NEVER use board_update for progress updates — use structured API instead. "
            "Group chat is coordination only, not board update.",
            source="system"
        )
    except Exception:
        pass

    join_tracking = ""
    if collab_id:
        try:
            from ..input_hub import input_hub
            from ..collab_board import update_task
            _agent_dir = input_hub.agent_dir or ""
            _agent_id = os.path.basename(_agent_dir) if _agent_dir else "unknown_agent"
            update_task(task_id=collab_id, add_member=_agent_id)
            join_tracking = f"joined task {collab_id}"
        except Exception as e:
            join_tracking = f"join tracking failed: {e}"

    return {
        "status": "success",
        "message": f"Joined collaboration with collab card '{card}'",
        "card_loaded": True,
        "join_tracking": join_tracking,
        "next_steps": (
            "⚠️ STEP 1 (MANDATORY): View the full collaboration board to understand context\n"
            "   Call: collaboration.board_view(collab_id='...')\n"
            "   - Review requirements, plan, and task assignments\n"
            "   - Understand what needs to be done before starting work\n\n"
            "⚠️ STEP 2 (MANDATORY): Find YOUR assigned tasks\n"
            "   Call: collaboration.board_list_my_tasks(collab_id='...')\n"
            "   - Note down your item_key and subtask_ids from the response\n"
            "   - DO NOT start working until you have read your task assignments\n\n"
            "STEP 3: Check skill library — use `agent_setup.list_skills()` to activate relevant skills\n\n"
            "STEP 4: Activate Task Watch — call `task_watch.start(description, check_interval=180)` for supervision\n\n"
            "STEP 5: Update progress after each subtask using collaboration.update_task_progress():\n"
            "   update_task_progress(collab_id='...', item_key='...', subtask_id='...', status='doing', note='...')\n"
            "   - status values: 'pending' → 'doing' → 'done' → 'blocked'\n"
            "   - Or use batch_update_tasks() to update multiple subtasks at once\n\n"
            "STEP 6: Communicate blockers and key progress in group chat"
        ),
        "task_update_guide": {
            "description": "Use structured API to update progress — NO Markdown reading/writing needed.",
            "workflow": (
                "1. Call board_list_my_tasks() to get your tasks with subtask IDs\n"
                "2. After completing a subtask, call update_task_progress(item_key, subtask_id, status='done')\n"
                "3. Overall progress is auto-calculated — you don't need to compute it"
            ),
            "pm_assigns_example": (
                '# PM calls assign_task() which returns subtask_ids:\n'
                '{"subtask_ids": {"登录API": "st_task_auth_1", "注册API": "st_task_auth_2"}}'
            ),
            "worker_update_example": (
                '# Worker calls update_task_progress (simple, no Markdown):\n'
                'update_task_progress(\n'
                '    collab_id="a8K2pQ",\n'
                '    item_key="task_auth",\n'
                '    subtask_id="st_task_auth_1",\n'
                '    status="done",\n'
                '    progress=100,\n'
                '    note="API已实现"\n'
                ')\n\n'
                '# Or batch update:\n'
                'batch_update_tasks(\n'
                '    collab_id="a8K2pQ",\n'
                '    item_key="task_auth",\n'
                '    updates=[\n'
                '        {"subtask_id": "st_task_auth_1", "status": "done", "progress": 100},\n'
                '        {"subtask_id": "st_task_auth_2", "status": "doing", "progress": 50},\n'
                '    ]\n'
                ')'
            ),
            "key_rules": (
                "1. MUST call board_list_my_tasks() BEFORE starting work — find your item_key and subtask_ids\n"
                "2. Use update_task_progress() for each subtask — pass subtask_id + status\n"
                "3. NEVER use board_update() for progress — use structured API only\n"
                "4. Status flow: pending → doing → done (or blocked)\n"
                "5. Use batch_update_tasks() when multiple subtasks complete simultaneously"
            ),
        },
    }


def end_collaboration(card: str, collab_id: str = "", group_id: str = "") -> Dict[str, Any]:
    """
    [PM only] End a collaboration session after user approval.

    This will:
    1. Unload the collab card from your prompt
    2. Notify all members via group chat to leave

    IMPORTANT: Only call this AFTER the user has confirmed project completion.

    Args:
        card: Collab card name to end
        group_id: ID or name of the group to send the end notification to.
                  Should be the same group used in start_collaboration().
    """
    # 1. Unload collab card
    from ..skill_loader import remove_skill
    unload_result = remove_skill(f"collab_{card}")

    # 2. Notify group chat
    im_result = None
    if not group_id:
        im_result = "No group_id provided; notify members manually"
    else:
        try:
            from ..bridge import bridge
            if bridge and bridge.token:
                # Resolve group name -> ID if needed
                target = group_id
                groups = bridge.list_groups_api()
                if not any(g.get("id") == group_id for g in groups if isinstance(g, dict)):
                    for g in groups:
                        if isinstance(g, dict) and g.get("name") == group_id:
                            target = g.get("id", group_id)
                            break
                mention_str = ""
                mentioned_members = []
                current_agent_id = ""
                try:
                    from ..input_hub import input_hub
                    _dir = input_hub.agent_dir or ""
                    current_agent_id = os.path.basename(_dir) if _dir else ""
                except Exception:
                    current_agent_id = ""

                if collab_id:
                    try:
                        from ..collab_board import list_tasks
                        tasks = list_tasks()
                        target_task = next((t for t in tasks if str(t.get("task_id", "")) == collab_id), None)
                        members = target_task.get("members", []) if isinstance(target_task, dict) else []
                        if members:
                            mentioned_members.extend(members)
                    except Exception as e:
                        logger.warning(f"[Collab] Failed to get members from collab_board: {e}")

                if not mentioned_members:
                    try:
                        from ..bridge import bridge as _bridge
                        if _bridge and _bridge.token:
                            detail = _bridge.get_group_detail_api(target)
                            if detail:
                                raw_members = detail.get("members", [])
                                member_map: Dict[str, str] = {}
                                for m in raw_members:
                                    if isinstance(m, dict):
                                        uid = str(m.get("id", ""))
                                        if uid:
                                            member_map[uid] = m.get("name", "")

                                agents_base = _agents_dir()
                                if os.path.isdir(agents_base):
                                    for entry in sorted(os.listdir(agents_base)):
                                        agent_path = os.path.join(agents_base, entry)
                                        config_path = os.path.join(agent_path, "config.json")
                                        if not os.path.isdir(agent_path) or not os.path.exists(config_path):
                                            continue
                                        from opensquad.json_cache import load_json_cached
                                        cfg = load_json_cached(config_path)
                                        if not cfg:
                                            continue

                                        agent_id = str(cfg.get("agent_id", ""))
                                        if agent_id in member_map:
                                            mentioned_members.append(agent_id)
                    except Exception as e:
                        logger.warning(f"[Collab] Failed to get group members for mention: {e}")

                # Exclude self from the mention list — PM doesn't need to be reminded
                # that they ended the collaboration themselves.
                unique_members = []
                if mentioned_members:
                    unique_members = [m for m in dict.fromkeys(mentioned_members) if m and m != current_agent_id]
                    mention_str = " ".join([f"@{m}" for m in unique_members]) + "\n" if unique_members else ""

                msg = (
                    f"{mention_str}"
                    f"[Collaboration Ended] Collab Card: {card}\n"
                    f"Task ID: {collab_id or '(not provided)'}\n"
                    f"Project completed. Please call leave_collaboration(card=\"{card}\") "
                    f"to unload the collab card."
                )
                bridge.send_message(msg, target_id=target, target_type="group")
                im_result = "End notification sent"
            else:
                im_result = "Bridge not connected; notify members manually"
        except Exception as e:
            logger.warning(f"[Collab] Failed to send end notification: {e}")

    if collab_id:
        try:
            from ..collab_board import update_task
            update_task(task_id=collab_id, status="done")
        except Exception:
            pass

    return {
        "status": "success",
        "message": f"Collaboration '{card}' ended",
        "card_unloaded": unload_result.get("success", False) if isinstance(unload_result, dict) else False,
        "notification": im_result or "Notify members manually",
    }


def delete_collaboration(collab_id: str) -> Dict[str, Any]:
    """
    Delete a collaboration task and all its associated board data.

    This permanently removes:
    - The task record from the collaboration board
    - All board items (requirements, plan, tasks, status, discussions)
    - All snapshot history for this task

    ⚠️ This action is irreversible. Only use when the collaboration is no longer needed.

    Args:
      collab_id: collaboration task id (from start_collaboration)

    Example:
      delete_collaboration(collab_id="a8K2pQ")
    """
    try:
        from ..collab_board import delete_task
        result = delete_task(task_id=collab_id)
        if not result.get("deleted"):
            return {"status": "error", "message": result.get("reason", "Task not found")}
        return {
            "status": "success",
            "message": f"Collaboration '{collab_id}' deleted",
            "items_removed": result.get("items_removed", 0),
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


def leave_collaboration(card: str) -> Dict[str, Any]:
    """
    [Worker] Leave a collaboration session and clean up.

    This will unload the collab card from your prompt.

    Call this after PM ends the collaboration.

    Args:
        card: Collab card name to leave
    """
    # Unload collab card
    from ..skill_loader import remove_skill
    unload_result = remove_skill(f"collab_{card}")

    return {
        "status": "success",
        "message": f"Left collaboration '{card}'",
        "card_unloaded": unload_result.get("success", False) if isinstance(unload_result, dict) else False,
    }


def list_active_collaborations() -> Dict[str, Any]:
    """
    [Worker] List all active collaboration sessions that the current agent has joined.

    Cross-references the current agent's IDs (from config.json and agent_dir)
    against the member list of all active collaborations in collab_board.

    Useful when:
    - Worker missed the @mention in group chat and doesn't know the collab_id
    - Worker wants to quickly discover which collaborations they're part of

    Returns:
      List of active collaborations with task_id, task_name, members, and progress.
    """
    try:
        my_ids = _resolve_my_agent_ids()
        from ..collab_board import list_tasks
        tasks = list_tasks(include_stale=False)

        active_tasks = []
        for t in tasks:
            if t.get("status") not in ("active",):
                continue
            members = t.get("members", [])
            if not isinstance(members, list):
                continue
            # Check if any of my IDs is in the member list
            if any(mid in members for mid in my_ids if mid):
                active_tasks.append({
                    "task_id": t.get("task_id"),
                    "task_name": t.get("task_name"),
                    "collab_card": t.get("task_name", ""),
                    "members": members,
                    "progress": t.get("progress", 0),
                    "created_at": t.get("created_at"),
                    "updated_at": t.get("updated_at"),
                    "member_count": t.get("member_count", len(members)),
                })

        return {
            "status": "success",
            "count": len(active_tasks),
            "collaborations": active_tasks,
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


def assign_task(
    collab_id: str,
    worker_id: str,
    task_name: str,
    item_key: str = "",
    description: str = "",
    file_scope: str = "",
    dependencies: str = "",
    deadline: str = "",
    acceptance_criteria: str = "",
    subtasks: Optional[List[Dict[str, str]]] = None,
    status: str = "pending",
    visibility: str = "public",
) -> Dict[str, Any]:
    """
    [PM only] Assign a structured task to a specific worker agent.

    Instead of writing Markdown in content, use explicit parameters for each field.
    Subtasks are passed as a list of dicts — the backend generates unique IDs for each.

    Args:
      collab_id: collaboration task id (from start_collaboration)
      worker_id: the target worker agent's id (e.g. 'coder', 'qa')
      task_name: short name for this task assignment
      item_key: unique key for this task (auto-generated if empty, e.g. 'task_coder_auth')
      description: brief description of what needs to be done
      file_scope: file/directory scope (e.g. 'src/auth/')
      dependencies: dependency info (e.g. 'none' or 'task_name')
      deadline: deadline string (e.g. '2h', '30min')
      acceptance_criteria: specific measurable acceptance criteria
      subtasks: list of subtask dicts, each with keys:
        - 'title': subtask name (required)
        - 'description': detailed description (optional)
        Example: [{'title': 'Login API', 'description': 'POST /api/login'}, {'title': 'Register API'}]
      status: initial status (default 'pending')
      visibility: 'public' or 'private'

    Returns:
      dict with status, item, assigned_to, and subtask_ids mapping

    Example:
      assign_task(
          collab_id="a8K2pQ",
          worker_id="coder",
          task_name="用户认证模块",
          description="实现完整的用户认证功能",
          file_scope="src/auth/",
          dependencies="none",
          deadline="2h",
          acceptance_criteria="单元测试全部通过，错误处理完善",
          subtasks=[
              {"title": "登录API接口", "description": "POST /api/login, 参数验证username/password, 返回JWT token"},
              {"title": "注册API接口", "description": "POST /api/register, bcrypt加密, 邮箱验证"},
              {"title": "Token刷新", "description": "POST /api/token/refresh, access_token 15min, refresh_token 7days"},
          ],
          item_key="task_coder_auth",
      )
    """
    try:
        from ..collab_board import upsert_item

        if not item_key:
            item_key = f"task_{worker_id}_{task_name[:20].replace(' ', '_').replace('/', '_')}"

        # Build structured subtasks with unique IDs
        subtask_records = []
        for i, st in enumerate(subtasks or [], start=1):
            st_id = f"st_{item_key}_{i}"
            subtask_records.append({
                "id": st_id,
                "title": st.get("title", f"Subtask {i}"),
                "description": st.get("description", ""),
                "status": "pending",
                "progress": 0,
            })

        # Store structured data in the 'extra' field, and generate clean Markdown for display
        extra_data = {
            "structured": True,
            "file_scope": file_scope,
            "dependencies": dependencies,
            "deadline": deadline,
            "acceptance_criteria": acceptance_criteria,
            "subtasks": subtask_records,
        }

        # Generate clean Markdown content from structured data (for display compatibility)
        content_lines = [
            f"## 主任务: {task_name} (@{worker_id})",
            f"**负责人**: {worker_id}",
            f"**文件范围**: {file_scope}",
            f"**依赖**: {dependencies}",
            f"**截止时间**: {deadline}",
            f"**验收标准**: {acceptance_criteria}",
            "",
        ]
        if description:
            content_lines.append(f"**描述**: {description}")
            content_lines.append("")

        for i, st in enumerate(subtask_records, start=1):
            content_lines.append(f"### 子任务 {i}.{i}: {st['title']}")
            content_lines.append(f"[ ] {i}.{i} {st['title']}")
            if st['description']:
                content_lines.append(f"- {st['description']}")
            content_lines.append("")

        content = "\n".join(content_lines).strip()

        # Hold the read-modify-write lock so that a concurrent add_subtask /
        # update_task_progress in the same process cannot interleave with this
        # whole-item overwrite (which would silently wipe worker progress).
        with _collab_rw_lock:
            item = upsert_item(
                collab_id=collab_id,
                agent_id=worker_id,
                item_type="task",
                task_name=task_name,
                title=task_name,
                content=content,
                status=status,
                progress=0,
                visibility=visibility,
                item_key=item_key,
                extra=extra_data,
            )

        subtask_id_map = {st["title"]: st["id"] for st in subtask_records}

        # Push notification: send group chat @mention to worker about new task assignment
        try:
            from ..collab_board import list_tasks as _cb_list_tasks
            _tasks = _cb_list_tasks()
            _my_task = next((t for t in _tasks if str(t.get("task_id", "")) == collab_id), None)
            if _my_task and isinstance(_my_task, dict):
                _extra = _my_task.get("extra") or {}
                _group_id = _extra.get("group_id", "")
                if _group_id:
                    from ..bridge import bridge
                    if bridge and bridge.token:
                        _sub_lines = "\n".join(
                            f"  - {st['title']}" for st in subtask_records
                        )
                        _assign_msg = (
                            f"@{worker_id}\n"
                            f"[Task Assigned] {task_name}\n"
                            f"Check your tasks via: collaboration.board_list_my_tasks(collab_id=\"{collab_id}\")\n"
                            f"Subtasks:\n{_sub_lines}"
                        )
                        bridge.send_message(_assign_msg, target_id=_group_id, target_type="group")
        except Exception:
            pass

        return {
            "status": "success",
            "item": item,
            "assigned_to": worker_id,
            "subtask_ids": subtask_id_map,
            "message": f"Task '{task_name}' assigned to @{worker_id} with {len(subtask_records)} subtasks",
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


def add_subtask(
    collab_id: str,
    item_key: str,
    title: str,
    description: str = "",
) -> Dict[str, Any]:
    """
    [PM only] Add a new subtask to an existing task assignment.

    Args:
      collab_id: collaboration task id
      item_key: the task's item_key (from assign_task return)
      title: subtask title
      description: subtask description

    Example:
      add_subtask(
          collab_id="a8K2pQ",
          item_key="task_coder_auth",
          title="密码重置功能",
          description="POST /api/password/reset, 邮件验证码验证"
      )
    """
    try:
        from ..collab_board import list_items, upsert_item

        # Acquire the read-modify-write lock to prevent concurrent subtask updates
        # from overwriting each other (collab_board._LOCK only protects single I/O).
        with _collab_rw_lock:
            items = list_items(collab_id=collab_id)
            target = next((i for i in items if str(i.get("item_key", "")) == item_key), None)
            if not target:
                return {"status": "error", "message": f"Task item_key '{item_key}' not found"}

            extra = target.get("extra") or {}
            subtasks = extra.get("subtasks", [])
            new_id = f"st_{item_key}_{len(subtasks) + 1}"
            subtasks.append({
                "id": new_id,
                "title": title,
                "description": description,
                "status": "pending",
                "progress": 0,
            })
            extra["subtasks"] = subtasks

            # Regenerate content
            content_lines = [
                f"## 主任务: {target.get('task_name', '')} (@{target.get('agent_id', '')})",
                f"**负责人**: {target.get('agent_id', '')}",
                f"**文件范围**: {extra.get('file_scope', '')}",
                f"**依赖**: {extra.get('dependencies', '')}",
                f"**截止时间**: {extra.get('deadline', '')}",
                f"**验收标准**: {extra.get('acceptance_criteria', '')}",
                "",
            ]
            for i, st in enumerate(subtasks, start=1):
                marker = "[x]" if st["status"] == "done" else "[>]" if st["status"] == "doing" else "[ ]"
                content_lines.append(f"### 子任务 {i}: {st['title']}")
                content_lines.append(f"{marker} {i} {st['title']}")
                if st.get("description"):
                    content_lines.append(f"- {st['description']}")
                content_lines.append("")

            item = upsert_item(
                collab_id=collab_id,
                agent_id=target.get("agent_id", ""),
                item_type="task",
                task_name=target.get("task_name", ""),
                title=target.get("title", ""),
                content="\n".join(content_lines).strip(),
                status=target.get("status", "pending"),
                progress=target.get("progress", 0),
                visibility=target.get("visibility", "public"),
                item_key=item_key,
                extra=extra,
            )

            return {
                "status": "success",
                "subtask_id": new_id,
                "message": f"Subtask '{title}' added to task '{item_key}'",
            }
    except Exception as e:
        return {"status": "error", "message": str(e)}


def update_task_progress(
    collab_id: str,
    item_key: str,
    subtask_id: str,
    status: str = "doing",
    progress: int = 0,
    note: str = "",
) -> Dict[str, Any]:
    """
    [Worker only] Update progress of a specific subtask.

    No need to read/rewrite Markdown — just specify which subtask and its new status.

    Status transition rules (enforced):
      pending → doing, blocked
      doing   → done, blocked
      blocked → doing, pending
      done    → doing (reopen)

    Args:
      collab_id: collaboration task id
      item_key: the task's item_key
      subtask_id: the subtask id (from assign_task return or board_list_my_tasks)
      status: 'pending', 'doing', 'done', 'blocked'
      progress: 0-100 for this subtask
      note: optional progress note

    Example:
      update_task_progress(
          collab_id="a8K2pQ",
          item_key="task_coder_auth",
          subtask_id="st_task_coder_auth_1",
          status="done",
          progress=100,
          note="API已实现并通过本地测试"
      )
    """
    # Valid status transition map.
    # Self-transitions (e.g. done→done) are allowed so that idempotent
    # re-reports from the LLM are not rejected as errors.
    _VALID_TRANSITIONS = {
        "pending": ["pending", "doing", "blocked", "failed"],
        "doing": ["doing", "done", "blocked", "failed"],
        "blocked": ["blocked", "doing", "pending", "failed"],
        "done": ["done", "doing", "failed"],  # reopen allowed
        "failed": ["failed", "doing", "pending"],  # retry allowed
    }

    try:
        from ..collab_board import list_items, upsert_item

        # Acquire the read-modify-write lock to prevent concurrent subtask updates
        # from overwriting each other.
        with _collab_rw_lock:
            items = list_items(collab_id=collab_id)
            target = next((i for i in items if str(i.get("item_key", "")) == item_key), None)
            if not target:
                return {"status": "error", "message": f"Task item_key '{item_key}' not found"}

            extra = target.get("extra") or {}
            subtasks = extra.get("subtasks", [])
            st_idx = next((i for i, s in enumerate(subtasks) if s.get("id") == subtask_id), -1)
            if st_idx < 0:
                return {"status": "error", "message": f"Subtask '{subtask_id}' not found"}

            old_status = subtasks[st_idx].get("status", "pending")
            allowed = _VALID_TRANSITIONS.get(old_status, ["pending", "doing", "done", "blocked"])
            if status not in allowed:
                return {
                    "status": "error",
                    "message": (
                        f"Invalid status transition: '{old_status}' → '{status}'. "
                        f"Allowed transitions: {allowed}"
                    ),
                }

            subtasks[st_idx]["status"] = status
            subtasks[st_idx]["progress"] = max(0, min(100, int(progress)))
            if note:
                subtasks[st_idx]["note"] = note

            # Recalculate overall progress
            total = len(subtasks)
            done_count = sum(1 for s in subtasks if s["status"] == "done")
            doing_count = sum(1 for s in subtasks if s["status"] == "doing")
            overall_progress = int(round(((done_count + doing_count * 0.5) / total) * 100)) if total > 0 else 0
            overall_status = "done" if done_count == total else "doing" if doing_count > 0 else "pending"

            extra["subtasks"] = subtasks

            # Regenerate content
            content_lines = [
                f"## 主任务: {target.get('task_name', '')} (@{target.get('agent_id', '')})",
                f"**负责人**: {target.get('agent_id', '')}",
                f"**文件范围**: {extra.get('file_scope', '')}",
                f"**依赖**: {extra.get('dependencies', '')}",
                f"**截止时间**: {extra.get('deadline', '')}",
                f"**验收标准**: {extra.get('acceptance_criteria', '')}",
                "",
            ]
            for i, st in enumerate(subtasks, start=1):
                marker = "[x]" if st["status"] == "done" else "[>]" if st["status"] == "doing" else "[ ]"
                content_lines.append(f"### 子任务 {i}: {st['title']}")
                content_lines.append(f"{marker} {i} {st['title']}")
                if st.get("description"):
                    content_lines.append(f"- {st['description']}")
                if st.get("note"):
                    content_lines.append(f"- 备注: {st['note']}")
                content_lines.append("")

            item = upsert_item(
                collab_id=collab_id,
                agent_id=target.get("agent_id", ""),
                item_type="task",
                task_name=target.get("task_name", ""),
                title=target.get("title", ""),
                content="\n".join(content_lines).strip(),
                status=overall_status,
                progress=overall_progress,
                visibility=target.get("visibility", "public"),
                item_key=item_key,
                extra=extra,
            )

            return {
                "status": "success",
                "item": item,
                "subtask": subtasks[st_idx],
                "overall_progress": overall_progress,
                "overall_status": overall_status,
                "message": f"Subtask '{subtasks[st_idx]['title']}' updated to '{status}'",
            }
    except Exception as e:
        return {"status": "error", "message": str(e)}


def batch_update_tasks(
    collab_id: str,
    item_key: str,
    updates: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    [Worker only] Batch update multiple subtasks in one call.

    Args:
      collab_id: collaboration task id
      item_key: the task's item_key
      updates: list of update dicts, each with:
        - 'subtask_id': subtask id (required)
        - 'status': new status (optional)
        - 'progress': new progress 0-100 (optional)
        - 'note': progress note (optional)

    Example:
      batch_update_tasks(
          collab_id="a8K2pQ",
          item_key="task_coder_auth",
          updates=[
              {"subtask_id": "st_task_coder_auth_1", "status": "done", "progress": 100},
              {"subtask_id": "st_task_coder_auth_2", "status": "doing", "progress": 50},
          ]
      )
    """
    results = []
    for u in updates:
        r = update_task_progress(
            collab_id=collab_id,
            item_key=item_key,
            subtask_id=u["subtask_id"],
            status=u.get("status", "doing"),
            progress=u.get("progress", 0),
            note=u.get("note", ""),
        )
        results.append(r)

    success_count = sum(1 for r in results if r.get("status") == "success")
    return {
        "status": "success",
        "updated": success_count,
        "total": len(updates),
        "results": results,
    }


def board_update(
    collab_id: str,
    task_name: str = "",
    title: str = "",
    content: str = "",
    status: str = "doing",
    progress: int = 0,
    visibility: str = "public",
    item_type: str = "task",
    item_key: str = "",
) -> Dict[str, Any]:
    """
    Upsert the current agent's collaboration board item.

    Use this to publish your latest plan/progress/status for teammates.
    Use item_key to create multiple independent entries of the same type
    (e.g. multiple requirements, multiple plan sections).

    item_type determines which board area the item appears in:
    - "requirement": Requirements area — submit or update a requirement.
      status field encodes priority + confirmation: "P0"/"P1"/"P2" for new,
      "已确认"/"已驳回" for reviewed.
      Use item_key to create separate requirement entries (e.g. item_key="req_auth").
    - "plan": Plan area — write or update the solution/architecture document.
      Use item_key to create separate plan sections (e.g. item_key="architecture").
    - "task": Task assignment area — PM should use assign_task() instead.
      Worker should use update_task_progress() instead.
      This function remains for custom/non-structured task entries.
    - "status": Progress area — auto-updated by runner after each tool call.
      You normally don't need to call this manually.
    - "discussion": Discussion history — use board_post_public_discussion instead.

    Example usage for PM:
      # Create multiple requirements
      board_update(collab_id="abc", title="用户登录", content="...", item_type="requirement", item_key="req_login", status="P0")
      board_update(collab_id="abc", title="数据库迁移", content="...", item_type="requirement", item_key="req_db", status="P1")

      # Write plan/architecture
      board_update(collab_id="abc", title="架构设计", content="...", item_type="plan", item_key="architecture")

    ⚠️ For task assignment and progress updates:
      - PM: use assign_task(worker_id, task_name, subtasks=[...]) instead
      - Worker: use update_task_progress(item_key, subtask_id, status) instead
      - Do NOT use board_update for structured task assignments or progress
    """
    try:
        from ..input_hub import input_hub
        from ..collab_board import upsert_item

        agent_dir = input_hub.agent_dir or ""
        agent_id = os.path.basename(agent_dir) if agent_dir else "unknown_agent"
        item = upsert_item(
            collab_id=collab_id,
            task_name=task_name,
            agent_id=agent_id,
            item_type=item_type,
            title=title,
            content=content,
            status=status,
            progress=progress,
            visibility=visibility,
            item_key=item_key,
        )
        return {"status": "success", "item": item}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def board_list(collab_id: str, agent_id: str = "", scope: str = "public", item_type: str = "") -> Dict[str, Any]:
    """
    Read collaboration board entries.

    Args:
      collab_id: collaboration id filter (required)
      agent_id: optional agent filter
      scope: 'public' or 'all' (all includes private entries; use carefully)
      item_type: optional type filter ('requirement', 'plan', 'task', 'status', 'discussion')

    Worker usage tip:
      To query PM-assigned task checklist, call with:
      - item_type='task'
      - scope='public'
      - agent_id='' (all) then pick items matching your own agent_id,
        or set agent_id to your own id directly.
    """
    try:
        from ..collab_board import list_items
        items = list_items(
            collab_id=collab_id,
            agent_id=agent_id or None,
            visibility="public" if scope != "all" else "all",
        )
        if item_type:
            items = [i for i in items if str(i.get("item_type", "")) == item_type]
        return {"status": "success", "count": len(items), "items": items}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def board_view(collab_id: str) -> Dict[str, Any]:
    """
    View the complete collaboration board — all zones (requirements, plan, tasks, discussions).

    Use this to get the full context of a collaboration session:
    - What are the requirements?
    - What is the plan/architecture?
    - What tasks have been assigned to whom?
    - What is the current progress?
    - What discussions have happened?

    This is the recommended entry point for workers to understand the full context
    before starting work. It returns all public items organized by zone.

    Args:
      collab_id: collaboration task id (from start_collaboration)

    Returns:
      dict with zones: requirements, plan, tasks, status, discussions
      Each zone contains a list of items with full details.

    Example:
      board = collaboration.board_view(collab_id="a8K2pQ")
      # Returns:
      # {
      #   "requirements": [...],
      #   "plan": [...],
      #   "tasks": [...],
      #   "status": [...],
      #   "discussions": [...]
      # }
    """
    try:
        from ..collab_board import list_items

        all_items = list_items(collab_id=collab_id, visibility="public")

        zones = {
            "requirements": [],
            "plan": [],
            "tasks": [],
            "status": [],
            "discussions": [],
        }

        for item in all_items:
            item_type = str(item.get("item_type", ""))
            if item_type in zones:
                # Enrich task items with structured subtask info
                if item_type == "task":
                    extra = item.get("extra") or {}
                    if extra.get("structured") and "subtasks" in extra:
                        item["subtasks"] = extra["subtasks"]
                        item["file_scope"] = extra.get("file_scope", "")
                        item["deadline"] = extra.get("deadline", "")
                        item["acceptance_criteria"] = extra.get("acceptance_criteria", "")
                zones[item_type].append(item)

        return {
            "status": "success",
            "collab_id": collab_id,
            "zones": zones,
            "summary": {
                "requirements_count": len(zones["requirements"]),
                "plan_count": len(zones["plan"]),
                "tasks_count": len(zones["tasks"]),
                "discussions_count": len(zones["discussions"]),
            },
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


def board_list_tasks(collab_id: str) -> Dict[str, Any]:
    """
    View all task assignments for a collaboration session.

    Simply provide the collab_id — no agent_id needed.
    Returns all task items with their assignments, progress, and structured subtasks.

    Use this to see:
    - Which tasks have been assigned to which agents
    - Current progress of each task
    - Structured subtask details

    Args:
      collab_id: collaboration task id (from start_collaboration)

    Returns:
      dict with status, count, and list of task items

    Example:
      tasks = collaboration.board_list_tasks(collab_id="a8K2pQ")
      # Returns: {
      #   "status": "success",
      #   "count": 3,
      #   "items": [
      #     {"item_key": "task_coder_auth", "agent_id": "coder", "subtasks": [...]},
      #     {"item_key": "task_qa_test", "agent_id": "qa", "subtasks": [...]}
      #   ]
      # }
    """
    try:
        from ..collab_board import list_items

        items = list_items(collab_id=collab_id, visibility="public")
        task_items = [i for i in items if str(i.get("item_type", "")) == "task"]

        # Enrich with structured subtask info
        for item in task_items:
            extra = item.get("extra") or {}
            if extra.get("structured") and "subtasks" in extra:
                item["subtasks"] = extra["subtasks"]
                item["file_scope"] = extra.get("file_scope", "")
                item["deadline"] = extra.get("deadline", "")
                item["acceptance_criteria"] = extra.get("acceptance_criteria", "")

        return {"status": "success", "count": len(task_items), "items": task_items}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def _resolve_my_agent_ids() -> List[str]:
    """Resolve current agent's possible identifiers for task matching.

    Returns a prioritized list of agent IDs:
    1. config.json 'agent_id' field (the canonical platform-side ID)
    2. Agent directory basename (the local-side ID)
    """
    try:
        from ..input_hub import input_hub
        agent_dir = input_hub.agent_dir or ""
        ids = []
        dir_name = os.path.basename(agent_dir) if agent_dir else ""
        if dir_name and dir_name != "unknown_agent":
            ids.append(dir_name)
        # Try to read config.json for the canonical agent_id
        if agent_dir:
            config_path = os.path.join(agent_dir, "config.json")
            if os.path.exists(config_path):
                from opensquad.json_cache import load_json_cached
                cfg = load_json_cached(config_path)
                if cfg:
                    cid = str(cfg.get("agent_id", "")).strip()
                    if cid and cid not in ids:
                        ids.insert(0, cid)
        return ids
    except Exception:
        return []


def board_list_my_tasks(collab_id: str, scope: str = "public", debug: bool = False) -> Dict[str, Any]:
    """
    Worker convenience helper: list task checklist items assigned to the current agent.

    For structured tasks (assign_task), each returned item includes:
    - 'subtasks': list of subtask dicts with id/title/status/description
    Workers can use subtask IDs directly with update_task_progress().

    Matching strategies:
    1. Exact match against config.json 'agent_id' (canonical, highest confidence)
    2. Exact match against agent directory basename
    3. Content mention (@<agent_id> or **负责人**: <agent_id>)

    Args:
      collab_id: collaboration id filter (required)
      scope: 'public' or 'all' (default public)
      debug: if True, include debug info about all items and matching attempts

    Returns:
      Task items assigned to the current agent, with structured subtask info exposed.
    """
    try:
        my_ids = _resolve_my_agent_ids()  # [config_agent_id, dir_name]

        # Fetch ALL task items for this collab_id (we'll filter locally)
        all_result = board_list(
            collab_id=collab_id,
            agent_id="",  # Get all, filter locally
            scope=scope,
            item_type="task",
        )
        all_items = all_result.get("items", [])

        matched = []
        seen_keys = set()
        unmatched_info = [] if debug else None

        for item in all_items:
            stored_agent_id = str(item.get("agent_id", "")).strip()
            content = str(item.get("content", ""))
            item_key = str(item.get("item_key", ""))
            match_reason = None

            # Strategy 1: Exact match against any of my resolved IDs (highest confidence)
            if stored_agent_id and any(stored_agent_id == mid for mid in my_ids if mid):
                match_reason = "exact_agent_id_match"
            # Strategy 2: Content mention (@<id> or **负责人**: <id>)
            elif any(
                f"@{mid}" in content or f"**负责人**: {mid}" in content
                for mid in my_ids if mid
            ):
                match_reason = "content_mention"
            else:
                if debug and unmatched_info is not None:
                    unmatched_info.append({
                        "item_key": item_key,
                        "stored_agent_id": stored_agent_id,
                        "my_ids": my_ids,
                    })
                continue

            # Avoid duplicates from multiple matching strategies
            if item_key and item_key in seen_keys:
                continue
            if item_key:
                seen_keys.add(item_key)

            matched.append(item)
            if debug:
                item["_match_reason"] = match_reason

        # Enrich matched items with structured subtask info from 'extra' field
        for item in matched:
            extra = item.get("extra") or {}
            if extra.get("structured") and "subtasks" in extra:
                item["subtasks"] = extra["subtasks"]
                item["file_scope"] = extra.get("file_scope", "")
                item["deadline"] = extra.get("deadline", "")
                item["acceptance_criteria"] = extra.get("acceptance_criteria", "")

        result = {"status": "success", "count": len(matched), "items": matched}
        if debug:
            result["debug"] = {
                "my_ids": my_ids,
                "total_items": len(all_items),
                "matched_count": len(matched),
                "unmatched_items": unmatched_info,
            }
        return result
    except Exception as e:
        return {"status": "error", "message": str(e)}


def board_post_public_discussion(collab_id: str, task_name: str, title: str, content: str) -> Dict[str, Any]:
    """
    Post a public discussion/decision memo visible to all agents.
    Use this for confirmed task plans and shared context to prevent forgetting.

    The discussion is stored via collab_board.append_public_discussion().
    This is the canonical tool — always use this function name.
    """
    try:
        from ..input_hub import input_hub
        from ..collab_board import append_public_discussion

        agent_dir = input_hub.agent_dir or ""
        agent_id = os.path.basename(agent_dir) if agent_dir else "unknown_agent"
        rec = append_public_discussion(
            collab_id=collab_id,
            task_name=task_name,
            author_agent_id=agent_id,
            title=title,
            content=content,
        )
        return {"status": "success", "item": rec}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def get_team_status() -> Dict[str, Any]:
    """
    Get real-time status of all collaboration-enabled agents.
    Reads each agent's ai_state.json for live status (idle/working/sleeping).

    Returns agent list with id, name, role, capabilities, and real-time status.
    """
    agents_dir = _agents_dir()
    if not os.path.isdir(agents_dir):
        return {"status": "error", "message": "Agents directory not found"}

    agents = []
    for entry in sorted(os.listdir(agents_dir)):
        agent_path = os.path.join(agents_dir, entry)
        config_path = os.path.join(agent_path, "config.json")
        if not os.path.isdir(agent_path) or not os.path.exists(config_path):
            continue

        from opensquad.json_cache import load_json_cached
        cfg = load_json_cached(config_path)
        if not cfg:
            continue

        collab = cfg.get("collaboration", {})
        if not collab.get("enabled", False):
            continue

        # Read real-time state from ai_state.json
        state_file = os.path.join(agent_path, "data", "ai_state.json")
        live_status = "offline"
        if os.path.exists(state_file):
            state = load_json_cached(state_file)
            live_status = state.get("ai_state", "offline") if state else "offline"

        agents.append({
            "agent_id": entry,
            "name": cfg.get("agent_name", entry),
            "role": collab.get("role", "unknown"),
            "capabilities": cfg.get("capabilities", []),
            "status": live_status,
        })

    return {"status": "success", "agents": agents}


def get_group_roster(group_id: str) -> Dict[str, Any]:
    """
    Get the roster of agent members in a specific group.

    Queries the group's member list and cross-references with local agent
    configs to return only agent members (human users are excluded).

    Useful for PM to discover available agents before assigning tasks,
    and for workers to see who else is collaborating in the same group.

    Note: you can only query groups you have joined.

    Args:
        group_id: Group ID (e.g. "g1abc") or group name (auto-resolved to ID)

    Returns:
        agents: list of dicts with name, agent_dir, role, status, capabilities
    """
    try:
        from ..bridge import bridge as _bridge

        if not _bridge or not _bridge.token:
            return {"status": "error", "message": "Bridge not connected"}

        # 1. Resolve group name -> ID if needed
        groups = _bridge.list_groups_api()
        target_id = group_id
        matched_group_name = group_id

        id_match = next((g for g in groups if isinstance(g, dict) and g.get("id") == group_id), None)
        if id_match:
            matched_group_name = id_match.get("name", group_id)
        else:
            name_match = next((g for g in groups if isinstance(g, dict) and g.get("name") == group_id), None)
            if name_match:
                target_id = name_match.get("id", group_id)
                matched_group_name = name_match.get("name", group_id)
            else:
                return {
                    "status": "error",
                    "message": f"Group '{group_id}' not found or you are not a member",
                }

        # 2. Fetch group detail to get member list (via bridge method with auto re-login)
        detail = _bridge.get_group_detail_api(target_id)
        if not detail:
            return {"status": "error", "message": f"Failed to fetch group detail for '{target_id}'"}

        raw_members = detail.get("members", [])

        # Build {user_id_str: display_name} from group members
        member_map: Dict[str, str] = {}
        for m in raw_members:
            if isinstance(m, dict):
                uid = str(m.get("id", ""))
                if uid:
                    member_map[uid] = m.get("name", "")

        # 3. Cross-reference with agents/*/config.json via agent_id
        agents_base = _agents_dir()
        agent_roster = []

        if os.path.isdir(agents_base):
            for entry in sorted(os.listdir(agents_base)):
                agent_path = os.path.join(agents_base, entry)
                config_path = os.path.join(agent_path, "config.json")
                if not os.path.isdir(agent_path) or not os.path.exists(config_path):
                    continue
                from opensquad.json_cache import load_json_cached
                cfg = load_json_cached(config_path)
                if not cfg:
                    continue

                agent_id = str(cfg.get("agent_id", ""))
                if agent_id not in member_map:
                    continue  # not a member of this group

                name = cfg.get("agent_name", entry)
                collab = cfg.get("collaboration", {})
                role = collab.get("role", "unknown")
                caps = cfg.get("capabilities", [])

                # 4. Read live status from ai_state.json
                state_file = os.path.join(agent_path, "data", "ai_state.json")
                status = "offline"
                if os.path.exists(state_file):
                    from opensquad.json_cache import load_json_cached
                    status_data = load_json_cached(state_file)
                    status = status_data.get("ai_state", "offline") if status_data else "offline"

                agent_roster.append({
                    "name": name,
                    "agent_dir": entry,
                    "role": role,
                    "status": status,
                    "capabilities": caps,
                })

        return {
            "status": "success",
            "group": matched_group_name,
            "group_id": target_id,
            "agent_count": len(agent_roster),
            "agents": agent_roster,
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ── Task Watch: PM queries worker status from launcher ──

def check_worker_status(collab_id: str = "", worker_id: str = "") -> Dict[str, Any]:
    """
    PM tool: query worker heartbeat status from the launcher.

    Pass either collab_id (checks all workers in a collaboration) or
    worker_id (checks a specific agent). If both are omitted, returns
    ALL monitored workers.

    Returns each worker's:
      - event:     "start" | "update" | "complete"
      - detail:    last progress description
      - elapsed_sec: seconds since last heartbeat
      - stalled:   true if no heartbeat > 300s

    Args:
        collab_id (str, optional): collaboration ID to filter workers
        worker_id (str, optional): specific agent ID to check

    Returns:
        dict with 'workers': {agent_id: {event, detail, elapsed_sec, stalled}}

    Example:
        # Check all workers
        collaboration.check_worker_status()
        # Check specific agent
        collaboration.check_worker_status(worker_id="agent301")
    """
    try:
        import urllib.request
        import json as _json
        launcher_port = os.environ.get("OPENSQUAD_LAUNCHER_PORT", "9600")
        url = f"http://127.0.0.1:{launcher_port}/api/task_watch_status"
        resp = urllib.request.urlopen(url, timeout=5)
        data = _json.loads(resp.read())
        workers = data.get("workers", {})

        if worker_id:
            return workers.get(worker_id, {"error": f"Worker '{worker_id}' not found"})
        if collab_id:
            filtered = {aid: info for aid, info in workers.items()
                        if collab_id in str(info.get("detail", ""))}
            return {"workers": filtered, "total": len(filtered)}
        return {"workers": workers, "total": len(workers)}
    except Exception as e:
        return {"error": str(e)}
