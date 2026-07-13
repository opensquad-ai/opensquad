"""Archive compression summaries into the self_learn corpus."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("plugins.self_learn.archive")


def archive_compression_summary(
    summary: str,
    *,
    session_id: str = "",
    session_title: str = "",
    source: str = "compress",
    agent_dir: str | None = None,
    agent_id: str = "",
) -> dict[str, Any] | None:
    """
    Append a compression summary to the agent's self_learn corpus.

    Safe to call from runner compression paths even if the plugin UI is unused.
    Returns the corpus entry dict, or None on skip/failure.
    """
    summary = (summary or "").strip()
    if not summary:
        return None

    try:
        from plugins.self_learn import store
    except ImportError:
        try:
            from . import store
        except ImportError:
            logger.debug("[self_learn] store import failed", exc_info=True)
            return None

    resolved = store.resolve_agent_dir(agent_dir)
    if not resolved:
        logger.debug("[self_learn] archive skipped: agent_dir unknown")
        return None

    try:
        if not agent_id:
            try:
                from opensquad import context_base

                cfg = getattr(context_base, "_agent_config", None) or {}
                agent_id = str(cfg.get("agent_id") or "")
            except Exception:
                agent_id = ""
        entry = store.append_corpus_entry(
            resolved,
            summary=summary,
            session_id=session_id,
            session_title=session_title,
            source=source,
            agent_id=agent_id,
        )
        logger.info(
            "[self_learn] archived corpus id=%s session=%s source=%s chars=%d",
            entry.get("id"),
            session_id or "-",
            source,
            len(summary),
        )
        return entry
    except Exception:
        logger.warning("[self_learn] archive_compression_summary failed", exc_info=True)
        return None
