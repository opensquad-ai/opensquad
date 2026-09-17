import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
  type RefObject,
  type UIEventHandler,
} from 'react';
import {
  layoutTimelineWindow,
  useTimelineVirtualRange,
} from '../../hooks/useTimelineVirtualRange';
import { isFoldAnimating } from '../Collapse';

export type TimelineKeyed = { _uid?: string };

/** Reveal window: kept in sync with --duration-reveal / --reveal-stagger. */
const REVEAL_DURATION_MS = 260;
const REVEAL_STAGGER_MS = 26;
/** Cap the stagger so a long page does not finish revealing half a second
 *  after the last row — beyond this many rows they all start together. */
const REVEAL_MAX_STAGGERED_ROWS = 12;
const REVEAL_TOTAL_MS = REVEAL_DURATION_MS + REVEAL_STAGGER_MS * REVEAL_MAX_STAGGERED_ROWS;

/**
 * Scroll container that mounts only near-viewport + trailing timeline rows.
 * Off-screen gaps are two spacer divs (O(window) React nodes, not O(n)).
 */
export function ChatTimeline<T extends TimelineKeyed>({
  scrollRef,
  entries,
  renderEntry,
  className,
  style,
  onScroll,
  columnClass,
  header,
  footer,
  unpinRef,
  freezeRef,
  revealKey,
}: {
  scrollRef: RefObject<HTMLDivElement | null>;
  entries: T[];
  renderEntry: (entry: T, index: number, key: string, revealStyle?: CSSProperties) => ReactNode;
  className?: string;
  style?: CSSProperties;
  onScroll?: UIEventHandler<HTMLDivElement>;
  columnClass?: string;
  header?: ReactNode;
  footer?: ReactNode;
  /** When true, skip stick-to-bottom (user scrolled away). */
  unpinRef?: RefObject<boolean>;
  /** When true, skip stick-to-bottom (text selection freeze). */
  freezeRef?: RefObject<boolean>;
  /**
   * Identity of the content being shown (the session id). Changing it replays
   * the entrance reveal once — so opening a session fills in instead of
   * appearing all at once. Leave undefined to disable the reveal entirely.
   */
  revealKey?: string | null;
}) {
  const columnRef = useRef<HTMLDivElement>(null);
  const userScrollingRef = useRef(false);
  const userScrollIdleRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const virt = useTimelineVirtualRange(scrollRef, entries.length, {
    unpinRef,
    scrollingRef: userScrollingRef,
  });
  const layout = layoutTimelineWindow(entries.length, virt);

  // Reveal gate. Deliberately null-gated on content: while the pane is empty
  // there is nothing to reveal, and firing on `revealKey` alone would burn the
  // one-shot window before the rows arrive (both cache-miss and cache-hit
  // paths can hand us the session id first and the entries a tick later).
  // Growing `entries` afterwards keeps the same signature, so streaming and
  // paging never replay it.
  const revealSignature = revealKey && entries.length > 0 ? revealKey : null;
  const [revealing, setRevealing] = useState(false);
  useEffect(() => {
    if (!revealSignature) return;
    setRevealing(true);
    const id = window.setTimeout(() => setRevealing(false), REVEAL_TOTAL_MS);
    return () => window.clearTimeout(id);
  }, [revealSignature]);

  /** Per-row delay for the currently mounted window, or undefined when idle. */
  const revealStyleAt = useMemo(() => {
    if (!revealing) return null;
    return (ordinal: number): CSSProperties => ({
      '--reveal-delay': `${Math.min(ordinal, REVEAL_MAX_STAGGERED_ROWS) * REVEAL_STAGGER_MS}ms`,
    } as CSSProperties);
  }, [revealing]);

  const nodes: ReactNode[] = [];
  // Ordinal among *rendered* rows, not the global index: the visible window of
  // a long history sits at the end of `entries`, so a global index would push
  // every delay past the cap and the stagger would be invisible.
  let revealOrdinal = 0;
  const pushRow = (i: number) => {
    const entry = entries[i];
    if (!entry) return;
    const entryKey = entry._uid || `entry-${i}`;
    const delay = revealStyleAt?.(revealOrdinal);
    revealOrdinal += 1;
    nodes.push(renderEntry(entry, i, entryKey, delay));
  };

  if (layout.padTopPx > 0) {
    nodes.push(
      <div key="virt-pad-top" className="timeline-virt-pad" style={{ height: layout.padTopPx }} aria-hidden />,
    );
  }
  for (let i = layout.midStart; i <= layout.midEnd; i++) {
    pushRow(i);
  }
  if (layout.padMidPx > 0) {
    nodes.push(
      <div key="virt-pad-mid" className="timeline-virt-pad" style={{ height: layout.padMidPx }} aria-hidden />,
    );
  }
  for (let i = layout.tailStart; i < entries.length; i++) {
    if (i <= layout.midEnd) continue;
    pushRow(i);
  }

  const markUserScrolling = useCallback(() => {
    userScrollingRef.current = true;
    if (userScrollIdleRef.current) clearTimeout(userScrollIdleRef.current);
    userScrollIdleRef.current = setTimeout(() => {
      userScrollingRef.current = false;
    }, 180);
  }, []);

  const syncUnpin = useCallback(
    (el: HTMLElement) => {
      if (!unpinRef) return;
      const dist = el.scrollHeight - el.scrollTop - el.clientHeight;
      unpinRef.current = dist > 80;
    },
    [unpinRef],
  );

  const handleScroll = useCallback<UIEventHandler<HTMLDivElement>>(
    (e) => {
      syncUnpin(e.currentTarget);
      markUserScrolling();
      onScroll?.(e);
    },
    [markUserScrolling, onScroll, syncUnpin],
  );

  useEffect(() => {
    const onUp = () => {
      if (userScrollIdleRef.current) clearTimeout(userScrollIdleRef.current);
      userScrollIdleRef.current = setTimeout(() => {
        userScrollingRef.current = false;
      }, 80);
    };
    window.addEventListener('pointerup', onUp);
    window.addEventListener('pointercancel', onUp);
    return () => {
      window.removeEventListener('pointerup', onUp);
      window.removeEventListener('pointercancel', onUp);
    };
  }, []);

  // Pin to bottom when the column actually grows (new tools / stream), not on
  // every parent re-render — that forced layout on each tool_call and janked
  // fast tool bursts. Never fight the thumb while the user is dragging.
  // A user-initiated fold expand also grows the column — but there the fold
  // header must stay put (content grows downward), so bail while it animates.
  useEffect(() => {
    const el = scrollRef.current;
    const col = columnRef.current;
    if (!el || !col) return;
    const pin = () => {
      if (freezeRef?.current || unpinRef?.current || userScrollingRef.current) return;
      if (isFoldAnimating()) return;
      const gap = el.scrollHeight - el.scrollTop - el.clientHeight;
      if (gap < 4) return;
      el.scrollTop = el.scrollHeight;
    };
    pin();
    const ro = new ResizeObserver(pin);
    ro.observe(col);
    return () => {
      ro.disconnect();
      if (userScrollIdleRef.current) clearTimeout(userScrollIdleRef.current);
    };
  }, [scrollRef, freezeRef, unpinRef]);

  return (
    <div
      ref={scrollRef}
      className={['os-chat-scroll', className].filter(Boolean).join(' ')}
      style={{ overflowAnchor: 'none', scrollBehavior: 'auto', ...style }}
      onPointerDown={(e) => {
        userScrollingRef.current = true;
        if (e.currentTarget instanceof HTMLElement) syncUnpin(e.currentTarget);
        markUserScrolling();
      }}
      onScroll={handleScroll}
    >
      <div
        ref={columnRef}
        className={[columnClass, revealing ? 'os-revealing' : ''].filter(Boolean).join(' ')}
      >
        {header}
        {nodes}
        {footer}
      </div>
    </div>
  );
}
