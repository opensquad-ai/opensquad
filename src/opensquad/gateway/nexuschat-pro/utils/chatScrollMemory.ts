/**
 * Where the user was reading, per conversation.
 *
 * The chat window unmounts when you switch to another area, and a fresh container starts at
 * scrollTop = 0 — the top of the history. The window did try to restore a position, but only when
 * the message it had recorded was already rendered, and on the frame you switch back the
 * conversation has not rendered yet, so the restore was skipped and nothing else moved the
 * container: you landed at the very top every time.
 *
 * Remembering the offset itself and restoring it once the conversation has rendered does not
 * depend on any message being present. `claimRestore`/`releaseRestore` keep it to one restore per
 * visit, so scrolling afterwards is never overridden by a stale offset.
 */

const positions = new Map<string, number>();
const claimed = new Set<string>();

/** Remember the offset for a conversation. Ignores anything that cannot be a real position. */
export const rememberScroll = (key: string, top: number): void => {
  if (!key || !Number.isFinite(top) || top < 0) return;
  positions.set(key, top);
};

/** The offset to come back to, or null when this conversation was never read. */
export const recallScroll = (key: string): number | null => {
  if (!key) return null;
  return positions.has(key) ? positions.get(key)! : null;
};

/** True the first time a conversation asks to restore during this visit. */
export const claimRestore = (key: string): boolean => {
  if (!key || claimed.has(key)) return false;
  claimed.add(key);
  return true;
};

/** Done with a conversation: the next visit may restore again. */
export const releaseRestore = (key: string): void => {
  claimed.delete(key);
};

export const forgetScroll = (key: string): void => {
  positions.delete(key);
  claimed.delete(key);
};

/** Test helper. */
export const __resetScrollMemory = (): void => {
  positions.clear();
  claimed.clear();
};
