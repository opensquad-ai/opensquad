/**
 * SessionChatPane — rich Agent Web timeline for a session tab that is not
 * hosting the live chatSlot (or mirrors the live timeline for the same sid).
 * Replaces SessionHistoryPreview's plain "你/AGENT" list.
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { agentSessionAPI } from '../../services/api';
import { OpenSquadLoader } from '../OpenSquadLoader';
import {
  buildTimelineFromSession,
  rebaseTimelineUids,
  timelineRichness,
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
import { CHAT_DOCUMENT_COLUMN_CLASS } from '../../utils/chatLayout';
import { useTextSelectionFreeze } from '../../hooks/useTextSelectionFreeze';
import { ChatTimeline } from './ChatTimeline';
import { ChatScrollComposerHint, ChatScrollHud } from './ChatScrollHud';
import { SoloMessage } from './SoloMessage';
import { MessageBubble, type ChatMessage } from './MessageBubble';
import { SoloActivityRow, mergeWorkflowBlocks } from './SoloActivityRow';
import { TimelineRow } from './TimelineRow';
import {
  SoloUserNavRail,
  buildUserNavNodesFromTimeline,
  userNavAnchorDomId,
} from './SoloUserNavRail';

/** 稳定的空 shell 流引用：SoloActivityRow 无实时 shell 流时传入，
 *  避免默认参数 `{}` 每次渲染新建对象导致 React.memo 浅比较失效。 */
const EMPTY_SHELL_STREAMS: Record<string, never> = {};

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
  /** Allow withdraw on user turns (same as live chatSlot). */
  canWithdraw?: boolean;
  onWithdrawUserMessage?: (entryUid: string, message: ChatMessage) => void;
}

