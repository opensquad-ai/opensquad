import React, {
  useCallback,
  useEffect,
  useRef,
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
}: {
  scrollRef: RefObject<HTMLDivElement | null>;
  entries: T[];
  renderEntry: (entry: T, index: number, key: string) => ReactNode;
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
}) {
  const columnRef = useRef<HTMLDivElement>(null);
  const userScrollingRef = useRef(false);
  const userScrollIdleRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const virt = useTimelineVirtualRange(scrollRef, entries.length, {
    unpinRef,
    scrollingRef: userScrollingRef,
  });
  const layout = layoutTimelineWindow(entries.length, virt);
  const nodes: ReactNode[] = [];

  if (layout.padTopPx > 0) {
    nodes.push(
      <div key="virt-pad-top" className="timeline-virt-pad" style={{ height: layout.padTopPx }} aria-hidden />,
    );
  }
  for (let i = layout.midStart; i <= layout.midEnd; i++) {
    const entry = entries[i];
    if (!entry) continue;
    const entryKey = entry._uid || `entry-${i}`;
    nodes.push(renderEntry(entry, i, entryKey));
  }
  if (layout.padMidPx > 0) {
    nodes.push(
      <div key="virt-pad-mid" className="timeline-virt-pad" style={{ height: layout.padMidPx }} aria-hidden />,
    );
  }
  for (let i = layout.tailStart; i < entries.length; i++) {
    if (i <= layout.midEnd) continue;
    const entry = entries[i];
    if (!entry) continue;
    const entryKey = entry._uid || `entry-${i}`;
    nodes.push(renderEntry(entry, i, entryKey));
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
      <div ref={columnRef} className={columnClass}>
        {header}
        {nodes}
        {footer}
      </div>
    </div>
  );
}
