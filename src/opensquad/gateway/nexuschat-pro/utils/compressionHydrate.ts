/**
 * Which session must a `history_sync {reason:'compression'}` frame re-hydrate?
 *
 * The compressor always runs against an EXPLICIT session and the frame names it
 * in `session_id`. The browser used to discard that id and re-fetch the agent's
 * `/current` pointer instead — which in a multi-tab / parallel layout is a
 * *different* session, so the compression repainted the wrong chat into the
 * pane (串页: the compressed session's view flipped to the current/newest one).
 *
 * Returning "" means "the frame named no session" — callers then keep the legacy
 * `/current` behaviour, so older agents that omit the field are unaffected.
 */
export function compressionHydrateSid(content: unknown): string {
  if (!content || typeof content !== 'object') return '';
  const data = content as Record<string, unknown>;
  const raw = data.session_id ?? data.sessionId ?? data.id ?? '';
  return typeof raw === 'string' ? raw.trim() : '';
}
