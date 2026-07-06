/**
 * AIChatPage - Native AI Chat Component
 *
 * Replaces the legacy iframe-based approach with a native React component
 * that connects directly to Gateway's WebSocket at port 9555.
 *
 * Features:
 *   - Real-time streaming via Gateway WebSocket
 *   - Markdown rendering with code highlighting
 *   - Thought blocks, tool calls, plan display
 *   - Workflow containers (collapsible)
 *   - Token progress bar
 *   - Session management sidebar
 *   - Image upload
 *   - Status indicators
 *   - Unified timeline: messages and workflow events interleaved
 */
import React, { useState, useEffect, useLayoutEffect, useRef, useCallback, useMemo } from 'react';
import {
  Bot, ArrowLeft, Send, Square, Image as ImageIcon,
  PanelLeftOpen, PanelLeftClose, X, Paperclip, FileIcon, Upload,
  ChevronUp, ChevronDown, Lightbulb, List, Moon, Zap, Bell, ClipboardList, Gauge, Scissors,
  Loader2, Archive, ArchiveRestore, Clock, FolderOpen,
} from 'lucide-react';

import { useTranslation } from 'react-i18next';
import { getAiWsService, releaseAiWsService, AIWSMessage, AIWebSocketStatus } from '../services/aiWebSocket';
import { agentSessionAPI, authAPI, adminAPI, AdminAgent, modelCardAPI, ModelCardInfo, SERVER_BASE_URL } from '../services/api';

// genUID() fallback for non-HTTPS / non-localhost environments
const genUID = (): string => {
  try {
    return genUID();
  } catch {
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
      const r = Math.random() * 16 | 0;
      return (c === 'x' ? r : (r & 0x3 | 0x8)).toString(16);
    });
  }
};

// AI Chat sub-components
import { MessageBubble, ChatMessage, FileAttachment } from './ai-chat/MessageBubble';
import { StreamingMessage } from './ai-chat/StreamingMessage';
import { WorkflowContainer } from './ai-chat/WorkflowContainer';
import { ThoughtBlock } from './ai-chat/ThoughtBlock';
import { ToolCallBlock } from './ai-chat/ToolCallBlock';
import { PlanBlock, PlanStep, parsePlanContent } from './ai-chat/PlanBlock';
import { StatusBadge, AgentStatus } from './ai-chat/StatusBadge';
import { TokenProgressBar } from './ai-chat/TokenProgressBar';
import { SessionSidebar } from './ai-chat/SessionSidebar';
import { ContextViewer, ContextEntry } from './ai-chat/ContextViewer';

interface AIChatPageProps {
  agentId: string;
  onBack: () => void;
  /** The currently logged-in user (for avatar/name in user bubbles). */
  currentUser?: { id: string; name: string; avatar?: string | null } | null;
}

// ---- Uploaded file info ----
interface UploadedFile {
  path: string;
  filename: string;
  original_name: string;
  url: string;
  size: number;
  content_type: string;
  is_image: boolean;
  is_audio?: boolean;
  is_video?: boolean;
}

// ---- Workflow event types ----
interface WorkflowEvent {
  _uid?: string; // Stable internal key
  type: 'thought' | 'tool_call' | 'tool_result' | 'info' | 'plan' | 'summary_stream' | 'compression_progress';
  content: any;
  timestamp: number;
  // For merged tool events: tool_result is merged INTO the tool_call entry
  result?: any;
  resultStatus?: 'success' | 'error';
  /** True when this event was emitted by a sub-agent (delegate_task). */
  subAgent?: boolean;
  /** Short label describing the sub-agent task. */
  subTaskLabel?: string;
}

// ---- Timeline entry types ----
interface WorkflowBlock {
  events: WorkflowEvent[];
  status: string | null;
  completed: boolean;
  /** Backend start timestamp (epoch ms) from turn_start event */
  started_ms?: number;
  /** Final elapsed time in ms (= ended_ms - started_ms) from turn_elapsed event */
  elapsed_ms?: number;
}

type TimelineEntry =
  | { kind: 'message'; data: ChatMessage; _uid: string }
  | { kind: 'workflow'; data: WorkflowBlock; _uid: string }
  | { kind: 'prompt'; data: { system_prompt: string; dynamic_prefix: string; changed: boolean; timestamp: string; diff?: string[] }; _uid: string }
  | { kind: 'status_hint'; data: { hintType: 'sleep' | 'wake' | 'state'; content: string | number; timestamp: number }; _uid: string }
  | { kind: 'archived_section'; data: {
      messageCount: number;
      eventCount: number;
      entries: TimelineEntry[];
      /** Pre-computed sorted timestamp range of archived content (for display). */
      startTs?: string;
      endTs?: string;
    }; _uid: string };

// ---- Agent Working Indicator (shown when workflow is hidden and agent is active) ----
const AgentWorkingIndicator: React.FC<{ agentProfile: AdminAgent | null; startedMs?: number }> = ({ agentProfile, startedMs }) => {
  const [dotCount, setDotCount] = React.useState(1);
  const [liveElapsed, setLiveElapsed] = React.useState(0);

  React.useEffect(() => {
    const t = setInterval(() => setDotCount(p => (p % 4) + 1), 450);
    return () => clearInterval(t);
  }, []);

  React.useEffect(() => {
    if (startedMs === undefined) { setLiveElapsed(0); return; }
    setLiveElapsed(Date.now() - startedMs);
    const t = setInterval(() => setLiveElapsed(Date.now() - startedMs), 100);
    return () => clearInterval(t);
  }, [startedMs]);

  const avatarSrc = agentProfile?.chat_profile?.chat_user_avatar;
  const resolvedAvatar = avatarSrc
    ? (avatarSrc.startsWith('http') ? avatarSrc : `${SERVER_BASE_URL}${avatarSrc.startsWith('/') ? avatarSrc : '/' + avatarSrc}`)
    : null;

  return (
    <div className="flex items-center gap-2 mb-4 pl-0.5">
      {/* Agent avatar */}
      <div className="w-7 h-7 rounded-full flex items-center justify-center flex-shrink-0 overflow-hidden bg-emerald-500/20">
        {resolvedAvatar
          ? <img src={resolvedAvatar} alt="" className="w-full h-full object-cover" loading="lazy" />
          : <Bot size={14} className="text-emerald-500" />
        }
      </div>

      {/* Spinner + time below it */}
      <div className="flex flex-col items-center gap-0.5">
        <svg width="26" height="26" viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg">
          <defs>
            <linearGradient id="osq-wk-arc" x1="0" y1="0" x2="32" y2="32" gradientUnits="userSpaceOnUse">
              <stop stopColor="#818cf8" />
              <stop offset="0.6" stopColor="#a78bfa" stopOpacity="0.5" />
              <stop offset="1" stopColor="#818cf8" stopOpacity="0" />
            </linearGradient>
          </defs>
          <g style={{ transformOrigin: '16px 16px', animation: 'osqSpin 1.4s linear infinite' }}>
            <circle cx="16" cy="16" r="13.5" stroke="url(#osq-wk-arc)" strokeWidth="2.5" strokeLinecap="round" strokeDasharray="53 33" />
          </g>
          <line x1="10" y1="10" x2="22" y2="10" stroke="#818cf8" strokeWidth="1" strokeOpacity="0.35" />
          <line x1="22" y1="10" x2="22" y2="22" stroke="#818cf8" strokeWidth="1" strokeOpacity="0.35" />
          <line x1="22" y1="22" x2="10" y2="22" stroke="#818cf8" strokeWidth="1" strokeOpacity="0.35" />
          <line x1="10" y1="22" x2="10" y2="10" stroke="#818cf8" strokeWidth="1" strokeOpacity="0.35" />
          <line x1="10" y1="10" x2="22" y2="22" stroke="#818cf8" strokeWidth="0.8" strokeOpacity="0.18" />
          <line x1="22" y1="10" x2="10" y2="22" stroke="#818cf8" strokeWidth="0.8" strokeOpacity="0.18" />
          <circle cx="10" cy="10" r="2.5" fill="#818cf8" style={{ animation: 'osqPulse 1.6s ease-in-out infinite' }} />
          <circle cx="22" cy="10" r="2.5" fill="#a78bfa" style={{ animation: 'osqPulse 1.6s ease-in-out 0.4s infinite' }} />
          <circle cx="22" cy="22" r="2.5" fill="#818cf8" style={{ animation: 'osqPulse 1.6s ease-in-out 0.8s infinite' }} />
          <circle cx="10" cy="22" r="2.5" fill="#a78bfa" style={{ animation: 'osqPulse 1.6s ease-in-out 1.2s infinite' }} />
        </svg>
        {startedMs !== undefined && (
          <span className="text-[10px] text-textMuted font-mono leading-none">{(liveElapsed / 1000).toFixed(1)}s</span>
        )}
      </div>
      <style>{`
        @keyframes osqSpin { to { transform: rotate(360deg); } }
        @keyframes osqPulse { 0%,100% { opacity:1; } 50% { opacity:0.25; } }
      `}</style>

      {/* Animated dots */}
      <span className="text-xs text-textMuted font-mono">{'.'.repeat(dotCount)}</span>
    </div>
  );
};

// ---- Workflow Block with event pagination ----
const WORKFLOW_EVENTS_PAGE_SIZE = 10;

const WorkflowBlockView: React.FC<{ block: WorkflowBlock; blockKey: number; turnStartedMs?: number }> = ({ block, blockKey, turnStartedMs }) => {
  const totalEvents = block.events.length;
  const [visibleCount, setVisibleCount] = useState(() =>
    totalEvents <= WORKFLOW_EVENTS_PAGE_SIZE ? totalEvents : WORKFLOW_EVENTS_PAGE_SIZE
  );

  // While the workflow is active, auto-grow visibleCount to always show all events.
  // This prevents the sliding-window effect where new events push user-expanded
  // tool calls out of the visible range.
  // For completed workflows, the count is frozen so the user can use "Show more".
  useEffect(() => {
    if (!block.completed) {
      // Active: show everything — no events ever get hidden during live work
      setVisibleCount(totalEvents);
    } else if (visibleCount > totalEvents) {
      // Completed and somehow over total (shouldn't happen, but guard it)
      setVisibleCount(totalEvents);
    }
    // Completed + visibleCount <= totalEvents: leave as-is (user controls via "Show more")
  }, [totalEvents, block.completed]);

  // For active (non-completed) workflows, always show ALL events during render.
  // This prevents a one-frame flash where useEffect hasn't yet updated visibleCount,
  // causing expanded tool calls to be sliced out of the DOM and losing the
  // data-tool-expanded attribute that freezes auto-scroll.
  const effectiveCount = block.completed ? visibleCount : totalEvents;
  const hiddenCount = Math.max(0, totalEvents - effectiveCount);
  const visibleEvents = hiddenCount <= 0
    ? block.events
    : block.events.slice(totalEvents - effectiveCount);

  const handleShowMore = () => {
    setVisibleCount(prev => Math.min(prev + WORKFLOW_EVENTS_PAGE_SIZE, totalEvents));
  };

  // Determine the status label to pass to WorkflowContainer
  const displayStatus = block.completed ? undefined : (block.status || undefined);

  return (
    <WorkflowContainer
      status={displayStatus}
      defaultOpen={!block.completed}
      startedMs={block.completed ? undefined : turnStartedMs}
      finalElapsedMs={block.elapsed_ms}
    >
      {hiddenCount > 0 && (
        <button
          onClick={handleShowMore}
          className="w-full text-center py-1.5 text-xs text-primary hover:text-primary/80 hover:bg-primary/5 rounded transition-colors"
        >
          Show {Math.min(WORKFLOW_EVENTS_PAGE_SIZE, hiddenCount)} more events ({hiddenCount} hidden)
        </button>
      )}
      {visibleEvents.map((evt, i) => {
        // Use pre-assigned _uid for stable key during window shifts
        const eventKey = evt._uid || `${evt.type}-${evt.timestamp}-${i}`;
        
        if (evt.type === 'thought') {
          return (
            <ThoughtBlock
              key={eventKey}
              content={typeof evt.content === 'string' ? evt.content : JSON.stringify(evt.content)}
              defaultOpen
            />
          );
        }
        if (evt.type === 'tool_call') {
          const data = typeof evt.content === 'object' ? evt.content : {};
          const status = evt.result ? (evt.resultStatus === 'error' ? 'error' : 'success') : 'running';
          return (
            <ToolCallBlock
              key={eventKey}
              persistKey={eventKey}
              toolName={data.name || data.tool || 'Tool'}
              args={data.arguments || data.args || data.input}
              result={evt.result}
              status={status}
              subAgent={evt.subAgent}
              subTaskLabel={evt.subTaskLabel}
            />
          );
        }
        if (evt.type === 'tool_result') {
          const data = typeof evt.content === 'object' ? evt.content : {};
          return (
            <ToolCallBlock
              key={eventKey}
              persistKey={eventKey}
              toolName={data.name || data.tool || 'Tool'}
              result={typeof data.result === 'string' ? data.result : (data.output || JSON.stringify(data))}
              status={data.error ? 'error' : 'success'}
              subAgent={evt.subAgent}
              subTaskLabel={evt.subTaskLabel}
            />
          );
        }
        if (evt.type === 'info') {
          const isSubInfo = !!evt.subAgent;
          const infoObj = typeof evt.content === 'object' && evt.content !== null ? evt.content as any : null;

          // --- Task Supervisor check-in: special badge rendering ---
          if (infoObj?.event === 'task_supervisor_checkin') {
            const stall = infoObj.stall_count || 0;
            const elapsed = Math.round(infoObj.elapsed_seconds || 0);
            const sinceActivity = Math.round(infoObj.since_last_activity || 0);
            const urgency = stall <= 2 ? 'reminder' : stall <= 4 ? 'warning' : 'urgent';
            const urgencyColor = urgency === 'urgent' ? 'text-red-400 border-red-500/40 bg-red-500/5'
              : urgency === 'warning' ? 'text-amber-400 border-amber-500/40 bg-amber-500/5'
              : 'text-sky-400 border-sky-500/40 bg-sky-500/5';
            const urgencyLabel = urgency === 'urgent' ? 'URGENT' : urgency === 'warning' ? 'WARNING' : 'REMINDER';
            return (
              <div key={eventKey} className={`flex flex-col gap-1 px-2 py-1.5 rounded-md border ${urgencyColor}`}>
                <div className="flex items-center gap-1.5 flex-wrap">
                  <span className="text-[10px] font-bold leading-none">&#x23F0;</span>
                  <span className={`text-[10px] font-semibold leading-none`}>TASK SUPERVISOR · {urgencyLabel}</span>
                  <span className="text-[9px] text-textMuted ml-auto">stall {stall} · {elapsed}s elapsed · idle {sinceActivity}s</span>
                </div>
                {infoObj.text && (
                  <details className="rounded bg-bgDark border border-border/50">
                    <summary className="cursor-pointer px-2 py-0.5 text-[10px] text-textMuted">View check-in message</summary>
                    <pre className="text-[10px] leading-5 whitespace-pre-wrap break-words p-2 text-textMain max-h-[200px] overflow-auto">
{infoObj.text}
                    </pre>
                  </details>
                )}
              </div>
            );
          }

          const summaryText = infoObj?.event === 'context_summary_generated' && typeof infoObj?.summary === 'string'
            ? infoObj.summary
            : null;
          // Build a meaningful label from the info content
          const infoLabel = (() => {
            if (summaryText) return 'Context summary generated';
            if (typeof evt.content === 'string') return evt.content;
            if (!infoObj) return 'Info';
            // Structured events: use event name as category + text as description
            const evtName = infoObj.event;
            const evtText = infoObj.text;
            if (evtName === 'model_card_switched') return `Model switched to ${infoObj.card || infoObj.model || 'new model'}`;
            if (evtName === 'context_compressed') return evtText || 'Context compressed';
            if (evtName === 'context_compress_skipped') return evtText || 'Context compression skipped';
            if (evtName === 'incoming_messages') return `Received ${infoObj.count || ''} message(s) from ${infoObj.source || 'external'}`;
            if (evtName === 'prompt_updated') return evtText || 'Prompt updated';
            // Fallback: use text field, or event name, or stringify
            if (evtText) return String(evtText).slice(0, 200);
            if (evtName) return String(evtName).replace(/_/g, ' ');
            return 'Info';
          })();
          // Determine if there's extra detail worth showing in a collapsible
          const infoDetail = (() => {
            if (summaryText) return summaryText;
            if (!infoObj) return null;
            // For incoming_messages, show the message list
            if (infoObj.event === 'incoming_messages' && Array.isArray(infoObj.messages) && infoObj.messages.length > 0) {
              return infoObj.messages.map((m: any) =>
                `[${m.sender_name || m.source_name || 'unknown'}] ${m.content || ''}`
              ).join('\n');
            }
            // For model switch, show full details
            if (infoObj.event === 'model_card_switched') {
              return `Card: ${infoObj.card || '-'}\nModel: ${infoObj.model || '-'}\nProtocol: ${infoObj.api_protocol || '-'}`;
            }
            // Generic: if there's a text field different from the label, show it
            if (infoObj.text && String(infoObj.text).length > 60) return String(infoObj.text);
            return null;
          })();
          return (
            <div
              key={eventKey}
              className={`flex flex-col gap-1.5 px-2 py-1 rounded-md ${
                isSubInfo
                  ? 'bg-violet-500/5 border border-violet-500/20'
                  : 'bg-blue-500/5 border border-blue-500/20'
              }`}
            >
              <div className="flex items-center gap-1.5">
                {isSubInfo
                  ? <span className="text-[9px] text-violet-400 font-semibold leading-none flex-shrink-0">&#x21B3;&nbsp;Sub</span>
                  : <span className="text-[11px]">&#x2139;&#xFE0F;</span>
                }
                <span className={`text-[11px] ${isSubInfo ? 'text-violet-400' : 'text-blue-400'}`}>
                  {infoLabel}
                </span>
              </div>
              {infoDetail && (
                <details className="rounded bg-bgLight border border-border">
                  <summary className="cursor-pointer px-2 py-1 text-[11px] text-textMuted">View details</summary>
                  <pre className="text-[11px] leading-5 whitespace-pre-wrap break-words p-2 text-textMain max-h-[320px] overflow-auto">
{infoDetail}
                  </pre>
                </details>
              )}
            </div>
          );
        }
        if (evt.type === 'summary_stream') {
          const data = typeof evt.content === 'object' && evt.content !== null ? evt.content as any : {};
          const text = typeof data.text === 'string' ? data.text : '';
          const done = !!data.done;
          const pending = !!data.pending;
          const statusLabel = done
            ? (text ? 'Context summary completed' : 'Context compression completed (no summary generated)')
            : (pending ? 'Waiting for context compression...' : 'Generating context summary...');
          // When done, show the summary text in a collapsible <details> block
          // so the user can always review the compressed content.
          const bodyText = pending ? '' : (text || 'Summarizing...');
          return (
            <div key={eventKey} className={`flex flex-col gap-1.5 px-2 py-1 rounded-md ${done ? 'bg-emerald-500/5 border border-emerald-500/20' : 'bg-indigo-500/5 border border-indigo-500/20'}`}>
              <div className="flex items-center gap-1.5">
                <span className="text-[11px]">{done ? '\u2705' : '\u2699\uFE0F'}</span>
                <span className={`text-[11px] ${done ? 'text-emerald-400' : 'text-indigo-400'}`}>{statusLabel}</span>
              </div>
              {bodyText && (
                done ? (
                  <details className="rounded bg-bgLight border border-border mt-1">
                    <summary className="cursor-pointer px-2 py-1 text-[11px] text-textMuted">View summary</summary>
                    <pre className="text-[11px] leading-5 whitespace-pre-wrap break-words p-2 text-textMain max-h-[320px] overflow-auto">
{bodyText}
                    </pre>
                  </details>
                ) : (
                  <pre className="text-[11px] leading-5 whitespace-pre-wrap break-words p-2 text-textMain max-h-[320px] overflow-auto rounded bg-bgLight border border-border">
{bodyText}
                  </pre>
                )
              )}
            </div>
          );
        }
        if (evt.type === 'plan') {
          const steps = Array.isArray(evt.content) ? evt.content : parsePlanContent(evt.content);
          return steps.length > 0 ? <PlanBlock key={eventKey} steps={steps} /> : null;
        }
        return null;
      })}
    </WorkflowContainer>
  );
};

// ---- Archived Section ----
// Renders a collapsible separator with a count of messages/events that
// were removed from the LLM context by context compression but kept on
// disk for UI display. When expanded, the original messages and
// workflow events are rendered inline in chronological order.
const ArchivedSection: React.FC<{
  data: {
    messageCount: number;
    eventCount: number;
    entries: TimelineEntry[];
    startTs?: string;
    endTs?: string;
  };
  currentUser: { id?: string; name?: string; avatar?: string | null } | null | undefined;
  agentProfile: AdminAgent | null;
}> = ({ data, currentUser, agentProfile }) => {
  const { t } = useTranslation();
  const [isOpen, setIsOpen] = useState(false);

  const totalCount = data.messageCount + data.eventCount;
  if (totalCount === 0) return null;

  const tsLabel = (ts?: string) => {
    if (!ts) return '';
    try {
      const d = new Date(ts);
      if (Number.isNaN(d.getTime())) return '';
      return d.toLocaleString();
    } catch {
      return '';
    }
  };

  return (
    <div className="my-3 mx-2 sm:mx-9" data-testid="archived-section">
      <button
        type="button"
        onClick={() => setIsOpen((o) => !o)}
        className="w-full flex items-center gap-2 py-1.5 text-textMuted/60 hover:text-textMuted/90 transition-colors group"
        aria-expanded={isOpen}
      >
        <div className="flex-1 h-px bg-border/30" />
        {isOpen
          ? <ArchiveRestore size={12} className="shrink-0 text-textMuted/60" />
          : <Archive size={12} className="shrink-0 text-textMuted/60" />
        }
        <span className="text-[10px] font-mono shrink-0">
          {isOpen
            ? t('aiChat.archivedCollapse')
            : t('aiChat.archivedSection', { count: totalCount })}
        </span>
        <span className="text-[10px] font-mono shrink-0 text-textMuted/40">
          ({data.messageCount}m / {data.eventCount}e)
        </span>
        <div className="flex-1 h-px bg-border/30" />
      </button>
      {isOpen && (
        <div className="mt-2 space-y-1 opacity-80">
          {data.startTs && data.endTs && (
            <div className="text-[10px] text-textMuted/45 font-mono text-center py-1">
              {tsLabel(data.startTs)} — {tsLabel(data.endTs)}
            </div>
          )}
          {data.entries.map((entry, i) => {
            const entryKey = entry._uid || `archived-entry-${i}`;
            if (entry.kind === 'message') {
              return (
                <MessageBubble
                  key={entryKey}
                  message={entry.data}
                  senderName={
                    entry.data.role === 'user'
                      ? (currentUser?.name || undefined)
                      : (agentProfile?.agent_name || undefined)
                  }
                  senderAvatar={
                    entry.data.role === 'user'
                      ? (currentUser?.avatar || null)
                      : (agentProfile?.chat_profile?.chat_user_avatar || null)
                  }
                />
              );
            }
            if (entry.kind === 'workflow') {
              return (
                <WorkflowBlockView
                  key={entryKey}
                  block={entry.data}
                  blockKey={i}
                />
              );
            }
            // status_hint / prompt / nested archived_section — skip
            return null;
          })}
        </div>
      )}
    </div>
  );
};

