/**
 * WorkflowContainer - collapsible container for AI workflow events.
 *
 * Groups thought blocks, tool calls, and tool results within a
 * collapsible section that shows timing information.
 * Matches the legacy HTML's workflow-container pattern.
 *
 * Usage:
 *   - Active workflow (still running): status="Thinking...", defaultOpen={true}
 *   - Completed workflow: status={undefined}, defaultOpen={false}
 *
 * Timing:
 *   - While running: pass `startedMs` (epoch ms from backend turn_start).
 *     The component computes `Date.now() - startedMs` every 100ms.
 *   - When completed: pass `finalElapsedMs` (ms, from backend turn_elapsed).
 *     The display freezes at this value. It is also persisted in the session.
 */
import React, { useState, useEffect, useRef, useLayoutEffect } from 'react';
import { Cpu, ChevronDown, ChevronRight, CheckCircle2 } from 'lucide-react';
import { formatElapsed } from '../../utils/formatElapsed';

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

export const WorkflowContainer: React.FC<WorkflowContainerProps> = ({
  status,
  children,
  defaultOpen = false,
  startedMs,
  finalElapsedMs,
}) => {
  const [isOpen, setIsOpen] = useState(defaultOpen);
  const [liveElapsed, setLiveElapsed] = useState(0);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const prevDefaultOpen = useRef(defaultOpen);
  const userOverride = useRef<'open' | 'closed' | null>(null);

  // Inner scroll: sticky-bottom auto-scroll
  const scrollRef = useRef<HTMLDivElement>(null);
  const isAtBottomRef = useRef(true);
  const prevScrollHeightRef = useRef(0);

  // "Running" means there's an active status string
  const isRunning = !!status;

  // Sync defaultOpen changes: auto-collapse when workflow completes
  useEffect(() => {
    if (prevDefaultOpen.current && !defaultOpen) {
      // Auto-collapse ONLY if the user has NOT pinned it open.
      if (userOverride.current !== 'open') {
        const t = setTimeout(() => setIsOpen(false), 800);
        prevDefaultOpen.current = defaultOpen;
        return () => clearTimeout(t);
      }
    }
    if (!prevDefaultOpen.current && defaultOpen) {
      // Auto-open while running, unless user explicitly collapsed earlier.
      if (userOverride.current !== 'closed') setIsOpen(true);
    }
    prevDefaultOpen.current = defaultOpen;
  }, [defaultOpen]);

  // Live timer: only runs while workflow is active and we have a start timestamp
  useEffect(() => {
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
    if (isRunning && startedMs !== undefined) {
      timerRef.current = setInterval(() => {
        setLiveElapsed(Date.now() - startedMs);
      }, 100);
    }
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [isRunning, startedMs]);

  // Displayed duration:
  //   completed → finalElapsedMs (exact, from backend)
  //   running   → live calculation from startedMs
  //   fallback  → 0
  const displayElapsed = formatElapsed(
    finalElapsedMs !== undefined ? finalElapsedMs : liveElapsed,
  );

  const displayStatus = status || 'Completed';
  const icon = isRunning ? (
    <Cpu size={14} className="text-primary animate-spin" />
  ) : (
    <CheckCircle2 size={14} className="text-emerald-500" />
  );

  const handleToggle = () => {
    setIsOpen(prev => {
      const next = !prev;
      userOverride.current = next ? 'open' : 'closed';
      return next;
    });
  };

  // Track whether inner scroll is at the bottom
  const handleInnerScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    const dist = el.scrollHeight - el.scrollTop - el.clientHeight;
    isAtBottomRef.current = dist < 30;
  };

  // After content changes, auto-scroll only if we were at the bottom.
  // If user has scrolled up to read a specific tool call, freeze position.
  useLayoutEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const prevH = prevScrollHeightRef.current;
    prevScrollHeightRef.current = el.scrollHeight;
    const delta = el.scrollHeight - prevH;
    if (delta <= 0) return;
    if (isAtBottomRef.current) {
      el.scrollTop = el.scrollHeight - el.clientHeight;
    }
  });

  return (
    <div className="mb-3 ml-2 sm:ml-9 border border-border rounded-lg overflow-hidden bg-panel/50">
      {/* Header */}
      <div
        className="flex items-center gap-2 px-3 py-2 cursor-pointer hover:bg-primary/10 transition-colors select-none"
        onClick={handleToggle}
      >
        {icon}
        <span className="text-xs text-textMuted flex-1 truncate">{displayStatus}</span>
        <span className="text-[10px] text-textMuted font-mono">{displayElapsed}</span>
        {isOpen
          ? <ChevronDown size={14} className="text-textMuted" />
          : <ChevronRight size={14} className="text-textMuted" />
        }
      </div>

      {/* Content — keep mounted (hidden when collapsed) so open SubAgentPanel
          portals keep receiving live async-delegate updates after the parent
          turn seals and this container auto-collapses. */}
      <div
        ref={scrollRef}
        onScroll={handleInnerScroll}
        className={`border-t border-border px-3 py-2 space-y-2 max-h-[600px] overflow-y-auto text-xs ${
          isOpen ? '' : 'hidden'
        }`}
        aria-hidden={!isOpen}
      >
        {children}
      </div>
    </div>
  );
};
