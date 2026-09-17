/**
 * Cross-surface window events.
 *
 * Only `AIChatPage` can open an L2 content tab (it owns the workspace tab
 * store), so a panel that wants the main surface to show something has to ask
 * through a window `CustomEvent`. That indirection has one nasty failure mode:
 *
 *     a listener with no producer is indistinguishable from a broken feature.
 *
 * `opensquad-open-session-tab` sat in exactly that state — `AIChatPage` listened
 * for it (its own comment promised "from the Scheduled Tasks 'task flow'
 * button"), and nothing in the codebase ever dispatched it. So "click the
 * finished parallel task to read its tool flow" silently did nothing, with no
 * error anywhere: `window.dispatchEvent` is only ever called if someone calls
 * it.
 *
 * Both sides therefore import the name from here, and `uiEvents.scan.test.ts`
 * pins that the literal lives in this file only — the same reasoning as
 * `wsFieldNames.ts` for the WS field spellings.
 */

/** `detail: { sessionId }` — open that session as an L2 content tab. */
export const OPEN_SESSION_TAB_EVENT = 'opensquad-open-session-tab';

/**
 * Ask the workspace to open `sessionId` as a session tab.
 *
 * Returns whether a request was actually dispatched: a task that never bound a
 * session (still queued, or failed before the first turn) has nothing to show,
 * and callers use this to keep the affordance disabled instead of opening an
 * empty tab.
 */
export const openSessionTab = (sessionId: string | null | undefined): boolean => {
  const id = (sessionId || '').trim();
  if (!id) return false;
  window.dispatchEvent(new CustomEvent(OPEN_SESSION_TAB_EVENT, { detail: { sessionId: id } }));
  return true;
};