export const AIChatPage: React.FC<AIChatPageProps> = ({ agentId, onBack, currentUser }) => {
  const { t } = useTranslation();
  // ---- State ----
  const [timeline, setTimeline] = useState<TimelineEntry[]>([]);
  const [streamingText, setStreamingText] = useState('');
  const [isStreaming, setIsStreaming] = useState(false);
  const summaryStreamCacheRef = useRef<Record<string, string>>({});
  const SUMMARY_STREAM_DEBUG = true;
  const [wsStatus, setWsStatus] = useState<AIWebSocketStatus>('disconnected');
  const [agentStatus, setAgentStatus] = useState<AgentStatus>('disconnected');
  const [inputText, setInputText] = useState('');
  const [images, setImages] = useState<string[]>([]);
  const [attachments, setAttachments] = useState<UploadedFile[]>([]);
  const [isDragOver, setIsDragOver] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  // Token stats
  const [tokenStats, setTokenStats] = useState<{
    used: number; max: number;
    breakdown?: { user: number; thought: number; tool: number; tool_defs?: number; response: number };
    session?: any;   // 本会话统计（后端已重置，直接使用）
    cumulative?: any; // 全量累计统计（本会话 + 历史）
  } | null>(null);
  // Ref that always reflects the latest tokenStats (to read in effects without stale closure)
  const tokenStatsRef = useRef(tokenStats);
  useEffect(() => { tokenStatsRef.current = tokenStats; }, [tokenStats]);

  // Session management
  const [currentSessionId, setCurrentSessionId] = useState<string | null>(null);
  useEffect(() => {
    currentSessionIdRef.current = currentSessionId;
    // Sync the active session filter on the WebSocket service so that
    // session-scoped streaming events from a different session are dropped
    // before reaching any handler (fixes cross-session bleed bug).
    wsServiceRef.current?.setActiveSession(currentSessionId);
  }, [currentSessionId]);
  const [viewingHistorySession, setViewingHistorySession] = useState(false); // true when viewing a non-current session
  const viewingHistorySessionRef = useRef(false);
  useEffect(() => {
    viewingHistorySessionRef.current = viewingHistorySession;
  }, [viewingHistorySession]);
  const [sessionTitleUpdate, setSessionTitleUpdate] = useState<{ id: string; title: string } | null>(null);
  const [sessionSidebarOpen, setSessionSidebarOpen] = useState(false);

  // Plan
  const [planSteps, setPlanSteps] = useState<PlanStep[]>([]);

  // Backend start timestamp for the current workflow turn (epoch ms from turn_start)
  const [turnStartedMs, setTurnStartedMs] = useState<number | undefined>(undefined);

  // Refs
  const chatEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const streamingTextRef = useRef('');   // mirror of streamingText for WS callbacks
  const finalizingRef = useRef(false);   // guard against duplicate finalization
  const diskSessionLoadedRef = useRef(false); // true after we loaded disk session (skip bare WS history)
  const dragCounterRef = useRef(0);      // counter for nested drag enter/leave events
  const messagesContainerRef = useRef<HTMLDivElement>(null); // messages scroll container
  const prevOuterScrollHeightRef = useRef(0); // for smart auto-scroll
  const pendingFilePushesRef = useRef<ChatMessage[]>([]);
  const pendingHydrationMediaRef = useRef<ChatMessage[]>([]); // media history received while hydrating
  const filePushDedupRef = useRef<Map<string, number>>(new Map());
  const isHydratingSessionRef = useRef(false); // true while restoring current session after refresh
  const currentSessionIdRef = useRef<string | null>(null);
  const sessionBootstrapDoneRef = useRef(false); // true after first canonical timeline set on connect
  const sessionReloadSeqRef = useRef(0);
  /** Separate from sessionReloadSeqRef so connected/hydrate cannot invalidate New Session timers. */
  const newSessionFallbackSeqRef = useRef(0);
  const sessionReloadTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

   // Auth expiry
  const [sessionExpired, setSessionExpired] = useState(false);

  // Agent chat profile (avatar + name)
  const [agentProfile, setAgentProfile] = useState<AdminAgent | null>(null);
  const [modelName, setModelName] = useState<string | null>(null);
  const [agentApiProtocol, setAgentApiProtocol] = useState<string | null>(null);
  const [agentProvider, setAgentProvider] = useState<string | null>(null);
  // Runtime model-switch dropdown: available cards + in-flight switch flag.
  const [modelCards, setModelCards] = useState<ModelCardInfo[]>([]);
  const [switchingModel, setSwitchingModel] = useState(false);
  // The currently-active card name (config.json model._card). Used to pinpoint
  // the selected <option> even when two cards from different vendors share the
  // same model_name (model_name alone is not a unique identity).
  const [currentCardName, setCurrentCardName] = useState<string | null>(null);
  const [agentCwd, setAgentCwd] = useState<string | null>(null);
  const [showContextViewer, setShowContextViewer] = useState(false);
  const [showPlanViewer, setShowPlanViewer] = useState<boolean>(() => {
    try {
      return localStorage.getItem('ai_chat_show_plan_viewer') === 'true';
    } catch {
      return false;
    }
  });
  const [showTokenStats, setShowTokenStats] = useState<boolean>(() => {
    try {
      const stored = localStorage.getItem('ai_chat_show_token_stats');
      // Backward-compatible default: visible when no setting exists.
      return stored === null ? true : stored === 'true';
    } catch {
      return true;
    }
  });
  const [isCompressingContext, setIsCompressingContext] = useState(false);

  // Lazy loading state
  const [hasMoreHistory, setHasMoreHistory] = useState(false);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const historyOffsetRef = useRef(0);        // how many messages already loaded (from the end)
  const loadingSessionIdRef = useRef<string | null>(null); // session being lazily loaded

  // Session loading state (加载/创建会话中)
  const [isLoadingSession, setIsLoadingSession] = useState(false);
  const [sessionLoadingLabel, setSessionLoadingLabel] = useState('');
  const newSessionPendingRef = useRef(false); // true after handleNewSession fires, cleared on next connected

  // Scroll button visibility
  const [showScrollTop, setShowScrollTop] = useState(false);
  const [showScrollBottom, setShowScrollBottom] = useState(false);
  const [scrollActive, setScrollActive] = useState(false);
  const scrollHideTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const userScrolledRef = useRef(false); // true when user manually scrolled away from bottom
  // Ref to the current per-agent WS service (set inside the WS useEffect, used by callbacks)
  const wsServiceRef = useRef<ReturnType<typeof getAiWsService> | null>(null);

  // ---- Pending message queue ----
  // When the agent is busy (working/thinking), messages the user sends are not
  // delivered immediately. Instead they land here and are auto-sent once the
  // agent returns to idle. Each pending message also offers a "Send now" button
  // that delivers it right away (the backend input_hub already queues working-
  // state messages and injects them on the next turn via event_pipeline).
  interface PendingMessage {
    id: string;
    text: string;
    images: string[];
    attachments: UploadedFile[];
    fileAtts: FileAttachment[];
  }
  const [pendingMessages, setPendingMessages] = useState<PendingMessage[]>([]);
  // Collapse the queue list into a single line. Useful when the user has
  // parked many messages and wants a more compact view.
  const [pendingCollapsed, setPendingCollapsed] = useState(false);
  // Ref mirror so the idle auto-send effect can read the latest queue without
  // a stale closure, and a guard to prevent re-entrancy while flushing.
  const pendingMessagesRef = useRef<PendingMessage[]>([]);
  const isFlushingPendingRef = useRef(false);
  useEffect(() => { pendingMessagesRef.current = pendingMessages; }, [pendingMessages]);

  // Workflow visibility toggle (persisted to localStorage, default: visible so users can see thought content)
  const [showWorkflow, setShowWorkflow] = useState<boolean>(() => {
    try {
      const stored = localStorage.getItem('ai_chat_show_workflow');
      return stored === 'true';
    } catch { return true; }
  });

  const toggleWorkflow = useCallback(() => {
    setShowWorkflow(prev => {
      const next = !prev;
      try { localStorage.setItem('ai_chat_show_workflow', String(next)); } catch {}
      return next;
    });
  }, []);

  // Derived: is there an active (incomplete) workflow in the timeline?
  // A workflow is considered "active" only if it has unresolved tool_calls
  // (agent is currently processing) OR has recent event activity (< 15s).
  // This prevents the agent working animation from staying on indefinitely
  // when the agent is idling (e.g. system.wait with all tools resolved).
  const hasActiveWorkflow = useMemo(() => {
    const now = Date.now();
    for (let i = timeline.length - 1; i >= 0; i--) {
      const e = timeline[i];
      if (e.kind !== 'workflow') continue;
      const wf = (e as { kind: 'workflow'; data: WorkflowBlock }).data;
      if (wf.completed) continue;

      // Unresolved tool_call → agent is actively waiting for a result
      const hasOpenToolCall = wf.events.some(evt => evt.type === 'tool_call' && !evt.result);
      if (hasOpenToolCall) return true;

      // No open tool_calls — check if the last event is recent enough
      // to consider the workflow still active (within 15 seconds).
      // This covers the case where the agent is thinking (thought events
      // without tool_calls) or has just finished a tool.
      const lastTs = wf.events.reduce((max, evt) =>
        evt.timestamp && evt.timestamp > max ? evt.timestamp : max, 0
      );
      if (lastTs > 0 && (now - lastTs < 15000)) return true;
    }
    return false;
  }, [timeline]);

  const latestPlanStepsFromTimeline = useMemo<PlanStep[]>(() => {
    for (let i = timeline.length - 1; i >= 0; i--) {
      const entry = timeline[i];
      if (entry.kind !== 'workflow') continue;
      const events = entry.data?.events || [];
      for (let j = events.length - 1; j >= 0; j--) {
        const evt = events[j] as any;
        if (evt?.type !== 'plan') continue;
        const c = evt.content;
        if (Array.isArray(c) && c.length > 0) {
          if (typeof c[0] === 'object') return c as PlanStep[];
          if (typeof c[0] === 'string') return parsePlanContent((c as string[]).join('\n'));
        }
        if (typeof c === 'string') {
          const parsed = parsePlanContent(c);
          if (parsed.length > 0) return parsed;
        }
      }
    }
    return [];
  }, [timeline]);

  const effectivePlanSteps = planSteps.length > 0 ? planSteps : latestPlanStepsFromTimeline;

  // ---- contextEntries: flatten timeline → ContextEntry[] for ContextViewer ----
  const contextEntries = useMemo((): ContextEntry[] => {
    const result: ContextEntry[] = [];
    let msgIdx = 0, evtIdx = 0, promptIdx = 0;
    for (const entry of timeline) {
      if (entry.kind === 'message') {
        result.push({
          id: `${currentSessionId || 'local'}-msg-${msgIdx++}`,
          kind: entry.data.role as 'user' | 'assistant',
          content: entry.data.content,
          timestamp: entry.data.timestamp,
          elapsed_ms: (entry.data as any).elapsed_ms,
        });
      } else if (entry.kind === 'workflow') {
        for (const evt of entry.data.events) {
          result.push({
            id: `${currentSessionId || 'local'}-evt-${evtIdx++}`,
            kind: evt.type as ContextEntry['kind'],
            content: evt.content,
            timestamp: typeof evt.timestamp === 'number'
              ? new Date(evt.timestamp).toISOString()
              : (evt.timestamp ? String(evt.timestamp) : undefined),
            result: evt.result,
            resultStatus: evt.resultStatus,
          });
        }
      } else if (entry.kind === 'prompt') {
        result.push({
          id: `${currentSessionId || 'local'}-prompt-${promptIdx++}`,
          kind: 'prompt',
          content: entry.data.system_prompt,
          dynamicPrefix: entry.data.dynamic_prefix || undefined,
          promptChanged: entry.data.changed,
          timestamp: entry.data.timestamp,
          diff: entry.data.diff,
        });
      } else if (entry.kind === 'status_hint') {
        const hint = entry.data;
        const hintKind: ContextEntry['kind'] =
          hint.hintType === 'sleep' ? 'sleep' :
          hint.hintType === 'wake'  ? 'wake'  : 'state_change';
        const label =
          hint.hintType === 'sleep' ? t('aiChat.sleepMode', { seconds: hint.content }) :
          hint.hintType === 'wake'  ? t('aiChat.wakeMode', { content: hint.content }) :
          t('aiChat.stateChanged', { content: hint.content });
        result.push({
          id: `${currentSessionId || 'local'}-hint-${evtIdx++}`,
          kind: hintKind,
          content: label,
          timestamp: new Date(hint.timestamp).toISOString(),
        });
      }
    }
    return result;
  }, [timeline, currentSessionId]);

  // ---- Fetch agent profile (avatar + name) + model name ----
  useEffect(() => {
    if (!agentId) return;
    adminAPI.getAgents()
      .then(res => {
        const found = res.agents.find(a => a.agent_id === agentId || a.dir_name === agentId);
        if (!found) return;
        setAgentProfile(found);
        // 用 agentProfile 中的 token_stats 作为 WebSocket 前的初始值
        if (found.token_stats) {
          setTokenStats(prev => prev ?? {
            used: found.token_stats!.used,
            max: found.token_stats!.max,
            breakdown: found.token_stats!.breakdown,
            session: found.token_stats!.session,
            cumulative: found.token_stats!.cumulative,
          });
        }
        // 拉取 config 获取 model_name / api_protocol / provider / runtime cwd
        return adminAPI.getConfig(found.dir_name).then(cfg => {
          const mn: string | undefined = cfg?.config?.model?.model_name;
          const ap: string | undefined = cfg?.config?.model?.api_protocol;
          const pv: string | undefined = cfg?.config?.model?.provider;
          const card: string | undefined = cfg?.config?.model?._card;
          const runtimeWd: string | undefined = (cfg as any)?.runtime_working_directory;
          if (mn) setModelName(mn);
          if (ap) setAgentApiProtocol(ap);
          if (pv) setAgentProvider(pv);
          if (card) setCurrentCardName(card);
          if (runtimeWd) setAgentCwd(runtimeWd);
        });
      })
      .catch(err => console.warn("[AIChatPage] Failed to load agent profile:", err.message));
  }, [agentId]);

  // Load available model cards once, to populate the runtime model-switch dropdown.
  useEffect(() => {
    let cancelled = false;
    modelCardAPI.getCards()
      .then(res => {
        if (!cancelled && Array.isArray(res.cards)) setModelCards(res.cards);
      })
      .catch(err => console.warn("[AIChatPage] Failed to load model cards:", err.message));
    return () => { cancelled = true; };
  }, []);

  // ---- Auto-scroll ----
  const scrollToBottom = useCallback(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    userScrolledRef.current = false;
  }, []);

  const scrollToTop = useCallback(() => {
    messagesContainerRef.current?.scrollTo({ top: 0, behavior: 'smooth' });
  }, []);

  // Track scroll position for button visibility + lazy load trigger
  const handleMessagesScroll = useCallback(() => {
    const el = messagesContainerRef.current;
    if (!el) return;
    const { scrollTop, scrollHeight, clientHeight } = el;
    const distFromBottom = scrollHeight - scrollTop - clientHeight;
    setShowScrollTop(scrollTop > 200);
    setShowScrollBottom(distFromBottom > 200);
    userScrolledRef.current = distFromBottom > 100;

    // Show buttons on scroll, then hide after 1.5s of inactivity
    setScrollActive(true);
    if (scrollHideTimerRef.current) clearTimeout(scrollHideTimerRef.current);
    scrollHideTimerRef.current = setTimeout(() => setScrollActive(false), 1500);

    // Lazy load: trigger when scrolled near top
    if (scrollTop < 100 && hasMoreHistory && !isLoadingMore) {
      loadMoreHistory();
    }
  }, [hasMoreHistory, isLoadingMore]);

  // Load earlier messages (prepend to timeline)
  const loadMoreHistory = useCallback(async () => {
    const sid = loadingSessionIdRef.current;
    if (!sid || isLoadingMore || !hasMoreHistory) return;

    setIsLoadingMore(true);
    const el = messagesContainerRef.current;
    const prevScrollHeight = el ? el.scrollHeight : 0;

    try {
      const resp = await agentSessionAPI.getSessionHistoryPaged(
        agentId, sid, historyOffsetRef.current, 50
      );
      const session = resp.session;
      if (session && (session.messages?.length > 0 || session.events?.length > 0)) {
        const olderEntries = _buildTimelineFromSession(
          session.messages || [],
          session.events || [],
        );
        if (olderEntries.length > 0) {
          setTimeline(prev => [...olderEntries, ...prev]);
          historyOffsetRef.current += session.messages?.length || 0;
        }
        setHasMoreHistory(session.has_more ?? false);
      } else {
        setHasMoreHistory(false);
      }
    } catch (err: any) {
      console.warn('[AIChatPage] Failed to load more history:', err.message);
    } finally {
      setIsLoadingMore(false);
      // Restore scroll position after prepending
      requestAnimationFrame(() => {
        if (el) {
          const newScrollHeight = el.scrollHeight;
          el.scrollTop = newScrollHeight - prevScrollHeight;
        }
      });
    }
  }, [agentId, isLoadingMore, hasMoreHistory]);

  // Smart auto-scroll: only scroll if user was near the bottom before content grew.
  // When ANY tool call is expanded (data-tool-expanded attribute present),
  // auto-scroll is completely frozen to avoid jitter while reading details.
  // Auto-scroll resumes only when all tool calls are collapsed.
  useLayoutEffect(() => {
    const el = messagesContainerRef.current;
    if (!el) return;
    const { scrollHeight, scrollTop, clientHeight } = el;
    const prevH = prevOuterScrollHeightRef.current;
    prevOuterScrollHeightRef.current = scrollHeight; // always track

    // If any tool call is expanded anywhere, freeze scroll position
    if (el.querySelector('[data-tool-expanded]')) return;

    const contentDelta = Math.max(0, scrollHeight - prevH);
    if (contentDelta === 0) return;

    // Reconstruct pre-update distance from bottom
    const distFromBottom = scrollHeight - scrollTop - clientHeight;
    const wasAtBottom = (distFromBottom - contentDelta) < 80;

    if (wasAtBottom) {
      el.scrollTop = scrollHeight - clientHeight; // instant, no smooth jitter
    }
  }, [timeline, streamingText]);

  // ---- Timeline helpers ----

  /**
   * Get or create the last incomplete workflow block in the timeline.
   * If the last entry is already an incomplete workflow, append to it.
   * Otherwise, create a new workflow entry at the end.
   */
  function appendWorkflowEvent(
    prev: TimelineEntry[],
    event: WorkflowEvent,
    status: string | null,
  ): TimelineEntry[] {
    const updated = [...prev];

    // Find the last incomplete workflow block, skipping over any trailing 'prompt' or
    // 'status_hint' entries (these can be interleaved within the same turn).
    let targetIdx = -1;
    for (let i = updated.length - 1; i >= 0; i--) {
      if (updated[i].kind === 'prompt' || updated[i].kind === 'status_hint') continue;
      if (updated[i].kind === 'workflow' && !(updated[i] as Extract<TimelineEntry, { kind: 'workflow' }>).data.completed) {
        targetIdx = i;
      }
      break;
    }

    // CRITICAL FIX for tool_result: when targetIdx is -1 (e.g. user message is the
    // last entry), search ALL workflow blocks — including completed ones and those
    // separated by user messages — for an unmatched tool_call. This prevents the
    // tool_result from being stranded in a new workflow block while the tool_call
    // remains permanently "running" in an older block.
    if (event.type === 'tool_result' && targetIdx < 0) {
      const resultData = event.content;
      const resultId = typeof resultData === 'object' ? (resultData.id || resultData.tool_use_id) : null;

      for (let wi = updated.length - 1; wi >= 0; wi--) {
        if (updated[wi].kind !== 'workflow') continue;
        const wf = (updated[wi] as Extract<TimelineEntry, { kind: 'workflow' }>).data;
        for (let ei = wf.events.length - 1; ei >= 0; ei--) {
          const evt = wf.events[ei];
          if (evt.type === 'tool_call' && !evt.result) {
            const callData = typeof evt.content === 'object' ? evt.content : {};
            const callId = callData.id || callData.tool_use_id;
            if (!resultId || !callId || resultId === callId) {
              // Found match — merge result into this tool_call
              const resStr = typeof resultData === 'object'
                ? (typeof resultData.result === 'string' ? resultData.result : (resultData.output || JSON.stringify(resultData)))
                : String(resultData);
              wf.events[ei] = {
                ...evt,
                result: resStr,
                resultStatus: (typeof resultData === 'object' && resultData.error) ? 'error' : 'success',
              };
              // Keep the workflow's completed flag unchanged. Setting completed=false
              // here would re-open a legitimately-finished workflow (e.g. system.wait
              // sends tool_result after to_user_reply), leaving hasActiveWorkflow
              // permanently true and the agent working animation stuck on.
              wf.status = status;
              // Return a new reference to trigger re-render
              return updated.map((entry, idx) =>
                idx === wi
                  ? { kind: 'workflow' as const, data: { ...wf, events: [...wf.events] }, _uid: entry._uid }
                  : entry
              );
            }
          }
        }
      }

      // No matching tool_call found anywhere — create new workflow block as fallback
      return [
        ...updated,
        {
          kind: 'workflow',
          data: { events: [{ ...event, _uid: genUID() }], status, completed: false },
          _uid: genUID(),
        },
      ];
    }

    if (targetIdx >= 0) {
      // Existing incomplete workflow block — append or merge event
      const wf = (updated[targetIdx] as Extract<TimelineEntry, { kind: 'workflow' }>).data;
      let newEvents: WorkflowEvent[];

      if (event.type === 'thought' && wf.events.length > 0) {
        // For thoughts, accumulate consecutive chunks into one block
        const lastEvt = wf.events[wf.events.length - 1];
        if (lastEvt.type === 'thought') {
          newEvents = [...wf.events];
          newEvents[newEvents.length - 1] = {
            ...lastEvt,
            content: lastEvt.content + event.content,
          };
        } else {
          newEvents = [...wf.events, event];
        }
      } else if (event.type === 'tool_result') {
        // Merge tool_result INTO the matching tool_call event
        const resultData = event.content;
        const resultId = typeof resultData === 'object' ? (resultData.id || resultData.tool_use_id) : null;
        newEvents = [...wf.events];

        // Find matching tool_call: by id if available, otherwise last tool_call without result
        let merged = false;
        for (let i = newEvents.length - 1; i >= 0; i--) {
          const evt = newEvents[i];
          if (evt.type === 'tool_call' && !evt.result) {
            const callData = typeof evt.content === 'object' ? evt.content : {};
            const callId = callData.id || callData.tool_use_id;
            if (!resultId || !callId || resultId === callId) {
              // Merge: add result to the tool_call event
              const resStr = typeof resultData === 'object'
                ? (typeof resultData.result === 'string' ? resultData.result : (resultData.output || JSON.stringify(resultData)))
                : String(resultData);
              newEvents[i] = {
                ...evt,
                result: resStr,
                resultStatus: (typeof resultData === 'object' && resultData.error) ? 'error' : 'success',
              };
              merged = true;
              break;
            }
          }
        }

        if (!merged) {
          // Fallback: add as standalone event (should be rare)
          newEvents.push(event);
        }
      } else {
        newEvents = [...wf.events, event];
      }

      updated[targetIdx] = {
        ...updated[targetIdx],
        data: { events: newEvents, status, completed: false },
      } as TimelineEntry;
    } else {
      // Create new workflow block
      updated.push({
        kind: 'workflow',
        data: { events: [{ ...event, _uid: genUID() }], status, completed: false },
        _uid: genUID(),
      });
    }

    return updated;
  }

  /**
   * Mark the last workflow block as completed, then append the
   * assistant's final message.
   */
  function finalizeWorkflowAndAddMessage(
    prev: TimelineEntry[],
    msg: ChatMessage,
  ): TimelineEntry[] {
    const updated = [...prev];

    // Dedup: if the last message entry has the same content and role, skip adding
    for (let i = updated.length - 1; i >= 0; i--) {
      const entry = updated[i];
      if (entry.kind !== 'message') continue;
      const existing = entry.data as ChatMessage;
      if (existing.role === 'user') break; // stop at user message boundary
      if (existing.role === 'assistant' && existing.content === msg.content) {
        // Already have this exact message, skip duplicate
        // Still mark workflow as completed
        for (let j = updated.length - 1; j >= 0; j--) {
          if (updated[j].kind === 'workflow' && !updated[j].data.completed) {
            updated[j] = { ...updated[j], data: { ...updated[j].data, status: null, completed: true } } as TimelineEntry;
            break;
          }
        }
        return updated;
      }
      break; // only check the last assistant message
    }

    // Mark last workflow as completed (if exists)
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

    // Add the assistant message
    updated.push({ kind: 'message', data: msg, _uid: genUID() });

    return updated;
  }

  // ---- WebSocket connection ----
  useEffect(() => {
    if (!agentId) return;

    const aiWsService = getAiWsService(agentId);
    aiWsService.connect(agentId);
    wsServiceRef.current = aiWsService;

    // Auth expiry detection — prompt re-login when token is invalid/expired
    const unsubAuthExpired = aiWsService.onAuthExpired(() => {
      setSessionExpired(true);
    });

    const unsubStatus = aiWsService.onStatusChange((status) => {
      setWsStatus(status);
      if (status === 'connected') {
        setAgentStatus('connected');
      } else if (status === 'disconnected') {
        setAgentStatus('disconnected');
      } else if (status === 'connecting') {
        setAgentStatus('connecting');
      } else if (status === 'agent-starting') {
        setAgentStatus('agent-starting');
      } else if (status === 'error') {
        setAgentStatus('error');
      }
    });

    // ---- Message handlers ----

    // Stream — accumulate chunks via ref, then sync to state
    const streamSeqRef = { current: 0 };
    const unsubStream = aiWsService.on('stream', (msg: AIWSMessage) => {
      if (finalizingRef.current) return;
      const text = _extractContent(msg);
      if (text) {
        streamSeqRef.current += 1;
        streamingTextRef.current += text;
        setStreamingText(streamingTextRef.current);
        setIsStreaming(true);
        setAgentStatus('thinking');
      }
    });

    // Final message / response — finalize streaming into a message
    const handleFinal = (msg: AIWSMessage) => {
      console.log('[AIChatPage] 📨 handleFinal called!', JSON.stringify(msg).substring(0, 200));
      // Guard: prevent duplicate finalization (both 'message' and 'response'
      // may fire for the same reply)
      if (finalizingRef.current) return;
      finalizingRef.current = true;

      const text = _extractContent(msg);
      // Prefer the event's text (complete user_msg from runner) over the
      // accumulated streaming ref (may be missing the last debounced chunk).
      const finalText = text || streamingTextRef.current;

      if (typeof finalText === 'string' && finalText.trim().length > 0) {
        const raw = msg as any;
        const messageId = raw.message_id || raw.id || undefined;
        const role = (raw.role === 'user' ? 'user' : 'assistant') as 'user' | 'assistant';
        const chatMsg: ChatMessage = {
          role,
          content: finalText,
          timestamp: new Date().toISOString(),
        };
        if (messageId) {
          chatMsg.message_id = messageId;
        }

        setTimeline(prev => {
          // Dedup late final events after refresh/reconnect. Walk backward until
          // the latest user message boundary so trailing workflow blocks do not
          // defeat duplicate detection for the already-rendered assistant reply.
          for (let i = prev.length - 1; i >= 0; i -= 1) {
            const entry = prev[i];
            if (entry.kind !== 'message') continue;
            const existing = entry.data as ChatMessage;
            if (existing.role === 'user') break;
            if (existing.role === 'assistant') {
              if (existing.content === finalText) {
                console.log('[AIChatPage] handleFinal: recent assistant content already present, skipping');
                return prev;
              }
              if (messageId && existing.message_id && existing.message_id === messageId) {
                console.log('[AIChatPage] handleFinal: recent assistant message_id already present, skipping');
                return prev;
              }
            }
          }
          // Broadcast message_id dedup across all entries (not just last)
          if (messageId) {
            const exists = prev.some(e =>
              e.kind === 'message' && (e.data as ChatMessage).message_id === messageId
            );
            if (exists) {
              console.log('[AIChatPage] handleFinal: message_id found earlier in timeline, skipping');
              return prev;
            }
          }

          const next = finalizeWorkflowAndAddMessage(prev, chatMsg);
          if (pendingFilePushesRef.current.length > 0) {
            const buffered = pendingFilePushesRef.current.map((m) => ({
              kind: 'message' as const,
              data: m,
              _uid: genUID(),
            }));
            pendingFilePushesRef.current = [];
            return [...next, ...buffered];
          }
          return next;
        });
      }

      // Clear streaming
      streamingTextRef.current = '';
      setStreamingText('');
      setIsStreaming(false);
      setAgentStatus('connected');

      // Fallback: clear new-session loading if a final message arrives before current_session
      if (newSessionPendingRef.current) {
        newSessionPendingRef.current = false;
        setIsLoadingSession(false);
      }

      // Reset guard after a short delay (allow next turn's final to work)
      setTimeout(() => { finalizingRef.current = false; }, 300);
    };
    const unsubMessage = aiWsService.on('message', handleFinal);
    const unsubResponse = aiWsService.on('response', handleFinal);
    const unsubToUserReply = aiWsService.on('to_user_reply', (msg: AIWSMessage) => {
      // to_user_reply behaves like final text but expects user input afterward
      handleFinal(msg);
      setAgentStatus('awaiting_reply');
    });

    const unsubSessionTitle = aiWsService.on('session_title', (msg: AIWSMessage) => {
      const data = msg.content || msg.data;
      const title = typeof data === 'object' ? data.title : null;
      const sessionId = typeof data === 'object' && typeof data?.id === 'string' ? data.id : null;
      if (title) {
        setCurrentSessionId(prev => prev || sessionId);
        if (sessionId) {
          setSessionTitleUpdate({ id: sessionId, title });
        }
      }
    });

    const unsubToUserFinal = aiWsService.on('to_user_final', (msg: AIWSMessage) => {
      handleFinal(msg);
    });

    // Thought — accumulate consecutive chunks into a single thought block
    const unsubThought = aiWsService.on('thought', (msg: AIWSMessage) => {
      const text = _extractContent(msg);
      if (text) {
        const event: WorkflowEvent = { type: 'thought', content: text, timestamp: Date.now() };
        setTimeline(prev => appendWorkflowEvent(prev, event, 'Thinking...'));
        setAgentStatus('thinking');
      }
    });

    // Tool call
    const unsubToolCall = aiWsService.on('tool_call', (msg: AIWSMessage) => {
      const data = msg.content || msg.data;
      const toolName = typeof data === 'object' ? (data.name || data.tool || 'Tool') : 'Tool';
      const isSubAgent = typeof data === 'object' && !!data.sub_agent;
      const event: WorkflowEvent = {
        type: 'tool_call',
        content: data,
        timestamp: Date.now(),
        subAgent: isSubAgent,
        subTaskLabel: typeof data === 'object' ? (data.sub_task_label || '') : '',
      };
      setTimeline(prev => appendWorkflowEvent(prev, event, `Calling ${toolName}...`));
      // 仅主 agent 工具调用时才清空流式文本缓冲区；子 agent 调用不应影响父 agent 的流式输出。
      // 正常情况下 to_user_final 已在 tool_call 之前到达并清空了缓冲区，此处无副作用。
      // 异常情况（JSON 参数泄漏）下，后端未发送 to_user_final，泄漏的 JSON 仍留在
      // streamingTextRef 中——在此强制清空，防止它被拼入下一条正式消息或永久显示。
      if (!isSubAgent) {
        streamingTextRef.current = '';
        setStreamingText('');
      }
    });

    // Tool result — merge into matching tool_call
    const unsubToolResult = aiWsService.on('tool_result', (msg: AIWSMessage) => {
      const data = msg.content || msg.data;
      const toolName = typeof data === 'object' ? (data.name || data.tool || 'Tool') : 'Tool';
      const event: WorkflowEvent = {
        type: 'tool_result',
        content: data,
        timestamp: Date.now(),
        subAgent: typeof data === 'object' ? !!data.sub_agent : false,
        subTaskLabel: typeof data === 'object' ? (data.sub_task_label || '') : '',
      };
      setTimeline(prev => appendWorkflowEvent(prev, event, `${toolName} completed`));
    });

    // Plan — Runner sends {id, text} after parsing <plan> tag
    const unsubPlan = aiWsService.on('plan', (msg: AIWSMessage) => {
      const data = msg.content || msg.data;
      // data is usually {id: "plan_XXXX", text: "..."} from Runner
      const planContent = typeof data === 'object' ? (data.text || data.content || data) : data;
      const steps = parsePlanContent(planContent);
      if (steps.length > 0) {
        setPlanSteps(steps);
        // Also add as a workflow event for inline display
        const event: WorkflowEvent = { type: 'plan', content: steps, timestamp: Date.now() };
        setTimeline(prev => appendWorkflowEvent(prev, event, 'Planning...'));
      }
    });

    // Summary stream (context compression)
    const unsubSummaryStream = aiWsService.on('summary_stream', (msg: AIWSMessage) => {
      const data = msg.content || msg.data || {};
      const streamId = typeof data === 'object' ? (data.id || 'summary') : 'summary';
      const delta = typeof data === 'object' ? (data.delta || '') : '';
      const fullText = typeof data === 'object' && typeof data.text === 'string' ? data.text : '';
      const tick = typeof data === 'object' ? (Number(data.tick) || 0) : 0;
      const done = typeof data === 'object' ? !!data.done : false;

      // When summary stream finishes, clear the compressing flag immediately
      if (done) {
        setIsCompressingContext(false);
      }

      const cache = summaryStreamCacheRef.current;
      if (!cache[streamId]) cache[streamId] = '';
      if (delta) {
        cache[streamId] += delta;
      } else if (fullText) {
        // Support final full-text payload (done=true, text=...)
        cache[streamId] = fullText;
      }
      const text = cache[streamId];
      console.debug('[AIChatPage] summary_stream recv', {
        streamId,
        deltaLen: delta.length,
        fullTextLen: fullText.length,
        cachedLen: text.length,
        done,
      });

      if (SUMMARY_STREAM_DEBUG) {
        console.log('[AIChatPage][summary_stream] recv', {
          streamId,
          deltaLen: typeof delta === 'string' ? delta.length : 0,
          fullTextLen: typeof fullText === 'string' ? fullText.length : 0,
          done,
          cacheLen: typeof text === 'string' ? text.length : 0,
        });
      }

      setTimeline(prev => {
        const updated = [...prev];
        let targetWfIdx = -1;
        for (let i = updated.length - 1; i >= 0; i--) {
          if (updated[i].kind === 'workflow' && !updated[i].data.completed) {
            targetWfIdx = i;
            break;
          }
        }
        if (targetWfIdx < 0) {
          return appendWorkflowEvent(prev, {
            type: 'summary_stream',
            content: { id: streamId, text, done },
            timestamp: Date.now(),
          }, done ? 'Summary completed' : 'Summarizing...');
        }

        const wf = updated[targetWfIdx].data;
        // Keep at most ONE summary_stream per workflow block:
        // - Streaming deltas (done=false) update in-place
        // - When done=true, the old streaming entry becomes the final green box
        // - No duplicate blue+green boxes
        const events = wf.events.filter((e: WorkflowEvent) => {
          if (e.type !== 'summary_stream') return true;
          // Remove all existing summary_stream entries — we only keep the latest
          return false;
        }) as WorkflowEvent[];
        events.push({
          type: 'summary_stream',
          content: { id: streamId, text, done },
          timestamp: Date.now(),
          _uid: genUID(),
        } as WorkflowEvent);

        updated[targetWfIdx] = {
          ...updated[targetWfIdx],
          data: {
            ...wf,
            events,
            status: done ? 'Summary completed' : 'Summarizing...',
          }
        } as TimelineEntry;
        return updated;
      });
    });

    // Compression progress (real-time progress updates from manual __COMPRESS_CONTEXT__)
    const unsubCompressionProgress = aiWsService.on('compression_progress', (msg: AIWSMessage) => {
      const data = msg.content || msg.data || {};
      const text = typeof data === 'object' ? (data.text || '') : '';
      const isFinal = typeof data === 'object' ? !!data.is_final : false;
      const traceId = typeof data === 'object' ? (data.trace_id || '') : '';

      console.debug('[AIChatPage] compression_progress recv', { text, isFinal, traceId });

      if (isFinal) {
        setIsCompressingContext(false);
      } else {
        setIsCompressingContext(true);
      }

      // Show progress in workflow timeline
      if (text) {
        setTimeline(prev => {
          const updated = [...prev];
          // Find the last incomplete workflow block
          let targetWfIdx = -1;
          for (let i = updated.length - 1; i >= 0; i--) {
            if (updated[i].kind === 'workflow' && !updated[i].data.completed) {
              targetWfIdx = i;
              break;
            }
          }
          if (targetWfIdx < 0) {
            return appendWorkflowEvent(prev, {
              type: 'compression_progress',
              content: { text, isFinal, trace_id: traceId },
              timestamp: Date.now(),
            }, text);
          }

          const wf = updated[targetWfIdx].data;
          const events = [...wf.events];
          // Merge into existing compression_progress event or append new one
          let merged = false;
          for (let i = events.length - 1; i >= 0; i--) {
            const evt = events[i];
            if (evt.type === 'compression_progress') {
              events[i] = {
                ...evt,
                content: { text, isFinal, trace_id: traceId },
                timestamp: Date.now(),
              };
              merged = true;
              break;
            }
          }
          if (!merged) {
            events.push({
              type: 'compression_progress',
              content: { text, isFinal, trace_id: traceId },
              timestamp: Date.now(),
              _uid: genUID(),
            });
          }

          updated[targetWfIdx] = {
            ...updated[targetWfIdx],
            data: {
              ...wf,
              events,
              status: isFinal ? 'Compression complete' : text,
            }
          } as TimelineEntry;
          return updated;
        });
      }
    });

    // Token stats
    const unsubTokenStats = aiWsService.on('token_stats', (msg: AIWSMessage) => {
      const data = msg.content || msg.data;
      if (data) {
        setTokenStats({
          used: data.used || 0,
          max: data.max || 0,
          breakdown: data.breakdown,
          session: data.session,
          cumulative: data.cumulative,
        });
      }
    });

    // Status / state / wake / sleep / info
    const handleStatus = (msg: AIWSMessage) => {
      const data = msg.content || msg.data;
      if (typeof data === 'string') {
        if (data === 'thinking' || data === 'processing') {
          setAgentStatus('thinking');
        } else if (data === 'working') {
          setAgentStatus('working');
        } else if (data === 'sleeping') {
          setAgentStatus('sleeping');
        } else if (data === 'idle' || data === 'ready') {
          setAgentStatus('idle');
         } else if (data.includes('Response complete') || data.includes('idle') || data.includes('ready') || data.includes('complete')) {
           // Catch status strings like "Response complete" and "Continuous mode - State: idle"
           setAgentStatus('idle');
        }
      }
    };
    const unsubState = aiWsService.on('state', (msg: AIWSMessage) => {
      // Only update agentStatus — do NOT add to timeline; StatusBadge already reflects state changes
      handleStatus(msg);
    });
    const unsubStatusEvt = aiWsService.on('status', handleStatus);
    const unsubWake = aiWsService.on('wake', (msg: AIWSMessage) => {
      setAgentStatus('connected');
      const data = msg.content || msg.data;
      if (data !== null && data !== undefined) {
        setTimeline(prev => [...prev, {
          kind: 'status_hint' as const,
          data: { hintType: 'wake' as const, content: String(data), timestamp: Date.now() },
          _uid: genUID(),
        }]);
      }
    });
    const unsubSleep = aiWsService.on('sleep', (msg: AIWSMessage) => {
      setAgentStatus('sleeping');
      const raw = msg.content ?? msg.data;
      const seconds = typeof raw === 'number' ? raw : parseInt(String(raw), 10);
      setTimeline(prev => [...prev, {
        kind: 'status_hint' as const,
        data: { hintType: 'sleep' as const, content: isNaN(seconds) ? 0 : seconds, timestamp: Date.now() },
        _uid: genUID(),
      }]);
    });
    const unsubInfo = aiWsService.on('info', (msg: AIWSMessage) => {
      const raw = msg.content || msg.data;
      const detailed =
        typeof raw === 'string'
          ? { text: raw }
          : (typeof raw === 'object' && raw !== null ? raw : { text: String(raw) });

      if (typeof detailed === 'object' && detailed !== null) {
        const evt = (detailed as any).event;
        if (evt === 'context_compressed' || evt === 'context_compress_skipped') {
          setIsCompressingContext(false);
          return;  // Skip system info — don't show blue prompt in workflow
        }

        if (evt === 'context_summary_generated' && typeof (detailed as any).summary === 'string') {
          return; // ignore info event; summary_stream will carry final content
        }

        // Runtime model switch confirmation: update the header label and clear
        // the switching spinner. Falls through so the existing info-event
        // renderer (event==='model_card_switched') still shows the timeline card.
        if (evt === 'model_card_switched') {
          const switchedModel = (detailed as any).model;
          const switchedCard = (detailed as any).card;
          if (typeof switchedModel === 'string' && switchedModel) setModelName(switchedModel);
          if (typeof switchedCard === 'string' && switchedCard) setCurrentCardName(switchedCard);
          setSwitchingModel(false);
        }

        // Task supervisor check-in: pass full payload but use a short status label
        if (evt === 'task_supervisor_checkin') {
          const stall = (detailed as any).stall_count || 0;
          const urgencyLabel = stall > 4 ? 'URGENT' : stall > 2 ? 'WARNING' : 'REMINDER';
          const checkinLabel = `Task Supervisor · ${urgencyLabel} (stall ${stall})`;
          const checkinEvent: WorkflowEvent = {
            type: 'info',
            content: detailed,
            timestamp: Date.now(),
          };
          setTimeline(prev => appendWorkflowEvent(prev, checkinEvent, checkinLabel));
          return;
        }
      }

      const summary =
        typeof detailed.text === 'string' && detailed.text.trim().length > 0
          ? detailed.text
          : (typeof detailed.message === 'string' && detailed.message.trim().length > 0
              ? detailed.message
              : 'Info event');

      const isSubAgent = typeof detailed === 'object' && detailed !== null && !!(detailed as any).sub_agent;
      const event: WorkflowEvent = {
        type: 'info',
        content: detailed, // always keep full detail payload
        timestamp: Date.now(),
        subAgent: isSubAgent,
        subTaskLabel: typeof detailed === 'object' && detailed !== null ? ((detailed as any).sub_task_label || '') : '',
      };
      setTimeline(prev => appendWorkflowEvent(prev, event, summary));
    });

    // Turn start — reset streaming state and record workflow start timestamp (first turn only)
    const unsubTurnStart = aiWsService.on('turn_start', (msg: AIWSMessage) => {
      const data = msg.content ?? msg.data;
      // turn=1 means the very first LLM call for this user message.
      // turn>=2 means the agent is re-entering the loop after a tool call (same workflow).
      // data===0 means a session management command (NEW_SESSION, LOAD_SESSION, etc.).
      const turnNumber = typeof data === 'object' && data !== null ? (data as any).turn : 0;
      const isFirstTurn = turnNumber <= 1; // turn=1 or data=0 (management)

      // Salvage unfinalized streaming text ONLY on the first turn of a new user message.
      // On subsequent turns (tool call re-entries), the agent is still in the same workflow —
      // salvaging here would incorrectly finalize the ongoing workflow block and cause a new
      // WorkflowContainer to be created for the next tool call.
      if (isFirstTurn && streamingTextRef.current && !finalizingRef.current) {
        const salvaged = streamingTextRef.current;
        if (salvaged.trim().length > 0) {
          const salvagedMsg: ChatMessage = {
            role: 'assistant',
            content: salvaged,
            timestamp: new Date().toISOString(),
          };
          setTimeline(prev => finalizeWorkflowAndAddMessage(prev, salvagedMsg));
        }
      }
      streamingTextRef.current = '';
      setStreamingText('');
      setIsStreaming(false);
      finalizingRef.current = false;
      setAgentStatus('thinking');
      // Only start the workflow timer when the backend supplies a real started_ms.
      // turn_start(0) is emitted for session management operations (__NEW_SESSION__,
      // __LOAD_SESSION__, etc.) and must NOT trigger the elapsed-time counter.
      const isRealWorkflow = typeof data === 'object' && data !== null && typeof (data as any).started_ms === 'number';
      if (isRealWorkflow && isFirstTurn) {
        const startedMs = (data as any).started_ms as number;
        // Reset timer on each new workflow (turn=1). Subsequent turns (2+ after tool calls)
        // must NOT overwrite it so the timer reflects the full workflow duration.
        setTurnStartedMs(startedMs);
      }
    });

    // Turn elapsed — backend sends {started_ms, ended_ms} after to_user_final
    const unsubTurnElapsed = aiWsService.on('turn_elapsed', (msg: AIWSMessage) => {
      const data = msg.content ?? msg.data;
      if (typeof data !== 'object' || data === null) return;
      const { started_ms, ended_ms } = data as { started_ms?: number; ended_ms?: number };
      if (typeof started_ms !== 'number' || typeof ended_ms !== 'number') return;
      const finalMs = ended_ms - started_ms;
      // Stamp the final elapsed time onto the last workflow block in the timeline,
      // and mark it completed so the live timer stops.
      setTimeline(prev => {
        const updated = [...prev];
        for (let i = updated.length - 1; i >= 0; i--) {
          if (updated[i].kind === 'workflow') {
            const wfData = (updated[i] as { kind: 'workflow'; data: WorkflowBlock }).data;
            updated[i] = {
              ...updated[i],
              data: { ...wfData, elapsed_ms: finalMs, completed: true, status: null },
            };
            break;
          }
        }
        return updated;
      });
      // Clear live start timestamp (turn is over)
      setTurnStartedMs(undefined);
    });

    // Prompt update — insert/update prompt entry in timeline (first item = first prompt)
    const unsubPromptUpdate = aiWsService.on('prompt_update', (msg: AIWSMessage) => {
      const data: any = msg.content ?? msg.data ?? msg;
      const systemPrompt: string = data?.system_prompt ?? '';
      const dynamicPrefix: string = data?.dynamic_prefix ?? '';
      const changed: boolean = data?.changed ?? false;
      const diff: string[] | undefined = Array.isArray(data?.diff) ? data.diff : undefined;
      if (!systemPrompt) return;
      const entry = {
        kind: 'prompt' as const,
        data: {
          system_prompt: systemPrompt,
          dynamic_prefix: dynamicPrefix,
          changed,
          timestamp: new Date().toISOString(),
          diff,
        },
      };
      setTimeline(prev => {
        // 按时间顺序追加；首次也不再插到最前
        const hasPrompt = prev.some(e => e.kind === 'prompt');
        if (!hasPrompt) return [...prev, entry];
        if (!changed) return prev;  // 系统提示词未变化，跳过
        return [...prev, entry];
      });

      // Skip system info — don't show blue "Context summary has been injected" prompt
    });

    // output_media — model-generated audio/images, patch onto last assistant message
    const unsubOutputMedia = aiWsService.on('output_media', (msg: AIWSMessage) => {
      const items: Array<{ type: string; url: string; mime: string }> = msg.content ?? msg.data ?? [];
      if (!Array.isArray(items) || items.length === 0) return;
      const audioItems = items.filter(i => i.type === 'audio');
      const imageItems = items.filter(i => i.type === 'image');
      setTimeline(prev => {
        const next = [...prev];
        for (let i = next.length - 1; i >= 0; i--) {
          const e = next[i];
          if (e.kind === 'message' && (e.data as ChatMessage).role === 'assistant') {
            const existing = e.data as ChatMessage;
            const patched: ChatMessage = {
              ...existing,
              output_audio: audioItems.length
                ? [...(existing.output_audio ?? []), ...audioItems.map(a => ({ url: a.url, mime: a.mime }))]
                : existing.output_audio,
              output_images: imageItems.length
                ? [...(existing.output_images ?? []), ...imageItems.map(a => a.url)]
                : existing.output_images,
            };
            next[i] = { ...e, data: patched };
            break;
          }
        }
        return next;
      });
    });

    const appendFilePushMessage = (entries: TimelineEntry[], assistantMsg: ChatMessage): TimelineEntry[] => {
      const k = _messageIdentityKey(assistantMsg);
      if (k) {
        const exists = entries.some((e) => e.kind === 'message' && _messageIdentityKey(e.data as ChatMessage) === k);
        if (exists) return entries;
      }
      return [...entries, { kind: 'message', data: assistantMsg, _uid: genUID() }];
    };

    const flushBufferedFilePushes = (entries: TimelineEntry[]): TimelineEntry[] => {
      if (pendingFilePushesRef.current.length === 0) return entries;
      let next = [...entries];
      for (const pendingMsg of pendingFilePushesRef.current) {
        next = appendFilePushMessage(next, pendingMsg);
      }
      pendingFilePushesRef.current = [];
      return next;
    };

    const queueBufferedFilePush = (assistantMsg: ChatMessage) => {
      const k = _messageIdentityKey(assistantMsg);
      if (k) {
        const exists = pendingFilePushesRef.current.some((m) => _messageIdentityKey(m) === k);
        if (exists) return;
      }
      pendingFilePushesRef.current = [...pendingFilePushesRef.current, assistantMsg];
    };

    const hydrateCurrentSession = (opts?: {
      showLoading?: boolean;
      wasNewSession?: boolean;
    }) => {
      const seq = ++sessionReloadSeqRef.current;
      isHydratingSessionRef.current = true;
      sessionBootstrapDoneRef.current = false;
      pendingHydrationMediaRef.current = [];
      diskSessionLoadedRef.current = false;

      (async () => {
        try {
          if (opts?.showLoading) {
            setIsLoadingSession(true);
            setSessionLoadingLabel(t('aiChat.loadingSession'));
          }
          const resp = await Promise.race([
            agentSessionAPI.getCurrentSession(agentId, 0, 50),
            new Promise<never>((_, reject) =>
              setTimeout(() => reject(new Error('Hydration timeout (10s)')), 10000)
            ),
          ]);
          if (seq !== sessionReloadSeqRef.current || viewingHistorySessionRef.current || newSessionPendingRef.current) {
            return;
          }
          const currentSid = resp.current_session_id;
          const session = resp.session;
          _logMediaDebug('current-session-response', {
            currentSid,
            messageCount: session?.messages?.length || 0,
            sample: (session?.messages || []).slice(-5).map((m: any) => ({
              mid: m?.message_id || m?.id,
              role: m?.role,
              type: m?.type,
              images: Array.isArray(m?.images) ? m.images.length : 0,
              files: Array.isArray(m?.files) ? m.files.length : 0,
              attachments: Array.isArray(m?.attachments) ? m.attachments.length : 0,
              contentHead: typeof m?.content === 'string' ? m.content.slice(0, 80) : '',
            })),
          });
          if (currentSid && session) {
            // Pre-deduplicate disk session messages by (role + normalized content).
            // In some refresh scenarios the runner snapshot can contain the same
            // user message twice (e.g. input-hub/Gateway racing). Removing exact
            // text duplicates here prevents them from showing up after refresh.
            const rawMessages = session.messages || [];
            const seenMsgKeys = new Set<string>();
            const dedupedMessages: any[] = [];
            for (const m of rawMessages) {
              const role = m?.role || '';
              const content = typeof m?.content === 'string' ? m.content : '';
              const normalized = content
                .replace(/\[File:.*?\]\([^)]*\)/g, '')
                .replace(/<image>[\s\S]*?<\/image>/gi, '')
                .trim();
              const key = normalized ? `${role}:${normalized}` : `empty:${role}:${dedupedMessages.length}`;
              if (seenMsgKeys.has(key)) {
                _logMediaDebug('hydrate-dedup-skip', { role, contentHead: content.slice(0, 80) });
                continue;
              }
              seenMsgKeys.add(key);
              dedupedMessages.push(m);
            }
            if (dedupedMessages.length !== rawMessages.length) {
              _logMediaDebug('hydrate-dedup-result', {
                before: rawMessages.length,
                after: dedupedMessages.length,
              });
            }

            const entries = _buildTimelineFromSession(
              dedupedMessages,
              session.events || [],
              session.archived_messages,
              session.archived_events,
            );
            _logMediaDebug('timeline-built-from-current', {
              entryCount: entries.length,
              messageSample: entries
                .filter((e: any) => e.kind === 'message')
                .slice(-5)
                .map((e: any) => ({
                  mid: e.data?.message_id,
                  role: e.data?.role,
                  type: e.data?.type,
                  images: Array.isArray(e.data?.images) ? e.data.images.length : 0,
                  attachments: Array.isArray(e.data?.attachments) ? e.data.attachments.length : 0,
                })),
            });
            let nextEntries = [...entries];

            if (pendingHydrationMediaRef.current.length > 0) {
              const buffered = pendingHydrationMediaRef.current;
              pendingHydrationMediaRef.current = [];
              const mergedEntries = [...nextEntries];
              // BUFFER_DEDUP_WINDOW_MS: WS history replay and disk-snapshot
              // can race during refresh, causing the same user/assistant
              // message to arrive in both sources. The disk snapshot is the
              // authoritative one — buffered events are merged in only when
              // they carry media the disk entry lacks (e.g. file_push). Plain
              // text duplicates are dropped here to prevent the
              // "last user message duplicated after refresh" bug.
              const BUFFER_DEDUP_WINDOW_MS = 30_000;
              for (const m of buffered) {
                // (1) Identity match: message_id or role+content fallback
                const k = _messageIdentityKey(m);
                if (k) {
                  const idx = mergedEntries.findIndex(
                    (e: any) => e.kind === 'message' && _messageIdentityKey(e.data as ChatMessage) === k,
                  );
                  if (idx >= 0) {
                    mergedEntries[idx] = {
                      ...mergedEntries[idx],
                      data: _mergeChatMessage(mergedEntries[idx].data as ChatMessage, m),
                    };
                    continue;
                  }
                }

                // (2) Content-level dedup: same role + same normalised
                //     content within BUFFER_DEDUP_WINDOW_MS — applies to
                //     plain text as well as media-bearing messages.
                const mTs = m.timestamp ? new Date(m.timestamp).getTime() : NaN;
                const mContentNorm = (typeof m.content === 'string' ? m.content : '')
                  .replace(/\[File:.*?\]\([^)]*\)/g, '')
                  .replace(/<image>.*?<\/image>/gis, '')
                  .trim();
                let contentDupIdx = -1;
                for (let i = mergedEntries.length - 1; i >= 0; i -= 1) {
                  const entry = mergedEntries[i];
                  if (entry.kind !== 'message') continue;
                  const d = entry.data as ChatMessage;
                  if (d.role !== m.role) continue;
                  const dContentNorm = (typeof d.content === 'string' ? d.content : '')
                    .replace(/\[File:.*?\]\([^)]*\)/g, '')
                    .replace(/<image>.*?<\/image>/gis, '')
                    .trim();
                  if (dContentNorm !== mContentNorm) continue;
                  const dTs = d.timestamp ? new Date(d.timestamp).getTime() : NaN;
                  if (
                    Number.isNaN(mTs) ||
                    Number.isNaN(dTs) ||
                    Math.abs(dTs - mTs) <= BUFFER_DEDUP_WINDOW_MS
                  ) {
                    contentDupIdx = i;
                    break;
                  }
                }
                if (contentDupIdx >= 0) {
                  mergedEntries[contentDupIdx] = {
                    ...mergedEntries[contentDupIdx],
                    data: _mergeChatMessage(mergedEntries[contentDupIdx].data as ChatMessage, m),
                  };
                  continue;
                }

                // (3) If the buffered message carries no media, the disk
                //     snapshot already has the canonical copy — drop it.
                //     Only media-bearing events (file_push / image /
                //     attachment) survive into the timeline below.
                const hasMedia = !!(
                  (m.images && m.images.length > 0) ||
                  (m.attachments && m.attachments.length > 0) ||
                  (Array.isArray((m as any).files) && (m as any).files.length > 0)
                );
                if (!hasMedia) continue;

                if (!Number.isNaN(mTs)) {
                  const nearIdx = mergedEntries.findIndex((e: any) => {
                    if (e.kind !== 'message') return false;
                    const d = e.data as ChatMessage;
                    if (d.role !== 'assistant') return false;
                    const dTs = d.timestamp ? new Date(d.timestamp).getTime() : NaN;
                    if (Number.isNaN(dTs)) return false;
                    return Math.abs(dTs - mTs) <= BUFFER_DEDUP_WINDOW_MS;
                  });
                  if (nearIdx >= 0) {
                    mergedEntries[nearIdx] = {
                      ...mergedEntries[nearIdx],
                      data: _mergeChatMessage(mergedEntries[nearIdx].data as ChatMessage, m),
                    };
                    continue;
                  }

                  const insertAt = mergedEntries.findIndex((e: any) => {
                    if (e.kind !== 'message') return false;
                    const d = e.data as ChatMessage;
                    const dTs = d.timestamp ? new Date(d.timestamp).getTime() : NaN;
                    if (Number.isNaN(dTs)) return false;
                    return dTs > mTs;
                  });
                  const entry = { kind: 'message' as const, data: m, _uid: genUID() };
                  if (insertAt >= 0) {
                    mergedEntries.splice(insertAt, 0, entry);
                  } else {
                    mergedEntries.push(entry);
                  }
                  continue;
                }
                mergedEntries.push({ kind: 'message', data: m, _uid: genUID() });
              }
              nextEntries = mergedEntries;
            }

            nextEntries = flushBufferedFilePushes(nextEntries);
            setTimeline(nextEntries);
            sessionBootstrapDoneRef.current = true;
            currentSessionIdRef.current = currentSid;
            wsServiceRef.current?.setActiveSession(currentSid);
            setCurrentSessionId(currentSid);
            viewingHistorySessionRef.current = false;
            setViewingHistorySession(false);
            loadingSessionIdRef.current = currentSid;
            historyOffsetRef.current = session.messages?.length || 0;
            setHasMoreHistory(session.has_more ?? false);
            diskSessionLoadedRef.current = true;
          } else {
            // Disk session unavailable — use buffered WS history as fallback
            const buffered = pendingHydrationMediaRef.current;
            pendingHydrationMediaRef.current = [];
            if (buffered.length > 0) {
              const fallbackEntries: TimelineEntry[] = [];
              for (const m of buffered) {
                fallbackEntries.push({ kind: 'message', data: m, _uid: genUID() });
              }
              let merged = flushBufferedFilePushes(fallbackEntries);
              setTimeline(merged);
              sessionBootstrapDoneRef.current = true;
              diskSessionLoadedRef.current = false;
            }
          }
        } catch (err: any) {
          console.warn('[AIChatPage] getCurrentSession failed, fallback to WS history:', err?.message || err);
          diskSessionLoadedRef.current = false;
          // Restore any buffered WS history that arrived during hydration
          const buffered = pendingHydrationMediaRef.current;
          pendingHydrationMediaRef.current = [];
          if (buffered.length > 0 && !viewingHistorySessionRef.current) {
            const fallbackEntries: TimelineEntry[] = [];
            for (const m of buffered) {
              fallbackEntries.push({ kind: 'message', data: m, _uid: genUID() });
            }
            setTimeline(prev => {
              if (prev.length > 0) return prev;
              return flushBufferedFilePushes(fallbackEntries);
            });
            sessionBootstrapDoneRef.current = true;
          }
        } finally {
          if (seq === sessionReloadSeqRef.current) {
            if (opts?.wasNewSession) {
              newSessionPendingRef.current = false;
            }
            setIsLoadingSession(false);
            isHydratingSessionRef.current = false;
            // Allow WS history events to flow through when disk session is unavailable
            if (!diskSessionLoadedRef.current) {
              sessionBootstrapDoneRef.current = true;
            }
          }
        }
      })();
    };

    const scheduleCurrentSessionHydration = (delayMs: number = 120) => {
      if (newSessionPendingRef.current) return;
      if (sessionReloadTimerRef.current) {
        clearTimeout(sessionReloadTimerRef.current);
      }
      sessionReloadTimerRef.current = setTimeout(() => {
        sessionReloadTimerRef.current = null;
        if (viewingHistorySessionRef.current || newSessionPendingRef.current) return;
        hydrateCurrentSession({ showLoading: false });
      }, delayMs);
    };

    // ---- Session & connection events ----

    // Gateway sends "connected" immediately after WS handshake with
    // session_id and history_count. This is the initial session info.
    // The message shape is: {type:"connected", agent_id, agent_name, session_id, history_count}
    // (all fields at top level, no content/data wrapper)
    const unsubConnected = aiWsService.on('connected', (msg: AIWSMessage) => {
      // Fields are at top level of the message object
      const raw = msg as any;
      const sid = raw.session_id || raw.sessionId || (raw.content && raw.content.session_id);
      // Accept any non-null session_id (gateway_session_key on connect, canonical
      // disk session ID arrives later via the `current_session` WS event).
      if (sid) {
        currentSessionIdRef.current = sid;
        wsServiceRef.current?.setActiveSession(sid);
        setCurrentSessionId(sid);
        setViewingHistorySession(false);
      }
      console.log('[AIChatPage] Connected to agent, session:', sid, 'history:', raw.history_count);

      // connected fires on WS reconnection (NOT after __NEW_SESSION__ command).
      // Capture the flag now; the finally block uses it to decide whether to
      // also clear a stuck new-session spinner (in case current_session was
      // lost during an unstable connection at agent startup).
      const wasNewSession = newSessionPendingRef.current;
      // If newSession is still pending when WS (re)connects, it means the
      // __NEW_SESSION__ command was sent but never reached the agent (e.g. WS was
      // disconnected at that moment). Re-send to make sure it takes effect.
      if (wasNewSession) {
        aiWsService.newSession();
        // Do not hydrate from disk — would reload stale session and bump sessionReloadSeqRef.
        return;
      }
      pendingFilePushesRef.current = [];
      hydrateCurrentSession({ showLoading: true });
    });

    // Gateway sends individual "history" messages (one per historical msg)
    // right after "connected". Shape: {type:"history", role:"user"|"assistant", content:"..."}
    // (fields at top level, no content/data wrapper)
    // NOTE: If disk session was loaded successfully, skip these bare text messages
    // because the disk session already contains full data with events.
    const unsubHistory = aiWsService.on('history', (msg: AIWSMessage) => {
      const raw = msg as any;
      const role = raw.role || 'assistant';
      const content = raw.content || '';
      const msgType = raw.msg_type || raw.type || 'text';

      const files: any[] = Array.isArray(raw.files) ? raw.files : [];
      const contentStr = typeof content === 'string' ? content : '';
      const hasInlineMedia = (Array.isArray(raw.images) && raw.images.length > 0)
        || (Array.isArray(raw.attachments) && raw.attachments.length > 0)
        || files.length > 0
        || msgType === 'file_push'
        || contentStr.includes('<image>')
        || contentStr.includes('[File:');

      // During hydration/rebuild, buffer ALL history events (not just media).
      // This ensures messages survive refresh even when the agent disk session is
      // temporarily unavailable. If disk session loads successfully, non-media
      // buffered events are discarded; if it fails, they become the timeline.
      if (isHydratingSessionRef.current) {
        _logMediaDebug('ws-history-buffering', {
          msgType,
          mid: raw.message_id || raw.id,
          contentHead: contentStr.slice(0, 120),
          hasInlineMedia,
        });
        // Session boundary in WS history replay: drop anything before latest __NEW_SESSION__ marker.
        if (contentStr.trim() === '__NEW_SESSION__') {
          pendingHydrationMediaRef.current = [];
          _logMediaDebug('ws-history-hydrating-reset-on-new-session', {
            mid: raw.message_id || raw.id,
            msgType,
          });
          return;
        }
        // Buffer every history message during hydration
        const imageUrlsFromFilesHyd = files
          .filter((f: any) => !!f && (f.is_image || (typeof f.content_type === 'string' && f.content_type.startsWith('image/'))))
          .map((f: any) => _toWebMediaUrl(f.url || f.path || f.src || (f.filename ? `/uploads/${f.filename}` : '')))
          .filter((u: any) => typeof u === 'string' && u.length > 0);
        const imageUrlsFromHistoryHyd = Array.isArray(raw.images)
          ? raw.images.map((u: any) => _toWebMediaUrl(u)).filter((u: any) => typeof u === 'string' && u.length > 0)
          : [];
        const imageUrlsFromContentHyd: string[] = [];
        if (typeof content === 'string') {
          const reImg = /<image>(.*?)<\/image>/gi;
          let im: RegExpExecArray | null;
          while ((im = reImg.exec(content)) !== null) {
            const u = _toWebMediaUrl((im[1] || '').trim());
            if (u) imageUrlsFromContentHyd.push(u);
          }
          const reFile = /\[File:\s*.*?\]\((.*?)\)/g;
          let fm: RegExpExecArray | null;
          while ((fm = reFile.exec(content)) !== null) {
            const u = _toWebMediaUrl((fm[1] || '').trim());
            if (u) imageUrlsFromContentHyd.push(u);
          }
        }
        const imageUrlsHyd = Array.from(new Set([
          ...imageUrlsFromFilesHyd,
          ...imageUrlsFromHistoryHyd,
          ...imageUrlsFromContentHyd,
        ]));
        const nonImageFileAttachments: FileAttachment[] = files
          .filter((f: any) => f && !f.is_image && !(typeof f.content_type === 'string' && f.content_type.startsWith('image/')))
          .map((f: any) => {
            const sz = (b: number) => {
              if (!b || b < 1024) return `${b || 0} B`;
              if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KB`;
              return `${(b / (1024 * 1024)).toFixed(1)} MB`;
            };
            const rawUrl = f.url || f.path || f.src || (f.filename ? `/uploads/${f.filename}` : '');
            return {
              name: f.original_name || f.filename || 'file',
              size: sz(f.size),
              url: rawUrl || undefined,
              type: f.is_video ? 'video' as const : f.is_audio ? 'audio' as const : 'file' as const,
            };
          });
        const attachmentsHyd = [
          ...(Array.isArray(raw.attachments) ? raw.attachments : []),
          ...nonImageFileAttachments,
        ];
        const pendingMsg: ChatMessage = {
          role,
          content: _cleanDisplayContent(content),
          message_id: raw.message_id || raw.id || (raw.extra && (raw.extra.message_id || raw.extra.id)) || undefined,
          timestamp: raw.timestamp,
          type: msgType,
          images: imageUrlsHyd.length > 0 ? imageUrlsHyd : undefined,
          attachments: attachmentsHyd.length > 0 ? attachmentsHyd : undefined,
        };
        pendingHydrationMediaRef.current.push(pendingMsg);
        return;
      }

      // Before first canonical timeline bootstrap is done, do not append WS history.
      if (!sessionBootstrapDoneRef.current) {
        _logMediaDebug('ws-history-skip-before-bootstrap', {
          msgType,
          mid: raw.message_id || raw.id,
          contentHead: contentStr.slice(0, 120),
          hasInlineMedia,
        });
        return;
      }
      // After canonical disk snapshot is loaded, ignore WS history entirely.
      // Canonical timeline already finalized (snapshot + hydration-buffer merge).
      // EXCEPTION: file_push messages are persisted only in Gateway session, not in
      // runner disk session, so they must be appended to avoid disappearing on refresh.
      if (diskSessionLoadedRef.current) {
        if (msgType === 'file_push' || files.length > 0) {
          _logMediaDebug('ws-history-filepush-after-disk', {
            msgType,
            mid: raw.message_id || raw.id,
            filesCount: files.length,
            contentHead: contentStr.slice(0, 120),
          });
          // fall through to append logic below
        } else {
          if (!hasInlineMedia) {
            _logMediaDebug('ws-history-skip-nonmedia-after-disk', {
              msgType,
              mid: raw.message_id || raw.id,
              contentHead: contentStr.slice(0, 120),
              hasInlineMedia,
            });
          } else {
            _logMediaDebug('ws-history-skip-media-after-disk', {
              msgType,
              mid: raw.message_id || raw.id,
              contentHead: contentStr.slice(0, 120),
              hasInlineMedia,
            });
          }
          return;
        }
      }
      // (reachable only when disk session is not loaded)
      _logMediaDebug('ws-history-in', {
        mid: raw.message_id || raw.id,
        role,
        msgType,
        images: Array.isArray(raw.images) ? raw.images.length : 0,
        files: files.length,
        attachments: Array.isArray(raw.attachments) ? raw.attachments.length : 0,
        contentHead: typeof content === 'string' ? content.slice(0, 80) : '',
      });

      const imageUrlsFromFiles = files
        .filter((f: any) => !!f && (f.is_image || (typeof f.content_type === 'string' && f.content_type.startsWith('image/'))))
        .map((f: any) => {
          const rawPath = f.url || f.path || f.src || (f.filename ? `/uploads/${f.filename}` : '');
          if (typeof rawPath !== 'string' || !rawPath) return '';
          if (rawPath.startsWith('http://') || rawPath.startsWith('https://')) return rawPath;
          if (rawPath.startsWith('/uploads/')) return rawPath;
          if (rawPath.includes('/uploads/')) {
            return `/uploads/${rawPath.split('/uploads/').pop()}`;
          }
          if (rawPath.includes('\\uploads\\')) {
            return `/uploads/${rawPath.split('\\uploads\\').pop()?.replace(/\\/g, '/')}`;
          }
          return rawPath.startsWith('/') ? rawPath : `/uploads/${rawPath.split(/[/\\]/).pop()}`;
        })
        .filter((u: any) => typeof u === 'string' && u.length > 0);
      const imageUrlsFromHistory = Array.isArray(raw.images)
        ? raw.images.filter((u: any) => typeof u === 'string' && u.length > 0)
        : [];
      const imageUrls = Array.from(new Set([...imageUrlsFromFiles, ...imageUrlsFromHistory]));
      // Convert non-image files to structured FileAttachment objects so
      // file cards survive history replay after page refresh.
      const fileAttachments: FileAttachment[] = files
        .filter((f: any) => f && !f.is_image && !(typeof f.content_type === 'string' && f.content_type.startsWith('image/')))
        .map((f: any) => {
          const sz = (b: number) => {
            if (!b || b < 1024) return `${b || 0} B`;
            if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KB`;
            return `${(b / (1024 * 1024)).toFixed(1)} MB`;
          };
          const rawUrl = f.url || f.path || f.src || (f.filename ? `/uploads/${f.filename}` : '');
          return {
            name: f.original_name || f.filename || 'file',
            size: sz(f.size),
            url: rawUrl || undefined,
            type: f.is_video ? 'video' as const : f.is_audio ? 'audio' as const : 'file' as const,
          };
        });
      const attachments = [
        ...(Array.isArray(raw.attachments) ? raw.attachments : []),
        ...fileAttachments,
      ];

      if (content || imageUrls.length > 0 || attachments.length > 0 || files.length > 0) {
        _logMediaDebug('ws-history-mapped', {
          mid: raw.message_id || raw.id,
          msgType,
          mappedImages: imageUrls,
          mappedAttachments: attachments,
          mappedFilesCount: files.length,
          contentHead: contentStr.slice(0, 160),
        });
        const fileSig = files.map((f: any) => f.url || f.filename || f.original_name || '').join('|');
        const dedupKey = `history:${msgType}:${role}:${content}:${fileSig}:${imageUrls.join('|')}:${attachments.length}`;
        const now = Date.now();
        const lastSeen = filePushDedupRef.current.get(dedupKey) || 0;
        if (now - lastSeen < 2500) {
          return;
        }
        filePushDedupRef.current.set(dedupKey, now);

        const histMsg: ChatMessage = {
          role,
          content: _cleanDisplayContent(content),
          message_id: raw.message_id || raw.id || (raw.extra && (raw.extra.message_id || raw.extra.id)) || undefined,
          timestamp: raw.timestamp,
          type: msgType,
          images: imageUrls.length > 0 ? imageUrls : undefined,
          attachments: attachments.length > 0 ? attachments : undefined,
        };
        setTimeline(prev => {
          const k = _messageIdentityKey(histMsg);
          if (k) {
            const exists = prev.some((e) => e.kind === 'message' && _messageIdentityKey(e.data as ChatMessage) === k);
            if (exists) return prev;
          }
          return [...prev, { kind: 'message', data: histMsg, _uid: genUID() }];
        });
      }
    });

    // Agent (via GatewayAdapter) sends "current_session" when session changes
    const unsubCurrentSession = aiWsService.on('current_session', (msg: AIWSMessage) => {
      const data = msg.content || msg.data;
      if (viewingHistorySessionRef.current) {
        return;
      }
      const previousSid = currentSessionIdRef.current;
      let sid: string | null = null;
      if (typeof data === 'object') {
        if (data.data && typeof data.data === 'object') {
          sid = data.data.id;
        } else {
          sid = data.id;
        }
      }
      if (typeof data === 'string') {
        sid = data;
      }
      if (sid) {
        currentSessionIdRef.current = sid;
        wsServiceRef.current?.setActiveSession(sid);
        setCurrentSessionId(sid);
      }
      // Clear new-session loading — current_session fires when server confirms the new session
      if (newSessionPendingRef.current) {
        newSessionPendingRef.current = false;
        setIsLoadingSession(false);
        sessionBootstrapDoneRef.current = true;
        // Keep empty timeline from handleNewSession; disk may still hold the old session.
        return;
      }
      if (!viewingHistorySessionRef.current && (sid !== previousSid || !sessionBootstrapDoneRef.current)) {
        scheduleCurrentSessionHydration();
      }
    });

    const unsubSessionList = aiWsService.on('session_list', () => {
      // Session list arrived via WS — sidebar will refresh via HTTP
    });

    // Use history_sync as a trigger to reload the canonical current session
    // snapshot, rather than trusting the WS payload directly.
    const unsubHistorySync = aiWsService.on('history_sync', (msg: AIWSMessage) => {
      if (viewingHistorySessionRef.current || newSessionPendingRef.current) return;
      const data: any = msg.content || msg.data || {};
      const sid = typeof data === 'object' ? (data.session_id || data.id || null) : null;
      if (!sid || !currentSessionIdRef.current || sid === currentSessionIdRef.current || !sessionBootstrapDoneRef.current) {
        scheduleCurrentSessionHydration();
      }
    });

    // Agent pushes files/attachments to chat (via HTTP push API -> WS forward)
    const unsubFilePush = aiWsService.on('file_push', (msg: AIWSMessage) => {
      if (viewingHistorySessionRef.current) {
        return;
      }
      const raw = msg as any;
      const files: any[] = raw.files || [];
      const pushMsg: string = raw.message || '';

      // Inline file size formatter (cannot reference component methods from useEffect)
      const fmtSize = (b: number) => {
        if (!b || b < 1024) return `${b || 0} B`;
        if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KB`;
        return `${(b / (1024 * 1024)).toFixed(1)} MB`;
      };

      // Build content: message text + file descriptions
      let content = pushMsg;
      const imageUrls: string[] = [];
      const fileDescs: string[] = [];

      for (const f of files) {
        if (f.is_image) {
          imageUrls.push(f.url || `/uploads/${f.filename}`);
        } else {
          const media = f.is_video ? 'video' : f.is_audio ? 'audio' : 'file';
          fileDescs.push(`[File: ${f.original_name || f.filename} (${fmtSize(f.size)}) type=${media}](${f.url || `/uploads/${f.filename}`})`);
        }
      }

      if (fileDescs.length > 0) {
        content = content ? `${content}\n\n${fileDescs.join('\n')}` : fileDescs.join('\n');
      }

      if (content || imageUrls.length > 0) {
        const dedupKey = JSON.stringify({
          m: pushMsg || '',
          f: files.map((f: any) => f.url || f.filename || f.original_name || ''),
        });
        const now = Date.now();
        const lastSeen = filePushDedupRef.current.get(dedupKey) || 0;
        // Deduplicate immediate duplicated push (strict-mode / reconnection / double dispatch)
        if (now - lastSeen < 2500) {
          return;
        }
        filePushDedupRef.current.set(dedupKey, now);

        const assistantMsg: ChatMessage = {
          role: 'assistant',
          content: content || '(files)',
          message_id: raw.message_id || raw.id || (raw.extra && (raw.extra.message_id || raw.extra.id)) || undefined,
          timestamp: raw.timestamp || new Date().toISOString(),
          type: 'file_push',
          images: imageUrls.length > 0 ? imageUrls : undefined,
        };

        if (isHydratingSessionRef.current || !sessionBootstrapDoneRef.current) {
          queueBufferedFilePush(assistantMsg);
          return;
        }

        setTimeline(prev => appendFilePushMessage(prev, assistantMsg));
      }
    });

    return () => {
      unsubAuthExpired();
      unsubStatus();
      unsubStream();
      unsubMessage();
      unsubResponse();
      unsubToUserReply();
      unsubToUserFinal();
      unsubThought();
      unsubToolCall();
      unsubToolResult();
      unsubPlan();
      unsubSummaryStream();
      unsubCompressionProgress();
      unsubTokenStats();
      unsubState();
      unsubStatusEvt();
      unsubWake();
      unsubSleep();
      unsubInfo();
      unsubTurnStart();
      unsubTurnElapsed();
      unsubPromptUpdate();
      unsubOutputMedia();
      unsubConnected();
      unsubHistory();
      unsubCurrentSession();
      unsubSessionList();
      unsubHistorySync();
      unsubFilePush();
      if (sessionReloadTimerRef.current) {
        clearTimeout(sessionReloadTimerRef.current);
        sessionReloadTimerRef.current = null;
      }
      releaseAiWsService(agentId);
    };
  }, [agentId]);

  // ---- Helpers ----

  const _MEDIA_DEBUG = false;

  function _logMediaDebug(stage: string, payload: any) {
    if (!_MEDIA_DEBUG) return;
    try {
      console.log(`[AIChatPage][media-debug] ${stage}`, payload);
      console.log(`[AIChatPage][media-debug-json] ${stage} ${JSON.stringify(payload)}`);
    } catch {}
  }

  /** Normalize media path/url to a browser-consumable form. */
  function _toWebMediaUrl(input: any): string {
    if (typeof input !== 'string') return '';
    const raw = input.trim();
    if (!raw) return '';

    // Absolute URL
    if (/^https?:\/\//i.test(raw)) return raw;

    // Already a web upload path
    if (raw.startsWith('/uploads/')) return raw;

    // Windows/Linux absolute file path containing uploads segment
    const m = raw.match(/[\\/]uploads[\\/](.+)$/i);
    if (m && m[1]) {
      const rel = m[1].replace(/\\/g, '/');
      return `/uploads/${rel}`;
    }

    // Any other leading-slash absolute path should not be served directly;
    // fall back to upload basename route.
    if (raw.startsWith('/')) {
      const base = raw.split('/').pop();
      return base ? `/uploads/${base}` : '';
    }

    // Plain filename
    return `/uploads/${raw.split(/[/\\]/).pop()}`;
  }

  /** Remove storage/replay-only media markers from visible bubble text. */
  function _cleanDisplayContent(input: string): string {
    if (typeof input !== 'string' || !input) return input || '';
    let s = input;
    // Remove <image>...</image>
    s = s.replace(/\n?\s*<image>[\s\S]*?<\/image>/gi, '');
    // Remove markdown file markers [File: ...](...)
    s = s.replace(/\n?\s*\[File:\s*.*?\]\(.*?\)/g, '');
    // Collapse excessive blank lines
    s = s.replace(/\n{3,}/g, '\n\n').trim();
    return s;
  }

  /** Extract text content from a WS message (handles various nested formats) */
  function _extractContent(msg: AIWSMessage): string {
    const raw = msg.content ?? msg.data;
    if (typeof raw === 'string') return raw;
    if (typeof raw === 'object' && raw !== null) {
      // {sid:..., data:...} wrapper from GatewayAdapter
      if ('data' in raw && typeof raw.data === 'string') return raw.data;
      if ('content' in raw && typeof raw.content === 'string') return raw.content;
      if ('text' in raw && typeof raw.text === 'string') return raw.text;
    }
    return '';
  }

  /** Strong identity key. Only message_id is trusted across snapshots/sessions. */
  function _messageIdentityKey(msg: Partial<ChatMessage>): string {
    if (msg.message_id && String(msg.message_id).trim()) {
      return `mid:${String(msg.message_id).trim()}`;
    }
    // Fallback identity: role + normalised content. Without this, a user
    // message loaded from disk (no message_id) and the same message echoed
    // via WS history (also no message_id) are treated as different and both
    // rendered — causing the "last user message duplicated after refresh" bug.
    const role = msg.role || '';
    const rawContent = typeof msg.content === 'string' ? msg.content : '';
    // Strip file/image markers so the same message with/without markers matches.
    const normalized = rawContent
      .replace(/\[File:.*?\]\([^)]*\)/g, '')
      .replace(/<image>.*?<\/image>/gis, '')
      .trim()
      .slice(0, 200);
    if (role && normalized) {
      return `rc:${role}:${normalized}`;
    }
    return '';
  }

  /** Merge two messages with the same identity, preferring richer media payload. */
  function _mergeChatMessage(base: ChatMessage, incoming: ChatMessage): ChatMessage {
    const uniq = (arr?: string[]) => Array.from(new Set((arr || []).filter(Boolean)));
    const mergedImages = uniq([...(base.images || []), ...(incoming.images || [])]);
    const mergedOutputImages = uniq([...(base.output_images || []), ...(incoming.output_images || [])]);

    const mergeAttachments = (a?: FileAttachment[], b?: FileAttachment[]) => {
      const out: FileAttachment[] = [];
      const seen = new Set<string>();
      for (const item of [...(a || []), ...(b || [])]) {
        const k = `${item?.url || ''}|${item?.path || ''}|${item?.name || ''}`;
        if (!k || seen.has(k)) continue;
        seen.add(k);
        out.push(item);
      }
      return out;
    };

    const mergeAudio = (a?: Array<{ url: string; mime: string }>, b?: Array<{ url: string; mime: string }>) => {
      const out: Array<{ url: string; mime: string }> = [];
      const seen = new Set<string>();
      for (const item of [...(a || []), ...(b || [])]) {
        const k = `${item?.url || ''}|${item?.mime || ''}`;
        if (!item?.url || seen.has(k)) continue;
        seen.add(k);
        out.push(item);
      }
      return out;
    };

    const mergedAttachments = mergeAttachments(base.attachments, incoming.attachments);
    const mergedAudio = mergeAudio(base.output_audio, incoming.output_audio);

    return {
      ...base,
      ...incoming,
      content: (incoming.content && incoming.content.trim().length > 0) ? incoming.content : base.content,
      images: mergedImages.length > 0 ? mergedImages : undefined,
      attachments: mergedAttachments.length > 0 ? mergedAttachments : undefined,
      output_images: mergedOutputImages.length > 0 ? mergedOutputImages : undefined,
      output_audio: mergedAudio.length > 0 ? mergedAudio : undefined,
      message_id: incoming.message_id || base.message_id,
      type: incoming.type || base.type,
      timestamp: incoming.timestamp || base.timestamp,
    };
  }

  /**
   * Merge existing timeline into a fresh snapshot WITHOUT importing unmatched old messages.
   *
   * Why: unmatched old messages may belong to a different session (race before snapshot arrives),
   * which causes cross-session mixing after refresh.
   */
  function _mergeTimelineByMessageIdentity(prev: TimelineEntry[], snapshot: TimelineEntry[]): TimelineEntry[] {
    const next = [...snapshot];
    const msgIndex = new Map<string, number>();

    next.forEach((e, i) => {
      if (e.kind === 'message') {
        msgIndex.set(_messageIdentityKey(e.data as ChatMessage), i);
      }
    });

    // If snapshot has no identifiable messages yet, trust snapshot directly.
    if (msgIndex.size === 0) {
      return next;
    }

    for (const e of prev) {
      if (e.kind !== 'message') continue;
      const key = _messageIdentityKey(e.data as ChatMessage);
      if (!key) continue;
      const idx = msgIndex.get(key);
      if (idx === undefined) {
        // Intentionally skip unmatched old messages to avoid cross-session contamination.
        continue;
      }
      const merged = _mergeChatMessage(next[idx].data as ChatMessage, e.data as ChatMessage);
      next[idx] = { ...next[idx], data: merged };
    }

    return next;
  }

  /**
   * Build a timeline from a session's messages[] and events[] arrays.
   *
   * Both records carry an ISO timestamp. We simply merge them by timestamp
   * and walk the sorted stream:
   *   - events accumulate into a pending raw buffer
   *   - when a message is encountered, flush the buffer as a completed
   *     workflow block placed immediately before that message
   *   - leftover events at the end mean the agent is still working
   *
   * This is correct by design: runner.py stores thought BEFORE the
   * assistant message, and tool_call/tool_result AFTER it — so the
   * natural timestamp order already places each event in the right slot.
   */

  /**
   * Post-processing pass: merge orphaned tool_result events across workflow
   * block boundaries into the nearest preceding unmatched tool_call.
   *
   * Why this is needed:
   *   User messages (from input_hub or event_pipeline) act as timeline boundaries.
   *   When a user sends a message while the agent is mid-tool-call-chain, the
   *   timeline becomes: [...tool_call] ← workflow A | user_msg | [...tool_result] ← workflow B
   *   The tool_call and tool_result are in different workflow blocks because the
   *   user message flushed the pending events. Without this fix, the tool_call
   *   stays permanently "running" and the tool_result appears as a standalone card.
   *
   * This function scans ALL workflow blocks, collects unmatched tool_calls and
   * orphaned tool_results, then merges each orphan into the closest preceding
   * tool_call (by position in the timeline). Empty workflow blocks are removed.
   *
   * IMPORTANT: Creates new references for mutated entries to trigger React re-render.
   */
  function _mergeOrphanedToolResultsAcrossWorkflows(timeline: TimelineEntry[]): TimelineEntry[] {
    type ToolCallRef = {
      wfIdx: number;
      eventIdx: number;
      callId: string | null;
    };
    type OrphanResultRef = {
      wfIdx: number;
      eventIdx: number;
      resultId: string | null;
    };

    const toolCalls: ToolCallRef[] = [];
    const orphanResults: OrphanResultRef[] = [];

    // Collect all tool_calls (without result) and orphaned tool_results
    timeline.forEach((entry, tlIdx) => {
      if (entry.kind !== 'workflow') return;
      const wf = entry as Extract<TimelineEntry, { kind: 'workflow' }>;
      wf.data.events.forEach((evt, evtIdx) => {
        if (evt.type === 'tool_call' && !evt.result) {
          const callId = (typeof evt.content === 'object' && evt.content)
            ? (evt.content.id || evt.content.tool_use_id || null)
            : null;
          toolCalls.push({ wfIdx: tlIdx, eventIdx: evtIdx, callId });
        } else if (evt.type === 'tool_result') {
          const resultId = (typeof evt.content === 'object' && evt.content)
            ? (evt.content.id || evt.content.tool_use_id || null)
            : null;
          orphanResults.push({ wfIdx: tlIdx, eventIdx: evtIdx, resultId });
        }
      });
    });

    if (orphanResults.length === 0) return timeline;

    // Track which entries have been mutated so we can create new references
    const mutatedWfIndices = new Set<number>();

    // Merge each orphaned result into the nearest preceding unmatched tool_call
    const usedCallIndices = new Set<number>();
    for (const orphan of orphanResults) {
      // Find the closest preceding tool_call that hasn't been used yet
      let bestMatch: ToolCallRef | null = null;
      let bestCallListIdx = -1;
      for (let i = toolCalls.length - 1; i >= 0; i--) {
        const tc = toolCalls[i];
        const tcGlobalIdx = tc.wfIdx * 1000 + tc.eventIdx;
        const orphanGlobalIdx = orphan.wfIdx * 1000 + orphan.eventIdx;
        if (tcGlobalIdx < orphanGlobalIdx && !usedCallIndices.has(i)) {
          // Prefer exact id match, otherwise accept any unmatched
          if (orphan.resultId && tc.callId && orphan.resultId === tc.callId) {
            bestMatch = tc;
            bestCallListIdx = i;
            break; // Exact match found, use it
          }
          if (!bestMatch) {
            bestMatch = tc;
            bestCallListIdx = i;
          }
        }
      }

      if (bestMatch) {
        usedCallIndices.add(bestCallListIdx);
        // Create new event objects with the merged result
        const orphanEvt = (timeline[orphan.wfIdx] as Extract<TimelineEntry, { kind: 'workflow' }>).data.events[orphan.eventIdx];
        const resStr = (typeof orphanEvt.content === 'object' && orphanEvt.content)
          ? (typeof orphanEvt.content.result === 'string'
            ? orphanEvt.content.result
            : (orphanEvt.content.output || JSON.stringify(orphanEvt.content)))
          : String(orphanEvt.content || '');

        const mergedEvent: WorkflowEvent = {
          ...(timeline[bestMatch.wfIdx] as Extract<TimelineEntry, { kind: 'workflow' }>).data.events[bestMatch.eventIdx],
          result: resStr,
          resultStatus: (typeof orphanEvt.content === 'object' && orphanEvt.content && orphanEvt.content.error)
            ? 'error'
            : 'success',
        };

        if (bestMatch.wfIdx !== orphan.wfIdx) {
          // Cross-workflow merge: keep merged event in the LATER position (orphan's workflow)
          // so the tool flow doesn't visually "jump up" to an older block.
          // Replace orphan with merged event, remove original tool_call from its workflow.
          const tcWf = (timeline[bestMatch.wfIdx] as Extract<TimelineEntry, { kind: 'workflow' }>).data;
          const newTcEvents = [...tcWf.events];
          newTcEvents.splice(bestMatch.eventIdx, 1);
          tcWf.events = newTcEvents;
          mutatedWfIndices.add(bestMatch.wfIdx);

          const orphanWf = (timeline[orphan.wfIdx] as Extract<TimelineEntry, { kind: 'workflow' }>).data;
          const newOrphanEvents = [...orphanWf.events];
          newOrphanEvents[orphan.eventIdx] = mergedEvent;
          orphanWf.events = newOrphanEvents;
          mutatedWfIndices.add(orphan.wfIdx);
        } else {
          // Same-workflow merge: standard path
          const tcWf = (timeline[bestMatch.wfIdx] as Extract<TimelineEntry, { kind: 'workflow' }>).data;
          const newTcEvents = [...tcWf.events];
          newTcEvents[bestMatch.eventIdx] = mergedEvent;
          tcWf.events = newTcEvents;
          mutatedWfIndices.add(bestMatch.wfIdx);

          const orphanWf = (timeline[orphan.wfIdx] as Extract<TimelineEntry, { kind: 'workflow' }>).data;
          const newOrphanEvents = [...orphanWf.events];
          newOrphanEvents.splice(orphan.eventIdx, 1);
          orphanWf.events = newOrphanEvents;
          mutatedWfIndices.add(orphan.wfIdx);
        }
      }
    }

    // Create new timeline array with updated workflow references for React
    // Also remove empty workflow blocks
    const result: TimelineEntry[] = [];
    for (let i = 0; i < timeline.length; i++) {
      const entry = timeline[i];
      if (entry.kind === 'workflow') {
        const wf = entry as Extract<TimelineEntry, { kind: 'workflow' }>;
        if (wf.data.events.length === 0) continue; // Remove empty blocks
        if (mutatedWfIndices.has(i)) {
          // Create new reference to trigger re-render
          result.push({ kind: 'workflow', data: { ...wf.data, events: [...wf.data.events] }, _uid: entry._uid });
        } else {
          result.push(entry);
        }
      } else {
        result.push(entry);
      }
    }

    return result;
  }

  function _buildTimelineFromSession(
    messages: any[],
    events: any[],
    archivedMessages?: any[],
    archivedEvents?: any[],
  ): TimelineEntry[] {
    const timeline: TimelineEntry[] = [];
    const getTs = (value: any): number => {
      const ts = value?.timestamp ? new Date(value.timestamp).getTime() : NaN;
      return Number.isNaN(ts) ? Number.MAX_SAFE_INTEGER : ts;
    };

    const records: Array<
      | { kind: 'message'; item: any; ts: number; order: number }
      | { kind: 'event'; item: any; ts: number; order: number }
    > = [];

    messages.forEach((m, index) => {
      records.push({ kind: 'message', item: m, ts: getTs(m), order: index });
    });
    events.forEach((evt, index) => {
      records.push({ kind: 'event', item: evt, ts: getTs(evt), order: messages.length + index });
    });

    records.sort((a, b) => {
      if (a.ts !== b.ts) return a.ts - b.ts;
      if (a.kind !== b.kind) return a.kind === 'event' ? -1 : 1;
      return a.order - b.order;
    });

    let pendingRaw: any[] = [];

    const flushPendingWorkflow = (opts?: { completed?: boolean; elapsedMs?: number }) => {
      if (pendingRaw.length === 0) return;

      const workflowEvents: any[] = [];
      for (const rawEvt of pendingRaw) {
        if (rawEvt.type === 'prompt_update') {
          const p = rawEvt.data || {};
          const systemPrompt = typeof p.system_prompt === 'string' ? p.system_prompt : '';
          if (systemPrompt) {
            timeline.push({
              kind: 'prompt',
              data: {
                system_prompt: systemPrompt,
                dynamic_prefix: typeof p.dynamic_prefix === 'string' ? p.dynamic_prefix : '',
                changed: !!p.changed,
                timestamp: rawEvt.timestamp || new Date().toISOString(),
                diff: Array.isArray(p.diff) ? p.diff : undefined,
              },
              _uid: genUID(),
            });
          }
        } else {
          workflowEvents.push(rawEvt);
        }
      }

      pendingRaw = [];

      if (workflowEvents.length === 0) return;
      const wfEvents = _convertSessionEventsToWorkflow(workflowEvents);
      if (wfEvents.length === 0) return;
      timeline.push({
        kind: 'workflow',
        data: {
          events: wfEvents,
          status: opts?.completed === false ? 'working' : null,
          completed: opts?.completed !== false,
          elapsed_ms: opts?.elapsedMs,
        },
        _uid: genUID(),
      });
    };

    for (const record of records) {
      if (record.kind === 'event') {
        pendingRaw.push(record.item);
        continue;
      }

      const m = record.item;

      // System context_summary → must NOT act as a workflow boundary.
      // Convert to summary_stream event and keep its original position in the
      // pending event buffer so ordering stays timestamp-accurate.
      if (m.role === 'system' && m.type === 'context_summary') {
        const summaryText = typeof m.content === 'string' ? m.content : '';
        if (summaryText) {
          pendingRaw.push({
            type: 'summary_stream',
            data: {
              id: 'summary_history',
              text: summaryText,
              done: true,
            },
            timestamp: m.timestamp || new Date().toISOString(),
          });
        }
        continue;
      }

      if (m.type === 'api_sync') {
        // api_sync assistant messages carry the agent's final to_user text
        // synced from the agent disk. Only skip them when they are truly
        // empty placeholders; if they have content, render it so the reply
        // is visible after a restart/history reload.
        const syncContent = typeof m.content === 'string' ? m.content.trim() : '';
        if (!syncContent) {
          continue;
        }
        // Fall through and render as a normal assistant text message.
      }

      flushPendingWorkflow({
        completed: true,
        elapsedMs: typeof m.elapsed_ms === 'number' ? m.elapsed_ms : undefined,
      });

      const extra = (m && typeof m.extra === 'object' && m.extra !== null) ? m.extra : {};
      const rawImagesInput = Array.isArray(m.images)
        ? m.images
        : (Array.isArray(extra.images) ? extra.images : []);
      const rawImages = rawImagesInput
        .map((img: any) => {
          if (typeof img === 'string') return img;
          if (img && typeof img === 'object') return img.url || img.path || img.src || '';
          return '';
        })
        .map((u: any) => _toWebMediaUrl(u))
        .filter((u: any) => typeof u === 'string' && u.length > 0);
      let rawAttachments = Array.isArray(m.attachments)
        ? m.attachments
        : (Array.isArray(extra.attachments) ? extra.attachments : []);

      // file_push compatibility: derive image urls from files when needed
      const rawFiles = Array.isArray((m as any).files)
        ? (m as any).files
        : (Array.isArray((extra as any).files) ? (extra as any).files : []);
      // Convert non-image files to FileAttachment objects (survives content cleaning).
      const filesAsAttachments: FileAttachment[] = rawFiles
        .filter((f: any) => f && !f.is_image && !(typeof f.content_type === 'string' && f.content_type.startsWith('image/')))
        .map((f: any) => {
          const sz = (b: number) => {
            if (!b || b < 1024) return `${b || 0} B`;
            if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KB`;
            return `${(b / (1024 * 1024)).toFixed(1)} MB`;
          };
          const rawUrl = f.url || f.path || f.src || (f.filename ? `/uploads/${f.filename}` : '');
          return {
            name: f.original_name || f.filename || 'file',
            size: sz(f.size),
            url: rawUrl || undefined,
            type: f.is_video ? 'video' as const : f.is_audio ? 'audio' as const : 'file' as const,
          };
        });
      if (filesAsAttachments.length > 0) {
        rawAttachments = [...rawAttachments, ...filesAsAttachments];
      }
      const fileImages = rawFiles
        .filter((f: any) => !!f && (f.is_image || (typeof f.content_type === 'string' && f.content_type.startsWith('image/'))))
        .map((f: any) => _toWebMediaUrl(f.url || f.path || f.src || (f.filename ? `/uploads/${f.filename}` : '')))
        .filter((u: any) => typeof u === 'string' && u.length > 0);

      // Parse legacy markers from content: [File: xxx](url)
      const markerUrls: string[] = [];
      if (typeof m.content === 'string') {
        if (m.content.includes('[File:')) {
          const re = /\[File:\s*.*?\]\((.*?)\)/g;
          let mm: RegExpExecArray | null;
          while ((mm = re.exec(m.content)) !== null) {
            const u = (mm[1] || '').trim();
            if (u) markerUrls.push(u);
          }
        }
        const reImg = /<image>(.*?)<\/image>/gi;
        let im: RegExpExecArray | null;
        while ((im = reImg.exec(m.content)) !== null) {
          const u = (im[1] || '').trim();
          if (u) markerUrls.push(u);
        }
      }

      const mergedImages = Array.from(new Set([
        ...rawImages.filter((u: any) => typeof u === 'string' && u.length > 0),
        ...fileImages,
        ...markerUrls
          .map((u) => _toWebMediaUrl(u))
          .filter((u) => typeof u === 'string' && u.length > 0),
      ]));

      const rawOutputImages = Array.isArray((m as any).output_images)
        ? (m as any).output_images
        : (Array.isArray((extra as any).output_images) ? (extra as any).output_images : []);
      const rawOutputAudio = Array.isArray((m as any).output_audio)
        ? (m as any).output_audio
        : (Array.isArray((extra as any).output_audio) ? (extra as any).output_audio : []);

      const cleanedContent = typeof m.content === 'string'
        ? m.content
            .replace(/\n?\s*<image>.*?<\/image>/gis, '')
            .replace(/\n?\s*\[File:\s*.*?\]\(.*?\)/g, '')
            .trim()
        : m.content;

      // CRITICAL: Skip creating a message entry if the cleaned content is empty AND there's no
      // media/attachments. This prevents empty white dialogs from being rendered when a
      // message contains only placeholder markers (e.g. <image> or [File:]) that get cleaned.
      const hasMedia = mergedImages.length > 0 || rawAttachments.length > 0 || rawOutputImages.length > 0 || rawOutputAudio.length > 0;
      const hasContentAfterClean = cleanedContent && cleanedContent.length > 0;
      if (!hasContentAfterClean && !hasMedia) {
        // Skip - don't add empty message to the timeline
        continue;
      }

      // Merge same-content duplicates that are very close in time.
      // The window is widened to 30 seconds because session snapshots and
      // real-time WS events can race, causing the same user/assistant message
      // to appear twice after a page refresh. Intentional repeated messages
      // are usually minutes apart, so 30s is a safe balance.
      // For media-bearing messages we additionally require the prior entry
      // to also have media, to avoid merging a media-rich message into a
      // plain text placeholder.
      let dupIdx = -1;
      const mTs = m.timestamp ? new Date(m.timestamp).getTime() : NaN;
      const DEDUP_WINDOW_MS = 30000;
      for (let i = timeline.length - 1; i >= 0; i -= 1) {
        const entry = timeline[i];
        if (entry.kind !== 'message') continue;
        const d = entry.data as ChatMessage;
        if (hasMedia) {
          const dHasMedia = !!(
            (Array.isArray(d.images) && d.images.length > 0) ||
            (Array.isArray(d.attachments) && d.attachments.length > 0) ||
            (Array.isArray(d.output_images) && d.output_images.length > 0) ||
            (Array.isArray(d.output_audio) && d.output_audio.length > 0)
          );
          if (!dHasMedia) continue;
        }
        if (d.role !== m.role || d.content !== cleanedContent) continue;
        const dTs = d.timestamp ? new Date(d.timestamp).getTime() : NaN;
        const withinWindow = Number.isNaN(mTs) || Number.isNaN(dTs) || Math.abs(dTs - mTs) <= DEDUP_WINDOW_MS;
        if (withinWindow) {
          dupIdx = i;
          break;
        }
      }
      if (dupIdx >= 0) {
        // Merge media from duplicate into the existing entry (prefer richer payload)
        const dupEntry = timeline[dupIdx];
        const dupData = dupEntry.data as ChatMessage;
        const mergedImagesDedup = Array.from(new Set([
          ...(dupData.images || []),
          ...mergedImages,
        ]));
        const mergedAttachmentsDedup = Array.from(new Set([
          ...(dupData.attachments || []),
          ...rawAttachments,
        ]));
        timeline[dupIdx] = {
          ...dupEntry,
          kind: 'message',
          data: {
            ...dupData,
            images: mergedImagesDedup.length > 0 ? mergedImagesDedup : undefined,
            attachments: mergedAttachmentsDedup.length > 0 ? mergedAttachmentsDedup : undefined,
          },
        };
        continue;
      }

      // Only user and assistant messages reach here (system/hidden handled above)
      timeline.push({
        kind: 'message',
        data: {
          role: m.role,
          content: cleanedContent,
          message_id: (m as any).message_id || (m as any).id || ((m as any).extra && ((m as any).extra.message_id || (m as any).extra.id)) || undefined,
          timestamp: m.timestamp,
          type: m.type,
          images: mergedImages.length > 0 ? mergedImages : undefined,
          attachments: rawAttachments.length > 0 ? rawAttachments : undefined,
          output_images: rawOutputImages.length > 0 ? rawOutputImages : undefined,
          output_audio: rawOutputAudio.length > 0 ? rawOutputAudio : undefined,
        },
        _uid: genUID(),
      });
    }

    // Remaining trailing events mean the agent was still working when the
    // session snapshot was taken.
    flushPendingWorkflow({ completed: false });

    // CRITICAL: After building the timeline, orphaned tool_result events may be
    // stuck in separate workflow blocks because user messages act as boundaries.
    // This post-processing pass merges them back into the nearest unmatched
    // tool_call across workflow block boundaries, so the UI shows a complete
    // tool_call card instead of a permanently "running" one.
    const mergedTimeline = _mergeOrphanedToolResultsAcrossWorkflows(timeline);

    // Prepend a single collapsed "已归档" section if the session has
    // archived content (messages / events removed by context compression
    // but preserved for UI display). Only do this for the outermost call
    // — recursive inner calls (for the archived sub-timeline itself) pass
    // undefined archived fields, so we don't nest.
    if (
      (archivedMessages && archivedMessages.length > 0) ||
      (archivedEvents && archivedEvents.length > 0)
    ) {
      const inner = _buildTimelineFromSession(
        archivedMessages || [],
        archivedEvents || [],
      );
      // Defensive: never let archived_section nest inside itself.
      const innerFiltered = inner.filter((e) => e.kind !== 'archived_section');
      if (innerFiltered.length > 0) {
        const messageCount = archivedMessages?.length || 0;
        const eventCount = archivedEvents?.length || 0;
        const allTs = [
          ...((archivedMessages || [])
            .map((m: any) => m?.timestamp)
            .filter((s: any) => typeof s === 'string')),
          ...((archivedEvents || [])
            .map((e: any) => e?.timestamp)
            .filter((s: any) => typeof s === 'string')),
        ].sort();
        mergedTimeline.unshift({
          kind: 'archived_section',
          data: {
            messageCount,
            eventCount,
            entries: innerFiltered,
            startTs: allTs[0],
            endTs: allTs[allTs.length - 1],
          },
          _uid: genUID(),
        });
      }
    }

    return mergedTimeline;
  }

  /**
   * Convert raw session events [{type, data}, ...] into WorkflowEvent[],
   * merging tool_result into matching tool_call entries.
   */
  function _convertSessionEventsToWorkflow(rawEvents: any[]): WorkflowEvent[] {
    const result: WorkflowEvent[] = [];

    for (const raw of rawEvents) {
      const type = raw.type;
      const data = raw.data || {};
      const ts = typeof raw.timestamp === 'number'
        ? raw.timestamp
        : (typeof raw.timestamp === 'string'
          ? new Date(raw.timestamp).getTime()
          : Date.now());
      const eventTimestamp = Number.isNaN(ts) ? Date.now() : ts;

      if (type === 'thought') {
        const text = typeof data === 'string' ? data : (data.text || data.content || '');
        if (!text) continue;
        // Merge consecutive thoughts
        const last = result[result.length - 1];
        if (last && last.type === 'thought') {
          last.content += '\n' + text;
        } else {
          result.push({ _uid: genUID(), type: 'thought', content: text, timestamp: eventTimestamp });
        }
      } else if (type === 'tool_call') {
        result.push({
          _uid: genUID(),
          type: 'tool_call',
          content: {
            id: data.id,
            name: data.name || data.tool || 'Tool',
            args: data.args || data.arguments || data.input,
          },
          timestamp: eventTimestamp,
        });
      } else if (type === 'tool_result') {
        // Merge into matching tool_call
        const resultId = data.id || data.tool_use_id;
        let merged = false;
        for (let i = result.length - 1; i >= 0; i--) {
          const evt = result[i];
          if (evt.type === 'tool_call' && !evt.result) {
            const callId = evt.content?.id;
            if (!resultId || !callId || resultId === callId) {
              const resStr = typeof data.result === 'string'
                ? data.result
                : (typeof data.result === 'object' ? JSON.stringify(data.result) : (data.output || JSON.stringify(data)));
              evt.result = resStr;
              evt.resultStatus = data.error ? 'error' : 'success';
              merged = true;
              break;
            }
          }
        }
        if (!merged) {
          // Standalone result (fallback)
          const resStr = typeof data.result === 'string' ? data.result : JSON.stringify(data.result || data);
          result.push({
            _uid: genUID(),
            type: 'tool_result',
            content: { name: data.name || 'Tool', result: resStr },
            timestamp: eventTimestamp,
          });
        }
      } else if (type === 'plan') {
        // data is {id, text} from Runner (runner.py:809)
        const planContent = typeof data === 'string' ? data : (data.text || data.content || data);
        const steps = parsePlanContent(planContent);
        if (steps.length > 0) {
          result.push({ _uid: genUID(), type: 'plan', content: steps, timestamp: eventTimestamp });
        }
      } else if (type === 'summary_stream') {
        const streamData = typeof data === 'object' && data !== null ? data : {};
        result.push({
          _uid: genUID(),
          type: 'summary_stream',
          content: { id: streamData.id || 'summary_history', text: streamData.text || '', done: !!streamData.done },
          timestamp: eventTimestamp,
        });
      } else if (type === 'info') {
        const detailed =
          typeof data === 'string'
            ? { text: data }
            : (typeof data === 'object' && data !== null ? data : { text: String(data) });
        // Skip system info prompts — these are internal state messages
        // that shouldn't display in the workflow UI (e.g. "Agent entering
        // wait mode", "Context summary generated", "Workflow started").
        if (detailed.text && /entering wait mode|listening for events|Workflow started|Context summary|Context compressed|compression skipped|injected into prompt/i.test(detailed.text)) {
          continue;
        }
        result.push({ _uid: genUID(), type: 'info', content: detailed, timestamp: eventTimestamp });
      }
      // Skip other event types (option, etc.) — or add handling as needed
    }

    return result;
  }

  // ---- Actions ----

  // Whether the agent is currently busy and cannot accept a message inline.
  // Messages sent while busy are parked in the pending queue and auto-flushed
  // once the agent returns to idle (or sent immediately via "Send now").
  const isAgentBusy = useMemo(
    () => isStreaming || agentStatus === 'working' || agentStatus === 'thinking',
    [isStreaming, agentStatus],
  );

  /**
   * Build the WS payload + display message from raw input state, then deliver
   * it through the WebSocket and append the user bubble to the timeline.
   *
   * `clearInputState` controls whether the composer (input text/images/attachments)
   * is cleared afterwards — it should be false when delivering a queued pending
   * message (which has its own snapshot of the data) and true for a live send.
   *
   * `salvageStream` controls whether unfinalized streaming text is salvaged into
   * the timeline before the new turn — only desired for a live send, not for
   * flushing the pending queue.
   */
  const deliverMessage = useCallback((
    payload: {
      text: string;
      images: string[];
      attachments: UploadedFile[];
    },
    opts?: { clearInputState?: boolean; salvageStream?: boolean },
  ) => {
    const { text, images: imgState, attachments: attState } = payload;
    const clearInputState = opts?.clearInputState ?? true;
    const salvageStream = opts?.salvageStream ?? true;

    if (!text && imgState.length === 0 && attState.length === 0) return;

    // Build attachment description to include in WS message text (for Agent)
    const nonImageAttachments = attState.filter(a => !a.is_image);

    // Collect all image paths (from images state + image attachments)
    const allImages = [
      ...imgState,
      ...attState.filter(a => a.is_image).map(a => a.path),
    ];

    let wsText = text;
    if (nonImageAttachments.length > 0) {
      const fileList = nonImageAttachments
        .map(a => {
          const media = a.is_video ? 'video' : a.is_audio ? 'audio' : 'file';
          return `[File: ${a.original_name} (${_formatFileSize(a.size)}) path=${a.path} type=${media}]`;
        })
        .join('\n');
      if (fileList) {
        wsText = wsText ? `${wsText}\n\n${fileList}` : fileList;
      }
    }

    // IMPORTANT: agent-side disk sessions (used by /agent-sessions/current) are text-centric.
    // Embed image markers into wsText so image messages can be reconstructed after refresh.
    if (allImages.length > 0) {
      const imageMarkers = allImages
        .map((u) => _toWebMediaUrl(u))
        .filter((u) => !!u)
        .map((u) => `<image>${u}</image>`)
        .join('\n');
      if (imageMarkers) {
        wsText = wsText ? `${wsText}\n\n${imageMarkers}` : imageMarkers;
      }
    }

    // Build structured file attachments for display
    const fileAtts: FileAttachment[] = nonImageAttachments.map(a => ({
      name: a.original_name,
      size: _formatFileSize(a.size),
      path: a.path,
      url: a.url,
      type: a.is_video ? 'video' : a.is_audio ? 'audio' : 'file',
    }));

    // Add user message to timeline (display text without [File: ...],
    // attachments stored separately for card rendering)
    const userMsg: ChatMessage = {
      role: 'user',
      content: text, // clean text without [File: ...] for display
      timestamp: new Date().toISOString(),
      images: allImages.length > 0 ? allImages : undefined,
      attachments: fileAtts.length > 0 ? fileAtts : undefined,
    };
    setTimeline(prev => [...prev, {
      kind: 'message',
      data: userMsg,
      _uid: genUID(),
    }]);

    _logMediaDebug('handleSend-payload', {
      text,
      wsTextHead: wsText.slice(0, 200),
      allImages,
      nonImageAttachmentCount: nonImageAttachments.length,
      viewingHistorySession,
      currentSessionId,
    });

    // Send via WS (full text with [File: ...] so Agent gets file paths)
    if (viewingHistorySession && currentSessionId) {
      // User is viewing a historical session — switch agent context to it first
      wsServiceRef.current?.switchAndReply(currentSessionId, wsText);
      setViewingHistorySession(false); // now we're in this session
    } else {
      wsServiceRef.current?.sendMessage(wsText, allImages.length > 0 ? allImages : undefined, nonImageAttachments);
    }

    if (clearInputState) {
      // Clear input
      setInputText('');
      setImages([]);
      setAttachments([]);

      // Reset textarea height to auto-shrink after send
      if (inputRef.current) {
        inputRef.current.style.height = 'auto';
      }
    }

    // Reset streaming state for the new turn.
    // IMPORTANT: If the previous turn produced naked streaming text (content outside
    // any tag) that was never finalized by a 'message'/'response' event, we must
    // save it to the timeline BEFORE clearing — otherwise it disappears the moment
    // the user hits Send. The turn_start salvage logic can't help here because
    // handleSend clears streamingTextRef before turn_start arrives.
    if (salvageStream && streamingTextRef.current && !finalizingRef.current) {
      const salvaged = streamingTextRef.current;
      if (salvaged.trim().length > 0) {
        const salvagedMsg: ChatMessage = {
          role: 'assistant',
          content: salvaged,
          timestamp: new Date().toISOString(),
        };
        setTimeline(prev => finalizeWorkflowAndAddMessage(prev, salvagedMsg));
      }
    }
    // NOTE: Do NOT touch finalizingRef here. If the previous turn's handleFinal
    // is still within its 300ms guard window (finalizingRef === true), clearing it
    // here would let late-arriving debounced stream chunks from turn N bleed into
    // turn N+1 and then get wiped by the incoming turn_start — causing the AI
    // reply to visually disappear. finalizingRef is reset by either:
    //   (a) the 300ms setTimeout inside handleFinal (normal path), or
    //   (b) the turn_start handler below (which fires at the actual start of the
    //       new agent turn, well after the previous turn is fully settled).
    if (salvageStream) {
      streamingTextRef.current = '';
      setStreamingText('');
      setIsStreaming(false);
    }
  }, [viewingHistorySession, currentSessionId]);

  const handleSend = () => {
    const text = inputText.trim();
    if (!text && images.length === 0 && attachments.length === 0) return;

    // When the agent is busy, park the message in the pending queue instead of
    // delivering immediately. It will be auto-sent once the agent returns to
    // idle, or the user can force it through right away with "Send now".
    if (isAgentBusy) {
      const snapshot: PendingMessage = {
        id: genUID(),
        text,
        images: [...images],
        attachments: attachments.map(a => ({ ...a })),
        fileAtts: attachments
          .filter(a => !a.is_image)
          .map(a => ({
            name: a.original_name,
            size: _formatFileSize(a.size),
            path: a.path,
            url: a.url,
            type: a.is_video ? 'video' : a.is_audio ? 'audio' : 'file',
          })),
      };
      setPendingMessages(prev => [...prev, snapshot]);
      // Clear the composer only — do not touch streaming state (agent is busy).
      setInputText('');
      setImages([]);
      setAttachments([]);
      if (inputRef.current) {
        inputRef.current.style.height = 'auto';
      }
      return;
    }

    deliverMessage(
      { text, images, attachments },
      { clearInputState: true, salvageStream: true },
    );
  };

  // Send a queued pending message immediately, even while the agent is working.
  // The backend input_hub already accumulates working-state messages and
  // injects them into the next turn via event_pipeline (runner.py), so the
  // agent will receive it without waiting for idle.
  const handleSendPendingNow = useCallback((id: string) => {
    const target = pendingMessagesRef.current.find(m => m.id === id);
    if (!target) return;
    // Remove from the pending queue first so it doesn't get double-sent by the
    // idle auto-flush.
    setPendingMessages(prev => prev.filter(m => m.id !== id));
    deliverMessage(
      { text: target.text, images: target.images, attachments: target.attachments },
      { clearInputState: false, salvageStream: false },
    );
  }, [deliverMessage]);

  // Cancel / remove a pending message without sending it.
  const handleCancelPending = useCallback((id: string) => {
    setPendingMessages(prev => prev.filter(m => m.id !== id));
  }, []);

  // Send all queued messages immediately, in order. Used by the header's
  // "Send all" button. We snapshot via ref to avoid a stale closure and
  // clear the queue first so the idle auto-flush effect cannot re-trigger
  // and double-send.
  const handleSendAllPending = useCallback(() => {
    const queue = pendingMessagesRef.current;
    if (queue.length === 0) return;
    const toFlush = [...queue];
    setPendingMessages([]);
    isFlushingPendingRef.current = true;
    for (const msg of toFlush) {
      deliverMessage(
        { text: msg.text, images: msg.images, attachments: msg.attachments },
        { clearInputState: false, salvageStream: false },
      );
    }
    setTimeout(() => { isFlushingPendingRef.current = false; }, 0);
  }, [deliverMessage]);

  // Clear the entire queue without sending anything.
  const handleCancelAllPending = useCallback(() => {
    setPendingMessages([]);
  }, []);

  // Auto-flush the pending queue once the agent returns to idle. We mirror
  // pendingMessages into a ref so the effect can read the latest snapshot
  // without depending on the array (which would re-fire on every append and
  // risk double delivery). A flush guard prevents re-entrancy.
  useEffect(() => {
    if (isAgentBusy) return;
    if (isFlushingPendingRef.current) return;
    const queue = pendingMessagesRef.current;
    if (queue.length === 0) return;
    // Agent is idle and there are parked messages — deliver them in order.
    isFlushingPendingRef.current = true;
    const toFlush = [...queue];
    setPendingMessages([]);
    for (const msg of toFlush) {
      deliverMessage(
        { text: msg.text, images: msg.images, attachments: msg.attachments },
        { clearInputState: false, salvageStream: false },
      );
    }
    // Defer clearing the guard to the next tick so a synchronous re-entry
    // (e.g. agentStatus flicker) cannot trigger a second flush.
    setTimeout(() => { isFlushingPendingRef.current = false; }, 0);
  }, [isAgentBusy, deliverMessage]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleStop = () => {
    wsServiceRef.current?.stopTask();
    // Finalize any streaming text
    const currentText = streamingTextRef.current;
    if (currentText) {
      const stoppedMsg: ChatMessage = {
        role: 'assistant',
        content: currentText + '\n\n[Stopped]',
        timestamp: new Date().toISOString(),
      };
      setTimeline(prev => finalizeWorkflowAndAddMessage(prev, stoppedMsg));
    }
    streamingTextRef.current = '';
    setStreamingText('');
    setIsStreaming(false);
    finalizingRef.current = false;
    setAgentStatus('connected');
  };

  const handleCompressContext = () => {
    if (isCompressingContext || isLoadingSession) return;
    setIsCompressingContext(true);
    // Local optimistic feedback: show immediate workflow info even before backend emits.
    setTimeline(prev => appendWorkflowEvent(prev, {
      type: 'summary_stream',
      content: { id: 'compress_pending', text: 'Generating context summary...', done: false, pending: true },
      timestamp: Date.now(),
    }, 'Summarizing...'));
    wsServiceRef.current?.compressContext();
    // Fallback timeout in case backend never responds (e.g. crash).
    // Normal flow clears this via summary_stream done or context_compressed event.
    setTimeout(() => setIsCompressingContext(false), 120000);
  };

  const handleNewSession = () => {
    const previousSid = currentSessionIdRef.current;
    newSessionPendingRef.current = true;
    setIsLoadingSession(true);
    setSessionLoadingLabel(t('aiChat.creatingSession'));
    // Clear session filter so responses with the new sid are not dropped while
    // we wait for current_session / HTTP fallback to set the canonical id.
    currentSessionIdRef.current = null;
    setCurrentSessionId(null);
    wsServiceRef.current?.setActiveSession(null);
    wsServiceRef.current?.newSession();
    setTimeline([]);
    streamingTextRef.current = '';
    setStreamingText('');
    finalizingRef.current = false;
    diskSessionLoadedRef.current = false;
    pendingFilePushesRef.current = [];
    pendingHydrationMediaRef.current = [];
    sessionBootstrapDoneRef.current = false;
    viewingHistorySessionRef.current = false;
    setViewingHistorySession(false);
    setPlanSteps([]);
    setTokenStats(null);
    setImages([]);
    setAttachments([]);
    // Reset lazy loading state
    setHasMoreHistory(false);
    setIsLoadingMore(false);
    historyOffsetRef.current = 0;
    loadingSessionIdRef.current = null;

    // Fallback: if Runner/Gateway WS ack (current_session) is delayed or lost,
    // confirm via HTTP — but never reload the OLD session back into the UI.
    const fallbackSeq = ++newSessionFallbackSeqRef.current;
    const finishNewSession = () => {
      if (fallbackSeq !== newSessionFallbackSeqRef.current) return;
      if (!newSessionPendingRef.current) return;
      newSessionPendingRef.current = false;
      setIsLoadingSession(false);
      sessionBootstrapDoneRef.current = true;
    };
    const hardTimeout = window.setTimeout(finishNewSession, 8000);

    window.setTimeout(async () => {
      if (fallbackSeq !== newSessionFallbackSeqRef.current || !newSessionPendingRef.current) return;
      if (viewingHistorySessionRef.current) return;
      try {
        const resp = await agentSessionAPI.getCurrentSession(agentId, 0, 50);
        if (fallbackSeq !== newSessionFallbackSeqRef.current || !newSessionPendingRef.current) return;
        const currentSid = resp.current_session_id;
        const session = resp.session;
        const msgCount = session?.messages?.length || 0;
        const sidChanged = !!currentSid && currentSid !== previousSid;
        if (sidChanged || msgCount === 0) {
          const entries = _buildTimelineFromSession(session?.messages || [], session?.events || []);
          setTimeline(entries);
          if (currentSid) {
            currentSessionIdRef.current = currentSid;
            wsServiceRef.current?.setActiveSession(currentSid);
            setCurrentSessionId(currentSid);
          }
          diskSessionLoadedRef.current = msgCount > 0;
          historyOffsetRef.current = msgCount;
          setHasMoreHistory(session?.has_more ?? false);
        } else {
          // Same session id still on disk — keep empty timeline, just unblock UI.
          setTimeline([]);
          diskSessionLoadedRef.current = false;
        }
        finishNewSession();
      } catch (err: any) {
        console.warn('[AIChatPage] new session HTTP fallback failed:', err?.message || err);
        finishNewSession();
      } finally {
        window.clearTimeout(hardTimeout);
      }
    }, 1500);
  };

  const handleViewSession = async (sessionId: string) => {
    // If the user clicks the CURRENT session (e.g. switching back from a
    // history view), re-hydrate from the Gateway cache rather than treating
    // it as a read-only history view. This preserves the latest to_user
    // replies that may not yet be flushed to disk.
    if (sessionId === currentSessionIdRef.current) {
      viewingHistorySessionRef.current = false;
      setViewingHistorySession(false);
      await hydrateCurrentSession();
      return;
    }
    setIsLoadingSession(true);
    setSessionLoadingLabel(t('aiChat.loadingSession'));
    pendingFilePushesRef.current = [];
    try {
      const resp = await agentSessionAPI.getSessionHistoryPaged(agentId, sessionId, 0, 50);
      const session = resp.session;
      if (session) {
        const entries = _buildTimelineFromSession(
          session.messages || [],
          session.events || [],
        );
        setTimeline(entries);
        setCurrentSessionId(sessionId);
        currentSessionIdRef.current = sessionId;
        viewingHistorySessionRef.current = true;
        setViewingHistorySession(true); // mark as viewing history (not the agent's current session)
        setStreamingText('');
        setIsStreaming(false);
        // Set up lazy loading state
        loadingSessionIdRef.current = sessionId;
        historyOffsetRef.current = session.messages?.length || 0;
        setHasMoreHistory(session.has_more ?? false);
      }
    } catch (err: any) {
      console.error('[AIChatPage] Failed to load session:', err);
    } finally {
      setIsLoadingSession(false);
    }
  };

  const handleSwitchAndReply = async (sessionId: string) => {
    wsServiceRef.current?.switchAndReply(sessionId, '');
    pendingFilePushesRef.current = [];
    currentSessionIdRef.current = sessionId;
    viewingHistorySessionRef.current = false;
    setCurrentSessionId(sessionId);
    setViewingHistorySession(false); // actively switched, no longer just "viewing"
    // Reload the session's history so the timeline shows the agent's previous
    // replies. Without this, switching back to a session leaves the timeline
    // showing the *other* session's content (or empty), because the
    // current_session WS event won't re-hydrate when sid === previousSid.
    try {
      const resp = await agentSessionAPI.getSessionHistoryPaged(agentId, sessionId, 0, 50);
      const session = resp.session;
      if (session) {
        const entries = _buildTimelineFromSession(
          session.messages || [],
          session.events || [],
        );
        setTimeline(entries);
        historyOffsetRef.current = session.messages?.length || 0;
        setHasMoreHistory(session.has_more ?? false);
      }
    } catch (err: any) {
      console.error('[AIChatPage] Failed to reload session after switch:', err);
    }
  };

  // ---- Image upload ----

  const handleImageUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (!files) return;

    for (const file of Array.from(files) as File[]) {
      try {
        const resp = await agentSessionAPI.uploadImage(agentId, file);
        // Store the web-relative URL (/uploads/filename) instead of the
        // absolute filesystem path so MessageBubble can use it directly as
        // an <img src> without platform-dependent path splitting.
        setImages(prev => [...prev, resp.url]);
      } catch (err: any) {
        console.error('[AIChatPage] Image upload failed:', err);
      }
    }

    // Reset file input
    if (fileInputRef.current) fileInputRef.current.value = '';
  };

  const removeImage = (index: number) => {
    setImages(prev => prev.filter((_, i) => i !== index));
  };

  const removeAttachment = (index: number) => {
    setAttachments(prev => prev.filter((_, i) => i !== index));
  };

  // ---- Drag & Drop upload ----

  const handleDragEnter = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    dragCounterRef.current++;
    if (dragCounterRef.current === 1) {
      setIsDragOver(true);
    }
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    dragCounterRef.current--;
    if (dragCounterRef.current === 0) {
      setIsDragOver(false);
    }
  }, []);

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
  }, []);

  const handleDrop = useCallback(async (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    dragCounterRef.current = 0;
    setIsDragOver(false);

    const droppedFiles = e.dataTransfer.files;
    if (!droppedFiles || droppedFiles.length === 0) return;

    setIsUploading(true);
    const fileArray = Array.from(droppedFiles) as File[];

    try {
      if (fileArray.length === 1) {
        // Single file upload
        const file = fileArray[0];
        const resp = await agentSessionAPI.uploadFile(agentId, file);
        if (resp.is_image) {
          setImages(prev => [...prev, resp.url]);
        } else {
          setAttachments(prev => [...prev, resp]);
        }
      } else {
        // Batch upload
        const resp = await agentSessionAPI.uploadFiles(agentId, fileArray);
        for (const f of resp.files) {
          if (f.is_image) {
            setImages(prev => [...prev, f.url]);
          } else {
            setAttachments(prev => [...prev, f]);
          }
        }
      }
    } catch (err: any) {
      console.error('[AIChatPage] Drop upload failed:', err);
    } finally {
      setIsUploading(false);
    }
  }, [agentId]);

  const handlePaste = useCallback(async (e: React.ClipboardEvent) => {
    const files = Array.from(e.clipboardData.items)
      .filter(item => item.kind === 'file')
      .map(item => item.getAsFile())
      .filter((f): f is File => f !== null);
    if (files.length === 0) return;
    e.preventDefault();

    setIsUploading(true);
    try {
      if (files.length === 1) {
        const resp = await agentSessionAPI.uploadFile(agentId, files[0]);
        if (resp.is_image) {
          setImages(prev => [...prev, resp.url]);
        } else {
          setAttachments(prev => [...prev, resp]);
        }
      } else {
        const resp = await agentSessionAPI.uploadFiles(agentId, files);
        for (const f of resp.files) {
          if (f.is_image) {
            setImages(prev => [...prev, f.url]);
          } else {
            setAttachments(prev => [...prev, f]);
          }
        }
      }
    } catch (err: any) {
      console.error('[AIChatPage] Paste upload failed:', err);
    } finally {
      setIsUploading(false);
    }
  }, [agentId]);

  // ---- File size formatting helper ----

  function _formatFileSize(bytes: number): string {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
    return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
  }

  // ---- Auth expiry handler ----
  const handleReLogin = useCallback(() => {
    authAPI.logout();   // clears stored token
    onBack();           // return to main view (triggers login screen)
  }, [onBack]);

  // ---- Guard ----
  if (!agentId) {
    onBack();
    return null;
  }

  // ---- Render helpers ----

  // Check if timeline has any messages (for empty state)
  const hasContent = timeline.length > 0 || isStreaming;

  // ---- Render ----
  return (
    <div
      className="flex-1 flex h-full w-full bg-bgLight overflow-hidden relative"
      onDragEnter={handleDragEnter}
      onDragLeave={handleDragLeave}
      onDragOver={handleDragOver}
      onDrop={handleDrop}
    >
      {/* Drag overlay */}
      {isDragOver && (
        <div className="absolute inset-0 z-50 flex items-center justify-center bg-primary/10 border-2 border-dashed border-primary rounded-lg pointer-events-none">
          <div className="flex flex-col items-center gap-2 text-primary">
            <Upload size={48} className="opacity-70" />
            <p className="text-lg font-medium">Drop files here to upload</p>
            <p className="text-sm opacity-70">Images, documents, and other files</p>
          </div>
        </div>
      )}

      {/* Agent starting overlay */}
      {(agentStatus === 'agent-starting' || wsStatus === 'agent-starting') && (
        <div className="absolute inset-0 z-40 flex items-center justify-center bg-bgLight/80 backdrop-blur-sm">
          <div className="flex flex-col items-center gap-3 p-6 bg-panel border border-border rounded-2xl shadow-xl">
            <Loader2 size={40} className="text-yellow-500 animate-spin" />
            <p className="text-base font-medium text-textMain">{t('chat.agentStarting')}</p>
            <p className="text-xs text-textMuted">{t('chat.agentStartingHint')}</p>
          </div>
        </div>
      )}

      {/* Session Sidebar */}
      <SessionSidebar
        agentId={agentId}
        currentSessionId={currentSessionId}
        onViewSession={handleViewSession}
        onNewSession={handleNewSession}
        onSwitchAndReply={handleSwitchAndReply}
        isOpen={sessionSidebarOpen}
        onClose={() => setSessionSidebarOpen(false)}
        sessionTitleUpdate={sessionTitleUpdate}
      />

      {showContextViewer && (
        <ContextViewer
          agentId={agentId}
          agentName={agentProfile?.agent_name || agentId}
          sessionId={currentSessionId}
          provider={agentProvider}
          apiProtocol={agentApiProtocol}
          model={modelName}
          cwd={agentCwd}
          tokenStats={tokenStats}
          entries={contextEntries}
          onClose={() => setShowContextViewer(false)}
        />
      )}

      {/* Main Chat Area */}
      <div className="flex-1 flex flex-col h-full min-w-0">
        {/* Header */}
        <div className="p-2 sm:p-3 border-b border-border bg-panel flex-shrink-0">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-1.5 sm:gap-3 min-w-0">
              <button
                onClick={onBack}
                className="p-1.5 sm:p-2 hover:bg-primary/10 rounded-lg transition-colors flex-shrink-0"
              >
                <ArrowLeft size={20} className="text-textMuted" />
              </button>
              <button
                onClick={() => setSessionSidebarOpen(!sessionSidebarOpen)}
                className="p-1.5 sm:p-2 hover:bg-primary/10 rounded-lg transition-colors flex-shrink-0"
                title={sessionSidebarOpen ? 'Close sessions' : 'Open sessions'}
              >
                {sessionSidebarOpen
                  ? <PanelLeftClose size={18} className="text-textMuted" />
                  : <PanelLeftOpen size={18} className="text-textMuted" />
                }
              </button>
              <div className="w-7 h-7 sm:w-8 sm:h-8 bg-primary/10 rounded-full flex items-center justify-center flex-shrink-0">
                <Bot size={16} className="text-primary" />
              </div>
              <div className="min-w-0">
                {/* Model-switch dropdown: replaces the former static model label.
                    Only the card name is sent over WS; the agent resolves the
                    full cfg (incl. api_key) locally. The `model_card_switched`
                    info event confirms the switch and clears switchingModel.

                    Selection identity is the card NAME (filename, unique), not
                    model_name -- two vendors can share a model_name without one
                    shadowing the other. Options are grouped by provider. */}
                {modelCards.length > 0 ? (
                  <div className="flex items-center gap-1">
                    <select
                      className="font-bold text-textMain text-sm truncate bg-transparent border-none outline-none cursor-pointer max-w-[200px] focus:ring-0"
                      value={(() => {
                        // Prefer exact card-name match (unique); fall back to
                        // model_name only when no _card is known yet.
                        if (currentCardName && modelCards.some(c => c.name === currentCardName)) {
                          return currentCardName;
                        }
                        if (modelName) {
                          const byModel = modelCards.find(c => c.model_name === modelName);
                          if (byModel) return byModel.name;
                        }
                        return '';
                      })()}
                      disabled={switchingModel}
                      onChange={(e) => {
                        const cardName = e.target.value;
                        if (!cardName) return;
                        setSwitchingModel(true);
                        wsServiceRef.current?.switchModel(cardName);
                      }}
                      title={switchingModel ? 'Switching model…' : 'Switch model'}
                    >
                      <option value="" disabled>
                        {modelName || agentProfile?.agent_name || agentId}
                      </option>
                      {(() => {
                        // Group cards by provider (empty provider -> a shared
                        // "Other" bucket), preserving the original card order.
                        const groups: { vendor: string; items: ModelCardInfo[] }[] = [];
                        const idx: Record<string, number> = {};
                        for (const c of modelCards) {
                          const v = c.provider?.trim() || '';
                          if (v in idx) {
                            groups[idx[v]].items.push(c);
                          } else {
                            idx[v] = groups.length;
                            groups.push({ vendor: v, items: [c] });
                          }
                        }
                        return groups.map(g => (
                          <optgroup
                            key={g.vendor || '__other'}
                            label={g.vendor || 'Other'}
                          >
                            {g.items.map(c => (
                              <option key={c.name} value={c.name}>
                                {c.title || c.name}
                              </option>
                            ))}
                          </optgroup>
                        ));
                      })()}
                    </select>
                    {switchingModel && (
                      <span className="text-[10px] text-textMuted animate-pulse">…</span>
                    )}
                  </div>
                ) : (
                  <h2 className="font-bold text-textMain text-sm truncate">{modelName || agentProfile?.agent_name || agentId}</h2>
                )}
                <StatusBadge status={agentStatus} />
              </div>
            </div>

            {/* Header actions (right side) */}
            <div className="flex items-center gap-1.5">
              <button
                onClick={() => {
                  setShowPlanViewer(v => {
                    const next = !v;
                    try { localStorage.setItem('ai_chat_show_plan_viewer', String(next)); } catch {}
                    return next;
                  });
                }}
                className={`p-1.5 sm:p-2 rounded-lg transition-colors flex-shrink-0 ${
                  showPlanViewer ? 'bg-primary/15 hover:bg-primary/20' : 'hover:bg-primary/10'
                }`}
                title={t('aiChat.planPanel')}
              >
                <ClipboardList size={18} className={showPlanViewer ? 'text-primary' : 'text-textMuted'} />
              </button>
              <button
                onClick={() => setShowContextViewer(v => !v)}
                className={`p-1.5 sm:p-2 rounded-lg transition-colors flex-shrink-0 ${
                  showContextViewer ? 'bg-primary/15 hover:bg-primary/20' : 'hover:bg-primary/10'
                }`}
                title={t('aiChat.contextDetails')}
              >
                <List size={18} className={showContextViewer ? 'text-primary' : 'text-textMuted'} />
              </button>
              <button
                onClick={() => {
                  setShowTokenStats(v => {
                    const next = !v;
                    try { localStorage.setItem('ai_chat_show_token_stats', String(next)); } catch {}
                    return next;
                  });
                }}
                className={`p-1.5 sm:p-2 rounded-lg transition-colors flex-shrink-0 ${
                  showTokenStats ? 'bg-primary/15 hover:bg-primary/20' : 'hover:bg-primary/10'
                }`}
                title={showTokenStats ? t('aiChat.hideTokenStats') : t('aiChat.showTokenStats')}
              >
                <Gauge size={18} className={showTokenStats ? 'text-primary' : 'text-textMuted'} />
              </button>
              <button
                onClick={toggleWorkflow}
                className={`p-1.5 sm:p-2 rounded-lg transition-colors flex-shrink-0 ${
                  showWorkflow ? 'bg-primary/15 hover:bg-primary/20' : 'hover:bg-primary/10'
                }`}
                title={showWorkflow ? 'Hide workflow details' : 'Show workflow details'}
              >
                {showWorkflow
                  ? <Lightbulb size={18} className="text-primary" />
                  : <Lightbulb size={18} className="text-textMuted" />
                }
              </button>
              <button
                onClick={handleCompressContext}
                disabled={isLoadingSession || isCompressingContext}
                className="p-1.5 sm:p-2 rounded-lg transition-colors flex-shrink-0 hover:bg-primary/10 disabled:opacity-50 disabled:cursor-not-allowed"
                title={isCompressingContext ? 'Summarizing session...' : 'Summarize/compress current session context'}
              >
                <Scissors size={18} className={isCompressingContext ? 'text-primary' : 'text-textMuted'} />
              </button>

            </div>
          </div>

          {showTokenStats && tokenStats && tokenStats.max > 0 && (
            <TokenProgressBar
              used={tokenStats.used}
              max={tokenStats.max}
              breakdown={tokenStats.breakdown}
              session={tokenStats.session}
            />
          )}
        </div>

        {/* Auth expired banner */}
        {sessionExpired && (
          <div className="px-4 py-3 bg-yellow-500/15 border-b border-yellow-500/30 flex items-center justify-between gap-3 flex-shrink-0">
            <span className="text-sm text-yellow-200">
              Session expired. Please re-login to continue chatting.
            </span>
            <button
              onClick={handleReLogin}
              className="px-3 py-1 text-xs font-medium bg-yellow-500/20 hover:bg-yellow-500/30 text-yellow-200 rounded transition-colors whitespace-nowrap"
            >
              Re-login
            </button>
          </div>
        )}

        {/* Messages Area */}
        <div className="flex-1 overflow-y-auto px-2 sm:px-4 py-3 sm:py-4 relative" style={{ minHeight: 0 }} ref={messagesContainerRef} onScroll={handleMessagesScroll}>
          {!hasContent && !isLoadingSession && (
            <div className="flex flex-col items-center justify-center h-full text-textMuted">
              <Bot size={48} className="mb-4 opacity-30" />
              <p className="text-sm">Start a conversation with the agent</p>
              <p className="text-xs mt-1">Type a message or drag files here to begin</p>
            </div>
          )}

          {/* Session loading overlay */}
          {isLoadingSession && (
            <div className="flex flex-col items-center justify-center h-full text-textMuted">
              <div className="w-6 h-6 border-2 border-primary border-t-transparent rounded-full animate-spin mb-3" />
              <p className="text-sm">{sessionLoadingLabel}</p>
            </div>
          )}

          {/* Render timeline entries (messages + workflow blocks interleaved) */}
          {/* Lazy loading indicators at top */}
          {isLoadingMore && (
            <div className="flex items-center justify-center py-3">
              <div className="w-4 h-4 border-2 border-primary border-t-transparent rounded-full animate-spin mr-2" />
              <span className="text-xs text-textMuted">Loading earlier messages...</span>
            </div>
          )}
          {!hasMoreHistory && !isLoadingMore && timeline.length > 0 && historyOffsetRef.current > 0 && (
            <div className="flex items-center justify-center py-2">
              <span className="text-xs text-textMuted opacity-50">All messages loaded</span>
            </div>
          )}


          {timeline.map((entry, i) => {
            // Stable key from _uid prevents remounting during turn updates or lazy loading
            const entryKey = entry._uid || `entry-${i}`;

            if (entry.kind === 'message') {
              return (
                <MessageBubble
                  key={entryKey}
                  message={entry.data}
                  senderName={
                    entry.data.role === 'user'
                      ? (currentUser?.name || undefined)
                      : (agentProfile?.agent_name || undefined)
                  }
                  senderAvatar={
                    entry.data.role === 'user'
                      ? (currentUser?.avatar || null)
                      : (agentProfile?.chat_profile?.chat_user_avatar || null)
                  }
                />
              );
            }
            if (entry.kind === 'workflow' && showWorkflow) {
              // Find the last incomplete workflow block index to pass turnStartedMs
              const lastIncompleteIdx = (() => {
                for (let j = timeline.length - 1; j >= 0; j--) {
                  if (timeline[j].kind === 'workflow' && !(timeline[j] as { kind: 'workflow'; data: WorkflowBlock }).data.completed) return j;
                }
                return -1;
              })();
              return (
                <WorkflowBlockView
                  key={entryKey}
                  block={entry.data}
                  blockKey={i}
                  turnStartedMs={i === lastIncompleteIdx ? turnStartedMs : undefined}
                />
              );
            }
            if (entry.kind === 'status_hint') {
              const hint = entry.data;
              let icon: React.ReactNode;
              let label: string;
              if (hint.hintType === 'sleep') {
                icon = <Moon size={11} className="text-indigo-400/60 shrink-0" />;
                label = t('aiChat.sleepMode', { seconds: hint.content });
              } else if (hint.hintType === 'wake') {
                icon = <Bell size={11} className="text-emerald-400/60 shrink-0" />;
                label = t('aiChat.wakeMode', { content: hint.content });
              } else {
                icon = <Zap size={11} className="text-amber-400/60 shrink-0" />;
                label = t('aiChat.stateLabel', { content: hint.content });
              }
              return (
                <div key={entryKey} className="flex items-center gap-1.5 py-0.5 my-0.5 mx-2 sm:mx-9">
                  <div className="flex-1 h-px bg-border/25" />
                  {icon}
                  <span className="text-[10px] text-textMuted/45 font-mono shrink-0">{label}</span>
                  <div className="flex-1 h-px bg-border/25" />
                </div>
              );
            }
            if (entry.kind === 'archived_section') {
              return (
                <ArchivedSection
                  key={entryKey}
                  data={entry.data}
                  currentUser={currentUser}
                  agentProfile={agentProfile}
                />
              );
            }
            return null;
          })}

          {/* Agent working indicator (shown when workflow is hidden but agent is active) */}
          {!showWorkflow && hasActiveWorkflow && (
            <div className="mb-1">
              <AgentWorkingIndicator
                agentProfile={agentProfile}
                startedMs={turnStartedMs}
              />
            </div>
          )}

          {/* Streaming message (always at the very bottom) */}
          {(streamingText || isStreaming) && (
            <StreamingMessage
              content={streamingText}
              isComplete={!isStreaming}
              avatarSrc={agentProfile?.chat_profile?.chat_user_avatar}
            />
          )}

          <div ref={chatEndRef} />

          {/* Scroll-to-top / scroll-to-bottom floating buttons */}
          {(showScrollTop || showScrollBottom) && (
            <div
              className="sticky bottom-4 z-10 pointer-events-none flex justify-end pr-1 transition-opacity duration-300"
              style={{ opacity: scrollActive ? 1 : 0, pointerEvents: scrollActive ? undefined : 'none' }}
            >
              <div className="flex flex-col gap-2 pointer-events-auto">
                {showScrollTop && (
                  <button
                    onClick={scrollToTop}
                    className="w-8 h-8 bg-white border border-gray-200 rounded-full shadow-md flex items-center justify-center hover:bg-gray-50 transition-colors"
                    title="Scroll to top"
                  >
                    <ChevronUp size={18} className="text-gray-500" />
                  </button>
                )}
                {showScrollBottom && (
                  <button
                    onClick={scrollToBottom}
                    className="w-8 h-8 bg-white border border-gray-200 rounded-full shadow-md flex items-center justify-center hover:bg-gray-50 transition-colors"
                    title="Scroll to bottom"
                  >
                    <ChevronDown size={18} className="text-gray-500" />
                  </button>
                )}
              </div>
            </div>
          )}
        </div>

        {/* Image & attachment preview */}
        {(images.length > 0 || attachments.length > 0 || isUploading) && (
          <div className="px-2 sm:px-4 py-2 border-t border-border bg-panel flex gap-2 flex-wrap items-center">
            {/* Images */}
            {images.map((img, i) => (
              <div key={`img-${i}`} className="relative group">
                <img
                  src={img.startsWith('http') ? img : img.startsWith('/') ? img : `/uploads/${img.split(/[/\\]/).pop()}`}
                  alt=""
                  className="w-16 h-16 rounded-lg object-cover border border-border"
                  loading="lazy"
                />
                <button
                  onClick={() => removeImage(i)}
                  className="absolute -top-1 -right-1 w-4 h-4 bg-red-500 rounded-full flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity"
                >
                  <X size={10} className="text-white" />
                </button>
              </div>
            ))}
            {/* File attachments */}
             {attachments.map((att, i) => {
               if (att.is_video && att.url) {
                 const videoSrc = att.url.startsWith('http') ? att.url : att.url;
                 return (
                   <div key={`att-${i}`} className="relative group">
                     <video
                       src={videoSrc}
                       className="w-16 h-16 rounded-lg object-cover border border-border"
                       preload="metadata"
                     />
                     <div className="absolute bottom-0 left-0 right-0 bg-black/50 rounded-b-lg px-1 py-0.5">
                       <p className="text-[9px] text-white truncate">VIDEO</p>
                     </div>
                     <button
                       onClick={() => removeAttachment(i)}
                       className="absolute -top-1 -right-1 w-4 h-4 bg-red-500 rounded-full flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity"
                     >
                       <X size={10} className="text-white" />
                     </button>
                   </div>
                 );
               }
               return (
                 <div key={`att-${i}`} className="relative group flex items-center gap-2 px-3 py-2 rounded-lg border border-border bg-bgLight max-w-[200px]">
                   <FileIcon size={16} className="text-textMuted flex-shrink-0" />
                   <div className="min-w-0 flex-1">
                     <p className="text-xs text-textMain truncate">{att.original_name}</p>
                     <p className="text-[10px] text-textMuted">
                       {att.is_audio ? 'AUDIO' : 'FILE'} • {_formatFileSize(att.size)}
                     </p>
                   </div>
                   <button
                     onClick={() => removeAttachment(i)}
                     className="w-4 h-4 bg-red-500 rounded-full flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity flex-shrink-0"
                   >
                     <X size={10} className="text-white" />
                   </button>
                 </div>
               );
             })}
            {/* Upload progress indicator */}
            {isUploading && (
              <div className="flex items-center gap-1.5 px-3 py-2 rounded-lg border border-border bg-bgLight">
                <div className="w-3 h-3 border-2 border-primary border-t-transparent rounded-full animate-spin" />
                <span className="text-xs text-textMuted">Uploading...</span>
              </div>
            )}
          </div>
        )}

        {/* Pending message queue — docked above the input area (and above the
            plan panel when it is open). Messages here are NOT yet part of the
            conversation: they only enter the timeline when the user clicks
            "Send now" or when the agent returns to idle and auto-flushes them.

            UI shape (one large container, no per-message card):
              ┌────────────────────────────────────────────────────────────┐
              │ ⏱ 2 Queued  ↗ auto-sent when idle    [Send all] [▾]       │
              ├────────────────────────────────────────────────────────────┤
              │ #1  How are you?                              [⚡] [×]      │
              │ #2  Feeling alright?                          [⚡] [×]      │
              └────────────────────────────────────────────────────────────┘
        */}
        {pendingMessages.length > 0 && (
          <div className="px-2 sm:px-3 pt-2 pb-1 border-t border-border bg-panel flex-shrink-0">
            <div className="rounded-lg border border-amber-200/70 bg-amber-50/40 dark:bg-amber-500/5 overflow-hidden">
              {/* Header bar: status + actions */}
              <div className="flex items-center gap-2 px-2.5 py-1.5 border-b border-amber-200/60 bg-amber-50/60 dark:bg-amber-500/10">
                <Clock size={11} className="text-amber-500 flex-shrink-0" />
                <span className="text-[11px] text-amber-700 dark:text-amber-300 font-semibold">
                  {t('aiChat.pendingCount', { count: pendingMessages.length })}
                </span>
                <span className="text-[10px] text-amber-600/80 dark:text-amber-300/70">
                  · ↗ {t('aiChat.pendingAutoSendHint')}
                </span>
                <div className="flex-1" />
                {/* Send-all: flush the whole queue immediately. */}
                <button
                  type="button"
                  onClick={handleSendAllPending}
                  className="flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium text-amber-700 dark:text-amber-300 hover:bg-amber-200/60 dark:hover:bg-amber-500/20 transition-colors"
                  title={t('aiChat.sendNow')}
                >
                  <Zap size={10} />
                  {t('aiChat.sendNow')}
                </button>
                <button
                  type="button"
                  onClick={handleCancelAllPending}
                  className="flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium text-gray-500 hover:bg-gray-200/60 dark:hover:bg-gray-700/40 transition-colors"
                  title={t('aiChat.pendingClearAll')}
                >
                  <X size={10} />
                  {t('aiChat.pendingClearAll')}
                </button>
                <button
                  type="button"
                  onClick={() => setPendingCollapsed(c => !c)}
                  className="flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[10px] font-medium text-gray-500 hover:bg-gray-200/60 dark:hover:bg-gray-700/40 transition-colors"
                  title={pendingCollapsed ? t('aiChat.pendingExpand') : t('aiChat.pendingCollapse')}
                >
                  {pendingCollapsed ? '▴' : '▾'}
                </button>
              </div>

              {/* Message rows — compact, no per-message card */}
              {!pendingCollapsed && (
                <div className="max-h-[200px] overflow-y-auto">
                  {pendingMessages.map((pm, idx) => {
                    const imgCount = pm.images.length;
                    const fileCount = pm.fileAtts.length;
                    const preview = (pm.text || '').replace(/\s+/g, ' ').trim();
                    return (
                      <div
                        key={pm.id}
                        className="group flex items-center gap-2 px-2.5 py-1.5 border-b border-amber-200/40 last:border-b-0 hover:bg-amber-100/40 dark:hover:bg-amber-500/10 transition-colors"
                      >
                        {/* Queue position badge */}
                        <span className="flex-shrink-0 text-[10px] font-mono text-amber-700/80 dark:text-amber-300/80 min-w-[28px]">
                          {t('aiChat.pendingQueuePosition', { index: idx + 1 })}
                        </span>
                        {/* Message preview + attachment summary */}
                        <div className="flex-1 min-w-0 flex items-center gap-2">
                          {preview ? (
                            <span className="truncate text-[12px] text-gray-700 dark:text-gray-200">
                              {preview}
                            </span>
                          ) : (
                            <span className="text-[12px] italic text-gray-400">
                              {imgCount > 0 || fileCount > 0
                                ? t('aiChat.pendingAttachments', { images: imgCount, files: fileCount })
                                : t('aiChat.pendingLabel')}
                            </span>
                          )}
                          {(imgCount > 0 || fileCount > 0) && preview && (
                            <span className="flex-shrink-0 text-[10px] text-gray-400 whitespace-nowrap">
                              {t('aiChat.pendingAttachments', { images: imgCount, files: fileCount })}
                            </span>
                          )}
                        </div>
                        {/* Row actions — visible on hover for a cleaner default */}
                        <div className="flex-shrink-0 flex items-center gap-0.5 opacity-60 group-hover:opacity-100 transition-opacity">
                          <button
                            type="button"
                            onClick={() => handleSendPendingNow(pm.id)}
                            className="p-1 rounded text-amber-600 hover:bg-amber-200/60 hover:text-amber-700 dark:text-amber-300 dark:hover:bg-amber-500/30 transition-colors"
                            title={t('aiChat.sendNow')}
                          >
                            <Zap size={12} />
                          </button>
                          <button
                            type="button"
                            onClick={() => handleCancelPending(pm.id)}
                            className="p-1 rounded text-gray-400 hover:bg-gray-200/60 hover:text-gray-600 dark:hover:bg-gray-700/40 dark:hover:text-gray-200 transition-colors"
                            title={t('aiChat.cancelPending')}
                          >
                            <X size={12} />
                          </button>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          </div>
        )}

        {/* Inline Plan Panel (docked above input) */}
        {showPlanViewer && (
          <div className="px-2 sm:px-3 pt-2 border-t border-border bg-panel flex-shrink-0">
            {effectivePlanSteps.length > 0 ? (
              <PlanBlock
                steps={effectivePlanSteps}
                className="mb-0 ml-0 border border-border rounded-lg overflow-hidden bg-white dark:bg-bgPage shadow-sm"
              />
            ) : (
              <div className="text-xs text-textMuted bg-white dark:bg-bgPage border border-border rounded-lg px-3 py-2 shadow-sm">{t('aiChat.noPlanYet')}</div>
            )}
          </div>
        )}

        {/* Input Area */}
        <div className="p-2 sm:p-3 border-t border-border bg-panel flex-shrink-0">
          <div className={`flex items-end gap-1.5 sm:gap-2${isLoadingSession ? ' opacity-50 pointer-events-none' : ''}`}>
            {/* Attachment button (any file) */}
            <button
              onClick={() => {
                // Create a temporary file input for any file type
                const input = document.createElement('input');
                input.type = 'file';
                input.multiple = true;
                input.onchange = async (e) => {
                  const files = (e.target as HTMLInputElement).files;
                  if (!files) return;
                  setIsUploading(true);
                  try {
                    const fileArray = Array.from(files) as File[];
                    if (fileArray.length === 1) {
                      const resp = await agentSessionAPI.uploadFile(agentId, fileArray[0]);
                      if (resp.is_image) {
                        setImages(prev => [...prev, resp.url]);
                      } else {
                        setAttachments(prev => [...prev, resp]);
                      }
                    } else {
                      const resp = await agentSessionAPI.uploadFiles(agentId, fileArray);
                      for (const f of resp.files) {
                        if (f.is_image) {
                          setImages(prev => [...prev, f.url]);
                        } else {
                          setAttachments(prev => [...prev, f]);
                        }
                      }
                    }
                  } catch (err: any) {
                    console.error('[AIChatPage] File upload failed:', err);
                  } finally {
                    setIsUploading(false);
                  }
                };
                input.click();
              }}
              disabled={isLoadingSession}
              className="p-1.5 sm:p-2 hover:bg-primary/10 rounded-lg transition-colors flex-shrink-0 disabled:cursor-not-allowed"
              title="Upload files"
            >
              <Paperclip size={18} className="text-textMuted" />
            </button>

            {/* Folder upload button */}
            <button
              onClick={() => {
                const input = document.createElement('input');
                input.type = 'file';
                (input as any).webkitdirectory = true;
                (input as any).directory = true;
                input.multiple = true;
                input.onchange = async (e) => {
                  const files = (e.target as HTMLInputElement).files;
                  if (!files || files.length === 0) return;
                  setIsUploading(true);
                  try {
                    const fileArray = Array.from(files) as File[];
                    const resp = await agentSessionAPI.uploadFiles(agentId, fileArray);
                    for (const f of resp.files) {
                      if (f.is_image) {
                        setImages(prev => [...prev, f.url]);
                      } else {
                        setAttachments(prev => [...prev, f]);
                      }
                    }
                  } catch (err: any) {
                    console.error('[AIChatPage] Folder upload failed:', err);
                  } finally {
                    setIsUploading(false);
                  }
                };
                input.click();
              }}
              disabled={isLoadingSession}
              className="p-1.5 sm:p-2 hover:bg-primary/10 rounded-lg transition-colors flex-shrink-0 disabled:cursor-not-allowed"
              title="Upload folder"
            >
              <Upload size={18} className="text-textMuted" />
            </button>

            {/* Image upload button */}
            <button
              onClick={() => fileInputRef.current?.click()}
              disabled={isLoadingSession}
              className="p-1.5 sm:p-2 hover:bg-primary/10 rounded-lg transition-colors flex-shrink-0 disabled:cursor-not-allowed"
              title="Upload image"
            >
              <ImageIcon size={18} className="text-textMuted" />
            </button>
            <input
              ref={fileInputRef}
              type="file"
              accept="image/*"
              multiple
              onChange={handleImageUpload}
              className="hidden"
            />

            {/* Set working directory button — opens OS folder picker (Electron)
                or manual input prompt (browser) */}
            <button
              onClick={async () => {
                try {
                  let pickedPath: string | null = null;

                  // 1. Try Electron IPC (native folder picker)
                  if (typeof (window as any).electronEnv?.pickWorkspaceFolder === 'function') {
                    pickedPath = await (window as any).electronEnv.pickWorkspaceFolder();
                  }

                  // 2. Browser fallback — prompt for path manually
                  //    (browsers can't access the real filesystem path for security)
                  if (!pickedPath) {
                    const current = agentCwd || '(workspace root)';
                    const input = window.prompt(
                      'Enter working directory path:\n\n' +
                      'This will be the default directory for agent shell commands\n' +
                      '(ls, dir, run_command, file operations, etc.)\n\n' +
                      `Current: ${current}`,
                      agentCwd || ''
                    );
                    if (!input || !input.trim()) return;
                    pickedPath = input.trim();
                  }

                  if (!pickedPath) return;

                  // Send to backend via admin API
                  const dirName = agentProfile?.dir_name || agentId;
                  await adminAPI.setWorkingDirectory(dirName, pickedPath);
                  // Update local state so ContextViewer reflects the change
                  setAgentCwd(pickedPath);
                  console.log('[AIChatPage] Working directory set to:', pickedPath);
                } catch (err: any) {
                  console.error('[AIChatPage] Failed to set working directory:', err);
                  alert(`Failed to set working directory: ${err.message || err}`);
                }
              }}
              disabled={isLoadingSession}
              className="p-1.5 sm:p-2 hover:bg-primary/10 rounded-lg transition-colors flex-shrink-0 disabled:cursor-not-allowed"
              title={agentCwd ? `Working dir: ${agentCwd} (click to change)` : 'Set working directory'}
            >
              <FolderOpen size={18} className={agentCwd ? 'text-primary' : 'text-textMuted'} />
            </button>

            {/* Text input */}
            <textarea
              ref={inputRef}
              value={inputText}
              onChange={(e) => setInputText(e.target.value)}
              onKeyDown={handleKeyDown}
              onPaste={handlePaste}
              placeholder={isLoadingSession ? sessionLoadingLabel : 'Type a message...'}
              disabled={isLoadingSession}
              className={`flex-1 min-w-0 border border-border rounded-xl px-3 py-2 text-sm text-textMain placeholder-textMuted resize-none focus:outline-none focus:ring-1 focus:ring-primary/50 min-h-[38px] max-h-[120px] ${isLoadingSession ? 'bg-border text-textMuted cursor-not-allowed' : 'bg-bgLight'}`}
              rows={1}
              style={{ height: 'auto' }}
              onInput={(e) => {
                const target = e.target as HTMLTextAreaElement;
                target.style.height = 'auto';
                target.style.height = Math.min(target.scrollHeight, 120) + 'px';
              }}
            />

            {/* Send / Stop buttons.
                When the agent is busy, Send is still available — it parks the
                message in the pending queue (auto-sent on idle, or via "Send
                now"). Stop is shown alongside as a separate red button. */}
            {isStreaming || agentStatus === 'working' || agentStatus === 'thinking' ? (
              <>
                <button
                  onClick={handleSend}
                  disabled={isLoadingSession || (!inputText.trim() && images.length === 0 && attachments.length === 0)}
                  className="p-2 bg-amber-500 hover:bg-amber-600 rounded-lg transition-colors flex-shrink-0 disabled:opacity-50 disabled:cursor-not-allowed"
                  title={t('aiChat.queueMessage')}
                >
                  <Send size={18} className="text-white" />
                </button>
                <button
                  onClick={handleStop}
                  className="p-2 bg-red-500 hover:bg-red-600 rounded-lg transition-colors flex-shrink-0"
                  title="Stop"
                >
                  <Square size={18} className="text-white" />
                </button>
              </>
            ) : (
              <button
                onClick={handleSend}
                disabled={isLoadingSession || (!inputText.trim() && images.length === 0 && attachments.length === 0)}
                className="p-2 bg-primary hover:bg-primary/90 rounded-lg transition-colors flex-shrink-0 disabled:opacity-50 disabled:cursor-not-allowed"
                title="Send"
              >
                <Send size={18} className="text-white" />
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};
