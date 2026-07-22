/**
 * SessionChatPane — rich Agent Web timeline for a session tab that is not
 * hosting the live chatSlot (or mirrors the live timeline for the same sid).
 * Replaces SessionHistoryPreview's plain "你/AGENT" list.
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { ChevronDown, ChevronUp, Loader2 } from 'lucide-react';
import { agentSessionAPI } from '../../services/api';
import {
  buildTimelineFromSession,
  rebaseTimelineUids,
  type TimelineEntry,
  type WorkflowBlock,
} from '../../utils/aiChatTimeline';
import {
  getCachedSessionTimeline,
  getCachedSessionTimelineMeta,
  putCachedSessionTimeline,
  SESSION_HISTORY_PAGE_SIZE,
} from '../../utils/sessionTimelineCache';
import { useWorkflowExpandLevel, type WorkflowExpandLevel } from '../../utils/workflowExpandPref';
import { SoloMessage } from './SoloMessage';
import { MessageBubble } from './MessageBubble';
import { SoloActivityRow, mergeWorkflowBlocks } from './SoloActivityRow';

export interface SessionChatPaneProps {
  agentId: string;
  sessionId: string;
  /** When set, render this instead of fetching history (same-session mirror of live pane). */
  liveTimeline?: TimelineEntry[] | null;
  isSolo?: boolean;
  /** @deprecated Prefer reading Settings → General; kept for optional override. */
  expandLevel?: WorkflowExpandLevel;
  columnClass?: string;
  userName?: string;
  agentName?: string;
  /** Focus this pane only — must NOT switch the global live session. */
  onFocus?: () => void;
  /**
   * Soft-poll session history every N ms without remounting / clearing the
   * timeline. Used by scheduled-task workflow view while an execution is
   * running (events land on disk; there is no live WS for the synthetic user).
   */
  pollIntervalMs?: number;
}

