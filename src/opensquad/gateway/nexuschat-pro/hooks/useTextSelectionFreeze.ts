/**
 * Freeze live chat DOM updates while the user is selecting text.
 *
 * Critical: do NOT setState on pointerdown. Freezing via state on mousedown
 * re-renders the whole chat tree and remounts markdown text nodes, which
 * clears the selection even when the agent is idle (no live stream).
 *
 * Instead: only snapshot when `liveValue` actually changes while selecting /
 * while a selection remains in the container. Idle select → zero React updates.
 */
import { useCallback, useEffect, useRef, useState, type RefObject } from 'react';

const CONTROL_SELECTOR = 'button, a, input, textarea, select, [role="button"]';

export function useTextSelectionFreeze<T>(
  containerRef: RefObject<HTMLElement | null>,
  liveValue: T,
): {
  /** Value to render — frozen snapshot while selecting over live updates, otherwise live. */
  displayValue: T;
  /** True while a freeze snapshot is held. */
  isFrozen: boolean;
  /** Sync flag for layout effects / scroll handlers. */
  isFrozenRef: React.MutableRefObject<boolean>;
} {
  const [held, setHeld] = useState<T | null>(null);
  const liveRef = useRef(liveValue);
  const prevLiveRef = useRef(liveValue);
  const selectingRef = useRef(false);
  const isFrozenRef = useRef(false);
  const releaseTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  liveRef.current = liveValue;

  const selectionInContainer = useCallback((): boolean => {
    const root = containerRef.current;
    const sel = window.getSelection();
    if (!root || !sel || sel.isCollapsed || !sel.anchorNode) return false;
    try {
      return root.contains(sel.anchorNode);
    } catch {
      return false;
    }
  }, [containerRef]);

  const markSelecting = useCallback(() => {
    selectingRef.current = true;
    isFrozenRef.current = true;
    // No setState — keep the currently painted liveValue on screen.
  }, []);

  const releaseHold = useCallback(() => {
    selectingRef.current = false;
    isFrozenRef.current = false;
    setHeld((prev) => (prev === null ? prev : null));
  }, []);

  const scheduleRelease = useCallback(() => {
    if (releaseTimerRef.current) clearTimeout(releaseTimerRef.current);
    // Defer: mouseup can emit a transient collapsed selectionchange before the
    // browser commits the final range.
    releaseTimerRef.current = setTimeout(() => {
      releaseTimerRef.current = null;
      if (selectionInContainer()) {
        // Keep protecting against future live updates — but do NOT setState.
        // Setting held=live here would re-render and wipe the new selection.
        isFrozenRef.current = true;
        return;
      }
      releaseHold();
    }, 80);
  }, [releaseHold, selectionInContainer]);

  // When live data changes during selection, freeze the previously painted value.
  useEffect(() => {
    const prev = prevLiveRef.current;
    if (Object.is(prev, liveValue)) return;
    prevLiveRef.current = liveValue;

    const protect = selectingRef.current || selectionInContainer() || isFrozenRef.current;
    if (!protect) return;

    setHeld((h) => h ?? prev);
    isFrozenRef.current = true;
  }, [liveValue, selectionInContainer]);

  useEffect(() => {
    const onPointerDown = (ev: PointerEvent) => {
      const root = containerRef.current;
      if (!root || !(ev.target instanceof Node) || !root.contains(ev.target)) return;
      if (ev.target instanceof Element && ev.target.closest(CONTROL_SELECTOR)) return;
      if (releaseTimerRef.current) {
        clearTimeout(releaseTimerRef.current);
        releaseTimerRef.current = null;
      }
      markSelecting();
    };

    const onPointerUp = () => {
      if (!selectingRef.current && !isFrozenRef.current) return;
      selectingRef.current = false;
      // Keep freeze if selection remains — do not release on mouseup alone.
      scheduleRelease();
    };

    const onSelChange = () => {
      if (selectionInContainer()) {
        isFrozenRef.current = true;
        return;
      }
      if (selectingRef.current) return;
      scheduleRelease();
    };

    document.addEventListener('pointerdown', onPointerDown, true);
    document.addEventListener('pointerup', onPointerUp, true);
    document.addEventListener('pointercancel', onPointerUp, true);
    document.addEventListener('selectionchange', onSelChange);
    return () => {
      document.removeEventListener('pointerdown', onPointerDown, true);
      document.removeEventListener('pointerup', onPointerUp, true);
      document.removeEventListener('pointercancel', onPointerUp, true);
      document.removeEventListener('selectionchange', onSelChange);
      if (releaseTimerRef.current) clearTimeout(releaseTimerRef.current);
    };
  }, [containerRef, markSelecting, scheduleRelease, selectionInContainer]);

  const isFrozen = held !== null;

  return {
    displayValue: held !== null ? held : liveValue,
    isFrozen,
    isFrozenRef,
  };
}
