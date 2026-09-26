/**
 * steerReveal — "jump to the interjection inside the tool fold".
 *
 * A 插话 row lives inside a workflow fold that may be collapsed, and a collapsed
 * fold does not mount its body at all (`useFold` + `<Collapse>`), so the row has
 * no DOM node to scroll to yet. The nav rail therefore has to ask the owning fold
 * to open first, and only then scroll.
 *
 * Keyed by the steer event's own `_uid` rather than the fold's entry uid: a fold
 * already holds its events, so it can answer "is this one of mine?" without any
 * new prop threading through ChatTimeline.
 *
 * One pending request at a time is fine — a jump is a deliberate click.
 */

import { useSyncExternalStore } from 'react';

let current: { id: string; seq: number } | null = null;
const listeners = new Set<() => void>();

/** Ask the fold holding this steer event to open and reveal it. */
export function requestSteerReveal(steerUid: string): void {
  current = { id: steerUid, seq: (current?.seq ?? 0) + 1 };
  for (const listener of listeners) listener();
}

/** The steer the rail last asked to reveal, or null. */
export function pendingSteerRevealId(): string | null {
  return current?.id ?? null;
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

// The sequence number is the store snapshot: it changes on every request, so a fold
// re-reads even when the same interjection is jumped to twice in a row. The id is
// read out of `current` afterwards, which keeps the hook's return value a stable
// string instead of a fresh object per render.
function getSnapshot(): number {
  return current?.seq ?? 0;
}

/**
 * The interjection a jump is asking for, or null.
 *
 * Every fold reads this and acts only when the id is one of its own steer events.
 */
export function usePendingSteerReveal(): string | null {
  useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
  return current?.id ?? null;
}