export const SessionChatPane: React.FC<SessionChatPaneProps> = ({
  agentId,
  sessionId,
  liveTimeline,
  isSolo = true,
  expandLevel: expandLevelProp,
  columnClass = 'max-w-3xl mx-auto w-full',
  userName,
  agentName,
  onFocus,
  pollIntervalMs,
}) => {
  const [prefLevel] = useWorkflowExpandLevel();
  const expandLevel = expandLevelProp ?? prefLevel;
  const cached = !Array.isArray(liveTimeline)
    ? getCachedSessionTimeline(agentId, sessionId)
    : null;
  // Cache-first: never block the tab on a spinner — paint cache/empty immediately
  // and refresh in the background so session switches feel instant.
  const [loading, setLoading] = useState(false);
  const [showSpinner, setShowSpinner] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fetched, setFetched] = useState<TimelineEntry[]>(() => cached || []);
  const [showScrollTop, setShowScrollTop] = useState(false);
  const [showScrollBottom, setShowScrollBottom] = useState(false);
  const [scrollActive, setScrollActive] = useState(false);
  const listRef = useRef<HTMLDivElement>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const userScrolledRef = useRef(false);
  const scrollHideTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const useLive = Array.isArray(liveTimeline);
  const timeline = useLive ? (liveTimeline as TimelineEntry[]) : fetched;

  useEffect(() => {
    // Only show a soft spinner if the first fetch for an uncached session
    // takes longer than ~800ms — otherwise switches stay silent.
    if (!loading || timeline.length > 0) {
      setShowSpinner(false);
      return;
    }
    const t = window.setTimeout(() => setShowSpinner(true), 800);
    return () => window.clearTimeout(t);
  }, [loading, timeline.length]);

  useEffect(() => {
    if (useLive) {
      setLoading(false);
      setError(null);
      return;
    }
    let cancelled = false;
    const meta = getCachedSessionTimelineMeta(agentId, sessionId);
    if (meta?.entries?.length) {
      setFetched(meta.entries);
      setLoading(false);
      // Complete cache: skip network for instant tab switches.
      if (meta.complete) {
        setError(null);
        return;
      }
    } else {
      // Instant empty paint — no blocking overlay while history loads.
      setFetched([]);
      setLoading(true);
    }
    setError(null);
    void (async () => {
      try {
        const resp = await agentSessionAPI.getSessionHistoryPaged(
          agentId,
          sessionId,
          0,
          SESSION_HISTORY_PAGE_SIZE,
        );
        if (cancelled) return;
        const session = resp.session as
          | {
              messages?: any[];
              events?: any[];
              archived_messages?: any[];
              archived_events?: any[];
              has_more?: boolean;
              total_messages?: number;
            }
          | undefined;
        const messages = session?.messages || [];
        const entries = buildTimelineFromSession(
          messages,
          session?.events || [],
          session?.archived_messages,
          session?.archived_events,
        );
        const hasMore = !!session?.has_more;
        putCachedSessionTimeline(agentId, sessionId, entries, {
          complete: !hasMore,
          messageCount: messages.length,
          totalMessages: session?.total_messages,
        });
        setFetched(entries);
      } catch (err: any) {
        if (!cancelled) setError(err?.message || '无法加载会话');
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [agentId, sessionId, useLive]);

  // Soft poll: refresh timeline in place (no loading spinner, no remount).
  // Skipped when liveTimeline is provided or pollIntervalMs is unset/<=0.
  useEffect(() => {
    if (useLive || !pollIntervalMs || pollIntervalMs <= 0) return;
    let cancelled = false;
    const softRefresh = async () => {
      try {
        const resp = await agentSessionAPI.getSessionHistoryPaged(
          agentId,
          sessionId,
          0,
          SESSION_HISTORY_PAGE_SIZE,
        );
        if (cancelled) return;
        const session = resp.session as
          | {
              messages?: any[];
              events?: any[];
              archived_messages?: any[];
              archived_events?: any[];
              has_more?: boolean;
              total_messages?: number;
            }
          | undefined;
        const messages = session?.messages || [];
        const entries = buildTimelineFromSession(
          messages,
          session?.events || [],
          session?.archived_messages,
          session?.archived_events,
        );
        putCachedSessionTimeline(agentId, sessionId, entries, {
          // While polling a live turn, never mark complete — next tick must refetch.
          complete: false,
          messageCount: messages.length,
          totalMessages: session?.total_messages,
        });
        // Keep React keys stable so expand/collapse state survives the poll.
        setFetched((prev) => rebaseTimelineUids(prev, entries));
        setError(null);
      } catch {
        // Keep showing the last good timeline; transient errors during a run
        // should not blank the pane.
      }
    };
    const id = window.setInterval(() => {
      void softRefresh();
    }, pollIntervalMs);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [agentId, sessionId, useLive, pollIntervalMs]);

  const updateScrollButtons = useCallback(() => {
    const el = listRef.current;
    if (!el) return;
    const { scrollTop, scrollHeight, clientHeight } = el;
    const distFromBottom = scrollHeight - scrollTop - clientHeight;
    setShowScrollTop(scrollTop > 200);
    setShowScrollBottom(distFromBottom > 200);
    userScrolledRef.current = distFromBottom > 100;
  }, []);

  const handleScroll = useCallback(() => {
    updateScrollButtons();
    setScrollActive(true);
    if (scrollHideTimerRef.current) clearTimeout(scrollHideTimerRef.current);
    scrollHideTimerRef.current = setTimeout(() => setScrollActive(false), 1500);
  }, [updateScrollButtons]);

  const scrollToBottom = useCallback(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' });
    userScrolledRef.current = false;
    setShowScrollBottom(false);
  }, []);

  const scrollToTop = useCallback(() => {
    listRef.current?.scrollTo({ top: 0, behavior: 'smooth' });
  }, []);

  // Auto-follow bottom only when user hasn't scrolled away
  useEffect(() => {
    if (loading && timeline.length === 0) return;
    if (userScrolledRef.current) {
      updateScrollButtons();
      return;
    }
    const el = listRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
    updateScrollButtons();
  }, [timeline.length, loading, useLive, updateScrollButtons]);

  useEffect(() => {
    return () => {
      if (scrollHideTimerRef.current) clearTimeout(scrollHideTimerRef.current);
    };
  }, []);

  return (
    <div
      className="flex-1 min-h-0 flex flex-col bg-panel relative"
      onMouseDown={() => onFocus?.()}
    >
      <div className="flex-1 relative min-h-0">
        {isSolo && (showScrollTop || showScrollBottom) && (
          <div
            className="pointer-events-none absolute right-1 bottom-4 z-20 transition-opacity duration-300"
            style={{ opacity: scrollActive ? 1 : 0, pointerEvents: scrollActive ? undefined : 'none' }}
          >
            <div className="pointer-events-auto flex flex-col gap-2">
              {showScrollTop && (
                <button
                  type="button"
                  onClick={scrollToTop}
                  className="w-8 h-8 bg-white border border-gray-200 rounded-full shadow-md flex items-center justify-center hover:bg-primary/10 transition-colors"
                  title="滚动到顶部"
                >
                  <ChevronUp size={18} className="text-gray-500" />
                </button>
              )}
              {showScrollBottom && (
                <button
                  type="button"
                  onClick={scrollToBottom}
                  className="w-8 h-8 bg-white border border-gray-200 rounded-full shadow-md flex items-center justify-center hover:bg-primary/10 transition-colors"
                  title="滚动到底部"
                >
                  <ChevronDown size={18} className="text-gray-500" />
                </button>
              )}
            </div>
          </div>
        )}
        <div
          ref={listRef}
          className="h-full min-h-0 overflow-y-auto px-2 sm:px-4 py-3 sm:py-4"
          onScroll={handleScroll}
        >
          <div className={columnClass}>
            {loading && timeline.length === 0 ? (
              showSpinner ? (
                <div className="flex items-center justify-center text-textMuted text-xs gap-2 py-12">
                  <Loader2 size={14} className="animate-spin" /> 加载中…
                </div>
              ) : (
                <div className="py-12" />
              )
            ) : error && timeline.length === 0 ? (
              <div className="px-1 py-8 text-[12px] text-rose-400 text-center">{error}</div>
            ) : timeline.length === 0 ? null : (
              timeline.map((entry, i) => {
                const entryKey = entry._uid || `entry-${i}`;
                if (entry.kind === 'message') {
                  const msgProps = {
                    message: entry.data,
                    senderName:
                      entry.data.role === 'user' ? userName : agentName,
                  };
                  return isSolo ? (
                    <SoloMessage key={entryKey} {...msgProps} anchorId={entryKey} />
                  ) : (
                    <MessageBubble key={entryKey} {...msgProps} />
                  );
                }
                if (entry.kind === 'workflow') {
                  const curBlock = entry.data as WorkflowBlock;
                  if (
                    i > 0 &&
                    timeline[i - 1].kind === 'workflow' &&
                    !(timeline[i - 1] as { kind: 'workflow'; data: WorkflowBlock }).data.completed &&
                    !curBlock.completed
                  ) {
                    return null;
                  }
                  const blocks: WorkflowBlock[] = [curBlock];
                  if (!curBlock.completed) {
                    let j = i + 1;
                    while (
                      j < timeline.length &&
                      timeline[j].kind === 'workflow' &&
                      !(timeline[j] as { kind: 'workflow'; data: WorkflowBlock }).data.completed
                    ) {
                      blocks.push((timeline[j] as { kind: 'workflow'; data: WorkflowBlock }).data);
                      j += 1;
                    }
                  }
                  const merged = blocks.length > 1 ? mergeWorkflowBlocks(blocks) : curBlock;
                  return (
                    <SoloActivityRow
                      key={entryKey}
                      block={merged}
                      expandLevel={expandLevel}
                      embedVisualizations={false}
                    />
                  );
                }
                return null;
              })
            )}
            <div ref={endRef} />
          </div>
        </div>
      </div>

      {/* Classic: scroll-to-bottom centered above composer */}
      {!isSolo && showScrollBottom && (
        <div className="relative flex-shrink-0 z-20 pointer-events-none h-0">
          <div className={`${columnClass} relative`}>
            <button
              type="button"
              onClick={scrollToBottom}
              className="pointer-events-auto absolute left-1/2 -translate-x-1/2 -top-10 w-8 h-8 rounded-full bg-white/95 dark:bg-[#2a2a2c]/95 border border-border/70 shadow-[0_2px_10px_rgba(0,0,0,0.08)] flex items-center justify-center hover:bg-primary/10 transition-opacity duration-300 cursor-pointer"
              style={{ opacity: scrollActive ? 1 : 0.55 }}
              title="滚动到底部"
            >
              <ChevronDown size={18} className="text-gray-500" />
            </button>
          </div>
        </div>
      )}
    </div>
  );
};
