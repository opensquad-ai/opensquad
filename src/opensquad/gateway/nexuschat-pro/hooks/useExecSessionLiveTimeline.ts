/**
 * Live timeline for a scheduled-task execution session.
 *
 * Hydrates from disk once, then applies Agent Web WS events filtered by
 * session id — same dialogue → tool-flow → dialogue interleaving as the
 * main chat pane. Token stats are taken from `token_stats` events for this sid.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { agentSessionAPI } from '../services/api';
import { getAiWsService, type AIWSMessage } from '../services/aiWebSocket';
import {
  appendWorkflowEvent,
  buildTimelineFromSession,
  foldTaskProcessSinceLastUser,
  genTimelineUID,
  rebaseTimelineUids,
  sealIncompleteWorkflows,
  timelineRichness,
  type TimelineEntry,
  type WorkflowEvent,
} from '../utils/aiChatTimeline';
import {
  putCachedSessionTimeline,
  SESSION_HISTORY_PAGE_SIZE,
} from '../utils/sessionTimelineCache';
import type { ChatMessage } from '../components/ai-chat/MessageBubble';
import type { SoloTokenStats } from '../components/ai-chat/SoloContextFooter';

export { timelineRichness };

function extractContent(msg: AIWSMessage): string {
  const raw = msg.content ?? msg.data;
  if (typeof raw === 'string') return raw;
  if (typeof raw === 'object' && raw !== null) {
    if ('data' in raw && typeof (raw as any).data === 'string') return (raw as any).data;
    if ('content' in raw && typeof (raw as any).content === 'string') return (raw as any).content;
    if ('text' in raw && typeof (raw as any).text === 'string') return (raw as any).text;
  }
  return '';
}

function msgSid(msg: AIWSMessage): string {
  const c = msg.content;
  const d = msg.data;
  return String(
    (msg as any).sid
    || (typeof c === 'object' && c && ((c as any).session_id || (c as any).sid))
    || (typeof d === 'object' && d && ((d as any).session_id || (d as any).sid
      || (typeof (d as any).data === 'object' && (d as any).data?.session_id)))
    || '',
  ).trim();
}

/** Normalize token_stats payloads (flat or still EventBus-wrapped). */
function unwrapTokenPayload(msg: AIWSMessage): Record<string, unknown> | null {
  let data: any = msg.content ?? msg.data;
  if (!data || typeof data !== 'object') return null;
  if (
    'data' in data
    && data.data
    && typeof data.data === 'object'
    && ('used' in data.data || 'max' in data.data)
  ) {
    data = data.data;
  }
  if (!('used' in data) && !('max' in data)) return null;
  return data as Record<string, unknown>;
}

function toSoloTokenStats(data: Record<string, unknown>): SoloTokenStats {
  return {
    used: Number(data.used) || 0,
    max: Number(data.max) || 0,
    breakdown: data.breakdown as SoloTokenStats['breakdown'],
    session: data.session as SoloTokenStats['session'],
  };
}

function finalizeWorkflowAndAddMessage(prev: TimelineEntry[], msg: ChatMessage): TimelineEntry[] {
  const updated = [...prev];
  for (let i = updated.length - 1; i >= 0; i--) {
    const entry = updated[i];
    if (entry.kind !== 'message') continue;
    const existing = entry.data as ChatMessage;
    if (existing.role === 'user') break;
    if (existing.role === 'assistant' && existing.content === msg.content) {
      for (let j = updated.length - 1; j >= 0; j--) {
        if (updated[j].kind === 'workflow' && !(updated[j] as Extract<TimelineEntry, { kind: 'workflow' }>).data.completed) {
          updated[j] = {
            ...updated[j],
            data: {
              ...(updated[j] as Extract<TimelineEntry, { kind: 'workflow' }>).data,
              status: null,
              completed: true,
            },
          } as TimelineEntry;
          break;
        }
      }
      return updated;
    }
    break;
  }
  for (let i = updated.length - 1; i >= 0; i--) {
    const entry = updated[i];
    if (entry.kind === 'workflow' && !entry.data.completed) {
      updated[i] = {
        ...entry,
        data: { ...entry.data, status: null, completed: true },
      } as TimelineEntry;
      break;
    }
  }
  updated.push({ kind: 'message', data: msg, _uid: genTimelineUID() });
  return updated;
}

