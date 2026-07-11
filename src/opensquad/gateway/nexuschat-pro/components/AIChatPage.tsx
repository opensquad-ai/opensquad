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
  Bot, ArrowLeft, Send, Square,
  PanelLeftOpen, PanelLeftClose, X, FileIcon, Upload,
  ChevronUp, ChevronDown, Lightbulb, List, Moon, Zap, Bell, ClipboardList, Gauge, Scissors,
  Loader2, Clock, AlignLeft, MessageSquare,
} from 'lucide-react';

import { useTranslation } from 'react-i18next';
import { getAiWsService, releaseAiWsService, AIWSMessage, AIWebSocketStatus } from '../services/aiWebSocket';
import { agentSessionAPI, authAPI, adminAPI, AdminAgent, modelCardAPI, ModelCardInfo, skillAPI, SkillInfo, SERVER_BASE_URL } from '../services/api';
import { resolveChatAvatar, toAbsoluteMediaUrl } from '../utils/image';
import {
  appendWorkflowEvent,
  buildTimelineFromSession,
  genTimelineUID,
  timelineHasToolEvent,
  workflowToolEventKey,
  shouldTreatWorkflowComplete,
  toWebMediaUrl,
  type TimelineEntry,
  type WorkflowBlock,
  type WorkflowEvent,
} from '../utils/aiChatTimeline';
import { pickFolderPath, pushCwdRecent } from '../utils/cwdRecents';
import { setSessionProjectPath, getSessionMeta, requestSessionListRefresh } from '../utils/sessionProjectMeta';

// AI Chat sub-components
import { MessageBubble, ChatMessage, FileAttachment } from './ai-chat/MessageBubble';
import { StreamingMessage } from './ai-chat/StreamingMessage';
import { SoloMessage } from './ai-chat/SoloMessage';
import { SoloActivityRow, mergeWorkflowBlocks } from './ai-chat/SoloActivityRow';
import { SoloUserNavRail, previewUserMessage } from './ai-chat/SoloUserNavRail';
import { SoloModelPicker } from './ai-chat/SoloModelPicker';
import { SoloAttachMenu } from './ai-chat/SoloAttachMenu';
import { SoloContextFooter } from './ai-chat/SoloContextFooter';
import { WorkflowContainer } from './ai-chat/WorkflowContainer';
import { ThoughtBlock } from './ai-chat/ThoughtBlock';
import { ToolCallBlock } from './ai-chat/ToolCallBlock';
import { DelegateFold } from './ai-chat/DelegateFold';
import { PlanBlock, PlanStep, parsePlanContent } from './ai-chat/PlanBlock';
import { StatusBadge, AgentStatus } from './ai-chat/StatusBadge';
import { TokenProgressBar } from './ai-chat/TokenProgressBar';
import { SessionSidebar } from './ai-chat/SessionSidebar';
import { ContextViewer, ContextEntry } from './ai-chat/ContextViewer';
import { buildDisplayWorkflowItems } from '../utils/delegateGrouping';

