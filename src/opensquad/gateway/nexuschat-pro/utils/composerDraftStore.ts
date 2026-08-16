/**
 * Per-session composer drafts kept off React state so typing does not
 * re-render AIChatPage (timeline, panes, sidebars). Composer owns the
 * live input; this map only survives remount / session switch.
 */

const drafts = new Map<string, string>();

export function getComposerDraft(sessionId: string): string {
  if (!sessionId) return '';
  return drafts.get(sessionId) ?? '';
}

export function setComposerDraft(sessionId: string, text: string): void {
  if (!sessionId) return;
  if (!text) {
    drafts.delete(sessionId);
    return;
  }
  drafts.set(sessionId, text);
}

export function clearComposerDraft(sessionId: string): void {
  if (!sessionId) return;
  drafts.delete(sessionId);
}

/** Test helper. */
export function resetComposerDrafts(): void {
  drafts.clear();
}
