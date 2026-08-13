import React, { type CSSProperties, type ReactNode, type RefObject, type UIEventHandler } from 'react';
import {
  layoutTimelineWindow,
  useTimelineVirtualRange,
} from '../../hooks/useTimelineVirtualRange';

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
}) {
  const virt = useTimelineVirtualRange(scrollRef, entries.length);
  const layout = layoutTimelineWindow(entries.length, virt);
  const nodes: ReactNode[] = [];

  if (layout.padTopPx > 0) {
    nodes.push(
      <div key="virt-pad-top" className="timeline-row" style={{ height: layout.padTopPx }} aria-hidden />,
    );
  }
  for (let i = layout.midStart; i <= layout.midEnd; i++) {
    const entry = entries[i];
    const entryKey = entry._uid || `entry-${i}`;
    nodes.push(renderEntry(entry, i, entryKey));
  }
  if (layout.padMidPx > 0) {
    nodes.push(
      <div key="virt-pad-mid" className="timeline-row" style={{ height: layout.padMidPx }} aria-hidden />,
    );
  }
  for (let i = layout.tailStart; i < entries.length; i++) {
    if (i <= layout.midEnd) continue;
    const entry = entries[i];
    const entryKey = entry._uid || `entry-${i}`;
    nodes.push(renderEntry(entry, i, entryKey));
  }

  return (
    <div ref={scrollRef} className={className} style={style} onScroll={onScroll}>
      <div className={columnClass}>
        {header}
        {nodes}
        {footer}
      </div>
    </div>
  );
}
