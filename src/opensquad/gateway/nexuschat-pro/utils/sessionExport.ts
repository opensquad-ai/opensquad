/**
 * Export a session transcript as a Markdown download.
 *
 * There are two entry points now — the sidebar row action and the context
 * panel's "导出上下文" — and the flow (cached timeline → session API → download)
 * must exist exactly once. Two hand-rolled copies of "read the session, render
 * it, save it" is how the export silently drifts apart from the row that calls
 * it, and a truncated transcript is not something the caller can notice.
 *
 * Returns a discriminated result instead of throwing / reporting on its own:
 * the two callers surface failures differently (the sidebar owns an error
 * line, the context panel shows one under the button).
 */
import { agentSessionAPI } from '../services/api';
import { buildTimelineFromSession, type TimelineEntry } from './aiChatTimeline';
import { downloadTextFile, exportFileName, sessionToMarkdown } from './sessionMarkdown';
import { getCachedSessionTimelineMeta } from './sessionTimelineCache';

export type SessionExportResult =
  | { ok: true; fileName: string; entries: number }
  | { ok: false; reason: 'empty' | 'busy' | 'error'; message?: string };

/** One export per session at a time — a double click must not fetch twice. */
const inFlight = new Set<string>();

export async function exportSessionToMarkdown(
  agentId: string,
  sessionId: string,
  title?: string,
): Promise<SessionExportResult> {
  const aid = (agentId || '').trim();
  const sid = (sessionId || '').trim();
  if (!aid || !sid) return { ok: false, reason: 'empty' };

  const key = `${aid}::${sid}`;
  if (inFlight.has(key)) return { ok: false, reason: 'busy' };
  inFlight.add(key);
  try {
    // Prefer the cached timeline, but only when it is the WHOLE conversation:
    // a partial cache page would silently export a truncated transcript.
    const cached = getCachedSessionTimelineMeta(aid, sid);
    let entries: TimelineEntry[] = cached?.complete && cached.entries.length
      ? cached.entries
      : [];
    if (!entries.length) {
      const resp = await agentSessionAPI.getSessionHistory(aid, sid);
      const data = resp.session;
      entries = buildTimelineFromSession(
        data?.messages || [],
        data?.events || [],
        data?.archived_messages,
        data?.archived_events,
      );
    }
    if (!entries.length) return { ok: false, reason: 'empty' };

    const fileName = exportFileName(title, sid);
    downloadTextFile(fileName, sessionToMarkdown({ title, sessionId: sid, entries }));
    return { ok: true, fileName, entries: entries.length };
  } catch (err: any) {
    return { ok: false, reason: 'error', message: err?.message || String(err ?? '') };
  } finally {
    inFlight.delete(key);
  }
}