export const SessionChatPane: React.FC<SessionChatPaneProps> = ({
  agentId,
  sessionId,
  liveTimeline,
  isSolo = true,
  expandLevel: expandLevelProp,
  columnClass = CHAT_DOCUMENT_COLUMN_CLASS,
  userName,
  agentName,
  onFocus,
  pollIntervalMs,
  canWithdraw = false,
  onWithdrawUserMessage,
}) => {
  const [prefLevel] = useWorkflowExpandLevel();
  const expandLevel = expandLevelProp ?? prefLevel;
  const { t } = useTranslation();
  const cached = !Array.isArray(liveTimeline)
    ? getCachedSessionTimeline(agentId, sessionId)
    : null;
  // Cache-first: never block the tab on a spinner — paint cache/empty immediately
  // and refresh in the background so session switches feel instant.
  const [loading, setLoading] = useState(false);
  const [showSpinner, setShowSpinner] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fetched, setFetched] = useState<TimelineEntry[]>(() => cached || []);
  const listRef = useRef<HTMLDivElement>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const userScrolledRef = useRef(false);
  // Empty array still counts as Array.isArray — treat it as a miss so we
  // fetch disk history instead of painting a blank pane forever.
  const useLive = Array.isArray(liveTimeline) && liveTimeline.length > 0;
  // When soft-polling (scheduled exec), keep merging disk into a live mirror
  // so missed WS frames still surface without a full refresh.
  const liveOrFetched = useMemo(() => {
    if (!useLive) return fetched;
    const live = liveTimeline as TimelineEntry[];
    if (!pollIntervalMs || pollIntervalMs <= 0 || fetched.length === 0) return live;
    return timelineRichness(fetched) > timelineRichness(live) ? fetched : live;
  }, [useLive, liveTimeline, fetched, pollIntervalMs]);
  const {
    displayValue: timeline,
    isFrozenRef,
  } = useTextSelectionFreeze(listRef, liveOrFetched);

  const markUnpinnedFromBottom = useCallback((away: boolean) => {
    userScrolledRef.current = away;
  }, []);

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
      setError(null);
      // Cache hit: paint instantly. Only background-refresh when incomplete.
      if (meta.complete) return;
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
          setFetched((prev) => rebaseTimelineUids(prev, entries));
        } catch {
          /* keep cached paint */
        }
      })();
      return () => {
        cancelled = true;
      };
    }
    // Instant empty paint — no blocking overlay while history loads.
    setFetched([]);
    setLoading(true);
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
  // Runs even alongside liveTimeline when pollIntervalMs is set (scheduled
  // exec catch-up). Skipped while the user is selecting text.
  useEffect(() => {
    if (!pollIntervalMs || pollIntervalMs <= 0) return;
    let cancelled = false;
    const softRefresh = async () => {
      if (isFrozenRef.current) return;
      // Skip while the tab is hidden — the poll is a catch-up fallback for
      // missed WS frames, not the real-time path.
      if (document.visibilityState !== 'visible') return;
      try {
        const resp = await agentSessionAPI.getSessionHistoryPaged(
          agentId,
          sessionId,
          0,
          SESSION_HISTORY_PAGE_SIZE,
        );
        if (cancelled || isFrozenRef.current) return;
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
          complete: !session?.has_more,
          messageCount: messages.length,
          totalMessages: session?.total_messages,
        });
        // Keep React keys stable so expand/collapse state survives the poll.
        setFetched((prev) => {
          const next = rebaseTimelineUids(prev, entries);
          // Bail when nothing meaningful changed — avoid idle re-renders that
          // destroy text selection.
          if (
            prev.length === next.length
            && prev.every((p, i) => {
              const n = next[i];
              if (p.kind !== n.kind || p._uid !== n._uid) return false;
              if (p.kind === 'message' && n.kind === 'message') {
                return p.data.content === n.data.content && p.data.role === n.data.role;
              }
              if (p.kind === 'workflow' && n.kind === 'workflow') {
                return (
                  p.data.completed === n.data.completed
                  && p.data.status === n.data.status
                  && (p.data.events?.length || 0) === (n.data.events?.length || 0)
                  && (p.data.elapsed_ms || 0) === (n.data.elapsed_ms || 0)
                );
              }
              if (p.kind === 'task_fold' && n.kind === 'task_fold') {
                return (
                  (p.data.entries?.length || 0) === (n.data.entries?.length || 0)
                  && (p.data.messageCount || 0) === (n.data.messageCount || 0)
                  && (p.data.eventCount || 0) === (n.data.eventCount || 0)
                );
              }
              return true;
            })
          ) {
            return prev;
          }
          return next;
        });
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
  }, [agentId, sessionId, pollIntervalMs]);

  const userNavNodes = useMemo(
    () => buildUserNavNodesFromTimeline(timeline),
    [timeline],
  );

  const jumpToUserMessage = useCallback((id: string) => {
    const container = listRef.current;
    const el = document.getElementById(userNavAnchorDomId(id));
    if (!container || !el) return;
    const cRect = container.getBoundingClientRect();
    const eRect = el.getBoundingClientRect();
    const top = eRect.top - cRect.top + container.scrollTop - 12;
    container.scrollTo({ top: Math.max(0, top), behavior: 'smooth' });
  }, []);

  return (
    <div
      className="flex-1 min-h-0 flex flex-col bg-panel relative"
      onMouseDown={() => onFocus?.()}
    >
      <div className="flex-1 relative min-h-0">
        {userNavNodes.length > 0 && (
          <div className="pointer-events-none absolute inset-y-0 right-0 z-30 flex items-center justify-end pr-1 overflow-visible">
            <div className="pointer-events-auto overflow-visible">
              <SoloUserNavRail
                nodes={userNavNodes}
                activeId={userNavNodes[userNavNodes.length - 1]?.id}
                onJump={jumpToUserMessage}
              />
            </div>
          </div>
        )}
        <ChatScrollHud
          scrollRef={listRef}
          onUnpin={markUnpinnedFromBottom}
        />
        <ChatTimeline
          scrollRef={listRef}
          entries={timeline}
          revealKey={sessionId}
          className="h-full min-h-0 overflow-y-auto px-2 sm:px-4 py-3 sm:py-4"
          columnClass={columnClass}
          unpinRef={userScrolledRef}
          freezeRef={isFrozenRef}
          header={
            loading && timeline.length === 0 ? (
              showSpinner ? (
                <div className="flex items-center justify-center text-textMuted text-xs gap-2 py-12">
                  <OpenSquadLoader size={18} /> 加载中…
                </div>
              ) : (
                <div className="py-12" />
              )
            ) : error && timeline.length === 0 ? (
              <div className="px-1 py-8 text-[12px] text-rose-400 text-center">{error}</div>
            ) : null
          }
          footer={<div ref={endRef} />}
          renderEntry={(entry, i, entryKey, revealStyle) => {
                const lockLayout =
                  i >= timeline.length - 8
                  || (entry.kind === 'workflow' && !entry.data.completed);
                if (entry.kind === 'message') {
                  const msgProps = {
                    message: entry.data,
                    senderName:
                      entry.data.role === 'user'
                        ? userName
                        : agentName,
                    // 助手回复紧跟工作流组时，名字已在工作流上方显示 —— 整行隐藏。
                    // 只传 undefined 不够：MessageBubble 会退化成兜底文案「Agent」，
                    // 统计行和正文之间就多出一行幽灵签名。
                    hideSenderLabel:
                      entry.data.role === 'assistant'
                      && i > 0
                      && timeline[i - 1].kind === 'workflow',
                    agentId,
                    canWithdraw:
                      canWithdraw &&
                      entry.data.role === 'user' &&
                      !!onWithdrawUserMessage,
                    onWithdraw:
                      entry.data.role === 'user' && onWithdrawUserMessage
                        ? () => onWithdrawUserMessage(entryKey, entry.data)
                        : undefined,
                    anchorId: entryKey,
                  };
                  return (
                    <TimelineRow key={entryKey} lockLayout={lockLayout} style={revealStyle}>
                      {isSolo ? (
                        <SoloMessage {...msgProps} />
                      ) : (
                        <MessageBubble {...msgProps} />
                      )}
                    </TimelineRow>
                  );
                }
                if (entry.kind === 'workflow') {
                  const curBlock = entry.data as WorkflowBlock;
                  if (i > 0 && timeline[i - 1].kind === 'workflow') {
                    return null;
                  }
                  const blocks: WorkflowBlock[] = [curBlock];
                  let j = i + 1;
                  while (j < timeline.length && timeline[j].kind === 'workflow') {
                    blocks.push((timeline[j] as { kind: 'workflow'; data: WorkflowBlock }).data);
                    j += 1;
                  }
                  const merged = blocks.length > 1 ? mergeWorkflowBlocks(blocks) : curBlock;
                  // 任务已交付判定：工作流组之后紧跟 assistant 最终回复 → 停止动画。
                  const nextAfterGroup = timeline[j];
                  const turnDelivered =
                    !!nextAfterGroup
                    && nextAfterGroup.kind === 'message'
                    && (nextAfterGroup.data as ChatMessage).role === 'assistant'
                    && typeof (nextAfterGroup.data as ChatMessage).content === 'string'
                    && !!(nextAfterGroup.data as ChatMessage).content.trim();
                  return (
                    <TimelineRow key={entryKey} lockLayout={lockLayout} style={revealStyle}>
                      <div className="w-full min-w-0">
                        {agentName && (
                          <div className="text-[11px] font-medium text-textMuted/70 mb-2">{agentName}</div>
                        )}
                        <SoloActivityRow
                          block={merged}
                          turnDelivered={turnDelivered}
                          expandLevel={expandLevel}
                          embedVisualizations={false}
                          uiMode={isSolo ? 'solo' : 'classic'}
                          shellStreams={EMPTY_SHELL_STREAMS}
                        />
                      </div>
                    </TimelineRow>
                  );
                }
                if (entry.kind === 'model_switch') {
                  // 模型切换提示（独立轻量条目，不属于工作流统计）。
                  const sw = entry.data;
                  const label = sw.model
                    ? t('aiChat.modelSwitched', { model: sw.model })
                    : sw.text;
                  return (
                    <TimelineRow key={entryKey} style={revealStyle}>
                      <div className="flex items-center gap-1.5 py-0.5 my-0.5 mx-0">
                        <div className="flex-1 h-px bg-border/25" />
                        <span className="text-[10px] text-textMuted/45 font-mono shrink-0">{label}</span>
                        <div className="flex-1 h-px bg-border/25" />
                      </div>
                    </TimelineRow>
                  );
                }
                return null;
          }}
        />
      </div>

      {!isSolo && (
        <ChatScrollComposerHint
          scrollRef={listRef}
          columnClass={columnClass}
          onUnpin={markUnpinnedFromBottom}
        />
      )}
    </div>
  );
};