const genUID = (): string => genTimelineUID();

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

  const avatarSrc = resolveChatAvatar(agentProfile?.chat_profile);
  const resolvedAvatar = toAbsoluteMediaUrl(avatarSrc, SERVER_BASE_URL);

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
  const displayItems = useMemo(
    () => buildDisplayWorkflowItems(block.events),
    [block.events],
  );
  const totalEvents = displayItems.length;
  const effectivelyCompleted = shouldTreatWorkflowComplete(block);
  const [visibleCount, setVisibleCount] = useState(() =>
    totalEvents <= WORKFLOW_EVENTS_PAGE_SIZE ? totalEvents : WORKFLOW_EVENTS_PAGE_SIZE
  );

  // While the workflow is active, auto-grow visibleCount to always show all events.
  // This prevents the sliding-window effect where new events push user-expanded
  // tool calls out of the visible range.
  // For completed workflows, the count is frozen so the user can use "Show more".
  useEffect(() => {
    if (!effectivelyCompleted) {
      // Active: show everything — no events ever get hidden during live work
      setVisibleCount(totalEvents);
    } else if (visibleCount > totalEvents) {
      // Completed and somehow over total (shouldn't happen, but guard it)
      setVisibleCount(totalEvents);
    }
    // Completed + visibleCount <= totalEvents: leave as-is (user controls via "Show more")
  }, [totalEvents, effectivelyCompleted]);

  // For active (non-completed) workflows, always show ALL events during render.
  // This prevents a one-frame flash where useEffect hasn't yet updated visibleCount,
  // causing expanded tool calls to be sliced out of the DOM and losing the
  // data-tool-expanded attribute that freezes auto-scroll.
  const effectiveCount = effectivelyCompleted ? visibleCount : totalEvents;
  const hiddenCount = Math.max(0, totalEvents - effectiveCount);
  const visibleItems = hiddenCount <= 0
    ? displayItems
    : displayItems.slice(totalEvents - effectiveCount);

  const handleShowMore = () => {
    setVisibleCount(prev => Math.min(prev + WORKFLOW_EVENTS_PAGE_SIZE, totalEvents));
  };

  // Trailing compression/summary blocks may never get a following chat message
  // to flip `completed`; treat settled terminal work as finished for display.
  const displayStatus = effectivelyCompleted ? undefined : (block.status || undefined);

  // Skip empty / lifecycle-only blocks (e.g. "New session started" → bare Completed).
  const hasRenderableContent = visibleItems.some((item) => {
    if (item.kind === 'delegation') return true;
    const evt = item.event;
    if (evt.subAgent) return false;
    if (evt.type === 'info') {
      const infoObj = typeof evt.content === 'object' && evt.content !== null ? evt.content as any : null;
      const text =
        typeof evt.content === 'string'
          ? evt.content
          : (infoObj?.text || infoObj?.message || '');
      if (/^New session started$/i.test(String(text).trim()) || /^Workflow started$/i.test(String(text).trim())) {
        return false;
      }
    }
    return true;
  });
  if (!hasRenderableContent) return null;

  return (
    <WorkflowContainer
      status={displayStatus}
      defaultOpen={!effectivelyCompleted}
      startedMs={effectivelyCompleted ? undefined : turnStartedMs}
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
      {visibleItems.map((item, i) => {
        if (item.kind === 'delegation') {
          return (
            <DelegateFold
              key={item.key}
              bundle={item.bundle}
              variant="classic"
            />
          );
        }

        const evt = item.event;
        // Nested under a delegate window — do not duplicate in the main stream.
        if (evt.subAgent) return null;

        // Use pre-assigned _uid for stable key during window shifts
        const eventKey = item.key || evt._uid || `${evt.type}-${evt.timestamp}-${i}`;

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
          const rawInfoText =
            typeof evt.content === 'string'
              ? evt.content
              : (infoObj?.text || infoObj?.message || '');
          if (/^New session started$/i.test(String(rawInfoText).trim()) || /^Workflow started$/i.test(String(rawInfoText).trim())) {
            return null;
          }

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

/** Expand any legacy archived_section folds into a flat timeline. */
function flattenArchivedSections(entries: TimelineEntry[]): TimelineEntry[] {
  const out: TimelineEntry[] = [];
  for (const e of entries) {
    if (e.kind === 'archived_section') {
      out.push(...flattenArchivedSections(e.data.entries));
    } else {
      out.push(e);
    }
  }
  return out;
}

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
  /** Skill selected from the + menu; shown as /name chip until send/clear. */
  const [pendingSkill, setPendingSkill] = useState<{ dir: string; name: string } | null>(null);
  const [availableSkills, setAvailableSkills] = useState<SkillInfo[]>([]);
  const [skillsLoading, setSkillsLoading] = useState(false);
  const skillsLoadedRef = useRef(false);
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
  /** Workflow WS events buffered while hydrating so they are not double-appended after snapshot replace. */
  const pendingHydrationWorkflowEventsRef = useRef<Array<{ event: WorkflowEvent; status: string | null }>>([]);
  /** When true, next hydrate merges archive into the live timeline instead of full replace. */
  const compressionHydrationPendingRef = useRef(false);
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
  /** Default workspace root from agent (used when user never picks a folder). */
  const [defaultCwd, setDefaultCwd] = useState<string | null>(null);
  /** Path chosen for the in-progress new session before sid is known. */
  const pendingProjectPathRef = useRef<string | null>(null);
  /** Provisional title from first user message, applied once sid is known. */
  const pendingSessionTitleRef = useRef<string | null>(null);
  /** Count of user messages in the current timeline (for first-message title). */
  const userMsgCountRef = useRef(0);
  useEffect(() => {
    userMsgCountRef.current = timeline.filter(
      (e) => e.kind === 'message' && (e.data as ChatMessage).role === 'user',
    ).length;
  }, [timeline]);
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
  // When the agent is busy (or a turn was just released and we are waiting for
  // busy→idle), new sends land here. Auto-drain sends ONE message at a time:
  // release → wait for agent reply/task finish → release next. Queue is persisted
  // per agent+session so refresh keeps the parked state.
  interface PendingMessage {
    id: string;
    text: string;
    images: string[];
    attachments: UploadedFile[];
    fileAtts: FileAttachment[];
    skillDir?: string;
    skillName?: string;
  }
  const [pendingMessages, setPendingMessages] = useState<PendingMessage[]>([]);
  const [pendingCollapsed, setPendingCollapsed] = useState(false);
  const pendingMessagesRef = useRef<PendingMessage[]>([]);
  const isFlushingPendingRef = useRef(false);
  /** After releasing a message, block further auto-drain until agent becomes busy once. */
  const waitForBusyAfterPendingSendRef = useRef(false);
  const pendingQueueHydratedKeyRef = useRef<string | null>(null);
  useEffect(() => { pendingMessagesRef.current = pendingMessages; }, [pendingMessages]);

  const pendingQueueStorageKey = useCallback((sid?: string | null) => {
    const sessionPart = (sid || currentSessionId || 'nosession').trim() || 'nosession';
    return `ai_chat_pending_queue:${agentId}:${sessionPart}`;
  }, [agentId, currentSessionId]);

  // Hydrate pending queue when agent/session is known (or nosession before sid arrives).
  useEffect(() => {
    if (!agentId) return;
    const key = pendingQueueStorageKey(currentSessionId);
    if (pendingQueueHydratedKeyRef.current === key) return;

    // If we just learned the real session id, migrate any queue parked under nosession.
    if (currentSessionId) {
      const nosessionKey = pendingQueueStorageKey(null);
      try {
        const orphan = localStorage.getItem(nosessionKey);
        if (orphan && !localStorage.getItem(key)) {
          localStorage.setItem(key, orphan);
          localStorage.removeItem(nosessionKey);
        } else if (orphan && localStorage.getItem(key)) {
          localStorage.removeItem(nosessionKey);
        }
      } catch { /* ignore */ }
    }

    pendingQueueHydratedKeyRef.current = key;
    try {
      const raw = localStorage.getItem(key);
      if (!raw) {
        // Keep in-memory queue when switching nosession→sid if we already have items.
        if (currentSessionId && pendingMessagesRef.current.length > 0) return;
        setPendingMessages([]);
        return;
      }
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed)) {
        setPendingMessages(parsed.filter((m) => m && typeof m.id === 'string'));
      } else {
        setPendingMessages([]);
      }
    } catch {
      setPendingMessages([]);
    }
  }, [agentId, currentSessionId, pendingQueueStorageKey]);

  // Persist pending queue for refresh recovery.
  useEffect(() => {
    if (!agentId) return;
    if (pendingQueueHydratedKeyRef.current == null) return;
    const key = pendingQueueStorageKey(currentSessionId);
    try {
      if (pendingMessages.length === 0) localStorage.removeItem(key);
      else localStorage.setItem(key, JSON.stringify(pendingMessages));
    } catch { /* ignore quota */ }
  }, [pendingMessages, agentId, currentSessionId, pendingQueueStorageKey]);

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

  // Solo: expand-all is session-only and defaults OFF so refresh keeps folds collapsed.
  const [soloExpandDetails, setSoloExpandDetails] = useState(false);

  // UI render mode: classic (chat bubbles) | solo (document stream). Global preference.
  type AiChatUiMode = 'classic' | 'solo';
  const [uiMode, setUiMode] = useState<AiChatUiMode>(() => {
    try {
      const stored = localStorage.getItem('ai_chat_ui_mode');
      return stored === 'solo' ? 'solo' : 'classic';
    } catch {
      return 'classic';
    }
  });
  const isSolo = uiMode === 'solo';
  const setUiModePersisted = useCallback((mode: AiChatUiMode) => {
    setUiMode(mode);
    try { localStorage.setItem('ai_chat_ui_mode', mode); } catch {}
  }, []);

  const soloColumnClass = isSolo ? 'max-w-3xl mx-auto w-full' : '';

  const soloUserNavNodes = useMemo(() => {
    if (!isSolo) return [];
    const nodes: { id: string; preview: string }[] = [];
    for (let i = 0; i < timeline.length; i++) {
      const entry = timeline[i];
      if (entry.kind !== 'message') continue;
      const msg = entry.data as ChatMessage;
      if (msg.role !== 'user') continue;
      const id = entry._uid || `entry-${i}`;
      nodes.push({ id, preview: previewUserMessage(msg.content || '') });
    }
    return nodes;
  }, [isSolo, timeline]);

  const jumpToSoloUserMessage = useCallback((id: string) => {
    const container = messagesContainerRef.current;
    const el = document.getElementById(`solo-msg-${id}`);
    if (!container || !el) return;
    const cRect = container.getBoundingClientRect();
    const eRect = el.getBoundingClientRect();
    const top = eRect.top - cRect.top + container.scrollTop - 12;
    container.scrollTo({ top: Math.max(0, top), behavior: 'smooth' });
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
          if (runtimeWd) {
            setDefaultCwd(runtimeWd);
            setAgentCwd((prev) => prev || runtimeWd);
          }
        });
      })
      .catch(err => console.warn("[AIChatPage] Failed to load agent profile:", err.message));
  }, [agentId]);

  // After agent profile loads, also fetch the session working directory
  // (set via the folder-picker button). This overrides the permanent
  // workspace root so ContextViewer shows the user-selected cwd.
  useEffect(() => {
    if (!agentProfile?.dir_name) return;
    adminAPI.getWorkingDirectory(agentProfile.dir_name)
      .then(res => {
        const active = res.active_cwd || res.session_cwd || res.workspace_root || null;
        if (res.workspace_root) setDefaultCwd(res.workspace_root);
        else if (active) setDefaultCwd(active);
        if (res.session_cwd) setAgentCwd(res.session_cwd);
        else if (active) setAgentCwd((prev) => prev || active);
      })
      .catch(() => {/* not critical, keep default */});
  }, [agentProfile?.dir_name]);

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
        const olderEntries = buildTimelineFromSession(
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

  // Timeline helpers live in utils/aiChatTimeline.ts


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
        // Agent-chosen title wins over the provisional first-message title.
        pendingSessionTitleRef.current = null;
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
        const raw = msg.content ?? msg.data;
        const isSubAgent =
          typeof raw === 'object' && raw !== null && !!(raw as any).sub_agent;
        const subTaskLabel =
          typeof raw === 'object' && raw !== null
            ? String((raw as any).sub_task_label || '')
            : '';
        const event: WorkflowEvent = {
          type: 'thought',
          content: text,
          timestamp: Date.now(),
          subAgent: isSubAgent || undefined,
          subTaskLabel: subTaskLabel || undefined,
          jobId:
            typeof raw === 'object' && raw !== null && (raw as any).job_id
              ? String((raw as any).job_id)
              : undefined,
        };
        if (isHydratingSessionRef.current) {
          pendingHydrationWorkflowEventsRef.current.push({ event, status: 'Thinking...' });
          return;
        }
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
        jobId: typeof data === 'object' && data?.job_id ? String(data.job_id) : undefined,
      };
      if (isHydratingSessionRef.current) {
        pendingHydrationWorkflowEventsRef.current.push({ event, status: `Calling ${toolName}...` });
      } else {
        setTimeline(prev => appendWorkflowEvent(prev, event, `Calling ${toolName}...`));
      }
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
        jobId: typeof data === 'object' && data?.job_id ? String(data.job_id) : undefined,
      };
      if (isHydratingSessionRef.current) {
        pendingHydrationWorkflowEventsRef.current.push({ event, status: `${toolName} completed` });
        return;
      }
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
            // Compression finished with no following chat message — stop "working".
            status: done ? null : 'Summarizing...',
            completed: done ? true : wf.completed,
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
              status: isFinal ? null : text,
              completed: isFinal ? true : wf.completed,
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

      // System lifecycle noise — never show as a workflow "Activity" / empty block.
      const infoText =
        typeof detailed.text === 'string'
          ? detailed.text
          : (typeof (detailed as any).message === 'string' ? (detailed as any).message : '');
      if (/^New session started$/i.test(infoText.trim()) || /^Workflow started$/i.test(infoText.trim())) {
        return;
      }

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
        jobId:
          typeof detailed === 'object' && detailed !== null && (detailed as any).job_id
            ? String((detailed as any).job_id)
            : undefined,
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
      // Only start the workflow timer when the backend supplies a real started_ms.
      // turn_start(0) alone is session management (__NEW_SESSION__, empty switch, …)
      // and must NOT flip the UI into "thinking" (looks like a blank turn started).
      const isRealWorkflow = typeof data === 'object' && data !== null && typeof (data as any).started_ms === 'number';
      const numericTurn =
        typeof data === 'number'
          ? data
          : typeof data === 'object' && data !== null
            ? Number((data as any).turn || 0)
            : 0;
      if (isRealWorkflow || numericTurn >= 1) {
        setAgentStatus('thinking');
      }
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
      pendingHydrationWorkflowEventsRef.current = [];
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

            const entries = buildTimelineFromSession(
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

            const isCompressionHydration = compressionHydrationPendingRef.current;
            compressionHydrationPendingRef.current = false;

            if (isCompressionHydration) {
              // Keep live message order + in-flight tool stream; disk snapshot
              // already has archived turns flattened into the normal timeline.
              setTimeline((prev) => {
                let merged = _mergeCompressionHydration(prev, nextEntries);
                const bufferedWf = pendingHydrationWorkflowEventsRef.current;
                pendingHydrationWorkflowEventsRef.current = [];
                for (const { event, status } of bufferedWf) {
                  merged = appendWorkflowEvent(merged, event, status);
                }
                return merged;
              });
            } else {
              // Full replace path (connect / session switch / refresh).
              const bufferedWf = pendingHydrationWorkflowEventsRef.current;
              pendingHydrationWorkflowEventsRef.current = [];
              let withBuffered = nextEntries;
              for (const { event, status } of bufferedWf) {
                withBuffered = appendWorkflowEvent(withBuffered, event, status);
              }
              setTimeline(withBuffered);
            }
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
            // Flush any workflow events that arrived between setTimeline and this
            // finally (race window while isHydratingSessionRef was still true).
            const lateWf = pendingHydrationWorkflowEventsRef.current;
            if (lateWf.length > 0) {
              pendingHydrationWorkflowEventsRef.current = [];
              setTimeline((prev) => {
                let next = prev;
                for (const { event, status } of lateWf) {
                  next = appendWorkflowEvent(next, event, status);
                }
                return next;
              });
            }
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
          .map((f: any) => toWebMediaUrl(f.url || f.path || f.src || (f.filename ? `/uploads/${f.filename}` : '')))
          .filter((u: any) => typeof u === 'string' && u.length > 0);
        const imageUrlsFromHistoryHyd = Array.isArray(raw.images)
          ? raw.images.map((u: any) => toWebMediaUrl(u)).filter((u: any) => typeof u === 'string' && u.length > 0)
          : [];
        const imageUrlsFromContentHyd: string[] = [];
        if (typeof content === 'string') {
          const reImg = /<image>(.*?)<\/image>/gi;
          let im: RegExpExecArray | null;
          while ((im = reImg.exec(content)) !== null) {
            const u = toWebMediaUrl((im[1] || '').trim());
            if (u) imageUrlsFromContentHyd.push(u);
          }
          const reFile = /\[File:\s*.*?\]\((.*?)\)/g;
          let fm: RegExpExecArray | null;
          while ((fm = reFile.exec(content)) !== null) {
            const u = toWebMediaUrl((fm[1] || '').trim());
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
        requestSessionListRefresh(agentId, sid);
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
      requestSessionListRefresh(agentId, currentSessionIdRef.current);
    });

    // Use history_sync as a trigger to reload the canonical current session
    // snapshot, rather than trusting the WS payload directly.
    const unsubHistorySync = aiWsService.on('history_sync', (msg: AIWSMessage) => {
      if (viewingHistorySessionRef.current || newSessionPendingRef.current) return;
      const data: any = msg.content || msg.data || {};
      const sid = typeof data === 'object' ? (data.session_id || data.id || null) : null;
      const reason = typeof data === 'object' ? data.reason : null;
      if (reason === 'compression') {
        // Always reload after compression so the "已归档" section appears
        // without requiring a page refresh. Merge path keeps in-flight tools.
        compressionHydrationPendingRef.current = true;
        scheduleCurrentSessionHydration(80);
        return;
      }
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
   * After context compression, the disk snapshot is authoritative (archived
   * turns are already flattened into the normal timeline). Keep only
   * in-flight / optimistic live entries that are not yet on disk.
   */
  function _mergeCompressionHydration(
    prev: TimelineEntry[],
    snapshot: TimelineEntry[],
  ): TimelineEntry[] {
    const snapFlat = flattenArchivedSections(snapshot);
    const prevFlat = flattenArchivedSections(prev);

    const collectMessageKeys = (entries: TimelineEntry[], into: Set<string>) => {
      for (const e of entries) {
        if (e.kind !== 'message') continue;
        const k = _messageIdentityKey(e.data as ChatMessage);
        if (k) into.add(k);
      }
    };

    const snapLiveKeys = new Set<string>();
    collectMessageKeys(snapFlat, snapLiveKeys);

    // Snapshot is the post-compress truth (already includes former archived turns).
    let next = [...snapFlat];

    for (const e of prevFlat) {
      if (e.kind === 'message') {
        const k = _messageIdentityKey(e.data as ChatMessage);
        if (!k) continue;
        if (snapLiveKeys.has(k)) continue;
        // Optimistic message not yet flushed to disk — keep at the end.
        next.push(e);
        snapLiveKeys.add(k);
        continue;
      }
      if (e.kind === 'workflow') {
        const wf = e.data;
        if (wf.completed) continue;
        const hasNew = wf.events.some((evt) => {
          const tk = workflowToolEventKey(evt);
          if (!tk) return evt.type === 'summary_stream';
          return !timelineHasToolEvent(next, evt);
        });
        if (!hasNew) continue;
        const filteredEvents = wf.events.filter((evt) => {
          const tk = workflowToolEventKey(evt);
          if (!tk) return evt.type === 'summary_stream';
          return !timelineHasToolEvent(next, evt);
        });
        if (filteredEvents.length === 0) continue;
        next.push({
          kind: 'workflow',
          data: { ...wf, events: filteredEvents },
          _uid: e._uid || genUID(),
        });
      }
    }

    return next;
  }




  // ---- Actions ----

  // Whether the agent is currently busy and cannot accept a message inline.
  // Messages sent while busy are parked in the pending queue and auto-flushed
  // once the agent returns to idle (or sent immediately via "Send now").
  const isAgentBusy = useMemo(
    () =>
      isStreaming ||
      agentStatus === 'working' ||
      agentStatus === 'thinking' ||
      agentStatus === 'sleeping',
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
  const loadSkillsIfNeeded = useCallback(async () => {
    if (skillsLoadedRef.current || skillsLoading) return;
    setSkillsLoading(true);
    try {
      const resp = await skillAPI.getSkills();
      const list = Array.isArray(resp?.skills) ? resp.skills : [];
      setAvailableSkills(
        [...list].sort((a, b) =>
          String(a.display_name || a.name || a.dir).localeCompare(
            String(b.display_name || b.name || b.dir),
            undefined,
            { sensitivity: 'base' },
          ),
        ),
      );
      skillsLoadedRef.current = true;
    } catch (err) {
      console.error('[AIChatPage] Failed to load skills:', err);
    } finally {
      setSkillsLoading(false);
    }
  }, [skillsLoading]);

  const deliverMessage = useCallback((
    payload: {
      text: string;
      images: string[];
      attachments: UploadedFile[];
      skillDir?: string;
      skillName?: string;
    },
    opts?: { clearInputState?: boolean; salvageStream?: boolean },
  ) => {
    const { text, images: imgState, attachments: attState, skillDir, skillName } = payload;
    const clearInputState = opts?.clearInputState ?? true;
    const salvageStream = opts?.salvageStream ?? true;
    const skillId = (skillDir || '').trim();

    if (!text && imgState.length === 0 && attState.length === 0 && !skillId) return;

    // Build attachment description to include in WS message text (for Agent)
    const nonImageAttachments = attState.filter(a => !a.is_image);

    // Collect all image paths (from images state + image attachments)
    const allImages = [
      ...imgState,
      ...attState.filter(a => a.is_image).map(a => a.path),
    ];

    let wsText = text;
    if (skillId) {
      const tag = `<user_send_skill>${skillId}</user_send_skill>`;
      wsText = wsText ? `${tag}\n\n${wsText}` : tag;
    }
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
        .map((u) => toWebMediaUrl(u))
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

    // Display: show /skill chip text + user text (not the XML tag)
    const displayText = skillId
      ? (text ? `/${skillId} ${text}` : `/${skillId}`)
      : text;

    // Add user message to timeline (display text without [File: ...],
    // attachments stored separately for card rendering)
    const userMsg: ChatMessage = {
      role: 'user',
      content: displayText,
      timestamp: new Date().toISOString(),
      images: allImages.length > 0 ? allImages : undefined,
      attachments: fileAtts.length > 0 ? fileAtts : undefined,
    };
    setTimeline(prev => [...prev, {
      kind: 'message',
      data: userMsg,
      _uid: genUID(),
    }]);

    // Lock project path for this session on first user message (Solo archive grouping).
    {
      const pathToLock = (agentCwd || defaultCwd || '').trim();
      if (pathToLock) {
        const sid = currentSessionIdRef.current;
        if (sid) {
          setSessionProjectPath(agentId, sid, pathToLock);
          pendingProjectPathRef.current = null;
        } else {
          pendingProjectPathRef.current = pathToLock;
        }
      }
    }

    // Provisional session title from the first user message (agent <title>/<task_start> may overwrite later).
    if (userMsgCountRef.current === 0) {
      const provisional = (text || '')
        .trim()
        .replace(/\s+/g, ' ')
        .slice(0, 80)
        || (allImages.length > 0 ? '[image]' : '')
        || (fileAtts.length > 0 ? '[file]' : '');
      if (provisional) {
        const sid = currentSessionIdRef.current;
        if (sid) {
          setSessionTitleUpdate({ id: sid, title: provisional });
          pendingSessionTitleRef.current = null;
        } else {
          pendingSessionTitleRef.current = provisional;
        }
      }
    }

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
      setPendingSkill(null);
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
  }, [viewingHistorySession, currentSessionId, agentCwd, defaultCwd, agentId]);

  // When session id arrives after first send, persist pending project path / provisional title.
  useEffect(() => {
    if (!currentSessionId) return;
    if (pendingProjectPathRef.current) {
      setSessionProjectPath(agentId, currentSessionId, pendingProjectPathRef.current);
      pendingProjectPathRef.current = null;
    }
    if (pendingSessionTitleRef.current) {
      setSessionTitleUpdate({ id: currentSessionId, title: pendingSessionTitleRef.current });
      pendingSessionTitleRef.current = null;
    }
  }, [currentSessionId, agentId]);

  const cwdLocked = useMemo(() => {
    if (viewingHistorySession) return true;
    return timeline.some(
      (e) => e.kind === 'message' && (e.data as ChatMessage).role === 'user',
    );
  }, [timeline, viewingHistorySession]);

  const handleSend = () => {
    const text = inputText.trim();
    const skillDir = pendingSkill?.dir || '';
    if (!text && images.length === 0 && attachments.length === 0 && !skillDir) return;

    // Park when agent is busy, OR a turn was just released and we are waiting
    // for busy status, OR there are already queued messages (keep FIFO order).
    // This prevents rapid idle sends from all racing to the backend at once.
    const shouldQueue =
      isAgentBusy ||
      waitForBusyAfterPendingSendRef.current ||
      isFlushingPendingRef.current ||
      pendingMessagesRef.current.length > 0;

    if (shouldQueue) {
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
        skillDir: pendingSkill?.dir,
        skillName: pendingSkill?.name,
      };
      setPendingMessages(prev => [...prev, snapshot]);
      // Clear the composer only — do not touch streaming state (agent is busy).
      setInputText('');
      setPendingSkill(null);
      setImages([]);
      setAttachments([]);
      if (inputRef.current) {
        inputRef.current.style.height = 'auto';
      }
      return;
    }

    waitForBusyAfterPendingSendRef.current = true;
    deliverMessage(
      {
        text,
        images,
        attachments,
        skillDir: pendingSkill?.dir,
        skillName: pendingSkill?.name,
      },
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
    waitForBusyAfterPendingSendRef.current = true;
    deliverMessage(
      {
        text: target.text,
        images: target.images,
        attachments: target.attachments,
        skillDir: target.skillDir,
        skillName: target.skillName,
      },
      { clearInputState: false, salvageStream: false },
    );
  }, [deliverMessage]);

  // Cancel / remove a pending message without sending it.
  const handleCancelPending = useCallback((id: string) => {
    setPendingMessages(prev => prev.filter(m => m.id !== id));
  }, []);

  // Header "Send now": release only the first queued message (sequential drain).
  const handleSendNextPending = useCallback(() => {
    const queue = pendingMessagesRef.current;
    if (queue.length === 0) return;
    const next = queue[0];
    setPendingMessages(prev => prev.filter(m => m.id !== next.id));
    waitForBusyAfterPendingSendRef.current = true;
    deliverMessage(
      {
        text: next.text,
        images: next.images,
        attachments: next.attachments,
        skillDir: next.skillDir,
        skillName: next.skillName,
      },
      { clearInputState: false, salvageStream: false },
    );
  }, [deliverMessage]);

  // Clear the entire queue without sending anything.
  const handleCancelAllPending = useCallback(() => {
    setPendingMessages([]);
    waitForBusyAfterPendingSendRef.current = false;
  }, []);

  // Auto-drain: when idle, release exactly ONE pending message, then wait until
  // the agent becomes busy (and later idle again) before releasing the next.
  useEffect(() => {
    if (isAgentBusy) {
      // Agent picked up the released turn — allow another drain on the next idle.
      waitForBusyAfterPendingSendRef.current = false;
      return;
    }
    if (isFlushingPendingRef.current) return;
    if (waitForBusyAfterPendingSendRef.current) return;
    const queue = pendingMessagesRef.current;
    if (queue.length === 0) return;

    const next = queue[0];
    isFlushingPendingRef.current = true;
    waitForBusyAfterPendingSendRef.current = true;
    setPendingMessages((prev) => prev.filter((m) => m.id !== next.id));
    deliverMessage(
      {
        text: next.text,
        images: next.images,
        attachments: next.attachments,
        skillDir: next.skillDir,
        skillName: next.skillName,
      },
      { clearInputState: false, salvageStream: false },
    );
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

  const handleNewSession = (projectPath?: string) => {
    const previousSid = currentSessionIdRef.current;
    // Stop parent + cancel in-flight sub-agents before switching sessions,
    // otherwise orphaned delegates keep streaming into the new timeline.
    wsServiceRef.current?.stopTask();
    newSessionPendingRef.current = true;
    setIsLoadingSession(true);
    setSessionLoadingLabel(t('aiChat.creatingSession'));
    // Clear session filter so responses with the new sid are not dropped while
    // we wait for current_session / HTTP fallback to set the canonical id.
    currentSessionIdRef.current = null;
    setCurrentSessionId(null);
    wsServiceRef.current?.setActiveSession(null);
    wsServiceRef.current?.newSession();
    requestSessionListRefresh(agentId, null);
    setTimeline([]);
    streamingTextRef.current = '';
    setStreamingText('');
    finalizingRef.current = false;
    diskSessionLoadedRef.current = false;
    pendingFilePushesRef.current = [];
    pendingHydrationMediaRef.current = [];
    pendingHydrationWorkflowEventsRef.current = [];
    compressionHydrationPendingRef.current = false;
    sessionBootstrapDoneRef.current = false;
    viewingHistorySessionRef.current = false;
    setViewingHistorySession(false);
    setPlanSteps([]);
    setTokenStats(null);
    setImages([]);
    setAttachments([]);
    // Drop parked sends for the previous session; new session starts empty.
    try {
      if (previousSid) localStorage.removeItem(pendingQueueStorageKey(previousSid));
      localStorage.removeItem(pendingQueueStorageKey(null));
    } catch { /* ignore */ }
    setPendingMessages([]);
    waitForBusyAfterPendingSendRef.current = false;
    pendingQueueHydratedKeyRef.current = null;
    pendingSessionTitleRef.current = null;
    // Folder-scoped new session: bind cwd to that project path immediately.
    const boundPath = typeof projectPath === 'string' ? projectPath.trim() : '';
    if (boundPath) {
      pendingProjectPathRef.current = boundPath;
      setAgentCwd(boundPath);
      const dirName = agentProfile?.dir_name || agentId;
      void adminAPI.setWorkingDirectory(dirName, boundPath).catch((err: any) => {
        console.error('[AIChatPage] Failed to set working directory for folder session:', err);
      });
      try {
        pushCwdRecent(boundPath);
      } catch { /* ignore */ }
    } else {
      pendingProjectPathRef.current = null;
      // New session: unlock path picker; keep last cwd as default selection (or system default).
      if (defaultCwd && !agentCwd) setAgentCwd(defaultCwd);
    }
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
          const entries = buildTimelineFromSession(session?.messages || [], session?.events || []);
          setTimeline(entries);
          if (currentSid) {
            currentSessionIdRef.current = currentSid;
            wsServiceRef.current?.setActiveSession(currentSid);
            setCurrentSessionId(currentSid);
            requestSessionListRefresh(agentId, currentSid);
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
        const entries = buildTimelineFromSession(
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
        const meta = getSessionMeta(agentId, sessionId);
        if (meta?.projectPath) setAgentCwd(meta.projectPath);
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
        const entries = buildTimelineFromSession(
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
  // Never render the old "已归档" fold — expand any leftover sections inline.
  const displayTimeline = useMemo(() => flattenArchivedSections(timeline), [timeline]);

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
        agentBusy={
          isStreaming ||
          agentStatus === 'working' ||
          agentStatus === 'thinking'
        }
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
                <h2 className="font-bold text-textMain text-sm truncate">
                  {agentProfile?.agent_name || modelName || agentId}
                  {switchingModel ? (
                    <span className="ml-1 text-[10px] font-normal text-textMuted animate-pulse">switching…</span>
                  ) : null}
                </h2>
                <StatusBadge status={agentStatus} />
              </div>
            </div>

            {/* Header actions (right side) */}
            <div className="flex items-center gap-1.5">
              {/* Classic | Solo render mode */}
              <div
                className="flex items-center rounded-lg border border-border overflow-hidden flex-shrink-0"
                title="Chat UI mode"
              >
                <button
                  type="button"
                  onClick={() => setUiModePersisted('classic')}
                  className={`px-1.5 sm:px-2 py-1.5 text-[10px] sm:text-[11px] font-medium transition-colors flex items-center gap-1 ${
                    !isSolo ? 'bg-primary/15 text-primary' : 'text-textMuted hover:bg-primary/10'
                  }`}
                  title="Classic chat bubbles"
                >
                  <MessageSquare size={14} />
                  <span className="hidden sm:inline">经典</span>
                </button>
                <button
                  type="button"
                  onClick={() => setUiModePersisted('solo')}
                  className={`px-1.5 sm:px-2 py-1.5 text-[10px] sm:text-[11px] font-medium transition-colors flex items-center gap-1 border-l border-border ${
                    isSolo ? 'bg-primary/15 text-primary' : 'text-textMuted hover:bg-primary/10'
                  }`}
                  title="Solo document stream (Codex / Cursor Agent style)"
                >
                  <AlignLeft size={14} />
                  <span className="hidden sm:inline">Solo</span>
                </button>
              </div>
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
                onClick={() => {
                  if (isSolo) setSoloExpandDetails((v) => !v);
                  else toggleWorkflow();
                }}
                className={`p-1.5 sm:p-2 rounded-lg transition-colors flex-shrink-0 ${
                  (isSolo ? soloExpandDetails : showWorkflow)
                    ? 'bg-primary/15 hover:bg-primary/20'
                    : 'hover:bg-primary/10'
                }`}
                title={
                  isSolo
                    ? (soloExpandDetails ? 'Collapse activity details' : 'Expand activity details')
                    : (showWorkflow ? 'Hide workflow details' : 'Show workflow details')
                }
              >
                {(isSolo ? soloExpandDetails : showWorkflow)
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
        <div className="flex-1 relative min-h-0" style={{ minHeight: 0 }}>
        <div className="h-full overflow-y-auto px-2 sm:px-4 py-3 sm:py-4 relative" style={{ minHeight: 0 }} ref={messagesContainerRef} onScroll={handleMessagesScroll}>
          {isSolo && soloUserNavNodes.length > 0 && (
            <div
              className="pointer-events-none sticky top-0 z-30 float-right w-0 h-0"
              aria-hidden={false}
            >
              <div className="pointer-events-auto absolute right-0 top-[42vh] -translate-y-1/2 translate-x-[-4px]">
                <SoloUserNavRail
                  nodes={soloUserNavNodes}
                  activeId={soloUserNavNodes[soloUserNavNodes.length - 1]?.id}
                  onJump={jumpToSoloUserMessage}
                />
              </div>
            </div>
          )}
          <div className={soloColumnClass}>
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


          {displayTimeline.map((entry, i) => {
            // Stable key from _uid prevents remounting during turn updates or lazy loading
            const entryKey = entry._uid || `entry-${i}`;

            if (entry.kind === 'message') {
              const msgProps = {
                message: entry.data,
                senderName:
                  entry.data.role === 'user'
                    ? (currentUser?.name || undefined)
                    : (agentProfile?.agent_name || undefined),
                senderAvatar:
                  entry.data.role === 'user'
                    ? (currentUser?.avatar || null)
                    : (resolveChatAvatar(agentProfile?.chat_profile) || null),
              };
              return isSolo
                ? <SoloMessage key={entryKey} {...msgProps} anchorId={entryKey} />
                : <MessageBubble key={entryKey} {...msgProps} />;
            }
            if (entry.kind === 'workflow') {
              const lastIncompleteIdx = (() => {
                for (let j = displayTimeline.length - 1; j >= 0; j--) {
                  if (displayTimeline[j].kind === 'workflow' && !(displayTimeline[j] as { kind: 'workflow'; data: WorkflowBlock }).data.completed) return j;
                }
                return -1;
              })();
              if (isSolo) {
                // Only merge consecutive *incomplete* workflow fragments (live turn).
                // Merging all adjacent completed blocks collapses separate turns after
                // compression and makes tool calls appear in the wrong order.
                const curBlock = (entry as { kind: 'workflow'; data: WorkflowBlock }).data;
                if (
                  i > 0 &&
                  displayTimeline[i - 1].kind === 'workflow' &&
                  !(displayTimeline[i - 1] as { kind: 'workflow'; data: WorkflowBlock }).data.completed &&
                  !curBlock.completed
                ) {
                  return null;
                }
                const blocks: WorkflowBlock[] = [curBlock];
                if (!curBlock.completed) {
                  let j = i + 1;
                  while (
                    j < displayTimeline.length &&
                    displayTimeline[j].kind === 'workflow' &&
                    !(displayTimeline[j] as { kind: 'workflow'; data: WorkflowBlock }).data.completed
                  ) {
                    blocks.push((displayTimeline[j] as { kind: 'workflow'; data: WorkflowBlock }).data);
                    j += 1;
                  }
                }
                const merged = blocks.length > 1 ? mergeWorkflowBlocks(blocks) : curBlock;
                const groupHasIncomplete = !merged.completed;
                const turnMs = groupHasIncomplete ? turnStartedMs : undefined;
                return (
                  <SoloActivityRow
                    key={entryKey}
                    block={merged}
                    expandDetails={soloExpandDetails}
                    turnStartedMs={turnMs}
                  />
                );
              }
              if (!showWorkflow) return null;
              const turnMs = i === lastIncompleteIdx ? turnStartedMs : undefined;
              return (
                <WorkflowBlockView
                  key={entryKey}
                  block={entry.data}
                  blockKey={i}
                  turnStartedMs={turnMs}
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
                <div key={entryKey} className={`flex items-center gap-1.5 py-0.5 my-0.5 ${isSolo ? 'mx-0' : 'mx-2 sm:mx-9'}`}>
                  <div className="flex-1 h-px bg-border/25" />
                  {icon}
                  <span className="text-[10px] text-textMuted/45 font-mono shrink-0">{label}</span>
                  <div className="flex-1 h-px bg-border/25" />
                </div>
              );
            }
            if (entry.kind === 'archived_section') {
              // Flattened by displayTimeline — should never reach here.
              return null;
            }
            return null;
          })}

          {/* Agent working indicator (classic only when workflow hidden) */}
          {!isSolo && !showWorkflow && hasActiveWorkflow && (
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
              avatarSrc={resolveChatAvatar(agentProfile?.chat_profile)}
              variant={isSolo ? 'solo' : 'classic'}
              senderName={agentProfile?.agent_name}
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
        </div>
        </div>

        {/* Image & attachment preview */}
        {(images.length > 0 || attachments.length > 0 || isUploading) && (
          <div className={`px-2 sm:px-4 py-2 flex gap-2 flex-wrap items-center flex-shrink-0 ${
            isSolo ? 'bg-transparent' : 'border-t border-border bg-panel'
          }`}>
            <div className={`${soloColumnClass} flex gap-2 flex-wrap items-center`}>
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
          </div>
        )}

        {/* Pending message queue — docked above the input area (and above the
            plan panel when it is open). Messages here are NOT yet part of the
            conversation: they only enter the timeline when drained one-by-one
            after each agent reply, or when the user clicks send-next / send-now.

            UI shape (one large container, no per-message card):
              ┌────────────────────────────────────────────────────────────┐
              │ ⏱ 2 Queued  ↗ one-by-one when idle    [Send next] [▾]     │
              ├────────────────────────────────────────────────────────────┤
              │ #1  How are you?                              [⚡] [×]      │
              │ #2  Feeling alright?                          [⚡] [×]      │
              └────────────────────────────────────────────────────────────┘
        */}
        {pendingMessages.length > 0 && (
          <div className="px-2 sm:px-3 pt-2 pb-1 border-t border-border/40 bg-bgLight flex-shrink-0">
            <div className={soloColumnClass}>
            <div className="rounded-lg border border-border/50 bg-transparent overflow-hidden">
              {/* Header bar: status + actions */}
              <div className="flex items-center gap-2 px-2.5 py-1.5 border-b border-border/40 bg-transparent">
                <Clock size={11} className="text-primary flex-shrink-0" />
                <span className="text-[11px] text-textMain font-semibold">
                  {t('aiChat.pendingCount', { count: pendingMessages.length })}
                </span>
                <span className="text-[10px] text-textMuted">
                  · ↗ {t('aiChat.pendingAutoSendHint')}
                </span>
                <div className="flex-1" />
                {/* Send-all: flush the whole queue immediately. */}
                <button
                  type="button"
                  onClick={handleSendNextPending}
                  className="flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium text-primary hover:bg-black/[0.04] dark:hover:bg-white/[0.06] transition-colors"
                  title={t('aiChat.sendNext')}
                >
                  <Zap size={10} />
                  {t('aiChat.sendNext')}
                </button>
                <button
                  type="button"
                  onClick={handleCancelAllPending}
                  className="flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium text-textMuted hover:bg-black/[0.04] dark:hover:bg-white/[0.06] transition-colors"
                  title={t('aiChat.pendingClearAll')}
                >
                  <X size={10} />
                  {t('aiChat.pendingClearAll')}
                </button>
                <button
                  type="button"
                  onClick={() => setPendingCollapsed(c => !c)}
                  className="flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[10px] font-medium text-textMuted hover:bg-black/[0.04] dark:hover:bg-white/[0.06] transition-colors"
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
                        className="group flex items-center gap-2 px-2.5 py-1.5 border-b border-border/30 last:border-b-0 hover:bg-black/[0.03] dark:hover:bg-white/[0.04] transition-colors"
                      >
                        {/* Queue position badge */}
                        <span className="flex-shrink-0 text-[10px] font-mono text-textMuted min-w-[28px]">
                          {t('aiChat.pendingQueuePosition', { index: idx + 1 })}
                        </span>
                        {/* Message preview + attachment summary */}
                        <div className="flex-1 min-w-0 flex items-center gap-2">
                          {preview ? (
                            <span className="truncate text-[12px] text-textMain">
                              {preview}
                            </span>
                          ) : (
                            <span className="text-[12px] italic text-textMuted">
                              {imgCount > 0 || fileCount > 0
                                ? t('aiChat.pendingAttachments', { images: imgCount, files: fileCount })
                                : t('aiChat.pendingLabel')}
                            </span>
                          )}
                          {(imgCount > 0 || fileCount > 0) && preview && (
                            <span className="flex-shrink-0 text-[10px] text-textMuted whitespace-nowrap">
                              {t('aiChat.pendingAttachments', { images: imgCount, files: fileCount })}
                            </span>
                          )}
                        </div>
                        {/* Row actions — visible on hover for a cleaner default */}
                        <div className="flex-shrink-0 flex items-center gap-0.5 opacity-60 group-hover:opacity-100 transition-opacity">
                          <button
                            type="button"
                            onClick={() => handleSendPendingNow(pm.id)}
                            className="p-1 rounded text-primary hover:bg-black/[0.04] dark:hover:bg-white/[0.06] transition-colors"
                            title={t('aiChat.sendNow')}
                          >
                            <Zap size={12} />
                          </button>
                          <button
                            type="button"
                            onClick={() => handleCancelPending(pm.id)}
                            className="p-1 rounded text-textMuted hover:bg-black/[0.04] dark:hover:bg-white/[0.06] hover:text-textMain transition-colors"
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
          </div>
        )}

        {/* Inline Plan Panel (docked above input) */}
        {showPlanViewer && (
          <div className="px-2 sm:px-3 pt-2 border-t border-border/40 bg-bgLight flex-shrink-0">
            <div className={soloColumnClass}>
            {effectivePlanSteps.length > 0 ? (
              <PlanBlock
                steps={effectivePlanSteps}
                className="mb-0 ml-0 border border-border/50 rounded-lg overflow-hidden bg-transparent"
              />
            ) : (
              <div className="text-xs text-textMuted bg-transparent border border-border/50 rounded-lg px-3 py-2">{t('aiChat.noPlanYet')}</div>
            )}
            </div>
          </div>
        )}

        {/* Input Area — shared Solo-style composer for classic + solo */}
        <div
          className={`flex-shrink-0 overflow-visible px-2 sm:px-4 py-2 sm:py-3 ${
            isSolo ? 'bg-transparent' : 'bg-transparent border-t border-border/30'
          }`}
        >
          <div className={`${soloColumnClass}${isLoadingSession ? ' opacity-50 pointer-events-none' : ''}`}>
              <div
                className={`w-full flex items-center gap-1 rounded-2xl border border-border/80 min-h-[40px] focus-within:ring-1 focus-within:ring-primary/50 shadow-sm ${
                  isLoadingSession ? 'bg-border/40' : 'bg-bgLight/80 dark:bg-black/20'
                }`}
              >
                <div className="pl-1.5 shrink-0 flex items-center self-end min-h-[38px]">
                  <SoloAttachMenu
                    disabled={isLoadingSession}
                    skills={availableSkills}
                    skillsLoading={skillsLoading}
                    onOpenSkills={loadSkillsIfNeeded}
                    onSelectSkill={(skill) => {
                      const dir = (skill.dir || skill.name || '').trim();
                      if (!dir) return;
                      setPendingSkill({
                        dir,
                        name: skill.display_name || skill.name || dir,
                      });
                      // Focus composer so user can add follow-up text.
                      requestAnimationFrame(() => inputRef.current?.focus());
                    }}
                    onUploadFiles={() => {
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
                            if (resp.is_image) setImages((prev) => [...prev, resp.url]);
                            else setAttachments((prev) => [...prev, resp]);
                          } else {
                            const resp = await agentSessionAPI.uploadFiles(agentId, fileArray);
                            for (const f of resp.files) {
                              if (f.is_image) setImages((prev) => [...prev, f.url]);
                              else setAttachments((prev) => [...prev, f]);
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
                    onUploadFolder={() => {
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
                            if (f.is_image) setImages((prev) => [...prev, f.url]);
                            else setAttachments((prev) => [...prev, f]);
                          }
                        } catch (err: any) {
                          console.error('[AIChatPage] Folder upload failed:', err);
                        } finally {
                          setIsUploading(false);
                        }
                      };
                      input.click();
                    }}
                    onUploadImages={() => fileInputRef.current?.click()}
                  />
                </div>
                {pendingSkill ? (
                  <button
                    type="button"
                    onClick={() => setPendingSkill(null)}
                    className="shrink-0 self-end mb-2 ml-0.5 inline-flex items-center gap-0.5 rounded-md px-1.5 py-0.5 text-[13px] font-medium border-0 cursor-pointer"
                    style={{ color: '#b08d57', background: 'color-mix(in srgb, #b08d57 12%, transparent)' }}
                    title={`Remove skill /${pendingSkill.dir}`}
                  >
                    <span>/{pendingSkill.dir}</span>
                    <X size={12} className="opacity-70" />
                  </button>
                ) : null}
                <textarea
                  ref={inputRef}
                  value={inputText}
                  onChange={(e) => setInputText(e.target.value)}
                  onKeyDown={handleKeyDown}
                  onPaste={handlePaste}
                  placeholder={
                    isLoadingSession
                      ? sessionLoadingLabel
                      : pendingSkill
                        ? 'Add details for this skill…'
                        : 'Type a message...'
                  }
                  disabled={isLoadingSession}
                  className={`flex-1 min-w-0 border-0 px-2 py-2 text-sm text-textMain placeholder-textMuted resize-none focus:outline-none min-h-[38px] max-h-[120px] bg-transparent leading-5 ${
                    isLoadingSession ? 'text-textMuted cursor-not-allowed' : ''
                  }`}
                  rows={1}
                  style={{ height: 'auto' }}
                  onInput={(e) => {
                    const target = e.target as HTMLTextAreaElement;
                    target.style.height = 'auto';
                    target.style.height = Math.min(target.scrollHeight, 120) + 'px';
                  }}
                />
                <div className="shrink-0 flex items-center gap-1 pr-1.5 self-end min-h-[38px]">
                  <SoloModelPicker
                    cards={modelCards}
                    currentCardName={currentCardName}
                    modelName={modelName}
                    fallbackLabel={agentProfile?.agent_name || agentId}
                    switching={switchingModel}
                    disabled={isLoadingSession}
                    onSelect={(cardName) => {
                      setSwitchingModel(true);
                      wsServiceRef.current?.switchModel(cardName);
                    }}
                    onAddModels={() => {
                      window.dispatchEvent(new CustomEvent('switchView', { detail: 'models' }));
                    }}
                  />
                  {isStreaming || agentStatus === 'working' || agentStatus === 'thinking' ? (
                    <>
                      <button
                        onClick={handleSend}
                        disabled={isLoadingSession || (!inputText.trim() && images.length === 0 && attachments.length === 0 && !pendingSkill)}
                        className="w-8 h-8 rounded-full bg-amber-500 hover:bg-amber-600 transition-colors flex items-center justify-center disabled:opacity-50 disabled:cursor-not-allowed border-0 cursor-pointer"
                        title={t('aiChat.queueMessage')}
                      >
                        <Send size={14} className="text-white" />
                      </button>
                      <button
                        onClick={handleStop}
                        className="w-8 h-8 rounded-full bg-red-500 hover:bg-red-600 transition-colors flex items-center justify-center border-0 cursor-pointer"
                        title="Stop"
                      >
                        <Square size={14} className="text-white" />
                      </button>
                    </>
                  ) : (
                    <button
                      onClick={handleSend}
                      disabled={isLoadingSession || (!inputText.trim() && images.length === 0 && attachments.length === 0 && !pendingSkill)}
                      className="w-8 h-8 rounded-full bg-primary hover:bg-primary/90 transition-colors flex items-center justify-center disabled:opacity-50 disabled:cursor-not-allowed border-0 cursor-pointer"
                      title="Send"
                    >
                      <Send size={14} className="text-white" />
                    </button>
                  )}
                </div>
              </div>
              <SoloContextFooter
                cwd={agentCwd || defaultCwd}
                tokenStats={tokenStats}
                locked={cwdLocked}
                onViewReport={() => setShowContextViewer(true)}
                onSelectCwd={async (pickedPath) => {
                  try {
                    const dirName = agentProfile?.dir_name || agentId;
                    await adminAPI.setWorkingDirectory(dirName, pickedPath);
                    pushCwdRecent(pickedPath);
                    setAgentCwd(pickedPath);
                    pendingProjectPathRef.current = pickedPath;
                  } catch (err: any) {
                    console.error('[AIChatPage] Failed to set working directory:', err);
                    alert(`Failed to set working directory: ${err.message || err}`);
                  }
                }}
              />

            <input
              ref={fileInputRef}
              type="file"
              accept="image/*"
              multiple
              onChange={handleImageUpload}
              className="hidden"
            />
          </div>
        </div>
      </div>
    </div>
  );
};
