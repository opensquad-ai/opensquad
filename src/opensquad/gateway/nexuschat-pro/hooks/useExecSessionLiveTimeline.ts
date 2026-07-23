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
  sealIncompleteWorkflows,
  type TimelineEntry,
  type WorkflowEvent,
} from '../utils/aiChatTimeline';
import {
  putCachedSessionTimeline,
  SESSION_HISTORY_PAGE_SIZE,
} from '../utils/sessionTimelineCache';
import type { ChatMessage } from '../components/ai-chat/MessageBubble';
import type { SoloTokenStats } from '../components/ai-chat/SoloContextFooter';

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
  return String(
    (msg as any).sid
    || (typeof msg.content === 'object' && msg.content && (msg.content as any).session_id)
    || (typeof msg.data === 'object' && msg.data && (msg.data as any).session_id)
    || '',
  ).trim();
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
): {
  timeline: TimelineEntry[];
  tokenStats: SoloTokenStats | null;
  appendOptimisticUser: (text: string) => void;
  busy: boolean;
} {
  const [timeline, setTimeline] = useState<TimelineEntry[]>([]);
  const [tokenStats, setTokenStats] = useState<SoloTokenStats | null>(null);
  const [busy, setBusy] = useState(false);
  const sessionIdRef = useRef(sessionId || '');
  const busyRef = useRef(false);
  const finalizingRef = useRef(false);

  useEffect(() => {
    sessionIdRef.current = sessionId || '';
  }, [sessionId]);

  useEffect(() => {
    busyRef.current = busy;
  }, [busy]);

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

  // Live WS for this session
  useEffect(() => {
    if (!agentId || !sessionId) return;

    const aiWs = getAiWsService(agentId);
    aiWs.connect(agentId);
    // Ask agent to push latest token ring for this session.
    try {
      aiWs.requestTokenStats();
    } catch {
      /* optional */
    }

    const forThisSession = (msg: AIWSMessage): boolean => {
      const sid = msgSid(msg);
      // Events without sid (rare agent-level) — ignore for exec pane to avoid
      // bleeding primary-session chatter into the scheduled-task timeline.
      if (!sid) return false;
      return sid === sessionIdRef.current;
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
        const elapsed = Number(data.elapsed_ms ?? data.elapsed ?? 0);
        if (!(elapsed > 0)) return;
        setTimeline((prev) => {
          const updated = [...prev];
          for (let i = updated.length - 1; i >= 0; i--) {
            if (updated[i].kind === 'workflow') {
              const wf = (updated[i] as Extract<TimelineEntry, { kind: 'workflow' }>).data;
              updated[i] = {
                ...updated[i],
                data: { ...wf, elapsed_ms: elapsed, started_ms: wf.started_ms ?? Date.now() - elapsed },
              } as TimelineEntry;
              break;
            }
          }
          return updated;
        });
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
        const data = msg.content || msg.data;
        if (!data || typeof data !== 'object') return;
        const sid = msgSid(msg);
        // Accept sid-matched stats, or agent-level (no sid) while this pane is open.
        if (sid && sid !== sessionIdRef.current) return;
        setTokenStats({
          used: Number((data as any).used) || 0,
          max: Number((data as any).max) || 0,
          breakdown: (data as any).breakdown,
          session: (data as any).session,
        });
      }),
    );

    // Safety net: slow disk refresh while still receiving live events can fill
    // gaps if a WS chunk was missed — but only when we have no in-flight busy
    // turn, to avoid clobbering optimistic / live interleaving.
    const pollId = window.setInterval(() => {
      if (busyRef.current) return;
      void (async () => {
        try {
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
            // Prefer live timeline when it already has at least as many entries.
            if (prev.length >= entries.length) return prev;
            return entries;
          });
        } catch {
          /* ignore */
        }
      })();
    }, 8000);

    return () => {
      unsubs.forEach((u) => {
        try {
          u();
        } catch {
          /* ignore */
        }
      });
      window.clearInterval(pollId);
    };
  }, [agentId, sessionId]);

  return { timeline, tokenStats, appendOptimisticUser, busy };
}