export function useExecSessionLiveTimeline(
  agentId: string,
  sessionId: string | null | undefined,
  opts?: {
    /**
     * Soft-merge from disk every 1.2s. Disable when Agent Web sessionBridge
     * already owns live WS updates — competing disk rewrites remount text
     * nodes and break mouse selection.
     */
    diskPoll?: boolean;
    /**
     * Keep soft-polling even when busyRef is false (running scheduled exec
     * that missed WS frames so busy never flipped).
     */
    forceDiskPoll?: boolean;
  },
): {
  timeline: TimelineEntry[];
  tokenStats: SoloTokenStats | null;
  appendOptimisticUser: (text: string) => void;
  /** Freeze open workflow timers (stop / terminate without assistant reply). */
  sealOnStop: () => void;
  busy: boolean;
} {
  const diskPoll = opts?.diskPoll !== false;
  const forceDiskPoll = !!opts?.forceDiskPoll;
  const [timeline, setTimeline] = useState<TimelineEntry[]>([]);
  const [tokenStats, setTokenStats] = useState<SoloTokenStats | null>(null);
  const [busy, setBusy] = useState(false);
  const sessionIdRef = useRef(sessionId || '');
  const busyRef = useRef(false);
  const forceDiskPollRef = useRef(forceDiskPoll);
  const finalizingRef = useRef(false);

  useEffect(() => {
    sessionIdRef.current = sessionId || '';
  }, [sessionId]);

  useEffect(() => {
    busyRef.current = busy;
  }, [busy]);

  useEffect(() => {
    forceDiskPollRef.current = forceDiskPoll;
  }, [forceDiskPoll]);

  // Reset + hydrate when session changes
  useEffect(() => {
    if (!agentId || !sessionId) {
      setTimeline([]);
      setTokenStats(null);
      setBusy(false);
      return;
    }
    // New session: clear previous execution's timeline before hydrate/WS.
    setTimeline([]);
    setTokenStats(null);
    setBusy(false);
    let cancelled = false;
    // Do not wipe timeline immediately — live WS may already be filling it.
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
              token_stats?: SoloTokenStats | null;
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
        setTimeline((prev) => {
          // Prefer the richer of disk vs already-live timeline.
          if (prev.length > entries.length) return prev;
          return entries;
        });
        const ts = session?.token_stats;
        if (ts && Number(ts.max) > 0) {
          setTokenStats((prev) => prev ?? {
            used: Number(ts.used) || 0,
            max: Number(ts.max) || 0,
            breakdown: ts.breakdown,
            session: ts.session,
          });
        }
      } catch {
        // Keep empty timeline; live WS may still populate.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [agentId, sessionId]);

  const appendOptimisticUser = useCallback((text: string) => {
    const trimmed = text.trim();
    if (!trimmed) return;
    setTimeline((prev) => {
      const sealed = sealIncompleteWorkflows(prev, { nowMs: Date.now() });
      return [
        ...sealed,
        {
          kind: 'message' as const,
          data: {
            role: 'user' as const,
            content: trimmed,
            timestamp: new Date().toISOString(),
          },
          _uid: genTimelineUID(),
        },
      ];
    });
    setBusy(true);
  }, []);

  const sealOnStop = useCallback(() => {
    const nowMs = Date.now();
    setTimeline((prev) => {
      const sealed = sealIncompleteWorkflows(prev, { nowMs });
      // Also cancel open tool cards so the fold is not stuck "running".
      return sealed.map((entry) => {
        if (entry.kind !== 'workflow') return entry;
        const events = entry.data.events.map((evt) => {
          if (evt.type === 'tool_call' && !evt.result) {
            return {
              ...evt,
              result: 'Cancelled: stopped by user',
              resultStatus: 'error' as const,
            };
          }
          return evt;
        });
        return { ...entry, data: { ...entry.data, events, status: null, completed: true } };
      });
    });
    setBusy(false);
  }, []);

  // Live WS for this session
  useEffect(() => {
    if (!agentId || !sessionId) return;

    const aiWs = getAiWsService(agentId);
    aiWs.connect(agentId);
    // Bind this browser to the exec session (Agent Web parity) and pull stats.
    const askStats = () => {
      try {
        aiWs.watchSession(sessionId);
      } catch {
        try {
          aiWs.requestTokenStats(sessionId);
        } catch {
          /* optional */
        }
      }
    };
    askStats();
    // Re-request shortly after connect in case the first command raced ahead
    // of the agent WS being ready.
    const askAgain = window.setTimeout(askStats, 800);
    // Only re-watch while this exec pane is busy. Polls fan out token_stats
    // and re-render the chat tree after the agent has stopped; the agent-side
    // TTL cache (12s) makes 30s polling plenty — stats are recomputed at most
    // once per TTL per session and broadcast on every turn boundary anyway.
    const askInterval = window.setInterval(() => {
      if (!busyRef.current) return;
      askStats();
    }, 30000);

    const forThisSession = (msg: AIWSMessage): boolean => {
      const sid = msgSid(msg);
      if (sid) return sid === sessionIdRef.current;
      // Some frames omit sid during a parallel turn. While this exec pane is
      // already busy, accept them so the fold updates live; otherwise drop to
      // avoid stealing the primary chat's chatter.
      return busyRef.current;
    };

    const onWs = (type: string, handler: (msg: AIWSMessage) => void) =>
      aiWs.on(type, (msg: AIWSMessage) => {
        if (!forThisSession(msg)) return;
        handler(msg);
      });

    const unsubs: Array<() => void> = [];

    unsubs.push(
      onWs('thought', (msg) => {
        const text = extractContent(msg);
        if (!text) return;
        const raw = msg.content ?? msg.data;
        const isSubAgent =
          typeof raw === 'object' && raw !== null && !!(raw as any).sub_agent;
        const event: WorkflowEvent = {
          type: 'thought',
          content: text,
          timestamp: Date.now(),
          subAgent: isSubAgent || undefined,
          subTaskLabel:
            typeof raw === 'object' && raw !== null
              ? String((raw as any).sub_task_label || '')
              : '',
          jobId:
            typeof raw === 'object' && raw !== null && (raw as any).job_id
              ? String((raw as any).job_id)
              : undefined,
        };
        setBusy(true);
        setTimeline((prev) => appendWorkflowEvent(prev, event, 'Thinking...'));
      }),
    );

    unsubs.push(
      onWs('tool_call', (msg) => {
        const data = msg.content || msg.data;
        const toolName = typeof data === 'object' ? (data.name || data.tool || 'Tool') : 'Tool';
        const event: WorkflowEvent = {
          type: 'tool_call',
          content: data,
          timestamp: Date.now(),
          subAgent: typeof data === 'object' ? !!data.sub_agent : false,
          subTaskLabel: typeof data === 'object' ? (data.sub_task_label || '') : '',
          jobId: typeof data === 'object' && data?.job_id ? String(data.job_id) : undefined,
        };
        setBusy(true);
        setTimeline((prev) => appendWorkflowEvent(prev, event, `Calling ${toolName}...`));
      }),
    );

    unsubs.push(
      onWs('tool_call_delta', (msg) => {
        const data = msg.content || msg.data;
        if (!data || typeof data !== 'object') return;
        const toolName = data.name || data.tool || 'Tool';
        const event: WorkflowEvent = {
          type: 'tool_call',
          content: {
            id: data.id,
            index: data.index,
            name: toolName,
            arguments: data.arguments ?? data.args ?? '',
            args: data.arguments ?? data.args ?? '',
            partial: true,
          },
          timestamp: Date.now(),
          subAgent: !!data.sub_agent,
          subTaskLabel: data.sub_task_label || '',
        };
        setBusy(true);
        setTimeline((prev) => appendWorkflowEvent(prev, event, `Writing ${toolName}...`));
      }),
    );

    unsubs.push(
      onWs('tool_result', (msg) => {
        const data = msg.content || msg.data;
        const toolName = typeof data === 'object' ? (data.name || data.tool || 'Tool') : 'Tool';
        const event: WorkflowEvent = {
          type: 'tool_result',
          content: data,
          timestamp: Date.now(),
          subAgent: typeof data === 'object' ? !!data.sub_agent : false,
          subTaskLabel: typeof data === 'object' ? (data.sub_task_label || '') : '',
          jobId: typeof data === 'object' && data?.job_id ? String(data.job_id) : undefined,
        };
        setTimeline((prev) => appendWorkflowEvent(prev, event, `${toolName} completed`));
      }),
    );

    const handleFinal = (msg: AIWSMessage) => {
      if (finalizingRef.current) return;
      finalizingRef.current = true;
      const text = extractContent(msg);
      if (typeof text === 'string' && text.trim().length > 0) {
        const raw = msg as any;
        const messageId = raw.message_id || raw.id || undefined;
        const role = (raw.role === 'user' ? 'user' : 'assistant') as 'user' | 'assistant';
        const chatMsg: ChatMessage = {
          role,
          content: text,
          timestamp: new Date().toISOString(),
        };
        if (messageId) chatMsg.message_id = messageId;

        setTimeline((prev) => {
          // Dedup: skip if same assistant content already after last user msg
          for (let i = prev.length - 1; i >= 0; i -= 1) {
            const entry = prev[i];
            if (entry.kind === 'message' && (entry.data as ChatMessage).role === 'user') break;
            if (entry.kind === 'message') {
              const existing = entry.data as ChatMessage;
              if (
                existing.role === 'assistant'
                && (messageId
                  ? existing.message_id === messageId
                  : existing.content === chatMsg.content)
              ) {
                return foldTaskProcessSinceLastUser(
                  sealIncompleteWorkflows(prev, { nowMs: Date.now() }),
                );
              }
            }
          }
          // Optimistic user echo: if backend mirrors the same user text, skip
          if (role === 'user') {
            for (let i = prev.length - 1; i >= 0; i -= 1) {
              const entry = prev[i];
              if (entry.kind !== 'message') continue;
              const existing = entry.data as ChatMessage;
              if (existing.role === 'user' && existing.content === chatMsg.content) {
                return prev;
              }
              break;
            }
          }
          let next = finalizeWorkflowAndAddMessage(prev, chatMsg);
          return foldTaskProcessSinceLastUser(next);
        });
      } else {
        setTimeline((prev) => sealIncompleteWorkflows(prev, { nowMs: Date.now() }));
      }
      setBusy(false);
      setTimeout(() => {
        finalizingRef.current = false;
      }, 300);
    };

    unsubs.push(onWs('message', handleFinal));
    unsubs.push(onWs('response', handleFinal));

    unsubs.push(
      onWs('to_user_end_task', (msg) => {
        if (finalizingRef.current) return;
        finalizingRef.current = true;
        const text = extractContent(msg);
        if (typeof text === 'string' && text.trim().length > 0) {
          const raw = msg as any;
          const messageId = raw.message_id || raw.id || undefined;
          const chatMsg: ChatMessage = {
            role: 'assistant',
            content: text,
            timestamp: new Date().toISOString(),
            end_task: true,
          };
          if (messageId) chatMsg.message_id = messageId;
          setTimeline((prev) => {
            for (let i = prev.length - 1; i >= 0; i -= 1) {
              const entry = prev[i];
              if (entry.kind === 'message' && (entry.data as ChatMessage).role === 'user') break;
              if (entry.kind === 'message') {
                const existing = entry.data as ChatMessage;
                if (
                  existing.role === 'assistant'
                  && (messageId
                    ? existing.message_id === messageId
                    : existing.content === chatMsg.content)
                ) {
                  const patched = prev.map((e, idx) =>
                    idx === i && e.kind === 'message'
                      ? { ...e, data: { ...e.data, end_task: true } }
                      : e,
                  );
                  return foldTaskProcessSinceLastUser(
                    sealIncompleteWorkflows(patched, { nowMs: Date.now() }),
                  );
                }
              }
            }
            return foldTaskProcessSinceLastUser(
              finalizeWorkflowAndAddMessage(prev, chatMsg),
            );
          });
        } else {
          setTimeline((prev) =>
            foldTaskProcessSinceLastUser(
              sealIncompleteWorkflows(prev, { nowMs: Date.now() }),
            ),
          );
        }
        setBusy(false);
        setTimeout(() => {
          finalizingRef.current = false;
        }, 300);
      }),
    );

    unsubs.push(
      onWs('stream', () => {
        setBusy(true);
      }),
    );

    unsubs.push(
      onWs('turn_start', () => {
        setBusy(true);
      }),
    );

    unsubs.push(
      onWs('turn_elapsed', (msg) => {
        const data = (msg.content || msg.data || {}) as Record<string, unknown>;
        // Backend sends {started_ms, ended_ms}; some paths may send elapsed_ms.
        let elapsed = Number(data.elapsed_ms ?? data.elapsed ?? NaN);
        const started = Number(data.started_ms ?? NaN);
        const ended = Number(data.ended_ms ?? NaN);
        if (!(elapsed >= 0) && Number.isFinite(started) && Number.isFinite(ended)) {
          elapsed = Math.max(0, ended - started);
        }
        if (!(elapsed >= 0)) return;
        setTimeline((prev) => {
          const updated = [...prev];
          for (let i = updated.length - 1; i >= 0; i--) {
            if (updated[i].kind === 'workflow') {
              const wf = (updated[i] as Extract<TimelineEntry, { kind: 'workflow' }>).data;
              updated[i] = {
                ...updated[i],
                data: {
                  ...wf,
                  elapsed_ms: elapsed,
                  started_ms: Number.isFinite(started)
                    ? started
                    : (wf.started_ms ?? Date.now() - elapsed),
                  completed: true,
                  status: null,
                },
              } as TimelineEntry;
              break;
            }
          }
          return updated;
        });
        setBusy(false);
      }),
    );

    // Stop / idle without a final assistant message — freeze the timer.
    unsubs.push(
      aiWs.on('state', (msg: AIWSMessage) => {
        if (!forThisSession(msg) && msgSid(msg)) return;
        const data = msg.content ?? msg.data;
        const state = typeof data === 'string'
          ? data
          : (typeof data === 'object' && data ? String((data as any).state || '') : '');
        const lower = state.toLowerCase();
        if (
          lower === 'idle'
          || lower === 'ready'
          || lower.includes('task stopped')
          || lower.includes('stopped')
        ) {
          setTimeline((prev) => sealIncompleteWorkflows(prev, { nowMs: Date.now() }));
          setBusy(false);
        }
      }),
    );

    unsubs.push(
      onWs('plan', (msg) => {
        const data = msg.content || msg.data;
        const planContent = typeof data === 'object' ? (data.text || data.content || data) : data;
        // Keep plan as a workflow event (Solo renders plan steps from content)
        const event: WorkflowEvent = {
          type: 'plan',
          content: planContent,
          timestamp: Date.now(),
        };
        setTimeline((prev) => appendWorkflowEvent(prev, event, 'Planning...'));
      }),
    );

    unsubs.push(
      aiWs.on('token_stats', (msg: AIWSMessage) => {
        const data = unwrapTokenPayload(msg);
        if (!data) return;
        const sid = msgSid(msg) || String(data.session_id || '').trim();
        // Prefer sid-matched stats; if the agent omitted sid (legacy), accept
        // while this pane is open so the ring is never stuck empty.
        if (sid && sid !== sessionIdRef.current) return;
        setTokenStats(toSoloTokenStats(data));
      }),
    );

    // Fast disk catch-up: scheduled-task events may miss the browser WS; switching
    // tabs remounts and hydrates (user sees updates then). Keep merging from disk
    // while this pane is open so the fold stays live without tab switching.
    // Skip when sessionBridge owns live WS — competing rewrites break selection.
    if (!diskPoll) {
      return () => {
        unsubs.forEach((u) => {
          try {
            u();
          } catch {
            /* ignore */
          }
        });
        window.clearTimeout(askAgain);
        window.clearInterval(askInterval);
      };
    }

    const pullDisk = async () => {
      try {
        // Don't rewrite DOM while the user is selecting text in the page.
        const sel = window.getSelection();
        if (sel && !sel.isCollapsed) return;
        const resp = await agentSessionAPI.getSessionHistoryPaged(
          agentId,
          sessionId,
          0,
          SESSION_HISTORY_PAGE_SIZE,
        );
        const session = resp.session as
          | {
              messages?: any[];
              events?: any[];
              archived_messages?: any[];
              archived_events?: any[];
            }
          | undefined;
        const messages = session?.messages || [];
        const entries = buildTimelineFromSession(
          messages,
          session?.events || [],
          session?.archived_messages,
          session?.archived_events,
        );
        setTimeline((prev) => {
          if (timelineRichness(entries) <= timelineRichness(prev)) return prev;
          // Keep React keys stable so expand/collapse survives the merge.
          return rebaseTimelineUids(prev, entries);
        });
      } catch {
        /* ignore */
      }
    };
    void pullDisk();
    // Poll while the turn is live OR the parent forced catch-up (running exec).
    // WS stream/turn_* events are the real-time path; this disk pull is a
    // catch-up fallback, so 5s (not 3s) keeps the fan-out low.
    const pollId = window.setInterval(() => {
      if (!forceDiskPollRef.current && !busyRef.current) return;
      if (document.visibilityState !== 'visible') return;
      void pullDisk();
    }, 5000);

    return () => {
      unsubs.forEach((u) => {
        try {
          u();
        } catch {
          /* ignore */
        }
      });
      window.clearInterval(pollId);
      window.clearTimeout(askAgain);
      window.clearInterval(askInterval);
    };
  }, [agentId, sessionId, diskPoll]);

  return { timeline, tokenStats, appendOptimisticUser, sealOnStop, busy };
}
