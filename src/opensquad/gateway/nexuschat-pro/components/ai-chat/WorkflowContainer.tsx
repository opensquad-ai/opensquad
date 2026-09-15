/**
 * WorkflowContainer - collapsible container for AI workflow events.
 *
 * Groups thought blocks, tool calls, and tool results within a
 * collapsible section that shows timing information.
 *
 * Usage:
 *   - Active workflow (still running): status="Thinking...", defaultOpen={true}
 *   - Completed workflow: status={undefined}, defaultOpen={false}
 *
 * Timing:
 *   - While running: pass `startedMs` (epoch ms from backend turn_start).
 *     Elapsed time is driven by a shared 400ms ticker (not a per-instance 100ms timer).
 *   - When completed: pass `finalElapsedMs` (ms, from backend turn_elapsed).
 */
import React, { useEffect, useRef } from 'react';
import { CheckCircle2 } from 'lucide-react';
import { Collapse, FoldChevron, isFoldAnimating, useFold } from '../Collapse';
import { formatElapsed } from '../../utils/formatElapsed';
import { useSharedNow } from '../../hooks/useSharedNow';
import { OpenSquadLoader } from '../OpenSquadLoader';

interface WorkflowContainerProps {
  status?: string;
  children: React.ReactNode;
  defaultOpen?: boolean;
  /** Backend start timestamp (epoch ms) from turn_start event.
   *  Used to compute live elapsed time while the workflow is running. */
  startedMs?: number;
  /** Final elapsed time in ms (= ended_ms - started_ms) from turn_elapsed event.
   *  When present the display freezes at this value (workflow completed). */
  finalElapsedMs?: number;
}

const WorkflowContainerInner: React.FC<WorkflowContainerProps> = ({
  status,
  children,
  defaultOpen = false,
  startedMs,
  finalElapsedMs,
}) => {
  const { open: isOpen, toggle: toggleOpen, setOpen } = useFold(defaultOpen);
  const prevDefaultOpen = useRef(defaultOpen);
  const userOverride = useRef<'open' | 'closed' | null>(null);

  const scrollRef = useRef<HTMLDivElement>(null);
  const columnRef = useRef<HTMLDivElement>(null);
  const isAtBottomRef = useRef(true);

  const isRunning = !!status;
  const now = useSharedNow(isRunning && startedMs !== undefined);

  useEffect(() => {
    if (prevDefaultOpen.current && !defaultOpen) {
      if (userOverride.current !== 'open') {
        const t = setTimeout(() => setOpen(false), 800);
        prevDefaultOpen.current = defaultOpen;
        return () => clearTimeout(t);
      }
    }
    if (!prevDefaultOpen.current && defaultOpen) {
      if (userOverride.current !== 'closed') setOpen(true);
    }
    prevDefaultOpen.current = defaultOpen;
  }, [defaultOpen, setOpen]);

  const liveElapsed =
    startedMs !== undefined ? Math.max(0, now - startedMs) : 0;
  const displayElapsed = formatElapsed(
    finalElapsedMs !== undefined ? finalElapsedMs : liveElapsed,
  );

  const displayStatus = status || 'Completed';
  const icon = isRunning ? (
    <OpenSquadLoader size={14} />
  ) : (
    <CheckCircle2 size={14} className="text-emerald-500" />
  );

  const handleToggle = () => {
    const next = toggleOpen();
    userOverride.current = next ? 'open' : 'closed';
  };

  const handleInnerScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    const dist = el.scrollHeight - el.scrollTop - el.clientHeight;
    isAtBottomRef.current = dist < 30;
  };

  useEffect(() => {
    const el = scrollRef.current;
    const col = columnRef.current;
    if (!el || !col || !isOpen) return;
    const pin = () => {
      if (!isAtBottomRef.current) return;
      // User expanding an inner fold grows the box content — keep the fold
      // header in place instead of yanking the box to its bottom.
      if (isFoldAnimating()) return;
      el.scrollTop = el.scrollHeight - el.clientHeight;
    };
    pin();
    const ro = new ResizeObserver(pin);
    ro.observe(col);
    return () => ro.disconnect();
  }, [isOpen]);

  return (
    <div className="mb-3 ml-2 sm:ml-9 border border-border rounded-lg overflow-hidden bg-panel/50">
      <div
        className="flex items-center gap-2 px-3 py-2 cursor-pointer hover:bg-primary/10 transition-colors select-none"
        onClick={handleToggle}
        aria-expanded={isOpen}
      >
        {icon}
        <span className="text-xs text-textMuted flex-1 truncate">{displayStatus}</span>
        <span className="text-[10px] text-textMuted font-mono">{displayElapsed}</span>
        <FoldChevron open={isOpen} size={14} />
      </div>

      {/* Children stay mounted either way — the scroll-pinning effect needs the
          column ref — so the fold is a pure height transition. */}
      <Collapse open={isOpen}>
        <div
          ref={scrollRef}
          onScroll={handleInnerScroll}
          className="border-t border-border px-3 py-2 max-h-[600px] overflow-y-auto text-xs"
        >
          <div ref={columnRef} className="space-y-2">
            {children}
          </div>
        </div>
      </Collapse>
    </div>
  );
};

export const WorkflowContainer = React.memo(WorkflowContainerInner);
