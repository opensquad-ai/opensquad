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
import React, { useState, useEffect, useLayoutEffect, useRef, useCallback, useMemo, Suspense } from 'react';
import {
  Send, Square,
  PanelLeftOpen, PanelLeftClose, PanelRightOpen, PanelRightClose, X, FileIcon, FileText, Upload,
  ChevronUp, ChevronDown, Moon, Zap, Bell,
  Clock,
} from 'lucide-react';

import { useTranslation } from 'react-i18next';
import { getAiWsService, releaseAiWsService, AIWSMessage, AIWebSocketStatus } from '../services/aiWebSocket';
import { agentSessionAPI, authAPI, adminAPI, AdminAgent, modelCardAPI, ModelCardInfo, skillAPI, SkillInfo, SERVER_BASE_URL } from '../services/api';
import type { AgentSession } from '../services/api';
import {
  cancelPendingVoiceHangup,
  clearVoiceCallPersist,
  readVoiceCallPersist,
  schedulePendingVoiceHangup,
  writeVoiceCallPersist,
} from '../utils/voiceCallPersist';
import { resolveChatAvatar, toAbsoluteMediaUrl } from '../utils/image';
import { playGentleNotificationSound } from '../utils/sounds';
import { OpenSquadLoader } from './OpenSquadLoader';
import {
  appendWorkflowEvent,
  buildTimelineFromSession,
  foldTaskProcessSinceLastUser,
  formatUserSkillDisplayContent,
  genTimelineUID,
  rebaseTimelineUids,
  timelineHasToolEvent,
  timelineHasVisibleChatContent,
  workflowToolEventKey,
  sealIncompleteWorkflows,
  toWebMediaUrl,
  type TimelineEntry,
  type WorkflowBlock,
  type WorkflowEvent,
} from '../utils/aiChatTimeline';
import { pushCwdRecent } from '../utils/cwdRecents';
import {
  putCachedSessionTimeline,
  getCachedSessionTimeline,
  getCachedSessionTimelineMeta,
  SESSION_HISTORY_PAGE_SIZE,
} from '../utils/sessionTimelineCache';
import { pickSessionLiveTimeline } from '../utils/sessionLiveTimeline';
import { useTextSelectionFreeze } from '../hooks/useTextSelectionFreeze';
import { useIsCompactAgentWeb, useIsMobileViewport } from '../hooks/useMatchMedia';
import {
  setSessionProjectPath,
  setSessionWorkspaceId,
  getSessionMeta,
  requestSessionListRefresh,
} from '../utils/sessionProjectMeta';
import {
  bindAgentWebUiSyncPush,
  pullAgentWebUiState,
  schedulePushAgentWebUiState,
  setAgentWebUiSyncTarget,
} from '../utils/agentWebUiSync';
import {
  loadWorkspaceStore,
  loadWorkspaceStoreResolved,
  setWorkspaceStoreAliases,
  pruneGoneSessionTabs,
  migrateProjectPathsToWorkspaces,
  ensureWorkspace,
  openWorkspaceTab,
  closeWorkspaceTab,
  openContentTab,
  closeContentTab,
  setActiveContentTab,
  reorderContentTabs,
  contentTabKey,
  workspaceDisplayName,
  pathsEqual,
  WORKSPACES_CHANGED_EVENT,
  splitPane,
  applySplitToLayout,
  commitWorkspaceLayout,
  closePane,
  closeAllTabsInPane,
  setFocusedPane,
  resizeSplit,
  collectLeaves,
  findLeaf,
  getFocusedPaneTabs,
  parseContentTabKey,
  type WorkspaceStoreSnapshot,
  type ContentTab,
  type Workspace,
  type SplitNode,
  type SplitDirection,
} from '../utils/workspaceStore';

// AI Chat sub-components
import { MessageBubble, ChatMessage, FileAttachment } from './ai-chat/MessageBubble';
import { StreamingMessage } from './ai-chat/StreamingMessage';
import { SoloMessage } from './ai-chat/SoloMessage';
import { SoloActivityRow, mergeWorkflowBlocks } from './ai-chat/SoloActivityRow';
import {
  collectHtmlEmbedsPrecedingMessage,
  HtmlEmbedBlock,
  type HtmlEmbedPayload,
} from './ai-chat/HtmlEmbedBlock';
import { ProjectFilesPanel, type ProjectFileOpenRequest } from './ai-chat/ProjectFilesPanel';
import { SessionChangesBar, COMMIT_PUSH_MESSAGE, type SessionChangesSummary } from './ai-chat/SessionChangesBar';
import { RestoreCheckpointModal } from './ai-chat/RestoreCheckpointModal';
import { WorkspaceTabBar } from './ai-chat/WorkspaceTabBar';
import { CloseWorkspaceModal } from './ai-chat/CloseWorkspaceModal';
import { CreateWorkspaceModal } from './ai-chat/CreateWorkspaceModal';
import { confirmDiscardFileDirty, prefetchWorkspaceFile, getWorkspaceFileCache } from './ai-chat/WorkspaceFileEditor';
import { PaneSplitLayout } from './ai-chat/PaneSplitLayout';
import type { PaneShellHandlers } from './ai-chat/WorkspacePaneShell';
import { SessionChatPane } from './ai-chat/SessionChatPane';
import {
  AgentWebComposer,
  type AgentWebComposerHandle,
  type ComposerSendPayload,
} from './ai-chat/AgentWebComposer';
import { useWorkflowExpandLevel } from '../utils/workflowExpandPref';
import {
  SoloUserNavRail,
  buildUserNavNodesFromTimeline,
  userNavAnchorDomId,
} from './ai-chat/SoloUserNavRail';
import { TaskFoldBlock } from './ai-chat/TaskFoldBlock';
import { TimelineRow } from './ai-chat/TimelineRow';
import { ChatTimeline } from './ai-chat/ChatTimeline';
import { SoloModelPicker } from './ai-chat/SoloModelPicker';
import { EffortPicker, type ReasoningEffort } from './ai-chat/EffortPicker';
import { ModePicker, type AgentMode } from './ai-chat/ModePicker';
import { ModeSwitchApprovalCard, type ModeSwitchApproval } from './ai-chat/ModeSwitchApprovalCard';
import { OptionsApprovalCard, type OptionsProposal, hydrateOptionsProposalsFromEvents } from './ai-chat/OptionsApprovalCard';
import { SoloAttachMenu } from './ai-chat/SoloAttachMenu';
import { SlashMenu } from './ai-chat/SlashMenu';
import {
  filterGoalSubcommands,
  filterSkillsForSlash,
  filterSlashCommands,
  parseGoalSendQuery,
  parseSlashInput,
  slashCommandTriggerText,
  type GoalSubcommandDef,
  type SlashCommandDef,
} from './ai-chat/slashCommands';
import { SoloContextFooter } from './ai-chat/SoloContextFooter';
import { PlanBlock, PlanStep, parsePlanContent } from './ai-chat/PlanBlock';
import { StatusBadge, AgentStatus } from './ai-chat/StatusBadge';
import { SessionSidebar } from './ai-chat/SessionSidebar';
import { SessionSearchModal } from './ai-chat/SessionSearchModal';
import { ContextViewer, ContextEntry } from './ai-chat/ContextViewer';

const SkillManagerPage = React.lazy(() =>
  import('./SkillManagerPage').then((m) => ({ default: m.SkillManagerPage })),
);
const PluginManagerPage = React.lazy(() =>
  import('./PluginManagerPage').then((m) => ({ default: m.PluginManagerPage })),
);
const RolesPage = React.lazy(() => import('./RolesPage'));
import {
  applyJobStatus,
  applyJobStdout,
  seedShellStreamFromToolCall,
  sealShellStreamFromResult,
  rebuildShellStreamsFromTimeline,
  type ShellStreamState,
} from '../utils/shellJobGrouping';

const genUID = (): string => genTimelineUID();

interface AIChatPageProps {
  agentId: string;
  onBack: () => void;
  /** The currently logged-in user (for avatar/name in user bubbles). */
  currentUser?: { id: string; name: string; avatar?: string | null } | null;
  onOpenProfile?: () => void;
  onOpenSettings?: () => void;
}

/** Last manually selected model / effort — survives refresh before config loads. */
function lastModelStorageKey(agentId: string) {
  return `opensquad.agent.${agentId}.lastModel`;
}
function loadLastModelPick(agentId: string): { card: string | null; effort: ReasoningEffort | null } {
  try {
    const raw = localStorage.getItem(lastModelStorageKey(agentId));
    if (!raw) return { card: null, effort: null };
    const parsed = JSON.parse(raw);
    const card = typeof parsed?.card === 'string' && parsed.card.trim() ? parsed.card.trim() : null;
    const effortRaw = parsed?.effort;
    const effort =
      effortRaw === 'low' || effortRaw === 'medium' || effortRaw === 'high' ? effortRaw : null;
    return { card, effort };
  } catch {
    return { card: null, effort: null };
  }
}
function saveLastModelPick(
  agentId: string,
  patch: { card?: string | null; effort?: ReasoningEffort | null },
) {
  try {
    const prev = loadLastModelPick(agentId);
    const next = {
      card: patch.card !== undefined ? patch.card : prev.card,
      effort: patch.effort !== undefined ? patch.effort : prev.effort,
    };
    localStorage.setItem(lastModelStorageKey(agentId), JSON.stringify(next));
  } catch {
    /* ignore */
  }
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
  type?: string;
  duration?: number;
}

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

export const AIChatPage: React.FC<AIChatPageProps> = ({ agentId, onBack, currentUser, onOpenProfile, onOpenSettings }) => {
  const { t } = useTranslation();
  // ---- State ----
  const [timeline, setTimelineState] = useState<TimelineEntry[]>([]);
  /** Per-session live timelines for split-pane parallel turns (sid → entries). */
  const [liveTimelinesBySession, setLiveTimelinesBySession] = useState<Record<string, TimelineEntry[]>>({});
  const liveTimelinesBySessionRef = useRef<Record<string, TimelineEntry[]>>({});
  const timelineRef = useRef<TimelineEntry[]>([]);
  /** Sid of the WS event currently being handled (routes setTimeline into the right bucket). */
  const eventSidRef = useRef<string>('');
  /** Whether the Agent Web page is currently in the foreground (visible + window focused). */
  const pageActiveRef = useRef<boolean>(
    typeof document !== 'undefined' && document.visibilityState === 'visible' && document.hasFocus(),
  );
  // 实时跟踪页面是否处于前台：切走/最小化/失焦 → false，回来 → true。
  // 供结束提示音判断"后台才响铃"使用。
  useEffect(() => {
    const update = () => {
      pageActiveRef.current =
        document.visibilityState === 'visible' && document.hasFocus();
    };
    document.addEventListener('visibilitychange', update);
    window.addEventListener('focus', update);
    window.addEventListener('blur', update);
    return () => {
      document.removeEventListener('visibilitychange', update);
      window.removeEventListener('focus', update);
      window.removeEventListener('blur', update);
    };
  }, []);
  const [streamingText, setStreamingText] = useState('');
  const [isStreaming, setIsStreaming] = useState(false);
  /** Per-session streaming preview (split panes must not share one global buffer). */
  const streamingTextBySessionRef = useRef<Record<string, string>>({});
  const [streamingTextBySession, setStreamingTextBySession] = useState<Record<string, string>>({});
  const isStreamingBySessionRef = useRef<Record<string, boolean>>({});
  const [isStreamingBySession, setIsStreamingBySession] = useState<Record<string, boolean>>({});
  /**
   * 流式 chunk 节流：WS 的 stream 事件每秒可达几十~上百个（多会话并行时更甚）。
   * 每次 chunk 直接 setState 会让整个 AIChatPage（含所有 pane/tab/workflow 行）
   * 每 chunk 全量重渲染。这里把 chunk 先累积进 ref，再以 ~66ms 窗口批量刷新
   * 一次 UI，把渲染频率从"事件频率"降为"~15fps 上限"。
   */
  const streamUiFlushTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const flushStreamingUi = useCallback(() => {
    setStreamingTextBySession({ ...streamingTextBySessionRef.current });
    setIsStreamingBySession({ ...isStreamingBySessionRef.current });
    setStreamingText(streamingTextRef.current);
    setIsStreaming(true);
    setAgentStatus('thinking');
    feedAutoTtsFromStreamRef.current?.(streamingTextRef.current);
  }, []);
  const scheduleStreamFlush = useCallback(() => {
    if (streamUiFlushTimerRef.current) return;
    streamUiFlushTimerRef.current = setTimeout(() => {
      streamUiFlushTimerRef.current = null;
      flushStreamingUi();
    }, 66);
  }, [flushStreamingUi]);
  const cancelStreamFlush = useCallback(() => {
    if (streamUiFlushTimerRef.current) {
      clearTimeout(streamUiFlushTimerRef.current);
      streamUiFlushTimerRef.current = null;
    }
  }, []);
  const summaryStreamCacheRef = useRef<Record<string, string>>({});
  const SUMMARY_STREAM_DEBUG = true;
  const [wsStatus, setWsStatus] = useState<AIWebSocketStatus>('disconnected');
  const [agentStatus, setAgentStatus] = useState<AgentStatus>('disconnected');
  /** Ready-stage from agent: '' (unknown) -> 'loading' (extensions done, MCP loading) -> 'ready'. */
  const [toolsStage, setToolsStage] = useState<'loading' | 'ready' | ''>('');
  const [inputText, setInputText] = useState('');
  /** Per-session composer drafts (未发送草稿，切换会话后切回仍保留)。 */
  const [draftsBySession, setDraftsBySession] = useState<Record<string, string>>({});
  /** Skill selected from the + menu or /skill; shown as /name chip until send/clear. */
  const [pendingSkill, setPendingSkill] = useState<{ dir: string; name: string } | null>(null);
  /** Active /goal from server (sticky across turns). */
  const [activeGoal, setActiveGoal] = useState<{
    objective: string;
    status: string;
    last_progress?: string;
    blocked_reason?: string;
  } | null>(null);
  const [availableSkills, setAvailableSkills] = useState<SkillInfo[]>([]);
  const [skillsLoading, setSkillsLoading] = useState(false);
  const skillsLoadedRef = useRef(false);
  /** Keyboard highlight index for the `/` command or arg picker. */
  const [slashHighlight, setSlashHighlight] = useState(0);
  const [images, setImages] = useState<string[]>([]);
  const [attachments, setAttachments] = useState<UploadedFile[]>([]);
  const [isDragOver, setIsDragOver] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [voicePanelOpen, setVoicePanelOpen] = useState(false);
  const [voiceRealtimeStatus, setVoiceRealtimeStatus] = useState('idle');
  const [voiceTranscript, setVoiceTranscript] = useState('');
  const [voiceRealtimeError, setVoiceRealtimeError] = useState('');
  /** Auto-speak each final agent reply via TTS (persisted per agent). */
  const [autoSpeechEnabled, setAutoSpeechEnabled] = useState(false);
  const autoSpeechEnabledRef = useRef(false);
  const voiceRealtimeStatusRef = useRef('idle');
  const autoTtsAudioRef = useRef<HTMLAudioElement | null>(null);
  const lastAutoSpokenRef = useRef('');
  /** Pipeline: cancel token + text already queued from the live stream. */
  const autoTtsGenRef = useRef(0);
  const autoTtsStreamOffsetRef = useRef(0);
  const autoTtsTextQueueRef = useRef<string[]>([]);
  const autoTtsUrlQueueRef = useRef<string[]>([]);
  /** Parallel synth results keyed by sequence — drained in order into the play queue. */
  const autoTtsOrderedUrlsRef = useRef<Map<number, string>>(new Map());
  const autoTtsNextSynthSeqRef = useRef(0);
  const autoTtsNextPlaySeqRef = useRef(0);
  const autoTtsSynthActiveRef = useRef(0);
  const autoTtsPlayingRef = useRef(false);
  const enqueueAutoTtsChunksRef = useRef<(chunks: string[]) => void>(() => {});
  const feedAutoTtsFromStreamRef = useRef<(fullText: string) => void>(() => {});
  /** Structured realtime captions — avoids user/assistant role mixing on streaming deltas. */
  const voiceCaptionRef = useRef<{ role: string; text: string }[]>([]);
  const voiceConnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  /** True while probing agent for an in-progress call after refresh. */
  const voiceResumeProbeRef = useRef(false);
  /** Set on pagehide so refresh/tab-close does not hang up the agent-side session. */
  const voicePageHideRef = useRef(false);
  const clearVoiceConnectTimer = useCallback(() => {
    if (voiceConnectTimerRef.current) {
      clearTimeout(voiceConnectTimerRef.current);
      voiceConnectTimerRef.current = null;
    }
  }, []);
  const armVoiceConnectTimeout = useCallback(() => {
    clearVoiceConnectTimer();
    voiceConnectTimerRef.current = setTimeout(() => {
      setVoiceRealtimeStatus((prev) => {
        if (prev === 'connecting') {
          setVoiceRealtimeError('Realtime connect timed out (Agent 未在 20s 内返回状态，请重启 Agent 后再试)');
          return 'error';
        }
        return prev;
      });
    }, 20000);
  }, [clearVoiceConnectTimer]);

  useEffect(() => {
    autoSpeechEnabledRef.current = autoSpeechEnabled;
  }, [autoSpeechEnabled]);

  useEffect(() => {
    voiceRealtimeStatusRef.current = voiceRealtimeStatus;
  }, [voiceRealtimeStatus]);

  useEffect(() => {
    if (!agentId) {
      setAutoSpeechEnabled(false);
      return;
    }
    try {
      setAutoSpeechEnabled(localStorage.getItem(`ai_chat_auto_tts:${agentId}`) === 'true');
    } catch {
      setAutoSpeechEnabled(false);
    }
  }, [agentId]);

  const stopAutoTts = useCallback(() => {
    autoTtsGenRef.current += 1;
    autoTtsTextQueueRef.current = [];
    autoTtsUrlQueueRef.current = [];
    autoTtsOrderedUrlsRef.current.clear();
    autoTtsNextSynthSeqRef.current = 0;
    autoTtsNextPlaySeqRef.current = 0;
    autoTtsSynthActiveRef.current = 0;
    autoTtsPlayingRef.current = false;
    autoTtsStreamOffsetRef.current = 0;
    const a = autoTtsAudioRef.current;
    if (a) {
      a.pause();
      a.removeAttribute('src');
      a.load();
    }
  }, []);

  /** Unlock autoplay during a user gesture (toggle Auto speech on). */
  const unlockAutoTtsAudio = useCallback(() => {
    try {
      // Tiny silent wav — browsers require a play() inside a click handler once.
      const silent =
        'data:audio/wav;base64,UklGRigAAABXQVZFZm10IBIAAAABAAEARKwAAIhYAQACABAAAABkYXRhAgAAAAEA';
      const a = new Audio(silent);
      a.volume = 0.01;
      void a.play().then(() => {
        a.pause();
        a.currentTime = 0;
      }).catch(() => { /* ignore */ });
      // Keep a dedicated element for subsequent auto-plays after unlock.
      if (!autoTtsAudioRef.current) {
        autoTtsAudioRef.current = new Audio();
      }
    } catch { /* ignore */ }
  }, []);

  /** Split reply into short sentences so first audio can start before full TTS finishes. */
  const splitAutoTtsChunks = useCallback((text: string, maxLen = 100): string[] => {
    const cleaned = (text || '').replace(/\s+/g, ' ').trim();
    if (!cleaned) return [];
    const rawParts = cleaned.split(/(?<=[。！？!?；;…\n])/);
    const chunks: string[] = [];
    for (const part of rawParts) {
      const s = part.trim();
      if (!s) continue;
      if (s.length <= maxLen) {
        chunks.push(s);
        continue;
      }
      for (let i = 0; i < s.length; i += maxLen) {
        const piece = s.slice(i, i + maxLen).trim();
        if (piece) chunks.push(piece);
      }
    }
    return chunks;
  }, []);

  const resolveAutoTtsUrl = useCallback((url: string) => {
    if (!url) return '';
    if (url.startsWith('http')) return url;
    return `${SERVER_BASE_URL}${url.startsWith('/') ? url : `/${url}`}`;
  }, []);

  const pumpAutoTtsPlay = useCallback(async (gen: number) => {
    if (autoTtsPlayingRef.current) return;
    autoTtsPlayingRef.current = true;
    try {
      while (gen === autoTtsGenRef.current) {
        const url = autoTtsUrlQueueRef.current.shift();
        if (!url) break;
        if (!autoSpeechEnabledRef.current) break;
        const audio = autoTtsAudioRef.current || new Audio();
        autoTtsAudioRef.current = audio;
        audio.src = url;
        try {
          await audio.play();
        } catch (playErr) {
          console.warn('[AIChatPage] Auto TTS play() blocked:', playErr);
          unlockAutoTtsAudio();
          await new Promise((r) => setTimeout(r, 40));
          try {
            await audio.play();
          } catch {
            continue;
          }
        }
        await new Promise<void>((resolve) => {
          const done = () => {
            audio.removeEventListener('ended', done);
            audio.removeEventListener('error', done);
            resolve();
          };
          audio.addEventListener('ended', done);
          audio.addEventListener('error', done);
        });
      }
    } finally {
      autoTtsPlayingRef.current = false;
      if (
        gen === autoTtsGenRef.current &&
        autoTtsUrlQueueRef.current.length > 0 &&
        autoSpeechEnabledRef.current
      ) {
        void pumpAutoTtsPlay(gen);
      }
    }
  }, [unlockAutoTtsAudio]);

  const pumpAutoTtsSynth = useCallback(async (gen: number) => {
    const CONCURRENCY = 2;
    const flushOrdered = () => {
      while (autoTtsOrderedUrlsRef.current.has(autoTtsNextPlaySeqRef.current)) {
        const url = autoTtsOrderedUrlsRef.current.get(autoTtsNextPlaySeqRef.current)!;
        autoTtsOrderedUrlsRef.current.delete(autoTtsNextPlaySeqRef.current);
        autoTtsNextPlaySeqRef.current += 1;
        if (url) autoTtsUrlQueueRef.current.push(url);
      }
      if (autoTtsUrlQueueRef.current.length > 0) {
        void pumpAutoTtsPlay(gen);
      }
    };
    while (
      gen === autoTtsGenRef.current &&
      autoSpeechEnabledRef.current &&
      agentId &&
      autoTtsTextQueueRef.current.length > 0 &&
      autoTtsSynthActiveRef.current < CONCURRENCY
    ) {
      const chunk = autoTtsTextQueueRef.current.shift();
      if (!chunk) break;
      const seq = autoTtsNextSynthSeqRef.current;
      autoTtsNextSynthSeqRef.current += 1;
      autoTtsSynthActiveRef.current += 1;
      void (async () => {
        try {
          console.log('[AIChatPage] Auto TTS chunk…', chunk.slice(0, 60));
          const t0 = performance.now();
          const res = await agentSessionAPI.synthesize(agentId, chunk);
          console.log('[AIChatPage] Auto TTS chunk ready', Math.round(performance.now() - t0), 'ms');
          if (gen !== autoTtsGenRef.current || !autoSpeechEnabledRef.current) return;
          const url = resolveAutoTtsUrl(res.url || '');
          autoTtsOrderedUrlsRef.current.set(seq, url || '');
          flushOrdered();
        } catch (err) {
          console.warn('[AIChatPage] Auto TTS chunk failed:', err);
          if (gen === autoTtsGenRef.current) {
            autoTtsOrderedUrlsRef.current.set(seq, '');
            flushOrdered();
          }
        } finally {
          autoTtsSynthActiveRef.current = Math.max(0, autoTtsSynthActiveRef.current - 1);
          if (gen === autoTtsGenRef.current) {
            void pumpAutoTtsSynth(gen);
          }
        }
      })();
    }
  }, [agentId, pumpAutoTtsPlay, resolveAutoTtsUrl]);

  const enqueueAutoTtsChunks = useCallback((chunks: string[]) => {
    const cleaned = chunks.map((c) => c.trim()).filter(Boolean);
    if (!cleaned.length || !agentId) return;
    if (!autoSpeechEnabledRef.current) return;
    const vs = voiceRealtimeStatusRef.current;
    if (vs === 'connected' || vs === 'connecting' || vs === 'tool_running') {
      console.log('[AIChatPage] Auto TTS skipped: realtime busy =', vs);
      return;
    }
    autoTtsTextQueueRef.current.push(...cleaned);
    void pumpAutoTtsSynth(autoTtsGenRef.current);
  }, [agentId, pumpAutoTtsSynth]);

  /** While the reply is still streaming, synthesize completed sentences early. */
  const feedAutoTtsFromStream = useCallback((fullText: string) => {
    if (!autoSpeechEnabledRef.current || !agentId) return;
    const vs = voiceRealtimeStatusRef.current;
    if (vs === 'connected' || vs === 'connecting' || vs === 'tool_running') return;

    const full = fullText || '';
    let offset = autoTtsStreamOffsetRef.current;
    if (offset > full.length) offset = 0;
    const pending = full.slice(offset);
    if (!pending.trim()) return;

    const chunks: string[] = [];
    let consumed = 0;
    const re = /[\s\S]*?[。！？!?\n]/g;
    let m: RegExpExecArray | null;
    while ((m = re.exec(pending)) !== null) {
      const piece = m[0].trim();
      if (piece) chunks.push(...splitAutoTtsChunks(piece));
      consumed = m.index + m[0].length;
    }
    // No punctuation yet — hard-cut once the buffer is long enough.
    if (!chunks.length && pending.trim().length >= 100) {
      const cut = pending.slice(0, 100);
      chunks.push(...splitAutoTtsChunks(cut));
      consumed = cut.length;
    }
    if (!chunks.length || consumed <= 0) return;
    autoTtsStreamOffsetRef.current = offset + consumed;
    enqueueAutoTtsChunks(chunks);
  }, [agentId, enqueueAutoTtsChunks, splitAutoTtsChunks]);

  const speakFinalReply = useCallback(async (text: string) => {
    const prompt = (text || '').trim();
    if (!agentId || !prompt) return;
    if (!autoSpeechEnabledRef.current) {
      console.log('[AIChatPage] Auto TTS skipped: disabled');
      return;
    }
    const vs = voiceRealtimeStatusRef.current;
    if (vs === 'connected' || vs === 'connecting' || vs === 'tool_running') {
      console.log('[AIChatPage] Auto TTS skipped: realtime busy =', vs);
      return;
    }
    if (lastAutoSpokenRef.current === prompt) {
      console.log('[AIChatPage] Auto TTS skipped: already spoken this text');
      return;
    }
    lastAutoSpokenRef.current = prompt;

    // Prefer remainder after stream-prefetch; fall back to full text.
    const offset = Math.min(autoTtsStreamOffsetRef.current, prompt.length);
    const rest = prompt.slice(offset).trim();
    autoTtsStreamOffsetRef.current = prompt.length;
    if (rest) {
      enqueueAutoTtsChunks(splitAutoTtsChunks(rest));
    } else if (autoTtsTextQueueRef.current.length === 0 && autoTtsUrlQueueRef.current.length === 0 && !autoTtsPlayingRef.current) {
      // Stream already covered everything but nothing queued (edge) — speak full.
      enqueueAutoTtsChunks(splitAutoTtsChunks(prompt));
    }
  }, [agentId, enqueueAutoTtsChunks, splitAutoTtsChunks]);

  const toggleAutoSpeech = useCallback((enabled: boolean) => {
    setAutoSpeechEnabled(enabled);
    autoSpeechEnabledRef.current = enabled;
    if (agentId) {
      try {
        localStorage.setItem(`ai_chat_auto_tts:${agentId}`, String(enabled));
      } catch { /* ignore */ }
    }
    if (enabled) {
      // Critical: unlock browser autoplay during this click gesture.
      unlockAutoTtsAudio();
    } else {
      stopAutoTts();
    }
  }, [agentId, stopAutoTts, unlockAutoTtsAudio]);

  useEffect(() => () => stopAutoTts(), [stopAutoTts]);

  enqueueAutoTtsChunksRef.current = enqueueAutoTtsChunks;
  feedAutoTtsFromStreamRef.current = feedAutoTtsFromStream;

  const speakFinalReplyRef = useRef(speakFinalReply);
  speakFinalReplyRef.current = speakFinalReply;

  // Session id first — token % is keyed per session for parallel panes.
  const [currentSessionId, setCurrentSessionId] = useState<string | null>(null);

  // Token stats — per session (parallel panes must not share one global %).
  type TokenStatsState = {
    used: number; max: number;
    breakdown?: { user: number; thought: number; tool: number; tool_defs?: number; response: number };
    session?: any;
    cumulative?: any;
  };
  const [tokenStatsBySession, setTokenStatsBySession] = useState<Record<string, TokenStatsState>>({});
  const tokenStatsBySessionRef = useRef(tokenStatsBySession);
  useEffect(() => { tokenStatsBySessionRef.current = tokenStatsBySession; }, [tokenStatsBySession]);
  /** Last agent-level window stats (fallback when per-session key is missing). */
  const [agentTokenStats, setAgentTokenStats] = useState<TokenStatsState | null>(null);
  const agentTokenStatsRef = useRef<TokenStatsState | null>(null);
  useEffect(() => { agentTokenStatsRef.current = agentTokenStats; }, [agentTokenStats]);
  /** Focused session stats only — never fall back to another session's %. */
  const tokenStats = currentSessionId
    ? (tokenStatsBySession[currentSessionId] ?? null)
    : agentTokenStats;
  const tokenStatsRef = useRef(tokenStats);
  useEffect(() => { tokenStatsRef.current = tokenStats; }, [tokenStats]);

  const applyTokenStats = useCallback((sid: string | null | undefined, next: TokenStatsState | null) => {
    const key = (sid || '').trim();
    const agentSid = (agentCurrentSessionIdRef.current || '').trim();
    if (next) {
      // Agent-level fallback tracks the agent-current session only — never
      // overwrite it with another pane / history session's stats.
      if (!key || !agentSid || key === agentSid) {
        setAgentTokenStats(next);
        agentTokenStatsRef.current = next;
      }
    }
    if (!key) return;
    setTokenStatsBySession((prev) => {
      if (next === null) {
        if (!(key in prev)) return prev;
        const copy = { ...prev };
        delete copy[key];
        return copy;
      }
      return { ...prev, [key]: next };
    });
  }, []);

  /** Ask the agent to rebroadcast context % for a session (safe no-op if WS down). */
  const requestSessionTokenStats = useCallback((sessionId?: string | null) => {
    const sid = (sessionId || '').trim();
    if (!sid) return;
    try {
      (wsServiceRef.current || getAiWsService(agentId)).requestTokenStats(sid);
    } catch {
      /* ignore */
    }
  }, [agentId]);

  // Focused session changed → always rebroadcast *that* session's context %.
  // Do not copy agentTokenStats onto arbitrary sids (that reused the previous
  // session's numbers). Only reuse agent fallback when focus is agent-current.
  useEffect(() => {
    if (!currentSessionId) return;
    const agentSid = (agentCurrentSessionIdRef.current || '').trim();
    if (
      agentSid
      && currentSessionId === agentSid
      && !tokenStatsBySessionRef.current[currentSessionId]
      && agentTokenStatsRef.current
      && agentTokenStatsRef.current.max > 0
    ) {
      applyTokenStats(currentSessionId, agentTokenStatsRef.current);
    }
    requestSessionTokenStats(currentSessionId);
  }, [currentSessionId, applyTokenStats, requestSessionTokenStats]);

  // Session management
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
  /** Narrow viewports: side rails overlay so the chat column never collapses. */
  const isCompactLayout = useIsCompactAgentWeb();
  const isMobileViewport = useIsMobileViewport();
  const [sessionSidebarOpen, setSessionSidebarOpen] = useState(() => {
    if (typeof window !== 'undefined' && window.matchMedia('(max-width: 767px)').matches) {
      return false;
    }
    return true;
  });
  /** Session search modal — Ctrl/Cmd+K or "搜索" sidebar button. */
  const [sessionSearchOpen, setSessionSearchOpen] = useState(false);
  /** Mirrored from SessionSidebar — used by the search modal for title lookup. */
  const [sidebarSessions, setSidebarSessions] = useState<AgentSession[]>([]);
  const openSessionSearch = useCallback(() => {
    if (!agentId) return;
    setSessionSearchOpen(true);
    // Refresh the title mirror in the background so the modal can show
    // human-friendly titles without an extra round-trip on the search call.
    void (async () => {
      try {
        const resp = await agentSessionAPI.getSessionList(agentId, 0, 100);
        const list = (resp.sessions || []).filter((s) => s.origin !== 'scheduled_task');
        setSidebarSessions((prev) => {
          if (prev.length === list.length) {
            let same = true;
            for (let i = 0; i < prev.length; i++) {
              if (prev[i].id !== list[i].id || prev[i].title !== list[i].title) {
                same = false;
                break;
              }
            }
            if (same) return prev;
          }
          return list;
        });
      } catch {
        /* non-fatal — search will still work, only display titles may be stale */
      }
    })();
  }, [agentId]);
  const closeSessionSearch = useCallback(() => setSessionSearchOpen(false), []);
  /** In-chat Skill 库 / 插件：keep SessionSidebar, replace center + files. */
  const [libraryView, setLibraryView] = useState<null | 'skills' | 'plugins' | 'roles'>(null);
  const [filesPanelOpen, setFilesPanelOpen] = useState(() => {
    try {
      if (typeof window !== 'undefined' && window.matchMedia('(max-width: 767px)').matches) {
        return false;
      }
      const raw = localStorage.getItem('opensquad.filesPanel.open');
      if (raw === null) return true;
      return raw === 'true';
    } catch {
      return true;
    }
  });
  const [filesPanelWidth, setFilesPanelWidth] = useState(() => {
    try {
      const raw = localStorage.getItem('opensquad.filesPanel.width');
      const n = raw ? parseInt(raw, 10) : 280;
      return Number.isFinite(n) ? Math.min(720, Math.max(220, n)) : 280;
    } catch {
      return 280;
    }
  });
  const [fileOpenRequest, setFileOpenRequest] = useState<ProjectFileOpenRequest | null>(null);
  const [wsSnap, setWsSnap] = useState<WorkspaceStoreSnapshot>(() =>
    loadWorkspaceStoreResolved(typeof agentId === 'string' ? agentId : ''),
  );
  const [closeWorkspaceTarget, setCloseWorkspaceTarget] = useState<Workspace | null>(null);
  const [createWorkspaceOpen, setCreateWorkspaceOpen] = useState(false);
  const [fileDirtyMap, setFileDirtyMap] = useState<Record<string, boolean>>({});
  const [tabSessionTitles, setTabSessionTitles] = useState<Record<string, string>>({});
  const pendingOpenSessionTabRef = useRef(false);
  /** Explicit pane that should receive the next new-session tab (avoids focus race after split). */
  const pendingTargetPaneIdRef = useRef<string | null>(null);
  const wsMigratedRef = useRef(false);
  const [sessionChanges, setSessionChanges] = useState<SessionChangesSummary | null>(null);
  const [focusChangedNonce, setFocusChangedNonce] = useState(0);
  const [changesBusy, setChangesBusy] = useState(false);
  const [restoreConfirm, setRestoreConfirm] = useState<{
    entryUid: string;
    message: ChatMessage;
    sessionId?: string;
  } | null>(null);
  const [filesLiveChanges, setFilesLiveChanges] = useState<{
    nonce: number;
    additions: number;
    deletions: number;
    count: number;
    files: Array<{
      name: string;
      path: string;
      type: 'file' | 'dir';
      status?: string;
      additions?: number;
      deletions?: number;
      oversized?: boolean;
      mtime?: number;
      size?: number;
      created?: boolean;
    }>;
  } | null>(null);
  const onSessionChangesStable = useCallback((summary: SessionChangesSummary) => {
    setSessionChanges(summary);
  }, []);
  const openProjectFile = useCallback((path: string) => {
    const p = (path || '').trim().replace(/\\/g, '/');
    if (!p) return;
    const mobile =
      typeof window !== 'undefined' && window.matchMedia('(max-width: 767px)').matches;
    if (mobile) {
      // Open in center tab; keep chat column visible (drawer closed).
      setFilesPanelOpen(false);
      setSessionSidebarOpen(false);
    } else {
      setFilesPanelOpen(true);
    }
    try {
      localStorage.setItem('opensquad.filesPanel.open', mobile ? 'false' : 'true');
    } catch {
      /* ignore */
    }
    setFileOpenRequest({ path: p, nonce: Date.now() });
    const snap = loadWorkspaceStore(agentId);
    const wsId = snap.chrome.activeWorkspaceId;
    const ws = wsId ? snap.workspaces.find((w) => w.id === wsId) : null;
    if (wsId && ws) {
      void (async () => {
        // Prefer agentId here — agentProfile is declared later in this component
        // (TDZ). Cache keys also accept agentId; dir_name is used when opening
        // from the files panel (handleOpenFileInTab) after profile is loaded.
        if (!getWorkspaceFileCache(agentId, ws.rootPath, p)) {
          await prefetchWorkspaceFile(agentId, ws.rootPath, p);
        }
        openContentTab(agentId, wsId, { kind: 'file', id: p });
        setWsSnap(loadWorkspaceStore(agentId));
      })();
    }
  }, [agentId]);

  // Plan
  const [planSteps, setPlanSteps] = useState<PlanStep[]>([]);

  // Backend start timestamp for the current workflow turn (epoch ms from turn_start)
  const [turnStartedMs, setTurnStartedMs] = useState<number | undefined>(undefined);
  const turnStartedMsRef = useRef<number | undefined>(undefined);
  useEffect(() => {
    turnStartedMsRef.current = turnStartedMs;
  }, [turnStartedMs]);

  // Refs
  const chatEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const streamingTextRef = useRef('');   // mirror of streamingText for WS callbacks
  const finalizingRef = useRef(false);   // guard against duplicate finalization
  /** After user hits Stop, ignore late stream/tool/thought until the next send. */
  const userStoppedRef = useRef(false);
  const diskSessionLoadedRef = useRef(false); // true after we loaded disk session (skip bare WS history)
  const dragCounterRef = useRef(0);      // counter for nested drag enter/leave events
  /** Live AgentWebComposer handles — keyed by pane id and by session id. */
  const composerApiByPaneRef = useRef(new Map<string, AgentWebComposerHandle>());
  const composerApiBySessionRef = useRef(new Map<string, AgentWebComposerHandle>());
  const focusedPaneIdRef = useRef<string | null>(null);
  const resolveComposerApi = useCallback((sessionId?: string | null) => {
    const sid = (sessionId || '').trim();
    if (sid) {
      const bySession = composerApiBySessionRef.current.get(sid);
      if (bySession) return bySession;
    }
    const pid = focusedPaneIdRef.current;
    if (pid) {
      const byPane = composerApiByPaneRef.current.get(pid);
      if (byPane) return byPane;
    }
    const first = composerApiByPaneRef.current.values().next();
    return first.done ? null : first.value;
  }, []);
  const messagesContainerRef = useRef<HTMLDivElement>(null); // messages scroll container
  const prevOuterScrollHeightRef = useRef(0); // for smart auto-scroll
  const pendingFilePushesRef = useRef<ChatMessage[]>([]);
  const pendingHydrationMediaRef = useRef<ChatMessage[]>([]); // media history received while hydrating
  /** Workflow WS events buffered while hydrating so they are not double-appended after snapshot replace. */
  const pendingHydrationWorkflowEventsRef = useRef<Array<{ event: WorkflowEvent; status: string | null }>>([]);
  /** Final assistant replies that arrived while hydrating (avoid full-replace wipe). */
  const pendingHydrationFinalsRef = useRef<ChatMessage[]>([]);
  /** When true, next hydrate merges archive into the live timeline instead of full replace. */
  const compressionHydrationPendingRef = useRef(false);
  const filePushDedupRef = useRef<Map<string, number>>(new Map());
  const isHydratingSessionRef = useRef(false); // true while restoring current session after refresh
  const currentSessionIdRef = useRef<string | null>(null);
  /** Agent's focused/current session id (from WS/HTTP), independent of UI tab focus. */
  const agentCurrentSessionIdRef = useRef<string | null>(null);

  // Keep timelineRef in sync for per-sid routing reads.
  useEffect(() => {
    timelineRef.current = timeline;
  }, [timeline]);
  useEffect(() => {
    liveTimelinesBySessionRef.current = liveTimelinesBySession;
  }, [liveTimelinesBySession]);

  /**
   * Route timeline mutations into the correct per-session bucket.
   * WS handlers set eventSidRef before mutating; local UI ops leave it empty
   * (falls back to currentSessionId) so solo mode keeps working.
   */
  const setTimeline = useCallback((update: React.SetStateAction<TimelineEntry[]>) => {
    const updater =
      typeof update === 'function'
        ? (update as (prev: TimelineEntry[]) => TimelineEntry[])
        : ((_prev: TimelineEntry[]) => update as TimelineEntry[]);
    const sid = (eventSidRef.current || currentSessionIdRef.current || '').trim();
    if (sid) {
      setLiveTimelinesBySession((prev) => {
        // Never seed a missing bucket from timelineRef — after a parallel /
        // scheduled-task current_session flip, timelineRef still holds the
        // previous focused session and would contaminate the new sid.
        const cur = prev[sid] ?? [];
        const next = updater(Array.isArray(cur) ? cur : []);
        const out = { ...prev, [sid]: next };
        liveTimelinesBySessionRef.current = out;
        return out;
      });
    }
    // Mirror into the focused solo timeline when this mutation is for the
    // focused session (or has no explicit event sid — local UI ops).
    // 主聊天区（chatSlot）始终渲染 solo timeline state，即使分屏时 focused
    // 会话的 pane 也在用它——因此这里不能按 splitModeRef 跳过镜像。
    if (!eventSidRef.current || eventSidRef.current === (currentSessionIdRef.current || '')) {
      setTimelineState(updater);
    }
  }, []);

  // Freeze rendered chat while selecting text. Keep freeze after mouseup until
  // the user clears the selection (click elsewhere) — otherwise live WS / scroll
  // remounts DOM nodes and kills copy-paste.
  const flatTimelineLive = useMemo(() => flattenArchivedSections(timeline), [timeline]);
  const liveSelectView = useMemo(
    () => ({ entries: flatTimelineLive, streaming: streamingText }),
    [flatTimelineLive, streamingText],
  );
  const {
    displayValue: selectFrozenView,
    isFrozenRef: textSelectFrozenRef,
  } = useTextSelectionFreeze(messagesContainerRef, liveSelectView);
  const displayTimeline = selectFrozenView.entries;
  const displayStreamingText = selectFrozenView.streaming;

  const sessionBootstrapDoneRef = useRef(false); // true after first canonical timeline set on connect
  const sessionReloadSeqRef = useRef(0);
  /** Separate from sessionReloadSeqRef so connected/hydrate cannot invalidate New Session timers. */
  const newSessionFallbackSeqRef = useRef(0);
  const sessionReloadTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  /**
   * Latest hydrateCurrentSession closure (the function is recreated per effect
   * run; handleViewSession lives outside that scope and must go through the ref).
   */
  const hydrateCurrentSessionRef = useRef<((opts?: { showLoading?: boolean; wasNewSession?: boolean }) => void) | null>(null);

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
  // The currently-active card name (last UI pick / config.json model._card).
  // Used to pinpoint the selected <option> even when two cards from different
  // vendors share the same model_name (model_name alone is not a unique identity).
  const [currentCardName, setCurrentCardName] = useState<string | null>(() =>
    loadLastModelPick(agentId).card,
  );
  const [voiceBindings, setVoiceBindings] = useState({
    asr_card: '',
    tts_card: '',
    realtime_card: '',
    realtime_voice: '',
  });

  const handleVoiceBindingsChange = useCallback(
    async (next: {
      asr_card: string;
      tts_card: string;
      realtime_card: string;
      realtime_voice: string;
    }) => {
      setVoiceBindings(next);
      wsServiceRef.current?.setVoiceConfig(next);
      const dir = agentProfile?.dir_name || agentId;
      if (!dir) return;
      try {
        const cfg = await adminAPI.getConfig(dir);
        const full = { ...(cfg.config || {}) };
        full.voice = { ...(full.voice || {}), ...next };
        await adminAPI.updateConfig(dir, full);
      } catch (e) {
        console.warn('[AIChatPage] persist voice config failed', e);
      }
    },
    [agentId, agentProfile?.dir_name],
  );

  const handleVoiceRealtimeStart = useCallback(
    (opts?: { forceAskAgent?: boolean }) => {
      setVoiceTranscript('');
      voiceCaptionRef.current = [];
      setVoiceRealtimeError('');
      setVoiceRealtimeStatus('connecting');
      writeVoiceCallPersist(agentId, opts?.forceAskAgent !== false);
      armVoiceConnectTimeout();
      wsServiceRef.current?.startVoiceRealtime({
        force_ask_agent: opts?.forceAskAgent !== false,
      });
    },
    [agentId, armVoiceConnectTimeout],
  );

  const handleVoiceRealtimeStop = useCallback(() => {
    clearVoiceConnectTimer();
    clearVoiceCallPersist(agentId);
    wsServiceRef.current?.stopVoiceRealtime();
    setVoiceRealtimeStatus('idle');
    setVoiceRealtimeError('');
  }, [agentId, clearVoiceConnectTimer]);

  const handleVoiceAudioChunk = useCallback((b64: string) => {
    wsServiceRef.current?.sendVoiceAudioIn(b64);
  }, []);

  const handleMouthpieceUtterance = useCallback((b64: string, sampleRate: number) => {
    wsServiceRef.current?.sendMouthpieceUtterance(b64, sampleRate);
  }, []);

  const handleForceAskAgentChange = useCallback((force: boolean) => {
    wsServiceRef.current?.setVoiceRealtimeOptions({ force_ask_agent: force });
  }, []);

  const [reasoningEffort, setReasoningEffort] = useState<ReasoningEffort>(
    () => loadLastModelPick(agentId).effort || 'high',
  );
  const [agentMode, setAgentMode] = useState<AgentMode>('build');
  /** Per-session overrides so split panes do not share Plan/Build or model. */
  const [agentModeBySession, setAgentModeBySession] = useState<Record<string, AgentMode>>({});
  const [cardNameBySession, setCardNameBySession] = useState<Record<string, string>>({});
  const [modelNameBySession, setModelNameBySession] = useState<Record<string, string>>({});
  const [reasoningBySession, setReasoningBySession] = useState<Record<string, ReasoningEffort>>({});
  const [switchingModelBySession, setSwitchingModelBySession] = useState<Record<string, boolean>>({});
  /** Always-fresh card maps for deliverMessage (avoid stale useCallback closures). */
  const cardNameBySessionRef = useRef<Record<string, string>>({});
  const currentCardNameRef = useRef<string | null>(null);
  cardNameBySessionRef.current = cardNameBySession;
  currentCardNameRef.current = currentCardName;
  /** Optimistic model-switch revert targets when agent reports failure. */
  const modelSwitchRevertRef = useRef<Record<string, { card: string | null; model: string }>>({});
  const [modeApprovals, setModeApprovals] = useState<ModeSwitchApproval[]>([]);
  const [optionsProposals, setOptionsProposals] = useState<OptionsProposal[]>([]);
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
  const [isCompressingContext, setIsCompressingContext] = useState(false);

  // Lazy loading state
  const [hasMoreHistory, setHasMoreHistory] = useState(false);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const historyOffsetRef = useRef(0);        // how many messages already loaded (from the end)
  const loadingSessionIdRef = useRef<string | null>(null); // session being lazily loaded

  // Session loading state (加载/创建会话中)
  const [isLoadingSession, setIsLoadingSession] = useState(false);
  const [sessionLoadingLabel, setSessionLoadingLabel] = useState('');
  /** False until first hydrate (or intentional New Chat) — blocks fake New Chat landing on refresh. */
  const [sessionBootstrapped, setSessionBootstrapped] = useState(false);
  const newSessionPendingRef = useRef(false); // true after handleNewSession fires, cleared on next connected
  /** After New Chat succeeds, ignore hydrates that would snap back to an older sid. */
  const newSessionGuardRef = useRef<{ sid: string; until: number } | null>(null);
  /**
   * Sticky centered landing until the user actually sends on that sid.
   * Timeline noise (empty workflow shells, sleep hints) must not dock the composer.
   */
  const composerLandingSessionsRef = useRef<Set<string>>(new Set());
  const [, bumpComposerLandingEpoch] = useState(0);
  const pinComposerLanding = useCallback((sid: string | null | undefined) => {
    const id = String(sid || '').trim();
    if (!id || composerLandingSessionsRef.current.has(id)) return;
    composerLandingSessionsRef.current.add(id);
    setSessionBootstrapped(true);
    setIsLoadingSession(false);
    bumpComposerLandingEpoch((n) => n + 1);
  }, []);
  const unpinComposerLanding = useCallback((sid: string | null | undefined) => {
    const id = String(sid || '').trim();
    if (!id || !composerLandingSessionsRef.current.has(id)) return;
    composerLandingSessionsRef.current.delete(id);
    bumpComposerLandingEpoch((n) => n + 1);
  }, []);

  // Scroll button visibility
  const [showScrollTop, setShowScrollTop] = useState(false);
  const [showScrollBottom, setShowScrollBottom] = useState(false);
  const [scrollActive, setScrollActive] = useState(false);
  const scrollHideTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const userScrolledRef = useRef(false); // true when user manually scrolled away from bottom
  // Ref to the current per-agent WS service (set inside the WS useEffect, used by callbacks)
  const wsServiceRef = useRef<ReturnType<typeof getAiWsService> | null>(null);

  // ---- Pending message queue (per-session only; other sessions run in parallel) ----
  // When a *specific* session is busy, further sends to that same session park here.
  // Different sessions send immediately (backend parallel turns).
  interface PendingMessage {
    id: string;
    text: string;
    images: string[];
    attachments: UploadedFile[];
    fileAtts: FileAttachment[];
    skillDir?: string;
    skillName?: string;
    /** Target session for multi-pane / multi-tab sends */
    sessionId?: string;
    paneId?: string;
  }
  const [pendingMessages, setPendingMessages] = useState<PendingMessage[]>([]);
  const [pendingCollapsed, setPendingCollapsed] = useState(false);
  const pendingMessagesRef = useRef<PendingMessage[]>([]);
  const isFlushingPendingRef = useRef(false);
  /** Blocks rapid double-send per session until the backend acknowledges the turn. */
  const outboundPendingBySessionRef = useRef<Record<string, boolean>>({});
  const outboundPendingTimersRef = useRef<Record<string, ReturnType<typeof setTimeout>>>({});
  const pendingQueueHydratedKeyRef = useRef<string | null>(null);
  /** Session ids currently running a parallel turn (from busy_sessions events). */
  const [busySessions, setBusySessions] = useState<string[]>([]);
  const busySessionsRef = useRef<string[]>([]);
  /** Sessions that finished a turn while not selected — grey-dot until user opens them. */
  const [unseenCompleteSessionIds, setUnseenCompleteSessionIds] = useState<string[]>([]);
  const viewedSessionIdRef = useRef<string | null>(null);
  const [primarySessionId, setPrimarySessionId] = useState<string | null>(null);
  const [pendingPrimarySessionId, setPendingPrimarySessionId] = useState<string | null>(null);
  const pendingPrimarySessionIdRef = useRef<string | null>(null);
  useEffect(() => {
    pendingPrimarySessionIdRef.current = pendingPrimarySessionId;
  }, [pendingPrimarySessionId]);
  useEffect(() => { pendingMessagesRef.current = pendingMessages; }, [pendingMessages]);
  useEffect(() => { busySessionsRef.current = busySessions; }, [busySessions]);

  /** Optimistically release one session's busy/streaming state after stop/new-chat. */
  const clearSessionRunState = useCallback((sid?: string | null) => {
    const key = String(sid || '').trim();
    const remaining = key
      ? busySessionsRef.current.filter((id) => id !== key)
      : [];
    busySessionsRef.current = remaining;
    setBusySessions(remaining);
    if (key) {
      if (streamingTextBySessionRef.current[key]) {
        const st = { ...streamingTextBySessionRef.current };
        delete st[key];
        streamingTextBySessionRef.current = st;
        setStreamingTextBySession(st);
      }
      if (isStreamingBySessionRef.current[key]) {
        const ib = { ...isStreamingBySessionRef.current };
        delete ib[key];
        isStreamingBySessionRef.current = ib;
        setIsStreamingBySession(ib);
      }
    }
    if (
      !key
      || key === currentSessionIdRef.current
      || key === agentCurrentSessionIdRef.current
    ) {
      streamingTextRef.current = '';
      setStreamingText('');
      setIsStreaming(false);
      setAgentStatus(remaining.length > 0 ? 'working' : 'connected');
      finalizingRef.current = false;
    }
  }, []);

  // When a session leaves busy_sessions and isn't currently selected → grey unread-complete dot.
  const prevBusySessionsRef = useRef<string[]>([]);
  useEffect(() => {
    const prev = prevBusySessionsRef.current;
    const next = busySessions;
    prevBusySessionsRef.current = next;
    if (prev.length === 0) return;
    const finished = prev.filter((id) => !next.includes(id));
    if (finished.length === 0) return;
    const viewed = viewedSessionIdRef.current;
    const newlyUnseen = finished.filter((id) => id && id !== viewed);
    if (newlyUnseen.length === 0) return;
    setUnseenCompleteSessionIds((cur) => {
      const merged = new Set(cur);
      for (const id of newlyUnseen) merged.add(id);
      const arr = Array.from(merged);
      if (arr.length === cur.length && arr.every((id) => cur.includes(id))) return cur;
      return arr;
    });
  }, [busySessions]);

  const clearOutboundTurnPending = useCallback((sid?: string) => {
    if (sid) {
      delete outboundPendingBySessionRef.current[sid];
      const t = outboundPendingTimersRef.current[sid];
      if (t) {
        clearTimeout(t);
        delete outboundPendingTimersRef.current[sid];
      }
      return;
    }
    for (const key of Object.keys(outboundPendingTimersRef.current)) {
      clearTimeout(outboundPendingTimersRef.current[key]);
    }
    outboundPendingTimersRef.current = {};
    outboundPendingBySessionRef.current = {};
  }, []);

  const armOutboundTurnPending = useCallback((sid: string) => {
    const key = sid || '__default__';
    outboundPendingBySessionRef.current[key] = true;
    if (outboundPendingTimersRef.current[key]) {
      clearTimeout(outboundPendingTimersRef.current[key]);
    }
    // Safety valve: never leave the send gate latched if status events were missed.
    outboundPendingTimersRef.current[key] = setTimeout(() => {
      delete outboundPendingBySessionRef.current[key];
      delete outboundPendingTimersRef.current[key];
    }, 8000);
  }, []);

  const isOutboundPending = useCallback((sid: string) => {
    return !!outboundPendingBySessionRef.current[sid || '__default__'];
  }, []);

  /** One queue per agent so split panes can park messages for different sessions. */
  const pendingQueueStorageKey = useCallback(() => {
    return `ai_chat_pending_queue:${agentId}`;
  }, [agentId]);

  // Hydrate agent-level pending queue (migrate legacy per-session keys once).
  useEffect(() => {
    if (!agentId) return;
    const key = pendingQueueStorageKey();
    if (pendingQueueHydratedKeyRef.current === key) return;
    pendingQueueHydratedKeyRef.current = key;
    try {
      let merged: PendingMessage[] = [];
      const raw = localStorage.getItem(key);
      if (raw) {
        const parsed = JSON.parse(raw);
        if (Array.isArray(parsed)) {
          merged = parsed.filter((m) => m && typeof m.id === 'string');
        }
      }
      // Migrate legacy per-session queues into the agent-level key
      const prefix = `ai_chat_pending_queue:${agentId}:`;
      for (let i = 0; i < localStorage.length; i++) {
        const k = localStorage.key(i);
        if (!k || !k.startsWith(prefix) || k === key) continue;
        try {
          const legacy = JSON.parse(localStorage.getItem(k) || '[]');
          if (Array.isArray(legacy)) {
            const sid = k.slice(prefix.length);
            for (const m of legacy) {
              if (m && typeof m.id === 'string') {
                merged.push({
                  ...m,
                  sessionId: m.sessionId || (sid !== 'nosession' ? sid : undefined),
                });
              }
            }
          }
          localStorage.removeItem(k);
        } catch { /* ignore */ }
      }
      // Dedupe by id
      const seen = new Set<string>();
      merged = merged.filter((m) => {
        if (seen.has(m.id)) return false;
        seen.add(m.id);
        return true;
      });
      setPendingMessages(merged);
      if (merged.length) localStorage.setItem(key, JSON.stringify(merged));
    } catch {
      setPendingMessages([]);
    }
  }, [agentId, pendingQueueStorageKey]);

  // Persist pending queue for refresh recovery.
  useEffect(() => {
    if (!agentId) return;
    if (pendingQueueHydratedKeyRef.current == null) return;
    const key = pendingQueueStorageKey();
    try {
      if (pendingMessages.length === 0) localStorage.removeItem(key);
      else localStorage.setItem(key, JSON.stringify(pendingMessages));
    } catch { /* ignore quota */ }
  }, [pendingMessages, agentId, pendingQueueStorageKey]);

  // Workflow fold auto-expand level (Settings → General); does not override manual toggles.
  const [workflowExpandLevel] = useWorkflowExpandLevel();
  /** Live stdout for system.start_job / run_session_job (keyed by tool call_id) */
  const [shellStreams, setShellStreams] = useState<Record<string, ShellStreamState>>({});

  // UI render mode: classic (user bubble + agent document) | solo (document stream). Global preference.
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

  // Document column for both classic + solo (classic: user bubble + agent doc stream)
  const soloColumnClass = 'max-w-3xl mx-auto w-full';

  // Right-edge user-turn nav — available in classic + solo (not solo-only).
  const soloUserNavNodes = useMemo(
    () => buildUserNavNodesFromTimeline(timeline),
    [timeline],
  );

  const jumpToSoloUserMessage = useCallback((id: string) => {
    const container = messagesContainerRef.current;
    const el = document.getElementById(userNavAnchorDomId(id));
    if (!container || !el) return;
    const cRect = container.getBoundingClientRect();
    const eRect = el.getBoundingClientRect();
    const top = eRect.top - cRect.top + container.scrollTop - 12;
    container.scrollTo({ top: Math.max(0, top), behavior: 'smooth' });
  }, []);

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
  const autoStartTriedRef = useRef<Record<string, number>>({});
  useEffect(() => {
    if (!agentId) return;
    adminAPI.getAgents()
      .then(res => {
        const found = res.agents.find(a => a.agent_id === agentId || a.dir_name === agentId);
        if (!found) return;
        setAgentProfile(found);
        const dirName = (found.dir_name || '').trim();
        const offline = !found.ready
          && (found.process_status === 'stopped' || found.process_status === 'crashed');
        if (dirName && offline) {
          const now = Date.now();
          const last = autoStartTriedRef.current[dirName] || 0;
          if (now - last > 30000) {
            autoStartTriedRef.current[dirName] = now;
            adminAPI.startAgent(dirName)
              .then(() => console.log('[AIChatPage] auto-started offline agent:', dirName))
              .catch((e: any) => console.warn('[AIChatPage] auto-start failed:', dirName, e?.message || e));
          }
        }
        // Seed agent-level fallback only. Per-session % is filled by
        // requestTokenStats(sid) when focus / current_session is known —
        // do not attach agent-wide file stats to a history tab sid.
        if (found.token_stats && Number(found.token_stats.max) > 0) {
          const stats = {
            used: found.token_stats!.used,
            max: found.token_stats!.max,
            breakdown: found.token_stats!.breakdown,
            session: found.token_stats!.session,
            cumulative: found.token_stats!.cumulative,
          };
          setAgentTokenStats((prev) => prev ?? stats);
          agentTokenStatsRef.current = agentTokenStatsRef.current ?? stats;
          const agentSid = (agentCurrentSessionIdRef.current || '').trim();
          if (agentSid) {
            setTokenStatsBySession((prev) => (prev[agentSid] ? prev : { ...prev, [agentSid]: stats }));
          }
        }
        // 拉取 config 获取 model_name / api_protocol / provider / runtime cwd。
        // 与 getWorkingDirectory 并行（原来 getConfig → getWorkingDirectory 串行两个 RTT）
        const wdP: Promise<any> = dirName
          ? adminAPI.getWorkingDirectory(dirName)
          : Promise.resolve(null);
        return Promise.all([adminAPI.getConfig(found.dir_name), wdP]).then(([cfg, wdRes]) => {
          const mn: string | undefined = cfg?.config?.model?.model_name;
          const ap: string | undefined = cfg?.config?.model?.api_protocol;
          const pv: string | undefined = cfg?.config?.model?.provider;
          const card: string | undefined = cfg?.config?.model?._card;
          const effortRaw: string | undefined = cfg?.config?.model?.reasoning_effort;
          const runtimeWd: string | undefined = (cfg as any)?.runtime_working_directory;
          if (mn) setModelName(mn);
          if (ap) setAgentApiProtocol(ap);
          if (pv) setAgentProvider(pv);
          // config.json is the authoritative persisted model state (updated by
          // both Web and TUI switches via switch_to_card). Seed from it first;
          // localStorage is only a same-browser fallback for the pre-config
          // window, never allowed to shadow a cross-client (e.g. TUI) switch.
          const lastPick = loadLastModelPick(agentId);
          if (card) {
            setCurrentCardName(card);
          } else if (lastPick.card) {
            setCurrentCardName(lastPick.card);
          }
          if (lastPick.effort) {
            setReasoningEffort(lastPick.effort);
          } else if (effortRaw === 'low' || effortRaw === 'medium' || effortRaw === 'high') {
            setReasoningEffort(effortRaw);
          }
          const modeRaw: string | undefined = cfg?.config?.agent_mode;
          if (modeRaw === 'plan' || modeRaw === 'build') {
            setAgentMode(modeRaw);
          }
          if (runtimeWd) {
            setDefaultCwd(runtimeWd);
            setAgentCwd((prev) => prev || runtimeWd);
          }
          const voice = cfg?.config?.voice || {};
          setVoiceBindings({
            asr_card: String(voice.asr_card || ''),
            tts_card: String(voice.tts_card || ''),
            realtime_card: String(voice.realtime_card || ''),
            realtime_voice: String(voice.realtime_voice || ''),
          });

          // 会话工作目录（原独立 effect 合并至此，与 config 并行获取）。
          // 覆盖永久 workspace root，使 ContextViewer 显示用户选择的 cwd。
          if (wdRes) {
            const active = wdRes.active_cwd || wdRes.session_cwd || wdRes.workspace_root || null;
            if (wdRes.workspace_root) setDefaultCwd(wdRes.workspace_root);
            else if (active) setDefaultCwd(active);
            const sid = currentSessionIdRef.current;
            const meta = sid ? getSessionMeta(agentId, sid) : null;
            if (meta?.projectPath?.trim()) {
              setAgentCwd(meta.projectPath.trim());
            } else if (wdRes.session_cwd) {
              setAgentCwd(wdRes.session_cwd);
            } else if (active) {
              setAgentCwd((prev) => prev || active);
            }
          }
        });
      })
      .catch(err => console.warn("[AIChatPage] Failed to load agent profile:", err.message));
  }, [agentId]);

  // When the active/viewed session changes, point the files panel at that
  // session's locked project folder (localStorage meta). Do not fall back to
  // defaultCwd here — that would clobber a freshly picked folder on new session.
  useEffect(() => {
    if (!agentId || !currentSessionId) return;
    const meta = getSessionMeta(agentId, currentSessionId);
    if (meta?.projectPath?.trim()) {
      setAgentCwd(meta.projectPath.trim());
    } else if (pendingProjectPathRef.current?.trim()) {
      setAgentCwd(pendingProjectPathRef.current.trim());
    }
  }, [agentId, currentSessionId]);

  const fsAgentName = agentProfile?.dir_name || agentId;
  const projectRoot = (agentCwd || defaultCwd || '').trim();

  const refreshSessionChanges = useCallback(async () => {
    if (!fsAgentName || !projectRoot) {
      setSessionChanges(null);
      return;
    }
    try {
      const resp = await adminAPI.listSessionChanges(fsAgentName, projectRoot);
      const files = (resp.files || resp.entries || []).map((e) => ({
        name: e.name,
        path: (e.path || '').replace(/\\/g, '/'),
        type: e.type,
        status: e.status,
        additions: e.additions,
        deletions: e.deletions,
        oversized: e.oversized,
        mtime: e.mtime,
        size: e.size,
        created: e.created,
        missing: !!(e as { missing?: boolean }).missing || e.status === 'D',
      }));
      const summary = {
        additions: resp.additions || 0,
        deletions: resp.deletions || 0,
        count: resp.count ?? files.length,
      };
      setSessionChanges(summary);
      // Push snapshot into files panel — in-place update, no loading flash
      setFilesLiveChanges({
        nonce: Date.now(),
        ...summary,
        files,
      });
    } catch {
      /* ignore — Launcher may be restarting */
    }
  }, [fsAgentName, projectRoot]);

  const refreshSessionChangesRef = useRef(refreshSessionChanges);
  useEffect(() => {
    refreshSessionChangesRef.current = refreshSessionChanges;
  }, [refreshSessionChanges]);

  const refreshSessionChangesDebouncedRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const scheduleRefreshSessionChanges = useCallback(() => {
    if (refreshSessionChangesDebouncedRef.current) {
      clearTimeout(refreshSessionChangesDebouncedRef.current);
    }
    refreshSessionChangesDebouncedRef.current = setTimeout(() => {
      refreshSessionChangesDebouncedRef.current = null;
      void refreshSessionChangesRef.current?.();
    }, 280);
  }, []);

  useEffect(() => {
    void refreshSessionChanges();
  }, [refreshSessionChanges]);

  useEffect(() => {
    return () => {
      if (refreshSessionChangesDebouncedRef.current) {
        clearTimeout(refreshSessionChangesDebouncedRef.current);
      }
    };
  }, []);

  // Load available model cards; refresh when window regains focus (desktop
  // may have created/edited cards while Agent Web stayed mounted).
  const refreshModelCards = useCallback(() => {
    modelCardAPI.getCards()
      .then(res => {
        if (Array.isArray(res.cards)) setModelCards(res.cards);
      })
      .catch(err => console.warn("[AIChatPage] Failed to load model cards:", err.message));
  }, []);

  useEffect(() => {
    refreshModelCards();
    const onVis = () => {
      if (document.visibilityState === 'visible') refreshModelCards();
    };
    const onFocus = () => refreshModelCards();
    document.addEventListener('visibilitychange', onVis);
    window.addEventListener('focus', onFocus);
    return () => {
      document.removeEventListener('visibilitychange', onVis);
      window.removeEventListener('focus', onFocus);
    };
  }, [refreshModelCards]);

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
          setTimeline(prev => {
            const next = [...olderEntries, ...prev];
            putCachedSessionTimeline(agentId, sid, next, {
              complete: !(session.has_more ?? false),
              messageCount: historyOffsetRef.current + (session.messages?.length || 0),
              totalMessages: session.total_messages,
            });
            return next;
          });
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
    // Selecting / holding a text selection: never move scrollTop.
    if (textSelectFrozenRef.current) return;
    const sel = window.getSelection();
    if (sel && !sel.isCollapsed && sel.anchorNode && el.contains(sel.anchorNode)) return;

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
          const wf = updated[j];
          if (wf.kind === 'workflow' && !wf.data.completed) {
            updated[j] = {
              ...wf,
              data: { ...wf.data, status: null, completed: true },
            } as TimelineEntry;
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

    cancelPendingVoiceHangup(agentId);
    voicePageHideRef.current = false;
    const onPageHide = () => {
      // Refresh / tab close: do not hang up agent-side realtime; sessionStorage resumes UI.
      voicePageHideRef.current = true;
      cancelPendingVoiceHangup(agentId);
    };
    window.addEventListener('pagehide', onPageHide);

    const aiWsService = getAiWsService(agentId);
    aiWsService.connect(agentId);
    wsServiceRef.current = aiWsService;

    const tryResumeVoiceCall = () => {
      if (!readVoiceCallPersist(agentId)) return;
      voiceResumeProbeRef.current = true;
      aiWsService.queryVoiceRealtime();
    };

    // Auth expiry detection — prompt re-login when token is invalid/expired
    const unsubAuthExpired = aiWsService.onAuthExpired(() => {
      setSessionExpired(true);
    });

    const unsubStatus = aiWsService.onStatusChange((status) => {
      setWsStatus(status);
      if (status === 'connected') {
        setAgentStatus('connected');
        tryResumeVoiceCall();
        // Session may already be focused before WS is ready — refresh % now.
        const sid =
          (currentSessionIdRef.current || agentCurrentSessionIdRef.current || '').trim();
        if (sid) {
          try {
            aiWsService.requestTokenStats(sid);
          } catch {
            /* ignore */
          }
        }
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
    // Route every live WS event into the correct per-session timeline bucket
    // via eventSidRef (see setTimeline wrapper above). Without this, parallel
    // pane B's events overwrite the global timeline while A is still running.
    const onWs = (type: string, handler: (msg: AIWSMessage) => void) =>
      aiWsService.on(type, (msg: AIWSMessage) => {
        // Prefer explicit msg.sid. Do NOT fall back to agentCurrentSessionId —
        // scheduled-task parallel turns update that without stealing UI focus,
        // and sid-less interactive events would land in the exec bucket.
        eventSidRef.current = String(
          (msg as any).sid
          || currentSessionIdRef.current
          || '',
        ).trim();
        try {
          handler(msg);
        } finally {
          // Clear so subsequent local UI mutations (send/compress/…) fall back
          // to currentSessionId instead of the last WS event's sid.
          eventSidRef.current = '';
        }
      });

    // Ready-stage notifications: chat is usable once WS connects; extensions /
    // MCP finishing arrive later as agent_ready_stage (extensions_ready / full_ready).
    const unsubReadyStage = onWs('agent_ready_stage', (msg: AIWSMessage) => {
      const stage = ((msg as any).data?.stage) || '';
      if (stage === 'extensions_ready') setToolsStage('loading');
      else if (stage === 'full_ready') setToolsStage('ready');
    });

    // Stream — accumulate chunks via ref, then sync to state (per-session)
    const streamSeqRef = { current: 0 };
    const unsubStream = onWs('stream', (msg: AIWSMessage) => {
      if (userStoppedRef.current || finalizingRef.current) return;
      const text = _extractContent(msg);
      if (text) {
        streamSeqRef.current += 1;
        const sid = eventSidRef.current || currentSessionIdRef.current || '';
        if (sid) {
          // 只累积到 ref；UI 刷新由 scheduleStreamFlush 节流合并。
          streamingTextBySessionRef.current = { ...streamingTextBySessionRef.current, [sid]: (streamingTextBySessionRef.current[sid] || '') + text };
          isStreamingBySessionRef.current = { ...isStreamingBySessionRef.current, [sid]: true };
        }
        // Solo / focused pane still uses the global streaming fields.
        if (!sid || sid === (currentSessionIdRef.current || '')) {
          streamingTextRef.current += text;
        }
        scheduleStreamFlush();
      }
    });

    // Final message / response — finalize streaming into a message
    const handleFinal = (msg: AIWSMessage) => {
      console.log('[AIChatPage] 📨 handleFinal called!', JSON.stringify(msg).substring(0, 200));
      // Guard: prevent duplicate finalization (both 'message' and 'response'
      // may fire for the same reply)
      if (userStoppedRef.current || finalizingRef.current) return;

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

        // During hydrate, buffer finals so the disk full-replace cannot wipe
        // a to_user that arrived mid-refresh (disk flush lag is common).
        if (isHydratingSessionRef.current) {
          pendingHydrationFinalsRef.current.push(chatMsg);
          return;
        }

        finalizingRef.current = true;

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
        // IMPORTANT: do NOT gate TTS on a flag mutated inside setTimeline —
        // React may defer the updater, leaving the flag false and silently skipping speech.
        // Dedup is handled inside speakFinalReply via lastAutoSpokenRef.
        if (role === 'assistant') {
          void speakFinalReplyRef.current(finalText);
          // 温和结束提示：仅在页面处于后台时才响铃（不打扰正在使用的用户）
          if (!pageActiveRef.current) playGentleNotificationSound();
        }
      }

      // Clear streaming (global + per-session bucket for this event's sid)
      cancelStreamFlush();
      const clearSid = eventSidRef.current || currentSessionIdRef.current || '';
      if (clearSid) {
        const st = { ...streamingTextBySessionRef.current };
        delete st[clearSid];
        streamingTextBySessionRef.current = st;
        setStreamingTextBySession(st);
        const ib = { ...isStreamingBySessionRef.current };
        delete ib[clearSid];
        isStreamingBySessionRef.current = ib;
        setIsStreamingBySession(ib);
        // A final message means this session's turn is over — release its
        // parallel busy marker immediately instead of waiting for the next
        // busy_sessions broadcast. The backend dispatcher re-broadcasts on a
        // ~5s idle loop, so without this the composer stays in "executing"
        // (busy) for seconds after the reply already rendered.
        if (busySessionsRef.current.includes(clearSid)) {
          const remaining = busySessionsRef.current.filter((id) => id !== clearSid);
          busySessionsRef.current = remaining;
          setBusySessions(remaining);
        }
      }
      if (!clearSid || clearSid === (currentSessionIdRef.current || '')) {
        streamingTextRef.current = '';
        setStreamingText('');
        setIsStreaming(false);
        setAgentStatus('connected');
      }

      // Fallback: clear new-session loading if a final message arrives before current_session
      if (newSessionPendingRef.current) {
        newSessionPendingRef.current = false;
        setIsLoadingSession(false);
      }

      // Reset guard after a short delay (allow next turn's final to work)
      setTimeout(() => { finalizingRef.current = false; }, 300);
    };
    const unsubMessage = onWs('message', handleFinal);
    const unsubResponse = onWs('response', handleFinal);
    const unsubToUserReply = onWs('to_user_reply', (msg: AIWSMessage) => {
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

    const unsubToUserFinal = onWs('to_user_final', (msg: AIWSMessage) => {
      handleFinal(msg);
    });

    const unsubToUserEndTask = onWs('to_user_end_task', (msg: AIWSMessage) => {
      // Same as final text, then fold agent process since the last user message.
      if (finalizingRef.current) return;
      finalizingRef.current = true;

      const text = _extractContent(msg);
      const finalText = text || streamingTextRef.current;

      if (typeof finalText === 'string' && finalText.trim().length > 0) {
        const raw = msg as any;
        const messageId = raw.message_id || raw.id || undefined;
        const chatMsg: ChatMessage = {
          role: 'assistant',
          content: finalText,
          timestamp: new Date().toISOString(),
          end_task: true,
        };
        if (messageId) {
          chatMsg.message_id = messageId;
        }

        setTimeline(prev => {
          for (let i = prev.length - 1; i >= 0; i -= 1) {
            const entry = prev[i];
            if (entry.kind !== 'message') continue;
            const existing = entry.data as ChatMessage;
            if (existing.role === 'user') break;
            if (existing.role === 'assistant') {
              if (existing.content === finalText && existing.end_task) {
                return foldTaskProcessSinceLastUser(prev);
              }
              if (messageId && existing.message_id && existing.message_id === messageId) {
                const patched = prev.map((e, idx) =>
                  idx === i && e.kind === 'message'
                    ? { ...e, data: { ...e.data, end_task: true } }
                    : e,
                );
                return foldTaskProcessSinceLastUser(patched);
              }
            }
          }
          if (messageId) {
            const exists = prev.some(e =>
              e.kind === 'message' && (e.data as ChatMessage).message_id === messageId
            );
            if (exists) {
              const patched = prev.map((e) =>
                e.kind === 'message' && (e.data as ChatMessage).message_id === messageId
                  ? { ...e, data: { ...(e.data as ChatMessage), end_task: true } }
                  : e,
              );
              return foldTaskProcessSinceLastUser(patched);
            }
          }

          let next = finalizeWorkflowAndAddMessage(prev, chatMsg);
          if (pendingFilePushesRef.current.length > 0) {
            const buffered = pendingFilePushesRef.current.map((m) => ({
              kind: 'message' as const,
              data: m,
              _uid: genUID(),
            }));
            pendingFilePushesRef.current = [];
            next = [...next, ...buffered];
          }
          return foldTaskProcessSinceLastUser(next);
        });
        void speakFinalReplyRef.current(finalText);
        // 温和结束提示：仅在页面处于后台时才响铃（不打扰正在使用的用户）
        if (!pageActiveRef.current) playGentleNotificationSound();
      }

      streamingTextRef.current = '';
      setStreamingText('');
      setIsStreaming(false);
      setAgentStatus('connected');

      if (newSessionPendingRef.current) {
        newSessionPendingRef.current = false;
        setIsLoadingSession(false);
      }

      setTimeout(() => { finalizingRef.current = false; }, 300);
    });

    // Thought — accumulate consecutive chunks into a single thought block
    const unsubThought = onWs('thought', (msg: AIWSMessage) => {
      if (userStoppedRef.current) return;
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
        // Background sub-agents (self-learn / delegate) must not flip the parent
        // chat into "thinking" — otherwise the Stop button stays on and the
        // idle message queue never drains after the sub-agent finishes.
        if (!isSubAgent) {
          setAgentStatus('thinking');
        }
      }
    });

    // Tool call
    const unsubToolCall = onWs('tool_call', (msg: AIWSMessage) => {
      if (userStoppedRef.current) return;
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
        setShellStreams((prev) => seedShellStreamFromToolCall(prev, event));
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

    // Live Native-FC tool arguments (file write/edit code streaming into tool fold)
    const unsubToolCallDelta = onWs('tool_call_delta', (msg: AIWSMessage) => {
      if (userStoppedRef.current) return;
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
      if (isHydratingSessionRef.current) {
        pendingHydrationWorkflowEventsRef.current.push({
          event,
          status: `Writing ${toolName}...`,
        });
        return;
      }
      setTimeline((prev) => appendWorkflowEvent(prev, event, `Writing ${toolName}...`));
      if (!data.sub_agent) {
        setAgentStatus('thinking');
      }
    });

    // Tool result — merge into matching tool_call
    const unsubToolResult = onWs('tool_result', (msg: AIWSMessage) => {
      if (userStoppedRef.current) return;
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
      const callId =
        typeof data === 'object' && data
          ? String(data.id || data.tool_use_id || '')
          : '';
      const resultText =
        typeof data === 'object' && data
          ? (typeof data.result === 'string'
              ? data.result
              : data.result != null
                ? JSON.stringify(data.result)
                : '')
          : '';
      if (callId && resultText) {
        setShellStreams((prev) => sealShellStreamFromResult(prev, callId, resultText));
      }
      if (isHydratingSessionRef.current) {
        pendingHydrationWorkflowEventsRef.current.push({ event, status: `${toolName} completed` });
        return;
      }
      setTimeline(prev => appendWorkflowEvent(prev, event, `${toolName} completed`));
      // Live-update session change stats / files panel after mutations (no page reload)
      const tn = String(toolName || '').toLowerCase();
      if (
        tn.includes('write') ||
        tn.includes('replace') ||
        tn.includes('delete') ||
        tn.includes('edit_file') ||
        tn.includes('filesystem') ||
        tn.includes('run_session') ||
        tn.includes('start_job') ||
        tn.includes('check_job') ||
        tn.includes('shell') ||
        tn.includes('cmd')
      ) {
        // Immediate refresh + debounced follow-up (covers slow disk / meta flush)
        void refreshSessionChangesRef.current?.();
        scheduleRefreshSessionChanges();
      }
    });

    // Live shell / background job stdout for CMD panel
    // Live shell / background job stdout for CMD panel
    const unsubJobStdout = onWs('job_stdout', (msg: AIWSMessage) => {
      const data = (msg.content || msg.data || {}) as Record<string, unknown>;
      setShellStreams((prev) => applyJobStdout(prev, data));
    });
    const unsubJobStatus = onWs('job_status', (msg: AIWSMessage) => {
      const data = (msg.content || msg.data || {}) as Record<string, unknown>;
      setShellStreams((prev) => applyJobStatus(prev, data));
    });

    // Plan — Runner sends {id, text} after parsing <plan> tag
    const unsubPlan = onWs('plan', (msg: AIWSMessage) => {
      if (userStoppedRef.current) return;
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
    const unsubSummaryStream = onWs('summary_stream', (msg: AIWSMessage) => {
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
          const entry = updated[i];
          if (entry.kind === 'workflow' && !entry.data.completed) {
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

        const targetWf = updated[targetWfIdx];
        if (targetWf.kind !== 'workflow') return prev;
        const wf = targetWf.data;
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
    const unsubCompressionProgress = onWs('compression_progress', (msg: AIWSMessage) => {
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
            const entry = updated[i];
            if (entry.kind === 'workflow' && !entry.data.completed) {
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

          const targetWf = updated[targetWfIdx];
          if (targetWf.kind !== 'workflow') return prev;
          const wf = targetWf.data;
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

    // Token stats (per-session — parallel panes show their own %)
    const unsubTokenStats = aiWsService.on('token_stats', (msg: AIWSMessage) => {
      const data = msg.content || msg.data;
      if (!data || typeof data !== 'object') return;
      const stats: TokenStatsState = {
        used: Number((data as any).used) || 0,
        max: Number((data as any).max) || 0,
        breakdown: (data as any).breakdown,
        session: (data as any).session,
        cumulative: (data as any).cumulative,
      };
      // Prefer explicit sid from the payload. Do not attribute another session's
      // broadcast to the focused history tab (currentSessionIdRef).
      const sid = String(
        msg.sid
        || (data as any).session_id
        || agentCurrentSessionIdRef.current
        || '',
      ).trim();
      applyTokenStats(sid || null, stats);
    });

    // Status / state / wake / sleep / info
    const handleStatus = (msg: AIWSMessage) => {
      const data = msg.content || msg.data;
      if (typeof data === 'string') {
        const lower = data.toLowerCase();
        const statusSid = String((msg as any).sid || '').trim();
        const otherBusy = busySessionsRef.current.some(
          (id) => id && id !== statusSid,
        );
        // Always allow idle / stopped so the Stop button releases — but do not
        // paint the whole agent idle while another parallel session is still busy.
        if (
          data === 'idle' ||
          data === 'ready' ||
          lower.includes('task stopped') ||
          lower.includes('response complete') ||
          lower.includes('idle') ||
          lower.includes('ready') ||
          lower.includes('complete')
        ) {
          if (!otherBusy) {
            setAgentStatus('idle');
          }
          if (lower.includes('task stopped')) {
            if (!statusSid || statusSid === (currentSessionIdRef.current || '')) {
              setIsStreaming(false);
            }
            if (statusSid) {
              const ib = { ...isStreamingBySessionRef.current };
              ib[statusSid] = false;
              isStreamingBySessionRef.current = ib;
              setIsStreamingBySession(ib);
            }
          }
          return;
        }
        if (userStoppedRef.current) return;
        if (data === 'thinking' || data === 'processing') {
          setAgentStatus('thinking');
        } else if (data === 'working') {
          setAgentStatus('working');
        } else if (data === 'sleeping') {
          setAgentStatus('sleeping');
        }
      }
    };
    const unsubState = aiWsService.on('state', (msg: AIWSMessage) => {
      // Only update agentStatus — do NOT add to timeline; StatusBadge already reflects state changes
      handleStatus(msg);
    });
    const unsubStatusEvt = aiWsService.on('status', handleStatus);
    const unsubWake = onWs('wake', (msg: AIWSMessage) => {
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
    const unsubSleep = onWs('sleep', (msg: AIWSMessage) => {
      setAgentStatus('sleeping');
      const raw = msg.content ?? msg.data;
      const seconds = typeof raw === 'number' ? raw : parseInt(String(raw), 10);
      setTimeline(prev => [...prev, {
        kind: 'status_hint' as const,
        data: { hintType: 'sleep' as const, content: isNaN(seconds) ? 0 : seconds, timestamp: Date.now() },
        _uid: genUID(),
      }]);
    });
    const unsubInfo = onWs('info', (msg: AIWSMessage) => {
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
          const sid = String((detailed as any).session_id || '').trim();
          // Always promote to agent-wide last pick (new chats + refresh default).
          if (typeof switchedCard === 'string' && switchedCard) {
            setCurrentCardName(switchedCard);
            saveLastModelPick(agentId, { card: switchedCard });
          }
          if (typeof switchedModel === 'string' && switchedModel) {
            setModelName(switchedModel);
          }
          if (sid) {
            delete modelSwitchRevertRef.current[sid];
            if (typeof switchedCard === 'string' && switchedCard) {
              setCardNameBySession((prev) => ({ ...prev, [sid]: switchedCard }));
            }
            if (typeof switchedModel === 'string' && switchedModel) {
              setModelNameBySession((prev) => ({ ...prev, [sid]: switchedModel }));
            }
            setSwitchingModelBySession((prev) => ({ ...prev, [sid]: false }));
          } else {
            // Agent-default switch (e.g. from TUI): it takes effect for every
            // session without its own override, so roll all panes onto the new
            // card instead of letting stale per-session picks keep showing the
            // old model.
            setSwitchingModel(false);
            if (typeof switchedCard === 'string' && switchedCard) {
              setCardNameBySession((prev) => {
                const next: Record<string, string> = {};
                for (const k of Object.keys(prev)) next[k] = switchedCard;
                return next;
              });
            }
            if (typeof switchedModel === 'string' && switchedModel) {
              setModelNameBySession((prev) => {
                const next: Record<string, string> = {};
                for (const k of Object.keys(prev)) next[k] = switchedModel;
                return next;
              });
            }
          }
        }

        if (evt === 'voice_config_updated') {
          const v = (detailed as any).voice || {};
          setVoiceBindings({
            asr_card: String(v.asr_card || ''),
            tts_card: String(v.tts_card || ''),
            realtime_card: String(v.realtime_card || ''),
            realtime_voice: String(v.realtime_voice || ''),
          });
          return;
        }

        if (evt === 'model_card_switch_failed') {
          const sid = String((detailed as any).session_id || '').trim();
          if (sid) {
            const revert = modelSwitchRevertRef.current[sid];
            if (revert) {
              if (revert.card) {
                setCardNameBySession((prev) => ({ ...prev, [sid]: revert.card as string }));
              }
              setModelNameBySession((prev) => ({ ...prev, [sid]: revert.model }));
              delete modelSwitchRevertRef.current[sid];
            }
            setSwitchingModelBySession((prev) => ({ ...prev, [sid]: false }));
          } else {
            setSwitchingModel(false);
          }
          // Fall through so the failure shows in the timeline / system info
        }

        if (evt === 'reasoning_effort_changed') {
          const next = (detailed as any).effort;
          const sid = String((detailed as any).session_id || '').trim();
          if (next === 'low' || next === 'medium' || next === 'high') {
            setReasoningEffort(next);
            saveLastModelPick(agentId, { effort: next });
            if (sid) {
              setReasoningBySession((prev) => ({ ...prev, [sid]: next }));
            }
          }
          return; // don't spam timeline
        }

        if (evt === 'mode_switch_approval') {
          const id = String((detailed as any).id || '');
          const fromMode = (detailed as any).from_mode;
          const toMode = (detailed as any).to_mode;
          if (id && (toMode === 'plan' || toMode === 'build')) {
            const approval: ModeSwitchApproval = {
              id,
              from_mode: fromMode === 'plan' || fromMode === 'build' ? fromMode : 'build',
              to_mode: toMode,
              reason: String((detailed as any).reason || (detailed as any).text || ''),
              status: 'pending',
            };
            setModeApprovals((prev) => {
              if (prev.some((a) => a.id === id)) return prev;
              return [...prev, approval];
            });
          }
          // Fall through so the timeline also shows the approval card
        }

        if (evt === 'agent_mode_changed') {
          const next = (detailed as any).mode;
          const sid = String((detailed as any).session_id || '').trim();
          if (next === 'plan' || next === 'build') {
            if (sid) {
              setAgentModeBySession((prev) => ({ ...prev, [sid]: next }));
            } else {
              setAgentMode(next);
            }
          }
          return;
        }

        if (evt === 'goal_changed') {
          const g = (detailed as any).goal;
          if (g && typeof g === 'object' && String(g.objective || '').trim()) {
            setActiveGoal({
              objective: String(g.objective || '').trim(),
              status: String(g.status || 'pursuing'),
              last_progress: g.last_progress ? String(g.last_progress) : undefined,
              blocked_reason: g.blocked_reason ? String(g.blocked_reason) : undefined,
            });
          } else {
            setActiveGoal(null);
          }
          return;
        }

        if (evt === 'mode_switch_resolved') {
          const id = String((detailed as any).id || (detailed as any).approved_request_id || '');
          const status = (detailed as any).status === 'denied' ? 'denied' : 'approved';
          const toMode = (detailed as any).to_mode || (detailed as any).mode;
          if (id) {
            setModeApprovals((prev) =>
              prev.map((a) => (a.id === id ? { ...a, status } : a)),
            );
          }
          if (status === 'approved' && (toMode === 'plan' || toMode === 'build')) {
            const sid = String((detailed as any).session_id || '').trim();
            if (sid) {
              setAgentModeBySession((prev) => ({ ...prev, [sid]: toMode }));
            } else {
              setAgentMode(toMode);
            }
          }
          // Fall through to update timeline card status via re-render of pending→resolved
        }

        if (evt === 'propose_options') {
          const id = String((detailed as any).id || '');
          const prompt = String((detailed as any).prompt || '请选择一个选项：');
          const rawOpts = (detailed as any).options || [];
          const options = Array.isArray(rawOpts)
            ? rawOpts
                .map((o: any) => ({
                  id: String((o && o.id) || ''),
                  title: String((o && (o.title || o.name || o.label)) || ''),
                  description: String((o && (o.description || o.summary)) || '') || undefined,
                }))
                .filter((o: { id: string; title: string }) => o.id && o.title)
            : [];
          const allowCustom = (detailed as any).allow_custom !== false;
          const allowMultiple = !!(detailed as any).allow_multiple;
          if (id && options.length >= 2) {
            const proposal: OptionsProposal = {
              id,
              prompt,
              options,
              allow_custom: allowCustom,
              allow_multiple: allowMultiple,
              status: 'pending',
            };
            setOptionsProposals((prev) => {
              if (prev.some((p) => p.id === id)) {
                return prev.map((p) => (p.id === id ? { ...p, ...proposal } : p));
              }
              return [...prev, proposal];
            });
          }
          // Fall through so the timeline also shows a compact status line
        }

        if (evt === 'propose_options_resolved') {
          const id = String((detailed as any).id || '');
          const statusRaw = String((detailed as any).status || 'chosen');
          const status: OptionsProposal['status'] =
            statusRaw === 'ignored' ? 'ignored' : statusRaw === 'custom' ? 'custom' : 'chosen';
          const chosenOptionId = String((detailed as any).chosen_option_id || '');
          const rawIds = (detailed as any).chosen_option_ids;
          const chosenOptionIds = Array.isArray(rawIds)
            ? rawIds.map((x: any) => String(x)).filter(Boolean)
            : chosenOptionId
              ? [chosenOptionId]
              : [];
          const customAnswer = String((detailed as any).custom_answer || '');
          if (id) {
            setOptionsProposals((prev) =>
              prev.map((p) =>
                p.id === id
                  ? {
                      ...p,
                      status,
                      chosen_option_id: chosenOptionIds[0] || chosenOptionId,
                      chosen_option_ids: chosenOptionIds,
                      custom_answer: customAnswer,
                    }
                  : p,
              ),
            );
          }
          // Fall through to update timeline compact status
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
      // Self-Learn runs outside a parent turn; nested thoughts used to leave the
      // chat stuck on "thinking". Only release for Self-Learn completions.
      const subEvt =
        typeof detailed === 'object' && detailed !== null
          ? String((detailed as any).event || '')
          : '';
      const subLabel =
        typeof detailed === 'object' && detailed !== null
          ? String((detailed as any).sub_task_label || (detailed as any).message || '')
          : '';
      if (
        isSubAgent &&
        subEvt === 'sub_agent_result' &&
        /self-learn|self_learn/i.test(subLabel)
      ) {
        setAgentStatus((prev) => (prev === 'thinking' ? 'idle' : prev));
        setIsStreaming(false);
      }
    });

    // Turn start — reset streaming state and record workflow start timestamp (first turn only)
    const unsubTurnStart = onWs('turn_start', (msg: AIWSMessage) => {
      if (userStoppedRef.current) return;
      const data = msg.content ?? msg.data;
      const turnSid = String(eventSidRef.current || '').trim();
      const isFocusedTurn =
        !turnSid || turnSid === (currentSessionIdRef.current || '');
      // turn=1 means the very first LLM call for this user message.
      // turn>=2 means the agent is re-entering the loop after a tool call (same workflow).
      // data===0 means a session management command (NEW_SESSION, LOAD_SESSION, etc.).
      const turnNumber = typeof data === 'object' && data !== null ? (data as any).turn : 0;
      const isFirstTurn = turnNumber <= 1; // turn=1 or data=0 (management)

      // Salvage unfinalized streaming text ONLY on the first turn of a new user message.
      // On subsequent turns (tool call re-entries), the agent is still in the same workflow —
      // salvaging here would incorrectly finalize the ongoing workflow block and cause a new
      // WorkflowContainer to be created for the next tool call.
      // Scope to this event's session so pane B's turn_start cannot seal pane A's stream.
      const salvageSrc = turnSid
        ? (streamingTextBySessionRef.current[turnSid] || (isFocusedTurn ? streamingTextRef.current : ''))
        : streamingTextRef.current;
      if (isFirstTurn && salvageSrc && !finalizingRef.current) {
        const salvaged = salvageSrc;
        if (salvaged.trim().length > 0) {
          const salvagedMsg: ChatMessage = {
            role: 'assistant',
            content: salvaged,
            timestamp: new Date().toISOString(),
          };
          setTimeline(prev => finalizeWorkflowAndAddMessage(prev, salvagedMsg));
        }
      }
      if (isFirstTurn && isFocusedTurn) {
        lastAutoSpokenRef.current = '';
        // Cancel any leftover auto-TTS from the previous turn.
        autoTtsGenRef.current += 1;
        autoTtsTextQueueRef.current = [];
        autoTtsUrlQueueRef.current = [];
        autoTtsOrderedUrlsRef.current.clear();
        autoTtsNextSynthSeqRef.current = 0;
        autoTtsNextPlaySeqRef.current = 0;
        autoTtsSynthActiveRef.current = 0;
        autoTtsPlayingRef.current = false;
        autoTtsStreamOffsetRef.current = 0;
        const a = autoTtsAudioRef.current;
        if (a) {
          a.pause();
          a.removeAttribute('src');
          a.load();
        }
      }
      if (turnSid) {
        cancelStreamFlush();
        const st = { ...streamingTextBySessionRef.current };
        delete st[turnSid];
        streamingTextBySessionRef.current = st;
        setStreamingTextBySession(st);
        const ib = { ...isStreamingBySessionRef.current };
        ib[turnSid] = false;
        isStreamingBySessionRef.current = ib;
        setIsStreamingBySession(ib);
      }
      if (isFocusedTurn) {
        streamingTextRef.current = '';
        setStreamingText('');
        setIsStreaming(false);
        finalizingRef.current = false;
      }
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
        if (isFocusedTurn) setAgentStatus('thinking');
        clearOutboundTurnPending(turnSid || undefined);
      }
      if (isRealWorkflow && isFirstTurn) {
        const startedMs = (data as any).started_ms as number;
        // Reset timer on each new workflow (turn=1). Subsequent turns (2+ after tool calls)
        // must NOT overwrite it so the timer reflects the full workflow duration.
        // Only the focused pane drives the solo chrome timer.
        if (isFocusedTurn) {
          setTurnStartedMs(startedMs);
        }
        // Stamp started_ms onto the active incomplete workflow (or create one) so
        // refresh can restore Working-for-Xs without relying on live turnStartedMs.
        setTimeline((prev) => {
          const updated = [...prev];
          for (let i = updated.length - 1; i >= 0; i--) {
            const entry = updated[i];
            if (entry.kind !== 'workflow') continue;
            if (entry.data.completed) break;
            updated[i] = {
              ...entry,
              data: { ...entry.data, started_ms: entry.data.started_ms ?? startedMs, completed: false },
            };
            return updated;
          }
          updated.push({
            kind: 'workflow',
            data: {
              events: [],
              status: 'working',
              completed: false,
              started_ms: startedMs,
            },
            _uid: genUID(),
          });
          return updated;
        });
      }
    });

    // Turn elapsed — backend sends {started_ms, ended_ms} after to_user_final
    const unsubTurnElapsed = onWs('turn_elapsed', (msg: AIWSMessage) => {
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
            } as TimelineEntry;
            break;
          }
        }
        return updated;
      });
      // Clear live start timestamp (turn is over)
      setTurnStartedMs(undefined);
      scheduleRefreshSessionChanges();
    });

    // Prompt update — insert/update prompt entry in timeline (first item = first prompt)
    const unsubPromptUpdate = onWs('prompt_update', (msg: AIWSMessage) => {
      const data: any = msg.content ?? msg.data ?? msg;
      const systemPrompt: string = data?.system_prompt ?? '';
      const dynamicPrefix: string = data?.dynamic_prefix ?? '';
      const changed: boolean = data?.changed ?? false;
      const diff: string[] | undefined = Array.isArray(data?.diff) ? data.diff : undefined;
      if (!systemPrompt) return;
      const entry: TimelineEntry = {
        kind: 'prompt' as const,
        data: {
          system_prompt: systemPrompt,
          dynamic_prefix: dynamicPrefix,
          changed,
          timestamp: new Date().toISOString(),
          diff,
        },
        _uid: genUID(),
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
    const unsubOutputMedia = onWs('output_media', (msg: AIWSMessage) => {
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

    const unsubVoiceAudioOut = aiWsService.on('voice_audio_out', (msg: AIWSMessage) => {
      const data: any = msg.content ?? msg.data ?? {};
      const audio = data?.audio || data?.data?.audio;
      const url = data?.url || data?.data?.url;
      const format = data?.format || data?.mime || '';
      if (audio) {
        window.dispatchEvent(new CustomEvent('opensquad-voice-audio-out', { detail: { audio } }));
      } else if (url) {
        window.dispatchEvent(
          new CustomEvent('opensquad-voice-audio-out', {
            detail: { url, format, mime: data?.mime },
          }),
        );
      }
    });
    const unsubVoiceTranscript = aiWsService.on('voice_transcript', (msg: AIWSMessage) => {
      const data: any = msg.content ?? msg.data ?? {};
      const role = (data?.role === 'user' ? 'user' : 'assistant') as 'user' | 'assistant';
      const chunk = String(data?.text || data?.delta || '');
      if (!chunk) return;
      const caps = voiceCaptionRef.current;
      const last = caps.length ? caps[caps.length - 1] : null;
      if (data?.final) {
        // Prefer replacing an in-progress same-role line with the authoritative final text.
        if (last && last.role === role) {
          last.text = chunk;
        } else {
          caps.push({ role, text: chunk });
        }
      } else if (last && last.role === role) {
        last.text += chunk;
      } else {
        caps.push({ role, text: chunk });
      }
      // Cap length so the panel stays readable
      if (caps.length > 40) {
        voiceCaptionRef.current = caps.slice(-40);
      }
      setVoiceTranscript(
        voiceCaptionRef.current.map((c) => `${c.role}: ${c.text}`).join('\n'),
      );
    });
    const unsubVoiceStatus = aiWsService.on('voice_realtime_status', (msg: AIWSMessage) => {
      const data: any = msg.content ?? msg.data ?? {};
      const status = data?.status || (typeof data === 'string' ? data : 'idle');
      clearVoiceConnectTimer();
      // Ignore option-ack / idle noise when not in a call attempt.
      if (status === 'options_updated' || (status === 'idle' && data?.note)) {
        return;
      }

      const active =
        status === 'connected' ||
        status === 'tool_running' ||
        status === 'connecting' ||
        status === 'session.created' ||
        status === 'session.updated';

      // Resume probe after refresh: keep live session, or restart if agent lost it.
      if (voiceResumeProbeRef.current) {
        voiceResumeProbeRef.current = false;
        if (active) {
          const force =
            data?.force_ask_agent != null
              ? Boolean(data.force_ask_agent)
              : (readVoiceCallPersist(agentId)?.forceAskAgent ?? true);
          writeVoiceCallPersist(agentId, force);
          setVoiceRealtimeStatus(String(status));
          setVoiceRealtimeError('');
          setVoicePanelOpen(true);
          return;
        }
        const persist = readVoiceCallPersist(agentId);
        if (persist) {
          setVoiceRealtimeStatus('connecting');
          setVoiceRealtimeError('');
          setVoicePanelOpen(true);
          armVoiceConnectTimeout();
          aiWsService.startVoiceRealtime({ force_ask_agent: persist.forceAskAgent });
          return;
        }
      }

      if (active) {
        const force =
          data?.force_ask_agent != null
            ? Boolean(data.force_ask_agent)
            : (readVoiceCallPersist(agentId)?.forceAskAgent ?? true);
        writeVoiceCallPersist(agentId, force);
      } else if (status === 'disconnected' || status === 'idle' || status === 'error') {
        clearVoiceCallPersist(agentId);
      }

      setVoiceRealtimeStatus(String(status));
      if (status === 'error') {
        const errText = data?.error ? String(data.error) : 'Realtime connection failed';
        setVoiceRealtimeError(errText);
        console.warn('[AIChatPage] voice realtime error', errText);
      } else if (status === 'connected' || status === 'disconnected' || status === 'idle') {
        setVoiceRealtimeError('');
      }
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
      hydrateCurrentSessionRef.current = hydrateCurrentSession;
      const seq = ++sessionReloadSeqRef.current;
      isHydratingSessionRef.current = true;
      sessionBootstrapDoneRef.current = false;
      // Only clear chrome-ready on explicit loading hydrates (refresh/connect).
      // Soft reloads must not flash the full-pane "加载会话中" over an open chat.
      if (opts?.showLoading) {
        setSessionBootstrapped(false);
      }
      pendingHydrationMediaRef.current = [];
      pendingHydrationWorkflowEventsRef.current = [];
      pendingHydrationFinalsRef.current = [];
      diskSessionLoadedRef.current = false;

      (async () => {
        try {
          if (opts?.showLoading) {
            setIsLoadingSession(true);
            setSessionLoadingLabel(t('aiChat.loadingSession'));
          }
          const resp = await Promise.race([
            // First page only — older turns load on scroll-up via loadMoreHistory.
            agentSessionAPI.getCurrentSession(agentId, 0, SESSION_HISTORY_PAGE_SIZE),
            new Promise<never>((_, reject) =>
              setTimeout(() => reject(new Error('Hydration timeout (10s)')), 10000)
            ),
          ]);
          if (seq !== sessionReloadSeqRef.current || viewingHistorySessionRef.current || newSessionPendingRef.current) {
            return;
          }
          const currentSid = resp.current_session_id;
          const session = resp.session;
          if (currentSid) {
            agentCurrentSessionIdRef.current = currentSid;
          }
          const guard = newSessionGuardRef.current;
          if (
            guard &&
            Date.now() < guard.until &&
            currentSid &&
            currentSid !== guard.sid
          ) {
            console.warn(
              '[AIChatPage] hydrate ignored (new-session guard): disk=%s keep=%s',
              currentSid,
              guard.sid,
            );
            return;
          }
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
            if (currentSid) {
              putCachedSessionTimeline(agentId, currentSid, entries, {
                complete: !(session.has_more ?? false),
                messageCount: dedupedMessages.length,
                totalMessages: session.total_messages,
              });
            }
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
                    } as TimelineEntry;
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
                  } as TimelineEntry;
                  continue;
                }

                // (3) Prefer richer assistant text onto the trailing disk
                //     assistant (Gateway history often has cleaned to_user
                //     while disk api_sync is still empty / lagging).
                const hasMedia = !!(
                  (m.images && m.images.length > 0) ||
                  (m.attachments && m.attachments.length > 0) ||
                  (Array.isArray((m as any).files) && (m as any).files.length > 0)
                );
                const hasText = mContentNorm.length > 0;
                if (m.role === 'assistant' && hasText) {
                  let richerIdx = -1;
                  for (let i = mergedEntries.length - 1; i >= 0; i -= 1) {
                    const entry = mergedEntries[i];
                    if (entry.kind !== 'message') continue;
                    const d = entry.data as ChatMessage;
                    if (d.role === 'user') break;
                    if (d.role !== 'assistant') continue;
                    const dLen = (typeof d.content === 'string' ? d.content : '').trim().length;
                    const mLen = mContentNorm.length;
                    if (mLen > dLen) {
                      richerIdx = i;
                    }
                    break;
                  }
                  if (richerIdx >= 0) {
                    mergedEntries[richerIdx] = {
                      ...mergedEntries[richerIdx],
                      data: _mergeChatMessage(mergedEntries[richerIdx].data as ChatMessage, m),
                    } as TimelineEntry;
                    continue;
                  }
                }

                // (4) Disk flush lag: keep Gateway-history text (and media)
                //     that is not already on disk. Previously only media
                //     survived here, which dropped to_user finals that only
                //     existed in the Gateway WS cache.
                if (!hasMedia && !hasText) continue;

                if (!Number.isNaN(mTs)) {
                  const nearIdx = mergedEntries.findIndex((e: any) => {
                    if (e.kind !== 'message') return false;
                    const d = e.data as ChatMessage;
                    if (d.role !== 'assistant') return false;
                    const dTs = d.timestamp ? new Date(d.timestamp).getTime() : NaN;
                    if (Number.isNaN(dTs)) return false;
                    return Math.abs(dTs - mTs) <= BUFFER_DEDUP_WINDOW_MS;
                  });
                  if (nearIdx >= 0 && hasMedia) {
                    mergedEntries[nearIdx] = {
                      ...mergedEntries[nearIdx],
                      data: _mergeChatMessage(mergedEntries[nearIdx].data as ChatMessage, m),
                    } as TimelineEntry;
                    continue;
                  }

                  const insertAt = mergedEntries.findIndex((e: any) => {
                    if (e.kind !== 'message') return false;
                    const d = e.data as ChatMessage;
                    const dTs = d.timestamp ? new Date(d.timestamp).getTime() : NaN;
                    if (Number.isNaN(dTs)) return false;
                    return dTs > mTs;
                  });
                  const entry = {
                    kind: 'message' as const,
                    data: m,
                    _uid: String((m as any).message_id || (m as any).client_id || '').trim() || genUID(),
                  };
                  if (insertAt >= 0) {
                    mergedEntries.splice(insertAt, 0, entry);
                  } else {
                    mergedEntries.push(entry);
                  }
                  continue;
                }
                mergedEntries.push({
                  kind: 'message',
                  data: m,
                  _uid: String((m as any).message_id || (m as any).client_id || '').trim() || genUID(),
                });
              }
              nextEntries = mergedEntries;
            }

            nextEntries = flushBufferedFilePushes(nextEntries);

            const isCompressionHydration = compressionHydrationPendingRef.current;
            compressionHydrationPendingRef.current = false;

            if (isCompressionHydration) {
              // Keep live message order + in-flight tool stream; disk snapshot
              // already has archived turns flattened into the normal timeline.
              eventSidRef.current = currentSid || '';
              setTimeline((prev) => {
                let merged = _mergeCompressionHydration(prev, nextEntries);
                const bufferedWf = pendingHydrationWorkflowEventsRef.current;
                pendingHydrationWorkflowEventsRef.current = [];
                for (const { event, status } of bufferedWf) {
                  merged = appendWorkflowEvent(merged, event, status);
                }
                const bufferedFinals = pendingHydrationFinalsRef.current;
                pendingHydrationFinalsRef.current = [];
                for (const chatMsg of bufferedFinals) {
                  const mid = chatMsg.message_id;
                  const already = merged.some((e) => {
                    if (e.kind !== 'message') return false;
                    const d = e.data as ChatMessage;
                    if (mid && d.message_id && d.message_id === mid) return true;
                    return d.role === 'assistant' && d.content === chatMsg.content;
                  });
                  if (already) continue;
                  merged = finalizeWorkflowAndAddMessage(merged, chatMsg);
                }
                return _stabilizeHydratedTimeline(prev, merged);
              });
              eventSidRef.current = '';
              // Restore CMD panels from disk; keep any live streams preferred.
              setShellStreams((live) => ({
                ...rebuildShellStreamsFromTimeline(nextEntries),
                ...live,
              }));
            } else {
              // Full replace path (connect / session switch / refresh).
              const bufferedWf = pendingHydrationWorkflowEventsRef.current;
              pendingHydrationWorkflowEventsRef.current = [];
              let withBuffered = nextEntries;
              for (const { event, status } of bufferedWf) {
                withBuffered = appendWorkflowEvent(withBuffered, event, status);
              }
              const bufferedFinals = pendingHydrationFinalsRef.current;
              pendingHydrationFinalsRef.current = [];
              for (const chatMsg of bufferedFinals) {
                // Dedup against disk/Gateway-merged timeline before sealing.
                const mid = chatMsg.message_id;
                const already = withBuffered.some((e) => {
                  if (e.kind !== 'message') return false;
                  const d = e.data as ChatMessage;
                  if (mid && d.message_id && d.message_id === mid) return true;
                  return d.role === 'assistant' && d.content === chatMsg.content;
                });
                if (already) continue;
                withBuffered = finalizeWorkflowAndAddMessage(withBuffered, chatMsg);
              }
              if (bufferedFinals.length > 0) {
                // Finals that landed during hydrate replace the streaming bubble.
                streamingTextRef.current = '';
                setStreamingText('');
                setIsStreaming(false);
              }
              // If disk snapshot lags (common right after new session / early tools),
              // keep any already-rendered live workflow so the Worked/Working fold
              // does not vanish mid-turn. Also keep optimistic user bubbles that
              // are not yet on disk (first send on a brand-new session).
              // Write into the disk response's sid — not whatever UI focus was
              // (parallel/scheduled current_session must not redirect hydrate).
              // IMPORTANT: mirror currentSid into currentSessionIdRef BEFORE
              // setTimeline. When the agent never announced current_session
              // (startup load) the `connected` event carries only the gateway
              // session key and currentSessionIdRef stays null; setTimeline's
              // solo-mirror condition (eventSidRef === currentSessionIdRef)
              // then fails and the hydrated timeline never reaches the chat
              // pane -> blank chat after service restart.
              currentSessionIdRef.current = currentSid || currentSessionIdRef.current;
              eventSidRef.current = currentSid || '';
              setTimeline((prev) => {
                let merged = withBuffered;
                // Preserve optimistic user messages missing from disk.
                const diskKeys = new Set<string>();
                for (const e of withBuffered) {
                  if (e.kind !== 'message') continue;
                  const k = _messageIdentityKey(e.data as ChatMessage);
                  if (k) diskKeys.add(k);
                }
                for (const e of prev) {
                  if (e.kind !== 'message') continue;
                  const msg = e.data as ChatMessage;
                  if (msg.role !== 'user') continue;
                  const k = _messageIdentityKey(msg);
                  if (k && diskKeys.has(k)) continue;
                  // Content-level fallback when message_id differs.
                  const norm = (typeof msg.content === 'string' ? msg.content : '').trim();
                  const dup = merged.some((m) => {
                    if (m.kind !== 'message') return false;
                    const d = m.data as ChatMessage;
                    return d.role === 'user' && (typeof d.content === 'string' ? d.content : '').trim() === norm;
                  });
                  if (dup) continue;
                  merged = [...merged, e];
                  if (k) diskKeys.add(k);
                }
                const liveWfs = prev.filter(
                  (e) => e.kind === 'workflow' && !(e as { data: WorkflowBlock }).data.completed,
                );
                if (liveWfs.length === 0) {
                  return _stabilizeHydratedTimeline(prev, merged);
                }
                const diskHasLive = merged.some(
                  (e) => e.kind === 'workflow' && !(e as { data: WorkflowBlock }).data.completed,
                );
                if (diskHasLive) {
                  return _stabilizeHydratedTimeline(prev, merged);
                }
                for (const wf of liveWfs) {
                  for (const evt of (wf as { data: WorkflowBlock }).data.events) {
                    merged = appendWorkflowEvent(
                      merged,
                      evt,
                      (wf as { data: WorkflowBlock }).data.status || 'Working...',
                    );
                  }
                }
                return _stabilizeHydratedTimeline(prev, merged);
              });
              eventSidRef.current = '';
              setShellStreams(rebuildShellStreamsFromTimeline(withBuffered));
              nextEntries = withBuffered;
            }
            // Restore pending propose_options cards after refresh / session switch.
            const allEvents = [
              ...(session.archived_events || []),
              ...(session.events || []),
            ];
            setOptionsProposals(hydrateOptionsProposalsFromEvents(allEvents));
            // Restore live Working timer from disk started_ms after refresh.
            {
              let restoredStart: number | undefined;
              for (let i = nextEntries.length - 1; i >= 0; i--) {
                const e = nextEntries[i];
                if (e.kind !== 'workflow' || e.data.completed) continue;
                if (typeof e.data.started_ms === 'number') {
                  restoredStart = e.data.started_ms;
                } else {
                  const firstTs = e.data.events[0]?.timestamp;
                  if (typeof firstTs === 'number') restoredStart = firstTs;
                }
                break;
              }
              setTurnStartedMs(restoredStart);
            }
            sessionBootstrapDoneRef.current = true;
            currentSessionIdRef.current = currentSid;
            wsServiceRef.current?.setActiveSession(currentSid);
            setCurrentSessionId(currentSid);
            // Hydration complete — ask agent for this session's context % now
            // (do not wait for the next user send).
            try {
              wsServiceRef.current?.requestTokenStats(currentSid);
            } catch {
              /* ignore */
            }
            // Composer model/effort follow the last UI pick (currentCardName /
            // reasoningEffort), not a stale per-session card from disk.
            viewingHistorySessionRef.current = false;
            setViewingHistorySession(false);
            loadingSessionIdRef.current = currentSid;
            historyOffsetRef.current = session.messages?.length || 0;
            setHasMoreHistory(session.has_more ?? false);
            diskSessionLoadedRef.current = true;
          } else {
            // Disk session unavailable — use buffered WS history as fallback
            setOptionsProposals([]);
            const buffered = pendingHydrationMediaRef.current;
            pendingHydrationMediaRef.current = [];
            if (buffered.length > 0) {
              const fallbackEntries: TimelineEntry[] = [];
              for (const m of buffered) {
                fallbackEntries.push({
                  kind: 'message',
                  data: m,
                  _uid: String((m as any).message_id || (m as any).client_id || '').trim() || genUID(),
                });
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
              fallbackEntries.push({
                kind: 'message',
                data: m,
                _uid: String((m as any).message_id || (m as any).client_id || '').trim() || genUID(),
              });
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
            setSessionBootstrapped(true);
            if (!sessionBootstrapDoneRef.current) {
              sessionBootstrapDoneRef.current = true;
            }
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
            const lateFinals = pendingHydrationFinalsRef.current;
            if (lateFinals.length > 0) {
              pendingHydrationFinalsRef.current = [];
              setTimeline((prev) => {
                let next = prev;
                for (const chatMsg of lateFinals) {
                  const mid = chatMsg.message_id;
                  const already = next.some((e) => {
                    if (e.kind !== 'message') return false;
                    const d = e.data as ChatMessage;
                    if (mid && d.message_id && d.message_id === mid) return true;
                    return d.role === 'assistant' && d.content === chatMsg.content;
                  });
                  if (already) continue;
                  next = finalizeWorkflowAndAddMessage(next, chatMsg);
                }
                return next;
              });
              streamingTextRef.current = '';
              setStreamingText('');
              setIsStreaming(false);
            }
            // Allow WS history events to flow through when disk session is unavailable
            if (!diskSessionLoadedRef.current) {
              sessionBootstrapDoneRef.current = true;
            }
          }
          // Superseded hydrates leave loading to the latest seq; mount failsafe
          // covers abandoned supersedes during reconnect churn.
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

    // Disk hydrate does NOT need agent WS. If we wait only for `connected`, a
    // reconnecting agent (keepalive timeout / 1013 agent_not_ready) leaves
    // sessionBootstrapped=false forever → stuck「加载会话中」.
    hydrateCurrentSession({ showLoading: true });
    const bootstrapFailsafeTimer = window.setTimeout(() => {
      if (!sessionBootstrapDoneRef.current) {
        console.warn('[AIChatPage] session bootstrap failsafe — clearing stuck loading overlay');
        setIsLoadingSession(false);
        isHydratingSessionRef.current = false;
        setSessionBootstrapped(true);
        sessionBootstrapDoneRef.current = true;
      } else {
        setIsLoadingSession(false);
        setSessionBootstrapped(true);
      }
    }, 12000);

    // ---- Session & connection events ----

    // Gateway sends "connected" immediately after WS handshake with
    // session_id and history_count. This is the initial session info.
    // The message shape is: {type:"connected", agent_id, agent_name, session_id, history_count}
    // (all fields at top level, no content/data wrapper)
    const unsubConnected = aiWsService.on('connected', (msg: AIWSMessage) => {
      // Fields are at top level of the message object
      const raw = msg as any;
      const sid = raw.session_id || raw.sessionId || (raw.content && raw.content.session_id);
      // Gateway fallback when the agent has not yet reported its disk session:
      // session_id == gateway_session_key ("<user_id>:<agent_id>") is NOT a
      // real history key — using it 404s every /agent-sessions/{sid} read and
      // pollutes the timeline cache. Skip it; the disk hydrate / current_session
      // event supplies the canonical id shortly after.
      const isGatewaySessionKey =
        !!sid &&
        typeof sid === 'string' &&
        sid.includes(':') &&
        sid.endsWith(`:${agentId}`);
      if (sid && !isGatewaySessionKey) {
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
      // First paint already hydrates with a spinner. Later reconnects must be soft
      // or every agent flap resets sessionBootstrapped and looks "stuck loading".
      hydrateCurrentSession({ showLoading: !sessionBootstrapDoneRef.current });
    });

    // Gateway sends individual "history" messages (one per historical msg)
    // right after "connected". Shape: {type:"history", role:"user"|"assistant", content:"..."}
    // (fields at top level, no content/data wrapper)
    // NOTE: If disk session was loaded successfully, skip these bare text messages
    // because the disk session already contains full data with events.
    const unsubHistory = onWs('history', (msg: AIWSMessage) => {
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
            const name = f.original_name || f.filename || 'file';
            const isVoice =
              f.type === 'voice' || f.type === 'audio' || !!f.is_audio
              || (typeof f.content_type === 'string' && f.content_type.startsWith('audio/'))
              || /^voice_.*\.webm$/i.test(name);
            return {
              name,
              size: sz(f.size),
              url: rawUrl || undefined,
              type: f.is_video && !isVoice ? 'video' as const : isVoice ? (f.type === 'voice' ? 'voice' as const : 'audio' as const) : 'file' as const,
              duration: typeof f.duration === 'number' ? f.duration : undefined,
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
      // After canonical disk snapshot is loaded, ignore most WS history.
      // EXCEPTION: file_push (Gateway-only) and assistant text that disk may
      // still be missing (async flush lag vs Gateway cache).
      if (diskSessionLoadedRef.current) {
        const isAssistantText =
          role === 'assistant'
          && typeof contentStr === 'string'
          && contentStr.trim().length > 0
          && msgType !== 'file_push';
        if (msgType === 'file_push' || files.length > 0 || isAssistantText) {
          _logMediaDebug('ws-history-enrich-after-disk', {
            msgType,
            mid: raw.message_id || raw.id,
            filesCount: files.length,
            isAssistantText,
            contentHead: contentStr.slice(0, 120),
          });
          // fall through to append / richer-merge logic below
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
          const name = f.original_name || f.filename || 'file';
          const isVoice =
            f.type === 'voice' || f.type === 'audio' || !!f.is_audio
            || (typeof f.content_type === 'string' && f.content_type.startsWith('audio/'))
            || /^voice_.*\.webm$/i.test(name);
          return {
            name,
            size: sz(f.size),
            url: rawUrl || undefined,
            type: f.is_video && !isVoice ? 'video' as const : isVoice ? (f.type === 'voice' ? 'voice' as const : 'audio' as const) : 'file' as const,
            duration: typeof f.duration === 'number' ? f.duration : undefined,
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
            const idx = prev.findIndex(
              (e) => e.kind === 'message' && _messageIdentityKey(e.data as ChatMessage) === k,
            );
            if (idx >= 0) {
              const next = [...prev];
              next[idx] = {
                ...next[idx],
                data: _mergeChatMessage(next[idx].data as ChatMessage, histMsg),
              } as TimelineEntry;
              return next;
            }
          }
          // Disk may have an empty/short api_sync bubble while Gateway history
          // carries the cleaned to_user — upgrade the trailing assistant.
          if (histMsg.role === 'assistant' && (histMsg.content || '').trim()) {
            for (let i = prev.length - 1; i >= 0; i -= 1) {
              const entry = prev[i];
              if (entry.kind !== 'message') continue;
              const d = entry.data as ChatMessage;
              if (d.role === 'user') break;
              if (d.role !== 'assistant') continue;
              const dLen = (d.content || '').trim().length;
              const mLen = (histMsg.content || '').trim().length;
              if (mLen > dLen) {
                const next = [...prev];
                next[i] = {
                  ...entry,
                  data: _mergeChatMessage(d, histMsg),
                };
                return next;
              }
              // Same-length content already present — skip duplicate.
              if (mLen > 0 && d.content === histMsg.content) return prev;
              break;
            }
          }
          return [...prev, { kind: 'message', data: histMsg, _uid: genUID() }];
        });
      }
    });

    // Agent (via GatewayAdapter) sends "current_session" when session changes
    const unsubCurrentSession = aiWsService.on('current_session', (msg: AIWSMessage) => {
      const data = msg.content || msg.data;
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
      const uid = String(msg.user_id || '').trim();
      // Scheduled-task parallel spawn announces current_session for exec binding
      // only — must NOT steal the interactive pane's focused session (that used
      // to hydrate the focused chat into the new sid's live bucket → 会话串台).
      if (uid.startsWith('scheduled-task:')) {
        // Do not seed an empty live bucket here — that blocks disk hydrate in
        // ExecWorkflowView until a full page refresh. First WS event for sid
        // creates the bucket via setTimeline.
        if (sid) {
          requestSessionListRefresh(agentId, previousSid || sid);
        }
        return;
      }
      // Always track agent current — even while a history tab is focused.
      if (sid) {
        agentCurrentSessionIdRef.current = sid;
        // Fetch this session's context % — never copy another session's stats.
        try {
          aiWsService.requestTokenStats(sid);
        } catch {
          /* ignore */
        }
      }
      if (viewingHistorySessionRef.current) {
        return;
      }
      if (sid) {
        currentSessionIdRef.current = sid;
        wsServiceRef.current?.setActiveSession(sid);
        setCurrentSessionId(sid);
        requestSessionListRefresh(agentId, sid);
        console.info('[AIChatPage] current_session →', sid, {
          previous: previousSid,
          newSessionPending: newSessionPendingRef.current,
        });
      }
      // Clear new-session loading — current_session fires when server confirms the new session
      if (newSessionPendingRef.current) {
        newSessionPendingRef.current = false;
        setIsLoadingSession(false);
        sessionBootstrapDoneRef.current = true;
        if (sid) {
          newSessionGuardRef.current = { sid, until: Date.now() + 20000 };
          pinComposerLanding(sid);
        }
        // Keep empty timeline from handleNewSession; disk may still hold the old session.
        return;
      }
      const guard = newSessionGuardRef.current;
      if (guard && Date.now() < guard.until && sid && sid !== guard.sid) {
        console.warn(
          '[AIChatPage] current_session ignored during new-session guard: got=%s keep=%s',
          sid,
          guard.sid,
        );
        // Stay on the newly created session — snap active filter back.
        currentSessionIdRef.current = guard.sid;
        wsServiceRef.current?.setActiveSession(guard.sid);
        setCurrentSessionId(guard.sid);
        return;
      }
      if (!viewingHistorySessionRef.current && (sid !== previousSid || !sessionBootstrapDoneRef.current)) {
        scheduleCurrentSessionHydration();
      }
    });

    const unsubSessionList = aiWsService.on('session_list', (msg: AIWSMessage) => {
      const data: any = (msg as any).content || (msg as any).data || msg;
      const list = Array.isArray(data) ? data : Array.isArray(data?.sessions) ? data.sessions : null;
      if (list) {
        const primary = list.find((s: any) => s?.primary)?.id;
        if (primary) setPrimarySessionId(String(primary));
      }
      requestSessionListRefresh(agentId, currentSessionIdRef.current);
    });

    const unsubBusySessions = aiWsService.on('busy_sessions', (msg: AIWSMessage) => {
      const data: any = (msg as any).content || (msg as any).data || msg;
      const sessions = Array.isArray(data?.sessions)
        ? data.sessions.map(String)
        : Array.isArray(data)
          ? data.map(String)
          : [];
      setBusySessions((prev) => {
        if (
          prev.length === sessions.length
          && prev.every((id, i) => id === sessions[i])
        ) {
          return prev;
        }
        return sessions;
      });
    });

    // Error frame → release busy state immediately. Backend has just emitted
    // (or is about to emit) the final "error" frame and busy_sessions snapshot
    // for the failing turn, but the snapshot can lag by seconds when the LLM
    // call itself was the hang. We must not leave the composer stuck in
    // "executing" while we wait for the scheduler reap loop.
    const unsubError = aiWsService.on('error', (msg: AIWSMessage) => {
      const data: any = (msg as any).content || (msg as any).data || msg;
      const errSid = String(
        (msg as any).sid
        || (data && typeof data === 'object' ? (data.session_id || data.sid) : '')
        || agentCurrentSessionIdRef.current
        || currentSessionIdRef.current
        || ''
      ).trim();
      const message = typeof data === 'string'
        ? data
        : String(data?.message || data?.error || data?.detail || '');
      if (message) {
        console.warn('[AIChatPage] ws error frame sid=%s message=%s', errSid || '-', message.slice(0, 200));
      }
      if (errSid) {
        clearSessionRunState(errSid);
      } else {
        clearSessionRunState();
      }
    });

    const unsubPrimarySession = aiWsService.on('primary_session', (msg: AIWSMessage) => {
      const data: any = (msg as any).content || (msg as any).data || msg;
      const sid = String(data?.primary_session_id || '').trim();
      const ok = data?.ok !== false;
      setPendingPrimarySessionId(null);
      pendingPrimarySessionIdRef.current = null;
      if (ok && sid) {
        setPrimarySessionId(sid);
        return;
      }
      console.warn('[AIChatPage] set_primary_session failed', data);
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
      if (reason === 'withdraw') {
        // Agent truncated messages+events on disk; apply payload immediately so
        // chat / tool-stream rewind without waiting for HTTP hydrate races.
        setIsStreaming(false);
        setAgentStatus('idle');
        setTurnStartedMs(undefined);
        clearOutboundTurnPending();
        setStreamingText('');
        streamingTextRef.current = '';
        pendingHydrationMediaRef.current = [];
        pendingHydrationWorkflowEventsRef.current = [];
        try {
          const msgs = Array.isArray(data.messages) ? data.messages : [];
          const evts = Array.isArray(data.events) ? data.events : [];
          const archivedMsgs = Array.isArray(data.archived_messages)
            ? data.archived_messages
            : undefined;
          const archivedEvts = Array.isArray(data.archived_events)
            ? data.archived_events
            : undefined;
          const entries = buildTimelineFromSession(
            msgs,
            evts,
            archivedMsgs,
            archivedEvts,
          );
          setTimeline(entries);
          if (data.session_id) {
            currentSessionIdRef.current = data.session_id;
          }
          sessionBootstrapDoneRef.current = true;
          diskSessionLoadedRef.current = true;
        } catch (err) {
          console.warn('[AIChatPage] withdraw history apply failed', err);
        }
        // Verify against disk shortly after (Gateway cache already invalidated)
        scheduleCurrentSessionHydration(120);
        // Refresh file panel so withdrawn creates show as red tombstones
        scheduleRefreshSessionChanges();
        return;
      }
      if (!sid || !currentSessionIdRef.current || sid === currentSessionIdRef.current || !sessionBootstrapDoneRef.current) {
        scheduleCurrentSessionHydration();
      }
    });

    // Agent pushes files/attachments to chat (via HTTP push API -> WS forward)
    const unsubFilePush = onWs('file_push', (msg: AIWSMessage) => {
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
      cancelStreamFlush();
      window.clearTimeout(bootstrapFailsafeTimer);
      unsubAuthExpired();
      unsubStatus();
      unsubReadyStage();
      unsubStream();
      unsubMessage();
      unsubResponse();
      unsubToUserReply();
      unsubToUserFinal();
      unsubToUserEndTask();
      unsubSessionTitle();
      unsubThought();
      unsubToolCall();
      unsubToolCallDelta();
      unsubToolResult();
      unsubJobStdout();
      unsubJobStatus();
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
      unsubVoiceAudioOut();
      unsubVoiceTranscript();
      unsubVoiceStatus();
      clearVoiceConnectTimer();
      window.removeEventListener('pagehide', onPageHide);
      setVoiceRealtimeStatus('idle');
      setVoiceRealtimeError('');
      setVoicePanelOpen(false);
      unsubConnected();
      unsubHistory();
      unsubCurrentSession();
      unsubSessionList();
      unsubBusySessions();
      unsubError();
      unsubPrimarySession();
      unsubHistorySync();
      unsubFilePush();
      if (sessionReloadTimerRef.current) {
        clearTimeout(sessionReloadTimerRef.current);
        sessionReloadTimerRef.current = null;
      }
      if (voicePageHideRef.current) {
        // Page refresh / tab close: keep agent mouthpiece/realtime session for resume.
        releaseAiWsService(agentId);
      } else {
        // In-app navigate away (or StrictMode remount): delay hangup so remount can cancel.
        schedulePendingVoiceHangup(agentId, () => {
          try {
            getAiWsService(agentId).stopVoiceRealtime();
          } catch {
            /* ignore */
          }
          clearVoiceCallPersist(agentId);
          releaseAiWsService(agentId);
        });
      }
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

  /**
   * Soft reconnect / history_sync hydrate: keep React keys stable and skip
   * no-op replaces so mobile WS flaps do not remount the whole chat tree.
   */
  function _stabilizeHydratedTimeline(prev: TimelineEntry[], next: TimelineEntry[]): TimelineEntry[] {
    const rebased = rebaseTimelineUids(prev, next);
    if (
      prev.length === rebased.length
      && prev.every((p, i) => {
        const n = rebased[i];
        if (!n || p.kind !== n.kind || p._uid !== n._uid) return false;
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
        return true;
      })
    ) {
      return prev;
    }
    return rebased;
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
      next[idx] = { ...next[idx], data: merged } as TimelineEntry;
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

  // Whether the focused session is busy (other sessions may still accept parallel sends).
  const isAgentBusy = useMemo(
    () =>
      isStreaming ||
      agentStatus === 'working' ||
      agentStatus === 'thinking' ||
      (!!currentSessionId && busySessions.includes(currentSessionId)),
    [isStreaming, agentStatus, busySessions, currentSessionId],
  );

  const isSessionBusy = useCallback(
    (sid: string) => {
      if (!sid) return isAgentBusy;
      if (busySessionsRef.current.includes(sid)) return true;
      if (isStreamingBySessionRef.current[sid]) return true;
      if (
        sid === currentSessionIdRef.current
        && busySessionsRef.current.length === 0
      ) {
        // Do not treat sleeping as busy — otherwise pending queue never drains
        // and the agent never receives a wake/chat to leave sleep.
        return (
          isStreaming ||
          agentStatus === 'working' ||
          agentStatus === 'thinking'
        );
      }
      return false;
    },
    [isAgentBusy, isStreaming, agentStatus, isStreamingBySession],
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

  const slashMode = useMemo(() => parseSlashInput(inputText), [inputText]);
  const slashResetKey = slashMode ? `${slashMode.kind}:${slashMode.query}` : null;
  const slashCommandOptions = useMemo(
    () => (slashMode?.kind === 'commands' ? filterSlashCommands(slashMode.query) : []),
    [slashMode],
  );
  const slashGoalOptions = useMemo(
    () => (slashMode?.kind === 'goal' ? filterGoalSubcommands(slashMode.query) : []),
    [slashMode],
  );
  const slashSkillOptions = useMemo(
    () => (slashMode?.kind === 'skill' ? filterSkillsForSlash(availableSkills, slashMode.query) : []),
    [slashMode, availableSkills],
  );
  const slashOptionCount =
    slashMode?.kind === 'skill'
      ? slashSkillOptions.length
      : slashMode?.kind === 'goal'
        ? slashGoalOptions.length
        : slashMode?.kind === 'plan'
          ? 1
          : slashCommandOptions.length;

  useEffect(() => {
    if (slashMode?.kind !== 'skill') return;
    void loadSkillsIfNeeded();
  }, [slashMode?.kind, loadSkillsIfNeeded]);

  useEffect(() => {
    setSlashHighlight(0);
  }, [slashResetKey]);

  useEffect(() => {
    if (slashResetKey === null) return;
    if (slashHighlight >= slashOptionCount) {
      setSlashHighlight(Math.max(0, slashOptionCount - 1));
    }
  }, [slashResetKey, slashHighlight, slashOptionCount]);

  const applyPendingSkill = useCallback((skill: SkillInfo) => {
    const dir = (skill.dir || skill.name || '').trim();
    if (!dir) return;
    setPendingSkill({
      dir,
      name: skill.display_name || skill.name || dir,
    });
    requestAnimationFrame(() => inputRef.current?.focus());
  }, []);

  const selectSlashCommand = useCallback((cmd: SlashCommandDef) => {
    setInputText(slashCommandTriggerText(cmd));
    requestAnimationFrame(() => {
      const el = inputRef.current;
      if (!el) return;
      el.focus();
      const len = el.value.length;
      el.setSelectionRange(len, len);
    });
  }, []);

  const runGoalAction = useCallback(
    (action: 'set' | 'pause' | 'resume' | 'clear' | 'status', objective?: string) => {
      wsServiceRef.current?.setGoal({ action, objective, nudge: action === 'resume' });
      if (action === 'set' && objective) {
        setActiveGoal({ objective, status: 'pursuing' });
      } else if (action === 'pause') {
        setActiveGoal((prev) => (prev ? { ...prev, status: 'paused' } : prev));
      } else if (action === 'resume') {
        setActiveGoal((prev) => (prev ? { ...prev, status: 'pursuing' } : prev));
      } else if (action === 'clear') {
        setActiveGoal(null);
      }
    },
    [],
  );

  const selectGoalSubcommand = useCallback(
    (cmd: GoalSubcommandDef) => {
      runGoalAction(cmd.id);
      setInputText('');
      requestAnimationFrame(() => inputRef.current?.focus());
    },
    [runGoalAction],
  );

  const selectSkillFromSlash = useCallback(
    (skill: SkillInfo) => {
      applyPendingSkill(skill);
      setInputText('');
      requestAnimationFrame(() => {
        if (inputRef.current) {
          inputRef.current.style.height = 'auto';
        }
      });
    },
    [applyPendingSkill],
  );

  const composerTextFromUserMessage = useCallback((message: ChatMessage): string => {
    let text = formatUserSkillDisplayContent(
      typeof message.content === 'string' ? message.content : '',
    );
    text = text
      .replace(/<image>[\s\S]*?<\/image>/gi, '')
      .replace(/\[File:[^\]]*\](?:\([^)]*\))?/g, '')
      .replace(/\n{3,}/g, '\n\n')
      .trim();
    return text;
  }, []);

  const requestWithdrawUserMessage = useCallback(
    (entryUid: string, message: ChatMessage, sessionId?: string) => {
      if (!entryUid || message.role !== 'user' || changesBusy) return;
      setRestoreConfirm({ entryUid, message, sessionId });
    },
    [changesBusy],
  );

  const handleWithdrawUserMessage = useCallback(
    async () => {
      if (!restoreConfirm) return;
      const { entryUid, message, sessionId: withdrawSid } = restoreConfirm;
      if (!entryUid || message.role !== 'user') {
        setRestoreConfirm(null);
        return;
      }
      if (isStreaming || agentStatus === 'working' || agentStatus === 'thinking') {
        // Still allow withdraw — stop only this session (preserve parallel panes).
        const stopSid = String(
          restoreConfirm.sessionId || currentSessionIdRef.current || '',
        ).trim();
        if (stopSid) {
          wsServiceRef.current?.stopTask({ session_id: stopSid });
        } else {
          wsServiceRef.current?.stopTask({ all: true });
        }
      }
      const root = (agentCwd || defaultCwd || '').trim();
      const dirName = agentProfile?.dir_name || agentId;
      // Prefer stable ids: checkpoint was created with timeline _uid (= message_id when set)
      const checkpointId = String(message.message_id || entryUid).trim();
      // Align with server utc_now_iso (second precision, no ms)
      const cutTs = String(message.timestamp || '')
        .trim()
        .replace(/\.\d{3}Z$/, 'Z');
      const refillText = composerTextFromUserMessage(message);
      const targetSid = String(
        withdrawSid || currentSessionIdRef.current || '',
      ).trim();
      setChangesBusy(true);
      // Route setTimeline into the session being withdrawn (pane / parallel).
      eventSidRef.current = targetSid;
      try {
        // Avoid hydration merge resurrecting withdrawn turns from WS buffers.
        pendingHydrationMediaRef.current = [];
        pendingHydrationWorkflowEventsRef.current = [];

        if (dirName && root && checkpointId) {
          await adminAPI.revertSessionChanges(dirName, checkpointId, root);
        }
        wsServiceRef.current?.withdrawTurn({
          message_id: checkpointId,
          timestamp: cutTs,
        });
        const truncate = (prev: TimelineEntry[]) => {
          const idx = prev.findIndex((e) => e._uid === entryUid);
          if (idx < 0) return prev;
          return prev.slice(0, idx);
        };
        // Seed live bucket from cache when withdrawing a history-only tab.
        if (targetSid) {
          setLiveTimelinesBySession((prev) => {
            const cur =
              prev[targetSid]
              ?? getCachedSessionTimeline(agentId, targetSid)
              ?? [];
            const next = truncate(Array.isArray(cur) ? cur : []);
            putCachedSessionTimeline(agentId, targetSid, next, { complete: true });
            const out = { ...prev, [targetSid]: next };
            liveTimelinesBySessionRef.current = out;
            return out;
          });
        }
        setTimeline(truncate);
        setStreamingText('');
        streamingTextRef.current = '';
        setIsStreaming(false);
        setAgentStatus('idle');
        setTurnStartedMs(undefined);
        setPendingMessages([]);
        clearOutboundTurnPending();
        const composer = resolveComposerApi(targetSid);
        if (composer) {
          composer.setText(refillText);
        } else {
          // Legacy fallback (no mounted AgentWebComposer yet)
          setInputText(refillText);
          requestAnimationFrame(() => {
            const el = inputRef.current;
            if (!el) return;
            el.focus();
            el.style.height = 'auto';
            el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
          });
        }
        setRestoreConfirm(null);
        await refreshSessionChanges();
        setFocusChangedNonce(Date.now());
      } catch (err) {
        console.warn('[withdraw] failed', err);
        window.alert(t('aiChat.restoreCheckpoint.failed'));
      } finally {
        eventSidRef.current = '';
        setChangesBusy(false);
      }
    },
    [
      restoreConfirm,
      isStreaming,
      agentStatus,
      agentCwd,
      defaultCwd,
      agentProfile?.dir_name,
      agentId,
      refreshSessionChanges,
      clearOutboundTurnPending,
      composerTextFromUserMessage,
      resolveComposerApi,
      setTimeline,
      t,
    ],
  );

  const deliverMessage = useCallback((
    payload: {
      text: string;
      images: string[];
      attachments: UploadedFile[];
      skillDir?: string;
      skillName?: string;
      /** Target session for parallel multi-session sends */
      sessionId?: string;
    },
    opts?: { clearInputState?: boolean; salvageStream?: boolean },
  ) => {
    const { text, images: imgState, attachments: attState, skillDir, skillName } = payload;
    const targetSessionId = (payload.sessionId || currentSessionIdRef.current || '').trim();
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
          const media = a.is_video
            ? 'video'
            : (a.is_audio || a.type === 'voice' || a.type === 'audio')
              ? (a.type === 'voice' ? 'voice' : 'audio')
              : 'file';
          const webUrl = toWebMediaUrl(a.url || a.path || '');
          // Prefer /uploads/... link for refresh; keep disk path for agent tools.
          const base = `[File: ${a.original_name} (${_formatFileSize(a.size)}) path=${a.path} type=${media}]`;
          return webUrl ? `${base}(${webUrl})` : base;
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
    const fileAtts: FileAttachment[] = nonImageAttachments.map(a => {
      const isVoice = a.type === 'voice' || a.type === 'audio' || !!a.is_audio;
      return {
        name: a.original_name,
        size: _formatFileSize(a.size),
        path: a.path,
        url: a.url,
        type: a.is_video && !isVoice ? 'video' : isVoice ? (a.type === 'voice' ? 'voice' : 'audio') : 'file',
        duration: typeof a.duration === 'number' ? a.duration : undefined,
      };
    });

    // Display: show /skill or /goal chip text (not XML tags)
    const displayText = skillId
      ? (text ? `/${skillId} ${text}` : `/${skillId}`)
      : formatUserSkillDisplayContent(text);

    // Add user message to timeline (display text without [File: ...],
    // attachments stored separately for card rendering)
    const userUid = genUID();
    // Second-precision UTC to match server utc_now_iso(); store message_id for checkpoint/withdraw
    const userTs = new Date().toISOString().replace(/\.\d{3}Z$/, 'Z');
    const userMsg: ChatMessage = {
      role: 'user',
      content: displayText,
      timestamp: userTs,
      message_id: userUid,
      images: allImages.length > 0 ? allImages : undefined,
      attachments: fileAtts.length > 0 ? fileAtts : undefined,
    };
    // Mid-turn insert: seal previous Working → Worked above the new user bubble.
    // Do NOT cancel open tools (unlike Stop) — the runner will continue / interrupt
    // via the new message; UI just closes the old fold's live timer.
    //
    // IMPORTANT: write the live bucket synchronously. setTimeline's React updater
    // is async — reading liveTimelinesBySessionRef right after used to see a
    // freshly-seeded [] (ensureSessionWatched on new sid) and wipe the optimistic
    // user bubble via setTimelineState([]).
    {
      const sid = (targetSessionId || currentSessionIdRef.current || '').trim();
      const prevBucket = sid
        ? (liveTimelinesBySessionRef.current[sid] ?? [])
        : (timelineRef.current ?? []);
      const next: TimelineEntry[] = [
        ...sealIncompleteWorkflows(prevBucket, {
          fallbackStartedMs: turnStartedMsRef.current,
        }),
        {
          kind: 'message',
          data: userMsg,
          _uid: userUid,
        },
      ];
      if (sid) {
        const out = { ...liveTimelinesBySessionRef.current, [sid]: next };
        liveTimelinesBySessionRef.current = out;
        setLiveTimelinesBySession(out);
      }
      if (!sid || sid === (currentSessionIdRef.current || '')) {
        setTimelineState(next);
      }
    }
    setTurnStartedMs(undefined);

    // Checkpoint dirty files at send time (for per-message withdraw).
    {
      const root = (agentCwd || defaultCwd || '').trim();
      const dirName = agentProfile?.dir_name || agentId;
      if (root && dirName) {
        void adminAPI.checkpointSessionChanges(dirName, userUid, root).catch(() => {});
      }
    }

    // Lock project path for this session on first user message (Solo archive grouping).
    {
      const pathToLock = (agentCwd || defaultCwd || '').trim();
      if (pathToLock) {
        const sid = targetSessionId || currentSessionIdRef.current;
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
        const sid = targetSessionId || currentSessionIdRef.current;
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
      viewingHistorySession: viewingHistorySessionRef.current,
      currentSessionId: currentSessionIdRef.current,
      targetSessionId,
    });

    // Always route by explicit targetSessionId via sendMessage.
    // Do NOT branch on viewingHistorySession React state: prepareSessionForSend
    // clears the ref synchronously but setState is async, so a stale `true`
    // would call switchAndReply(oldCurrentSessionId) and crosstalk to session A.
    const paneCard =
      (targetSessionId && cardNameBySessionRef.current[targetSessionId]) ||
      currentCardNameRef.current ||
      undefined;
    console.info('[AIChatPage] deliverMessage → WS', {
      targetSessionId,
      viewingHistorySession: viewingHistorySessionRef.current,
      currentSessionId: currentSessionIdRef.current,
      textHead: wsText.slice(0, 80),
      wsStatus: wsServiceRef.current?.getStatus?.() ?? 'n/a',
      model_card: paneCard || null,
    });
    wsServiceRef.current?.sendMessage(
      wsText,
      allImages.length > 0 ? allImages : undefined,
      nonImageAttachments,
      {
        client_id: userUid,
        session_id: targetSessionId || undefined,
        model_card: paneCard,
      },
    );
    if (viewingHistorySessionRef.current) {
      viewingHistorySessionRef.current = false;
      setViewingHistorySession(false);
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
    userStoppedRef.current = false;
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
  }, [agentCwd, defaultCwd, agentId, agentProfile?.dir_name]);

  // When session id arrives after first send, persist pending project path / provisional title.
  useEffect(() => {
    if (!currentSessionId) return;
    if (pendingProjectPathRef.current) {
      const path = pendingProjectPathRef.current;
      setSessionProjectPath(agentId, currentSessionId, path);
      try {
        const ws = ensureWorkspace(agentId, path);
        setSessionWorkspaceId(agentId, currentSessionId, ws.id, path);
      } catch {
        /* ignore */
      }
      pendingProjectPathRef.current = null;
    }
    if (pendingSessionTitleRef.current) {
      setSessionTitleUpdate({ id: currentSessionId, title: pendingSessionTitleRef.current });
      pendingSessionTitleRef.current = null;
    }
  }, [currentSessionId, agentId]);

  const handleSend = () => {
    // /goal composer path (before normal send)
    const slash = parseSlashInput(inputText);
    if (slash?.kind === 'plan') {
      const topic = slash.query.trim() || 'Plan the next change';
      const tag = `<user_plan>${topic}</user_plan>`;
      // Optimistic Plan mode — user explicitly started /plan (scoped to current session)
      {
        const sid = currentSessionIdRef.current || '';
        if (sid) {
          setAgentModeBySession((prev) => ({ ...prev, [sid]: 'plan' }));
          wsServiceRef.current?.setAgentMode('plan', undefined, sid);
        } else {
          setAgentMode('plan');
          wsServiceRef.current?.setAgentMode('plan');
        }
      }
      if (autoSpeechEnabledRef.current) {
        unlockAutoTtsAudio();
      }
      const sid = currentSessionIdRef.current || '';
      const shouldQueue =
        isSessionBusy(sid) ||
        isOutboundPending(sid) ||
        pendingMessagesRef.current.some((m) => (m.sessionId || '') === sid);
      if (shouldQueue) {
        const snapshot: PendingMessage = {
          id: genUID(),
          text: tag,
          images: [...images],
          attachments: attachments.map((a) => ({ ...a })),
          fileAtts: attachments
            .filter((a) => !a.is_image)
            .map((a) => {
              const isVoice = a.type === 'voice' || a.type === 'audio' || !!a.is_audio;
              return {
                name: a.original_name,
                size: _formatFileSize(a.size),
                path: a.path,
                url: a.url,
                type: a.is_video && !isVoice ? 'video' : isVoice ? (a.type === 'voice' ? 'voice' : 'audio') : 'file',
                duration: typeof a.duration === 'number' ? a.duration : undefined,
              };
            }),
          sessionId: sid || undefined,
        };
        setPendingMessages((prev) => [...prev, snapshot]);
        setInputText('');
        setImages([]);
        setAttachments([]);
        if (inputRef.current) inputRef.current.style.height = 'auto';
        return;
      }
      armOutboundTurnPending(currentSessionIdRef.current || "");
      deliverMessage(
        { text: tag, images: [...images], attachments: [...attachments] },
        { clearInputState: true, salvageStream: true },
      );
      return;
    }
    if (slash?.kind === 'goal') {
      const parsed = parseGoalSendQuery(slash.query);
      if (parsed.action === 'status') {
        runGoalAction('status');
        setInputText('');
        return;
      }
      if (parsed.action === 'pause' || parsed.action === 'resume' || parsed.action === 'clear') {
        runGoalAction(parsed.action);
        setInputText('');
        return;
      }
      const objective = (parsed.objective || '').trim();
      if (!objective) {
        runGoalAction('status');
        setInputText('');
        return;
      }
      runGoalAction('set', objective);
      const tag = `<user_goal>${objective}</user_goal>`;
      if (autoSpeechEnabledRef.current) {
        unlockAutoTtsAudio();
      }
      const sid = currentSessionIdRef.current || '';
      const shouldQueue =
        isSessionBusy(sid) ||
        isOutboundPending(sid) ||
        pendingMessagesRef.current.some((m) => (m.sessionId || '') === sid);
      if (shouldQueue) {
        const snapshot: PendingMessage = {
          id: genUID(),
          text: tag,
          images: [...images],
          attachments: attachments.map((a) => ({ ...a })),
          fileAtts: attachments
            .filter((a) => !a.is_image)
            .map((a) => {
              const isVoice = a.type === 'voice' || a.type === 'audio' || !!a.is_audio;
              return {
                name: a.original_name,
                size: _formatFileSize(a.size),
                path: a.path,
                url: a.url,
                type: a.is_video && !isVoice ? 'video' : isVoice ? (a.type === 'voice' ? 'voice' : 'audio') : 'file',
                duration: typeof a.duration === 'number' ? a.duration : undefined,
              };
            }),
          sessionId: sid || undefined,
        };
        setPendingMessages((prev) => [...prev, snapshot]);
        setInputText('');
        setImages([]);
        setAttachments([]);
        if (inputRef.current) inputRef.current.style.height = 'auto';
        return;
      }
      armOutboundTurnPending(currentSessionIdRef.current || "");
      deliverMessage(
        { text: tag, images: [...images], attachments: [...attachments] },
        { clearInputState: true, salvageStream: true },
      );
      return;
    }

    const text = inputText.trim();
    const skillDir = pendingSkill?.dir || '';
    if (!text && images.length === 0 && attachments.length === 0 && !skillDir) return;

    // If Auto speech was restored from localStorage (no toggle click this session),
    // unlock autoplay on this send click so the final reply can play.
    if (autoSpeechEnabledRef.current) {
      unlockAutoTtsAudio();
    }

    // Park only when THIS session is busy / already has a parked queue.
    // Other sessions run in parallel and must not force a global queue.
    const sid = currentSessionIdRef.current || '';
    const shouldQueue =
      isSessionBusy(sid) ||
      isOutboundPending(sid) ||
      pendingMessagesRef.current.some((m) => (m.sessionId || '') === sid);

    if (shouldQueue) {
      const snapshot: PendingMessage = {
        id: genUID(),
        text,
        images: [...images],
        attachments: attachments.map(a => ({ ...a })),
        fileAtts: attachments
          .filter(a => !a.is_image)
          .map(a => {
            const isVoice = a.type === 'voice' || a.type === 'audio' || !!a.is_audio;
            return {
              name: a.original_name,
              size: _formatFileSize(a.size),
              path: a.path,
              url: a.url,
              type: a.is_video && !isVoice ? 'video' : isVoice ? (a.type === 'voice' ? 'voice' : 'audio') : 'file',
              duration: typeof a.duration === 'number' ? a.duration : undefined,
            };
          }),
        skillDir: pendingSkill?.dir,
        skillName: pendingSkill?.name,
        sessionId: sid || undefined,
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

    armOutboundTurnPending(currentSessionIdRef.current || "");
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

  // Flush one pending item: switch to its session without stop_task, then deliver.
  const flushPendingMessage = useCallback(async (target: PendingMessage) => {
    const sid = (target.sessionId || '').trim();
    if (sid && sid !== currentSessionIdRef.current) {
      wsServiceRef.current?.switchAndReply(sid, '', { stopCurrent: false });
      pendingFilePushesRef.current = [];
      currentSessionIdRef.current = sid;
      viewingHistorySessionRef.current = false;
      setCurrentSessionId(sid);
      setViewingHistorySession(false);
      try {
        const resp = await agentSessionAPI.getSessionHistoryPaged(
          agentId, sid, 0, SESSION_HISTORY_PAGE_SIZE,
        );
        if (currentSessionIdRef.current !== sid) return;
        const session = resp.session;
        if (session) {
          const messages = session.messages || [];
          const entries = buildTimelineFromSession(
            messages,
            session.events || [],
            session.archived_messages,
            session.archived_events,
          );
          const hasMore = !!session.has_more;
          putCachedSessionTimeline(agentId, sid, entries, {
            complete: !hasMore,
            messageCount: messages.length,
            totalMessages: session.total_messages,
          });
          setTimeline(entries);
          setShellStreams(rebuildShellStreamsFromTimeline(entries));
          loadingSessionIdRef.current = sid;
          historyOffsetRef.current = messages.length;
          setHasMoreHistory(hasMore);
        }
      } catch (err: any) {
        console.error('[AIChatPage] Failed to reload session before pending flush:', err);
      }
      if (target.paneId) {
        const snap = loadWorkspaceStore(agentId);
        const wsId = snap.chrome.activeWorkspaceId;
        if (wsId) {
          setFocusedPane(agentId, target.paneId);
          openContentTab(agentId, wsId, { kind: 'session', id: sid }, target.paneId);
          setWsSnap(loadWorkspaceStore(agentId));
        }
      }
    } else {
      viewingHistorySessionRef.current = false;
      setViewingHistorySession(false);
    }
    const flushSid = sid || (currentSessionIdRef.current || '').trim();
    armOutboundTurnPending(flushSid);
    deliverMessage(
      {
        text: target.text,
        images: target.images,
        attachments: target.attachments,
        skillDir: target.skillDir,
        skillName: target.skillName,
        sessionId: flushSid || undefined,
      },
      { clearInputState: false, salvageStream: false },
    );
  }, [agentId, armOutboundTurnPending, deliverMessage]);

  const handleSendPendingNow = useCallback((id: string) => {
    const target = pendingMessagesRef.current.find(m => m.id === id);
    if (!target) return;
    setPendingMessages(prev => prev.filter(m => m.id !== id));
    void flushPendingMessage(target);
  }, [flushPendingMessage]);

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
    void flushPendingMessage(next);
  }, [flushPendingMessage]);

  // Clear the entire queue without sending anything.
  const handleCancelAllPending = useCallback(() => {
    setPendingMessages([]);
    clearOutboundTurnPending();
  }, [clearOutboundTurnPending]);

  // Auto-drain: when idle, release exactly ONE pending message (any session),
  // switching without stop_task, then wait for that turn before the next.
  useEffect(() => {
    // Drain per-session queues independently — do not block on other sessions.
    if (isFlushingPendingRef.current) return;
    const queue = pendingMessagesRef.current;
    if (queue.length === 0) return;
    const next = queue.find((m) => {
      const sid = (m.sessionId || "").trim();
      if (sid && isSessionBusy(sid)) return false;
      if (sid && isOutboundPending(sid)) return false;
      return true;
    });
    if (!next) return;

    isFlushingPendingRef.current = true;
    setPendingMessages((prev) => prev.filter((m) => m.id !== next.id));
    void (async () => {
      try {
        await flushPendingMessage(next);
      } finally {
        setTimeout(() => { isFlushingPendingRef.current = false; }, 0);
      }
    })();
  }, [busySessions, isStreaming, agentStatus, pendingMessages.length, flushPendingMessage, isSessionBusy, isOutboundPending]);

  useEffect(() => () => {
    for (const key of Object.keys(outboundPendingTimersRef.current)) {
      clearTimeout(outboundPendingTimersRef.current[key]);
    }
    outboundPendingTimersRef.current = {};
  }, []);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (slashMode) {
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        if (slashOptionCount === 0) return;
        setSlashHighlight((i) => (i + 1) % slashOptionCount);
        return;
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault();
        if (slashOptionCount === 0) return;
        setSlashHighlight((i) => (i - 1 + slashOptionCount) % slashOptionCount);
        return;
      }
      if (e.key === 'Escape') {
        e.preventDefault();
        setInputText('');
        return;
      }
      // Goal: Enter submits objective / lifecycle text; Tab picks highlighted subcommand.
      if (slashMode.kind === 'goal') {
        if (e.key === 'Tab') {
          e.preventDefault();
          const cmd = slashGoalOptions[slashHighlight];
          if (cmd) selectGoalSubcommand(cmd);
          return;
        }
        if (e.key === 'Enter' && !e.shiftKey) {
          e.preventDefault();
          handleSend();
          return;
        }
      } else if (slashMode.kind === 'plan') {
        if ((e.key === 'Enter' && !e.shiftKey) || e.key === 'Tab') {
          e.preventDefault();
          handleSend();
          return;
        }
      } else if ((e.key === 'Enter' && !e.shiftKey) || e.key === 'Tab') {
        e.preventDefault();
        if (slashMode.kind === 'commands') {
          const cmd = slashCommandOptions[slashHighlight];
          if (cmd) selectSlashCommand(cmd);
        } else {
          const skill = slashSkillOptions[slashHighlight];
          if (skill) selectSkillFromSlash(skill);
        }
        return;
      }
    }
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleStop = (sessionId?: string | null) => {
    const sid = (typeof sessionId === 'string' ? sessionId : currentSessionIdRef.current || '').trim();
    userStoppedRef.current = true;
    // Prefer per-session stop so a parallel pane's Stop does not cancel the other turn.
    if (sid) {
      wsServiceRef.current?.stopTask({ session_id: sid });
    } else {
      wsServiceRef.current?.stopTask({ all: true });
    }
    eventSidRef.current = sid;
    const currentText = sid
      ? (streamingTextBySessionRef.current[sid] || (sid === (currentSessionIdRef.current || '') ? streamingTextRef.current : ''))
      : streamingTextRef.current;
    const stoppedMsg: ChatMessage = {
      role: 'assistant',
      content: (currentText ? `${currentText}\n\n` : '') + '[Stopped]',
      timestamp: new Date().toISOString(),
    };
    // Always seal open workflow / Running tools — even when there is no streaming text
    // (e.g. hung tool_call with no result yet).
    setTimeline((prev) => {
      const nowMs = Date.now();
      const updated = [...prev];
      for (let i = updated.length - 1; i >= 0; i--) {
        const entry = updated[i];
        if (entry.kind !== 'workflow' || entry.data.completed) continue;
        const events = entry.data.events.map((evt: WorkflowEvent) => {
          if (evt.type === 'tool_call' && !evt.result) {
            return {
              ...evt,
              result: 'Cancelled: stopped by user',
              resultStatus: 'error' as const,
            };
          }
          return evt;
        });
        const started =
          typeof entry.data.started_ms === 'number'
            ? entry.data.started_ms
            : (typeof turnStartedMsRef.current === 'number'
              ? turnStartedMsRef.current
              : events[0]?.timestamp);
        const elapsed =
          typeof entry.data.elapsed_ms === 'number'
            ? entry.data.elapsed_ms
            : (typeof started === 'number' ? Math.max(0, nowMs - started) : undefined);
        updated[i] = {
          ...entry,
          data: {
            ...entry.data,
            events,
            status: null,
            completed: true,
            started_ms: typeof started === 'number' ? started : entry.data.started_ms,
            elapsed_ms: elapsed,
          },
        } as TimelineEntry;
        break;
      }
      return finalizeWorkflowAndAddMessage(updated, stoppedMsg);
    });
    setTurnStartedMs(undefined);
    clearSessionRunState(sid);
    eventSidRef.current = '';
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
    // Compare against the agent's current sid — UI tab focus may point at another
    // history session while the empty "new" chat is still agent-current.
    const previousSid =
      agentCurrentSessionIdRef.current || currentSessionIdRef.current;
    const boundPath = typeof projectPath === 'string' ? projectPath.trim() : '';

    // Reuse empty draft: jump back to the cached New Session shell without
    // minting another sid (backend also no-ops, but skip the round-trip).
    const draftSid = String(previousSid || '').trim();
    if (draftSid) {
      const draftEntries =
        liveTimelinesBySessionRef.current[draftSid]
        ?? (draftSid === (currentSessionIdRef.current || '') ? timelineRef.current : null)
        ?? [];
      const draftEmpty = !timelineHasVisibleChatContent(
        flattenArchivedSections(Array.isArray(draftEntries) ? draftEntries : []),
      );
      if (draftEmpty) {
        pendingOpenSessionTabRef.current = true;
        if (!pendingTargetPaneIdRef.current && focusedPaneId) {
          pendingTargetPaneIdRef.current = focusedPaneId;
        }
        userStoppedRef.current = false;
        clearSessionRunState(draftSid);
        setIsLoadingSession(false);
        setSessionBootstrapped(true);
        viewingHistorySessionRef.current = false;
        setViewingHistorySession(false);
        currentSessionIdRef.current = draftSid;
        agentCurrentSessionIdRef.current = draftSid;
        wsServiceRef.current?.setActiveSession(draftSid);
        setCurrentSessionId(draftSid);
        setTimeline([]);
        setShellStreams({});
        setOptionsProposals([]);
        setModeApprovals([]);
        setActiveGoal(null);
        setPendingSkill(null);
        setSessionChanges(null);
        setFocusChangedNonce(Date.now());
        streamingTextRef.current = '';
        setStreamingText('');
        finalizingRef.current = false;
        diskSessionLoadedRef.current = false;
        sessionBootstrapDoneRef.current = true;
        setPlanSteps([]);
        setImages([]);
        setAttachments([]);
        setPendingMessages([]);
        clearOutboundTurnPending();
        pinComposerLanding(draftSid);
        if (activeWorkspace) {
          const targetPane =
            pendingTargetPaneIdRef.current || focusedPaneId || null;
          pendingTargetPaneIdRef.current = null;
          pendingOpenSessionTabRef.current = false;
          openContentTab(
            agentId,
            activeWorkspace.id,
            { kind: 'session', id: draftSid },
            targetPane,
          );
          if (targetPane) setFocusedPane(agentId, targetPane);
          refreshWsSnap();
        }
        if (boundPath) {
          pendingProjectPathRef.current = boundPath;
          setAgentCwd(boundPath);
          const dirName = agentProfile?.dir_name || agentId;
          void adminAPI.setWorkingDirectory(dirName, boundPath).catch((err: any) => {
            console.error('[AIChatPage] Failed to set working directory for folder session:', err);
          });
        }
        return;
      }
    }

    pendingOpenSessionTabRef.current = true;
    // Only abort the session we are leaving — never bare stopTask() (that is
    // agent-wide and cancels every parallel pane mid-turn).
    const prevBusy =
      !!previousSid &&
      (busySessionsRef.current.includes(previousSid) ||
        isStreamingBySessionRef.current[previousSid] ||
        (previousSid === (currentSessionIdRef.current || '') &&
          (isStreaming || agentStatus === 'thinking' || agentStatus === 'working')));
    if (prevBusy && previousSid) {
      wsServiceRef.current?.stopTask({ session_id: previousSid });
    }
    clearSessionRunState(previousSid);
    newSessionPendingRef.current = true;
    userStoppedRef.current = false;
    setIsLoadingSession(false);
    setSessionBootstrapped(true);
    // Optimistic clear so we never treat the pre-rotation empty sid as "new".
    currentSessionIdRef.current = null;
    setCurrentSessionId(null);
    wsServiceRef.current?.newSession();
    requestSessionListRefresh(agentId, null);
    setTimeline([]);
    setShellStreams({});
    setOptionsProposals([]);
    setModeApprovals([]);
    setActiveGoal(null);
    setPendingSkill(null);
    setSessionChanges(null);
    setFocusChangedNonce(Date.now());
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
    // New session starts at 0%; keyed when sid arrives via current_session / token_stats
    setTokenStatsBySession((prev) => {
      if (!previousSid) return prev;
      const copy = { ...prev };
      delete copy[previousSid];
      return copy;
    });
    setImages([]);
    setAttachments([]);
    // Drop parked sends for the previous session; new session starts empty.
    try {
      if (previousSid) localStorage.removeItem(pendingQueueStorageKey());
      localStorage.removeItem(pendingQueueStorageKey());
    } catch { /* ignore */ }
    setPendingMessages([]);
    clearOutboundTurnPending();
    pendingQueueHydratedKeyRef.current = null;
    pendingSessionTitleRef.current = null;
    // Folder-scoped new session: bind cwd to that project path immediately.
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
    // confirm via HTTP — but only adopt when the agent actually rotated sessions.
    // Finishing early on the OLD sid left currentSessionId=null and no new tab.
    const fallbackSeq = ++newSessionFallbackSeqRef.current;
    const finishNewSession = () => {
      if (fallbackSeq !== newSessionFallbackSeqRef.current) return;
      if (!newSessionPendingRef.current) return;
      newSessionPendingRef.current = false;
      setIsLoadingSession(false);
      sessionBootstrapDoneRef.current = true;
    };
    const adoptSession = (currentSid: string | null | undefined, session: any) => {
      if (!currentSid) return false;
      const msgCount = session?.messages?.length || 0;
      const entries = buildTimelineFromSession(session?.messages || [], session?.events || []);
      setTimeline((prev) => {
        const liveWfs = prev.filter(
          (e) => e.kind === 'workflow' && !(e as { data: WorkflowBlock }).data.completed,
        );
        if (liveWfs.length === 0) return entries;
        const diskHasLive = entries.some(
          (e) => e.kind === 'workflow' && !(e as { data: WorkflowBlock }).data.completed,
        );
        if (diskHasLive) return entries;
        return [...entries, ...liveWfs];
      });
      setShellStreams(rebuildShellStreamsFromTimeline(entries));
      agentCurrentSessionIdRef.current = currentSid;
      currentSessionIdRef.current = currentSid;
      wsServiceRef.current?.setActiveSession(currentSid);
      setCurrentSessionId(currentSid);
      requestSessionListRefresh(agentId, currentSid);
      diskSessionLoadedRef.current = msgCount > 0;
      historyOffsetRef.current = msgCount;
      setHasMoreHistory(session?.has_more ?? false);
      if (msgCount === 0 && !timelineHasVisibleChatContent(entries)) {
        pinComposerLanding(currentSid);
      }
      return true;
    };
    const hardTimeout = window.setTimeout(async () => {
      if (fallbackSeq !== newSessionFallbackSeqRef.current || !newSessionPendingRef.current) return;
      try {
        const resp = await agentSessionAPI.getCurrentSession(agentId, 0, 50);
        if (fallbackSeq !== newSessionFallbackSeqRef.current || !newSessionPendingRef.current) return;
        const currentSid = resp.current_session_id;
        const session = resp.session;
        const sidChanged = !!currentSid && currentSid !== previousSid;
        // Only adopt a *rotated* sid. Empty same-sid is NOT a successful new session
        // (that path reused the previous empty chat and never archived it into the list).
        if (sidChanged) {
          adoptSession(currentSid, session);
          if (currentSid) {
            newSessionGuardRef.current = { sid: currentSid, until: Date.now() + 20000 };
          }
        } else {
          console.warn(
            '[AIChatPage] new-session hard-timeout: disk still on old sid=%s (previous=%s)',
            currentSid,
            previousSid,
          );
          setTimeline((prev) => [
            ...prev,
            {
              kind: 'message',
              data: {
                role: 'assistant',
                content:
                  '新建会话未得到 Agent 确认（磁盘仍是旧会话）。请检查 Agent 是否在线后重试；若持续失败，请只保留一套 Gateway/Agent 进程并重启 Agent305。',
                timestamp: new Date().toISOString(),
              },
              _uid: genUID(),
            },
          ]);
        }
      } catch (err: any) {
        console.warn('[AIChatPage] new session hard-timeout HTTP failed:', err?.message || err);
      } finally {
        finishNewSession();
      }
    }, 8000);

    window.setTimeout(async () => {
      if (fallbackSeq !== newSessionFallbackSeqRef.current || !newSessionPendingRef.current) return;
      if (viewingHistorySessionRef.current) return;
      try {
        const resp = await agentSessionAPI.getCurrentSession(agentId, 0, 50);
        if (fallbackSeq !== newSessionFallbackSeqRef.current || !newSessionPendingRef.current) return;
        const currentSid = resp.current_session_id;
        const session = resp.session;
        const sidChanged = !!currentSid && currentSid !== previousSid;
        // Only finish when the agent rotated to a new sid.
        if (sidChanged) {
          adoptSession(currentSid, session);
          window.clearTimeout(hardTimeout);
          finishNewSession();
        }
      } catch (err: any) {
        console.warn('[AIChatPage] new session HTTP fallback failed:', err?.message || err);
        // Keep waiting for WS / hardTimeout — do not clear pending on transient errors.
      }
    }, 600);
  };

  /** Apply a paged/full session payload into timeline + cache (no network). */
  const applySessionPayload = useCallback(
    (
      sessionId: string,
      session: {
        messages?: any[];
        events?: any[];
        archived_messages?: any[];
        archived_events?: any[];
        has_more?: boolean;
        total_messages?: number;
      },
    ) => {
      const messages = session.messages || [];
      const entries = buildTimelineFromSession(
        messages,
        session.events || [],
        session.archived_messages,
        session.archived_events,
      );
      const hasMore = !!session.has_more;
      putCachedSessionTimeline(agentId, sessionId, entries, {
        complete: !hasMore,
        messageCount: messages.length,
        totalMessages: session.total_messages,
      });
      eventSidRef.current = sessionId;
      setTimeline(entries);
      eventSidRef.current = '';
      setShellStreams(rebuildShellStreamsFromTimeline(entries));
      loadingSessionIdRef.current = sessionId;
      historyOffsetRef.current = messages.length;
      setHasMoreHistory(hasMore);
      // Session history is on screen — ask agent for matching context %.
      requestSessionTokenStats(sessionId);
    },
    [agentId, requestSessionTokenStats],
  );

  /**
   * Load session timeline: cache-first paint, paged first page on miss,
   * optional background soft-refresh when cache is stale/incomplete.
   */
  const loadSessionTimelineFast = useCallback(
    async (
      sessionId: string,
      opts?: { forceFetch?: boolean; softRefresh?: boolean; allowNonCurrent?: boolean },
    ): Promise<boolean> => {
      const stillTarget = () =>
        opts?.allowNonCurrent || currentSessionIdRef.current === sessionId;
      const meta = getCachedSessionTimelineMeta(agentId, sessionId);
      const cached = meta?.entries;
      if (cached && cached.length > 0 && !opts?.forceFetch) {
        eventSidRef.current = sessionId;
        setTimeline(cached);
        eventSidRef.current = '';
        if (!opts?.allowNonCurrent) {
          setShellStreams(rebuildShellStreamsFromTimeline(cached));
          loadingSessionIdRef.current = sessionId;
          historyOffsetRef.current = meta.messageCount || cached.filter((e) => e.kind === 'message').length;
          setHasMoreHistory(!meta.complete);
        }
        requestSessionTokenStats(sessionId);
        // Complete cache: skip network. Incomplete: soft-refresh in background.
        if (meta.complete && !opts?.softRefresh) return true;
        void (async () => {
          try {
            const resp = await agentSessionAPI.getSessionHistoryPaged(
              agentId,
              sessionId,
              0,
              SESSION_HISTORY_PAGE_SIZE,
            );
            if (!stillTarget()) return;
            const session = resp.session;
            if (!session) return;
            const total = session.total_messages ?? 0;
            const prevTotal = meta.totalMessages;
            // Skip replace when totals match and we already showed a page.
            if (
              meta.complete &&
              prevTotal != null &&
              prevTotal === total &&
              meta.messageCount >= Math.min(SESSION_HISTORY_PAGE_SIZE, total)
            ) {
              return;
            }
            // Soft-refresh must not clobber a richer live bucket (e.g. WS already
            // has the assistant reply / tool stream that disk has not flushed yet).
            const live = liveTimelinesBySessionRef.current[sessionId];
            if (Array.isArray(live) && live.length > 0) {
              const liveScore = live.reduce((n, e) => {
                if (e.kind === 'workflow') {
                  return n + 10 + (e.data.events?.length || 0) * 3 + (e.data.completed ? 0 : 2);
                }
                if (e.kind === 'message') {
                  const c = String((e.data as ChatMessage).content || '').trim();
                  return n + 2 + Math.min(40, Math.floor(c.length / 40));
                }
                return n;
              }, 0);
              const diskEntries = buildTimelineFromSession(
                session.messages || [],
                session.events || [],
                session.archived_messages,
                session.archived_events,
              );
              const diskScore = diskEntries.reduce((n, e) => {
                if (e.kind === 'workflow') {
                  return n + 10 + (e.data.events?.length || 0) * 3 + (e.data.completed ? 0 : 2);
                }
                if (e.kind === 'message') {
                  const c = String((e.data as ChatMessage).content || '').trim();
                  return n + 2 + Math.min(40, Math.floor(c.length / 40));
                }
                return n;
              }, 0);
              if (liveScore >= diskScore) return;
            }
            applySessionPayload(sessionId, session);
          } catch {
            /* keep cached paint */
          }
        })();
        return true;
      }

      try {
        const resp = await agentSessionAPI.getSessionHistoryPaged(
          agentId,
          sessionId,
          0,
          SESSION_HISTORY_PAGE_SIZE,
        );
        if (!stillTarget()) return false;
        const session = resp.session;
        if (session) {
          applySessionPayload(sessionId, session);
          return true;
        }
      } catch (err: any) {
        console.error('[AIChatPage] Failed to load session:', err);
      }
      return false;
    },
    [agentId, applySessionPayload, requestSessionTokenStats],
  );

  const handleViewSession = async (sessionId: string) => {
    // If the user clicks the CURRENT session (e.g. switching back from a
    // history view), re-hydrate from the Gateway cache rather than treating
    // it as a read-only history view. This preserves the latest to_user
    // replies that may not yet be flushed to disk.
    if (sessionId === currentSessionIdRef.current) {
      viewingHistorySessionRef.current = false;
      setViewingHistorySession(false);
      // hydrateCurrentSession lives inside the WS-subscription effect; expose
      // it via ref so this top-level handler can reuse the same logic.
      hydrateCurrentSessionRef.current?.({ showLoading: false });
      return;
    }
    // Silent switch: paint cache immediately, never show the global overlay.
    pendingFilePushesRef.current = [];
    currentSessionIdRef.current = sessionId;
    setCurrentSessionId(sessionId);
    viewingHistorySessionRef.current = true;
    setViewingHistorySession(true);
    setStreamingText('');
    setIsStreaming(false);
    const meta = getCachedSessionTimelineMeta(agentId, sessionId);
    if (!meta?.entries?.length) {
      setTimeline([]);
      setShellStreams({});
    }
    const projectMeta = getSessionMeta(agentId, sessionId);
    if (projectMeta?.projectPath) setAgentCwd(projectMeta.projectPath);
    else if (pendingProjectPathRef.current?.trim()) setAgentCwd(pendingProjectPathRef.current.trim());
    else if (defaultCwd) setAgentCwd(defaultCwd);

    await loadSessionTimelineFast(sessionId);
  };

  /** Delete a session; if it is the agent-current, rotate via abandon_current_draft first so it becomes a history file (or is dropped for an empty draft). */
  const handleDeleteSession = async (sessionId: string) => {
    const isAgentCurrent = sessionId === agentCurrentSessionIdRef.current;

    const waitForRotation = (prev: string) =>
      new Promise<void>((resolve, reject) => {
        const started = Date.now();
        const seq = ++newSessionFallbackSeqRef.current;
        newSessionPendingRef.current = true;
        // Use abandonCurrent rather than newSession: newSession reuses an empty
        // draft (sid never changes), so waitForRotation would time out for the
        // exact case the user reported ("无法放弃当前会话，删除失败"). abandonCurrent
        // always mints a fresh sid for both empty and non-empty current.
        wsServiceRef.current?.abandonCurrent();
        const tick = window.setInterval(async () => {
          if (seq !== newSessionFallbackSeqRef.current) {
            window.clearInterval(tick);
            reject(new Error('新建会话已取消'));
            return;
          }
          const cur = agentCurrentSessionIdRef.current;
          if (cur && cur !== prev) {
            window.clearInterval(tick);
            newSessionPendingRef.current = false;
            resolve();
            return;
          }
          if (Date.now() - started > 6000) {
            window.clearInterval(tick);
            try {
              const resp = await agentSessionAPI.getCurrentSession(agentId, 0, 10);
              if (resp.current_session_id && resp.current_session_id !== prev) {
                agentCurrentSessionIdRef.current = resp.current_session_id;
                currentSessionIdRef.current = resp.current_session_id;
                setCurrentSessionId(resp.current_session_id);
                newSessionPendingRef.current = false;
                resolve();
                return;
              }
            } catch { /* ignore */ }
            newSessionPendingRef.current = false;
            reject(new Error('无法放弃当前会话，删除失败'));
          }
        }, 200);
      });

    if (isAgentCurrent) {
      // Avoid stealing UI focus from another open session tab during rotation.
      const preserveUi =
        !!currentSessionIdRef.current && currentSessionIdRef.current !== sessionId;
      const prevViewing = viewingHistorySessionRef.current;
      if (preserveUi) viewingHistorySessionRef.current = true;
      try {
        await waitForRotation(sessionId);
      } finally {
        if (preserveUi) viewingHistorySessionRef.current = prevViewing;
      }
    }

    try {
      await agentSessionAPI.deleteSession(agentId, sessionId);
    } catch (err: any) {
      // Race: agent current flag was stale — rotate and retry once.
      if (!isAgentCurrent) {
        try {
          const resp = await agentSessionAPI.getCurrentSession(agentId, 0, 10);
          if (resp.current_session_id === sessionId) {
            agentCurrentSessionIdRef.current = sessionId;
            await waitForRotation(sessionId);
            await agentSessionAPI.deleteSession(agentId, sessionId);
          } else {
            throw err;
          }
        } catch (err2) {
          throw err2;
        }
      } else {
        throw err;
      }
    }

    if (currentSessionIdRef.current === sessionId) {
      const next = agentCurrentSessionIdRef.current;
      if (next && next !== sessionId) {
        currentSessionIdRef.current = next;
        setCurrentSessionId(next);
        setTimeline([]);
        setShellStreams({});
      } else {
        currentSessionIdRef.current = null;
        setCurrentSessionId(null);
        setTimeline([]);
        setShellStreams({});
      }
    }
    // 删除会话时清理其 composer 草稿
    setDraftsBySession((prev) => {
      if (!Object.prototype.hasOwnProperty.call(prev, sessionId)) return prev;
      const out = { ...prev };
      delete out[sessionId];
      return out;
    });
    requestSessionListRefresh(agentId, agentCurrentSessionIdRef.current);
  };

  const handleSwitchAndReply = async (
    sessionId: string,
    opts?: { stopCurrent?: boolean; content?: string },
  ) => {
    if (isCompactLayout) {
      setSessionSidebarOpen(false);
      setFilesPanelOpen(false);
    }
    const stopCurrent = opts?.stopCurrent !== false;
    const content = opts?.content ?? '';
    wsServiceRef.current?.switchAndReply(sessionId, content, { stopCurrent });
    pendingFilePushesRef.current = [];
    currentSessionIdRef.current = sessionId;
    viewingHistorySessionRef.current = false;
    setCurrentSessionId(sessionId);
    setViewingHistorySession(false);
    // When content was sent with the switch, timeline will update via WS events.
    // Still reload history for empty switches so the pane shows prior messages.
    if (content) return;
    try {
      await loadSessionTimelineFast(sessionId, { softRefresh: true });
      const meta = getSessionMeta(agentId, sessionId);
      if (meta?.projectPath?.trim()) setAgentCwd(meta.projectPath.trim());
      else if (defaultCwd) setAgentCwd(defaultCwd);
    } catch (err: any) {
      console.error('[AIChatPage] Failed to reload session after switch:', err);
    }
  };

  /** Prepare UI/WS for sending into a session without aborting another pane's turn. */
  const prepareSessionForSend = async (
    sessionId: string,
    opts?: { stay?: boolean },
  ) => {
    viewingHistorySessionRef.current = false;
    setViewingHistorySession(false);

    if (sessionId === currentSessionIdRef.current) {
      // Still ensure the focused solo timeline matches this sid's live bucket
      // (tab switch may have left timeline state on another session's entries).
      const liveSame = liveTimelinesBySessionRef.current[sessionId];
      if (Array.isArray(liveSame) && liveSame.length > 0) {
        setTimelineState(liveSame);
        setShellStreams(rebuildShellStreamsFromTimeline(liveSame));
      }
      wsServiceRef.current?.setActiveSession(sessionId);
      return;
    }

    const liveBucket = liveTimelinesBySessionRef.current[sessionId];
    const hasLiveContent = Array.isArray(liveBucket) && liveBucket.length > 0;

    // stay=true (scheduled-task exec / parallel pane follow-up): route by
    // session_id only. Do NOT steal the focused Agent Web session, and do NOT
    // reload-from-disk when a live bucket already exists — disk/cache lag behind
    // the WS-finalized assistant reply and would erase it before append.
    if (opts?.stay) {
      if (!hasLiveContent) {
        try {
          eventSidRef.current = sessionId;
          await loadSessionTimelineFast(sessionId, { allowNonCurrent: true });
          eventSidRef.current = '';
        } catch (err: any) {
          eventSidRef.current = '';
          console.error('[AIChatPage] Failed to prepare stay-session for send:', err);
        }
      }
      return;
    }

    // Focus locally for outbound routing metadata, but do NOT wipe other
    // panes' live timeline buckets when this sid already has live content.
    pendingFilePushesRef.current = [];
    currentSessionIdRef.current = sessionId;
    setCurrentSessionId(sessionId);
    wsServiceRef.current?.setActiveSession(sessionId);

    const meta = getSessionMeta(agentId, sessionId);
    if (meta?.projectPath?.trim()) setAgentCwd(meta.projectPath.trim());
    else if (defaultCwd) setAgentCwd(defaultCwd);

    if (hasLiveContent) {
      // Critical: adopt this session's live bucket into the focused solo
      // timeline. Without this, deliverMessage's setTimeline mirror appends
      // onto the previous session's entries → message appears in session A.
      setTimelineState(liveBucket);
      setShellStreams(rebuildShellStreamsFromTimeline(liveBucket));
      return;
    }

    try {
      eventSidRef.current = sessionId;
      await loadSessionTimelineFast(sessionId);
      eventSidRef.current = '';
    } catch (err: any) {
      eventSidRef.current = '';
      console.error('[AIChatPage] Failed to prepare session for send:', err);
    }
  };

  // ---- Workspace chrome (L1 / L2) ----
  // Restore only previously open L2 tabs from localStorage — never seed from
  // the full session list (that made every session reappear on each start).
  const refreshWsSnap = useCallback(() => {
    setWsSnap(loadWorkspaceStoreResolved(agentId, [agentProfile?.dir_name]));
  }, [agentId, agentProfile?.dir_name]);

  useEffect(() => {
    setWorkspaceStoreAliases(agentId, [
      agentProfile?.dir_name,
      agentProfile?.agent_id,
    ]);
    setWsSnap(loadWorkspaceStoreResolved(agentId, [agentProfile?.dir_name]));
    wsMigratedRef.current = false;
  }, [agentId, agentProfile?.dir_name, agentProfile?.agent_id]);

  // Persist workspace chrome + session↔project bindings on the agent host so
  // LAN / different origins (localhost vs 192.168.x.x) share the same state.
  useEffect(() => {
    if (!agentId) return;
    const serverName = (agentProfile?.dir_name || agentId).trim();
    setAgentWebUiSyncTarget(agentId, serverName, [
      agentProfile?.dir_name,
      agentProfile?.agent_id,
    ]);
    const unbind = bindAgentWebUiSyncPush();
    let cancelled = false;
    void (async () => {
      const applied = await pullAgentWebUiState();
      if (cancelled) return;
      if (applied) {
        refreshWsSnap();
        requestSessionListRefresh(agentId);
      } else {
        // Seed server from this browser if it already has local chrome.
        schedulePushAgentWebUiState(200);
      }
    })();
    return () => {
      cancelled = true;
      unbind();
    };
  }, [agentId, agentProfile?.dir_name, agentProfile?.agent_id, refreshWsSnap]);

  useEffect(() => {
    const onCh = (e: Event) => {
      const detail = (e as CustomEvent).detail;
      if (detail?.agentId && detail.agentId !== agentId) return;
      refreshWsSnap();
    };
    window.addEventListener(WORKSPACES_CHANGED_EVENT, onCh);
    return () => window.removeEventListener(WORKSPACES_CHANGED_EVENT, onCh);
  }, [agentId, refreshWsSnap]);

  useEffect(() => {
    if (!agentId) return;
    const def = (defaultCwd || agentCwd || '').trim();
    migrateProjectPathsToWorkspaces(agentId, def || null);
    wsMigratedRef.current = true;
    refreshWsSnap();
  }, [agentId, defaultCwd, refreshWsSnap]);

  const activeWorkspace = useMemo(() => {
    const id = wsSnap.chrome.activeWorkspaceId;
    if (!id) return null;
    return wsSnap.workspaces.find((w) => w.id === id) || null;
  }, [wsSnap]);

  const workspaceLayout: SplitNode | null = useMemo(() => {
    if (!activeWorkspace) return null;
    return wsSnap.chrome.layoutByWorkspace?.[activeWorkspace.id] || null;
  }, [wsSnap, activeWorkspace]);

  // 防御性兜底：切换聚焦会话 / 布局变化时，把对应 bucket 同步为 solo
  // timeline 的数据源（主聊天区始终渲染 timeline state）。正常路径下
  // setTimeline 的 mirror 已保持同步，这里只处理极端的跨路径切换。
  useEffect(() => {
    if (activeWorkspace && workspaceLayout) return; // 分屏中 pane 各自消费 bucket
    const sid = currentSessionIdRef.current || '';
    const bucket = sid ? liveTimelinesBySessionRef.current[sid] : timelineRef.current;
    if (Array.isArray(bucket)) {
      setTimelineState(bucket);
      setShellStreams(rebuildShellStreamsFromTimeline(bucket));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceLayout, activeWorkspace, currentSessionId]);

  const focusedPaneId = wsSnap.chrome.focusedPaneId;
  focusedPaneIdRef.current = focusedPaneId;

  /** Sidebar highlight: focused pane's active session tab (not backend session.current). */
  const sidebarSelectedSessionId = useMemo(() => {
    if (activeWorkspace && agentId) {
      const tabs = getFocusedPaneTabs(agentId, activeWorkspace.id);
      const tab = parseContentTabKey(tabs.activeKey);
      if (tab?.kind === 'session') return tab.id;
    }
    return currentSessionId;
  }, [wsSnap, activeWorkspace, agentId, currentSessionId]);

  useEffect(() => {
    viewedSessionIdRef.current = sidebarSelectedSessionId;
    if (!sidebarSelectedSessionId) return;
    setUnseenCompleteSessionIds((cur) =>
      cur.includes(sidebarSelectedSessionId)
        ? cur.filter((id) => id !== sidebarSelectedSessionId)
        : cur,
    );
  }, [sidebarSelectedSessionId]);

  // Ensure project files panel is open when a workspace is active (desktop only).
  // On compact/mobile, both rails overlay — auto-opening would hide the chat.
  useEffect(() => {
    if (!activeWorkspace || isCompactLayout) return;
    try {
      const raw = localStorage.getItem('opensquad.filesPanel.open');
      if (raw === null) {
        setFilesPanelOpen(true);
      }
    } catch {
      setFilesPanelOpen(true);
    }
  }, [activeWorkspace?.id, isCompactLayout]);

  // Entering a narrow viewport: keep chat full-width (close in-flow rails).
  useEffect(() => {
    if (!isCompactLayout) return;
    setSessionSidebarOpen(false);
    setFilesPanelOpen(false);
  }, [isCompactLayout]);

  useEffect(() => {
    if (!activeWorkspace || !agentId) return;
    const path = activeWorkspace.rootPath;
    setAgentCwd((prev) => (pathsEqual(prev || '', path) ? prev : path));
    // Wait for profile — agentId alone may not match the on-disk agent directory.
    const dirName = agentProfile?.dir_name;
    if (!dirName || !path) return;
    void adminAPI.setWorkingDirectory(dirName, path).catch((err: any) => {
      console.error('[AIChatPage] Failed to set cwd for workspace:', err);
    });
  }, [activeWorkspace?.id, activeWorkspace?.rootPath, agentId, agentProfile?.dir_name]);

  useEffect(() => {
    if (!currentSessionId || !activeWorkspace) return;
    if (!pendingOpenSessionTabRef.current) return;
    pendingOpenSessionTabRef.current = false;
    const targetPane =
      pendingTargetPaneIdRef.current || focusedPaneId || null;
    pendingTargetPaneIdRef.current = null;
    openContentTab(
      agentId,
      activeWorkspace.id,
      { kind: 'session', id: currentSessionId },
      targetPane,
    );
    pinComposerLanding(currentSessionId);
    if (targetPane) setFocusedPane(agentId, targetPane);
    refreshWsSnap();
  }, [currentSessionId, activeWorkspace?.id, agentId, refreshWsSnap, focusedPaneId, pinComposerLanding]);

  useEffect(() => {
    if (!sessionTitleUpdate) return;
    setTabSessionTitles((prev) => ({
      ...prev,
      [sessionTitleUpdate.id]: sessionTitleUpdate.title,
    }));
  }, [sessionTitleUpdate]);

  /** Keep L2 tab labels in sync with sidebar session titles (not truncated session ids). */
  const handleSessionsChange = useCallback((
    sessions: Array<{ id: string; title?: string }>,
    complete: boolean = true,
  ) => {
    setTabSessionTitles((prev) => {
      let changed = false;
      const next = { ...prev };
      for (const s of sessions) {
        const title = (s.title || '').trim();
        if (!title) continue;
        if (next[s.id] !== title) {
          next[s.id] = title;
          changed = true;
        }
      }
      return changed ? next : prev;
    });
    // Prune L2 tabs for deleted sessions — never open the whole list as tabs.
    // Protect agent-current / focused sid: new sessions often appear as L2 tabs
    // before the HTTP list includes them; pruning flipped the active tab away
    // and docked the empty composer while the user was still typing.
    const ids = sessions.map((s) => s.id).filter(Boolean);
    if (complete !== false && agentId && ids.length > 0) {
      const protect = new Set(ids);
      const cur = currentSessionIdRef.current;
      const agentCur = agentCurrentSessionIdRef.current;
      if (cur) protect.add(cur);
      if (agentCur) protect.add(agentCur);
      const pruned = pruneGoneSessionTabs(agentId, protect);
      setWsSnap(pruned);
    }
    // Prefetch recent session first pages into timeline cache (idle).
    if (!agentId || !sessions.length) return;
    const current = currentSessionIdRef.current;
    const toPrefetch = sessions
      .map((s) => s.id)
      .filter((id) => id && id !== current && !getCachedSessionTimelineMeta(agentId, id))
      .slice(0, 3);
    if (toPrefetch.length === 0) return;
    const run = () => {
      for (const sid of toPrefetch) {
        void (async () => {
          try {
            if (getCachedSessionTimelineMeta(agentId, sid)) return;
            const resp = await agentSessionAPI.getSessionHistoryPaged(
              agentId,
              sid,
              0,
              SESSION_HISTORY_PAGE_SIZE,
            );
            const session = resp.session;
            if (!session) return;
            const messages = session.messages || [];
            const entries = buildTimelineFromSession(
              messages,
              session.events || [],
              session.archived_messages,
              session.archived_events,
            );
            putCachedSessionTimeline(agentId, sid, entries, {
              complete: !(session.has_more ?? false),
              messageCount: messages.length,
              totalMessages: session.total_messages,
            });
          } catch {
            /* ignore prefetch errors */
          }
        })();
      }
    };
    if (typeof window !== 'undefined' && 'requestIdleCallback' in window) {
      (window as Window & { requestIdleCallback: (cb: () => void) => number }).requestIdleCallback(run);
    } else {
      // `window` is narrowed to undefined in this branch — use the global timer.
      setTimeout(run, 400);
    }
  }, [agentId]);

  // Also refresh titles when workspace/agent changes (sidebar may be closed).
  useEffect(() => {
    if (!agentId) return;
    let cancelled = false;
    void (async () => {
      try {
        const resp = await agentSessionAPI.getSessionList(agentId);
        if (cancelled) return;
        handleSessionsChange(resp.sessions || [], !resp.has_more);
      } catch {
        /* ignore */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [agentId, activeWorkspace?.id, handleSessionsChange]);

  const handleSelectWorkspace = (id: string) => {
    openWorkspaceTab(agentId, id);
    refreshWsSnap();
    const snap = loadWorkspaceStore(agentId);
    const ws = snap.workspaces.find((w) => w.id === id);
    if (ws) {
      setAgentCwd(ws.rootPath);
      pendingProjectPathRef.current = ws.rootPath;
    }
    // Session tabs load via SessionChatPane; do not call handleViewSession
    // (avoids global「加载会话中」and remounting the live chatSlot).
  };

  const handleOpenExistingWorkspace = (rootPath: string) => {
    const ws = ensureWorkspace(agentId, rootPath);
    openWorkspaceTab(agentId, ws.id);
    refreshWsSnap();
    setAgentCwd(ws.rootPath);
    pendingProjectPathRef.current = ws.rootPath;
  };

  const handleCreateWorkspace = (name: string, rootPath: string) => {
    const ws = ensureWorkspace(agentId, rootPath, name);
    openWorkspaceTab(agentId, ws.id);
    refreshWsSnap();
    setAgentCwd(ws.rootPath);
    pendingProjectPathRef.current = ws.rootPath;
    setCreateWorkspaceOpen(false);
  };

  const handleConfirmCloseWorkspace = () => {
    if (!closeWorkspaceTarget) return;
    closeWorkspaceTab(agentId, closeWorkspaceTarget.id);
    refreshWsSnap();
    setCloseWorkspaceTarget(null);
  };

  const handleOpenFileInTab = (relPath: string) => {
    if (!activeWorkspace) return;
    const p = (relPath || '').replace(/\\/g, '/');
    if (!p) return;
    const agentDir = agentProfile?.dir_name || agentId;
    const root = activeWorkspace.rootPath;
    const wsId = activeWorkspace.id;
    // Always open into the anchored (focused) pane — never touch other panes
    const pane = focusedPaneId;
    const openNow = () => {
      openContentTab(agentId, wsId, { kind: 'file', id: p }, pane);
      if (pane) setFocusedPane(agentId, pane);
      refreshWsSnap();
      // Mobile: close the files drawer so the center editor is visible.
      if (isCompactLayout) {
        setFilesPanelOpen(false);
        setSessionSidebarOpen(false);
      }
    };
    // Cache hit → open instantly. Miss → await prefetch so the keep-alive
    // editor mounts with content (no spinner flash on first paint).
    if (getWorkspaceFileCache(agentDir, root, p)) {
      openNow();
      return;
    }
    void (async () => {
      await prefetchWorkspaceFile(agentDir, root, p);
      openNow();
    })();
  };

  const handleContentTabSelect = (tab: ContentTab, paneId?: string) => {
    if (!activeWorkspace) return;
    const pid = paneId || focusedPaneId;
    setActiveContentTab(agentId, activeWorkspace.id, tab, pid);
    if (pid) setFocusedPane(agentId, pid);
    refreshWsSnap();
    // Session tabs: SessionChatPane loads history in-pane. Calling
    // handleViewSession here forced global isLoadingSession + dual fetch.
  };

  const handleContentTabClose = (tab: ContentTab, paneId?: string) => {
    if (!activeWorkspace) return;
    if (tab.kind === 'file' && fileDirtyMap[tab.id]) {
      if (!confirmDiscardFileDirty(true)) return;
    }
    closeContentTab(agentId, activeWorkspace.id, tab, paneId || focusedPaneId);
    refreshWsSnap();
    if (tab.kind === 'file') {
      setFileDirtyMap((prev) => {
        const next = { ...prev };
        delete next[tab.id];
        return next;
      });
    }
  };

  const handleContentTabReorder = (from: ContentTab, to: ContentTab, paneId?: string) => {
    if (!activeWorkspace) return;
    reorderContentTabs(
      agentId,
      activeWorkspace.id,
      contentTabKey(from),
      contentTabKey(to),
      paneId || focusedPaneId,
    );
    refreshWsSnap();
  };

  /** Open / continue a session in the currently anchored pane only. */
  const handleSidebarViewSession = (sessionId: string) => {
    setLibraryView(null);
    if (isCompactLayout) {
      setSessionSidebarOpen(false);
      setFilesPanelOpen(false);
    }
    if (activeWorkspace) {
      const pane = focusedPaneId;
      openContentTab(
        agentId,
        activeWorkspace.id,
        { kind: 'session', id: sessionId },
        pane,
      );
      if (pane) setFocusedPane(agentId, pane);
      refreshWsSnap();
      // History loads in SessionChatPane; send path uses prepareSessionForSend.
      return;
    }
    // No workspace chrome — fall back to legacy full-pane session load.
    void handleViewSession(sessionId);
  };

  const handleNewSessionInWorkspace = (projectPath?: string) => {
    setLibraryView(null);
    if (isCompactLayout) {
      setSessionSidebarOpen(false);
      setFilesPanelOpen(false);
    }
    const path = (projectPath || activeWorkspace?.rootPath || '').trim();
    // Sidebar / global new-session → anchored pane
    if (!pendingTargetPaneIdRef.current && focusedPaneId) {
      pendingTargetPaneIdRef.current = focusedPaneId;
    }
    pendingOpenSessionTabRef.current = true;
    handleNewSession(path || undefined);
  };

  const handleSplitPane = (paneId: string, direction: SplitDirection) => {
    if (!activeWorkspace) {
      console.warn('[AIChatPage] split ignored: no active workspace');
      return;
    }
    const layout = workspaceLayout;
    const applied = layout
      ? applySplitToLayout(layout, paneId, direction, focusedPaneId)
      : null;
    let newLeafId: string | null = null;
    if (applied) {
      commitWorkspaceLayout(
        agentId,
        activeWorkspace.id,
        applied.tree,
        applied.newLeafId,
      );
      newLeafId = applied.newLeafId;
    } else {
      const result = splitPane(agentId, activeWorkspace.id, paneId, direction);
      if (!result) {
        console.warn('[AIChatPage] split failed', {
          paneId,
          direction,
          ws: activeWorkspace.id,
          leafIds: layout ? collectLeaves(layout).map((l) => l.id) : [],
          focusedPaneId,
        });
        return;
      }
      newLeafId = result.newLeafId;
    }
    refreshWsSnap();
    // New leaf is anchored and gets a fresh session (other panes untouched)
    pendingTargetPaneIdRef.current = newLeafId;
    pendingOpenSessionTabRef.current = true;
    handleNewSession(activeWorkspace.rootPath || undefined);
  };

  const handleCloseAllInPane = (paneId: string) => {
    if (!activeWorkspace) return;
    if (!window.confirm('关闭该窗格内全部标签？本地会话与文件不会删除。')) return;
    closeAllTabsInPane(agentId, activeWorkspace.id, paneId);
    setFocusedPane(agentId, paneId);
    refreshWsSnap();
    pendingTargetPaneIdRef.current = paneId;
    pendingOpenSessionTabRef.current = true;
    handleNewSession(activeWorkspace.rootPath || undefined);
  };

  const handleClosePane = (paneId: string) => {
    if (!activeWorkspace) return;
    closePane(agentId, activeWorkspace.id, paneId);
    refreshWsSnap();
  };

  const handleResizeSplit = (splitId: string, ratio: number) => {
    if (!activeWorkspace) return;
    resizeSplit(agentId, activeWorkspace.id, splitId, ratio);
    refreshWsSnap();
  };

  const handlePaneComposerSend = async (
    paneId: string,
    sessionId: string,
    payload: ComposerSendPayload,
    opts?: { stay?: boolean },
  ) => {
    if (!activeWorkspace) return;
    // First real send leaves the centered new-session landing and promotes
    // the draft into the sidebar session list.
    unpinComposerLanding(sessionId);
    requestSessionListRefresh(agentId, sessionId);
    setFocusedPane(agentId, paneId);
    if (!opts?.stay) {
      openContentTab(
        agentId,
        activeWorkspace.id,
        { kind: 'session', id: sessionId },
        paneId,
      );
      refreshWsSnap();
    }

    if (autoSpeechEnabledRef.current) {
      unlockAutoTtsAudio();
    }

    const shouldQueue =
      isSessionBusy(sessionId) ||
      isOutboundPending(sessionId) ||
      pendingMessagesRef.current.some((m) => m.sessionId === sessionId);

    if (shouldQueue) {
      // Always park visually when this session is busy / already queued.
      // Do not mid-turn deliverMessage — that puts the bubble in the timeline
      // and makes continuous sends look "already sent" instead of 待发送.
      const snapshot: PendingMessage = {
        id: genUID(),
        text: payload.text,
        images: [...payload.images],
        attachments: payload.attachments.map((a) => ({ ...a })) as UploadedFile[],
        fileAtts: payload.attachments
          .filter((a) => !a.is_image)
          .map((a) => {
            const isVoice = a.type === 'voice' || a.type === 'audio' || !!a.is_audio;
            return {
              name: a.original_name,
              size: _formatFileSize(a.size),
              path: a.path,
              url: a.url,
              type: a.is_video && !isVoice ? 'video' : isVoice ? (a.type === 'voice' ? 'voice' : 'audio') : 'file',
              duration: typeof a.duration === 'number' ? a.duration : undefined,
            };
          }) as FileAttachment[],
        skillDir: payload.skillDir,
        skillName: payload.skillName,
        sessionId,
        paneId,
      };
      setPendingMessages((prev) => [...prev, snapshot]);
      return;
    }

    // Other sessions may be busy — still send immediately (true parallel).
    await prepareSessionForSend(sessionId, { stay: opts?.stay });
    armOutboundTurnPending(sessionId);
    // stay / cross-session: do not salvage focused-pane stream text into the
    // target sid (and vice versa) — follow-up must only APPEND on that sid.
    const sameFocused = sessionId === (currentSessionIdRef.current || '');
    deliverMessage(
      {
        text: payload.text,
        images: payload.images,
        attachments: payload.attachments as UploadedFile[],
        skillDir: payload.skillDir,
        skillName: payload.skillName,
        sessionId,
      },
      { clearInputState: false, salvageStream: sameFocused && !opts?.stay },
    );
  };

  const makePaneHandlers = (paneId: string): PaneShellHandlers => {
    /** Centered landing until this session has real chat (not draft typing / lifecycle noise). */
    const isSessionComposerLanding = (sessionId: string): boolean => {
      if (!sessionId) return false;
      // New Chat: keep centered until first send, regardless of timeline noise.
      if (composerLandingSessionsRef.current.has(sessionId)) return true;
      // Refresh / reconnect: empty timeline before hydrate must NOT look like New Chat.
      if (!sessionBootstrapped || isLoadingSession) return false;
      if (isStreamingBySession[sessionId]) return false;
      if (sessionId === currentSessionId && isStreaming) return false;
      const hasBucket = Object.prototype.hasOwnProperty.call(liveTimelinesBySession, sessionId);
      const cached = !hasBucket && sessionId !== currentSessionId
        ? getCachedSessionTimeline(agentId, sessionId)
        : null;
      const entries = hasBucket
        ? (liveTimelinesBySession[sessionId] || [])
        : sessionId === currentSessionId
          ? timeline
          : (cached || []);
      // No local knowledge yet → loading / unknown, not landing.
      if (!hasBucket && sessionId !== currentSessionId && !cached) return false;
      return !timelineHasVisibleChatContent(flattenArchivedSections(entries));
    };
    const isSessionPaneLoading = (sessionId: string): boolean => {
      if (!sessionId) return false;
      if (composerLandingSessionsRef.current.has(sessionId)) return false;
      // Only the very first agent bootstrap may show a full-pane overlay.
      // Session tab switches must stay cache-first (SessionChatPane) — never
      // block on isLoadingSession flaps / soft reconnect hydrates.
      if (!sessionBootstrapped && (!currentSessionId || sessionId === currentSessionId)) {
        return true;
      }
      return false;
    };
    const renderPendingFor = (sessionId: string): React.ReactNode => {
      const queue = pendingMessages.filter((m) => m.sessionId === sessionId);
      if (queue.length === 0) return null;
      return (
        <div className="rounded-lg border border-border/50 bg-transparent overflow-hidden">
          <div className="flex items-center gap-2 px-2.5 py-1.5 border-b border-border/40 bg-transparent">
            <Clock size={11} className="text-primary flex-shrink-0" />
            <span className="text-[11px] text-textMain font-semibold">
              {t('aiChat.pendingCount', { count: queue.length })}
            </span>
            <span className="text-[10px] text-textMuted">
              · ↗ {t('aiChat.pendingAutoSendHint')}
            </span>
            <div className="flex-1" />
            <button
              type="button"
              onClick={handleSendNextPending}
              className="flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium text-primary hover:bg-primary/10 transition-colors"
              title={t('aiChat.sendNext')}
            >
              <Zap size={10} />
              {t('aiChat.sendNext')}
            </button>
            <button
              type="button"
              onClick={handleCancelAllPending}
              className="flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium text-textMuted hover:bg-primary/10 transition-colors"
              title={t('aiChat.pendingClearAll')}
            >
              <X size={10} />
              {t('aiChat.pendingClearAll')}
            </button>
            <button
              type="button"
              onClick={() => setPendingCollapsed((c) => !c)}
              className="flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[10px] font-medium text-textMuted hover:bg-primary/10 transition-colors"
              title={pendingCollapsed ? t('aiChat.pendingExpand') : t('aiChat.pendingCollapse')}
            >
              {pendingCollapsed ? '▴' : '▾'}
            </button>
          </div>
          {!pendingCollapsed && (
            <div className="max-h-[200px] overflow-y-auto">
              {queue.map((pm, idx) => {
                const imgCount = pm.images.length;
                const fileCount = pm.fileAtts.length;
                const preview = (pm.text || '').replace(/\s+/g, ' ').trim();
                return (
                  <div
                    key={pm.id}
                    className="group flex items-center gap-2 px-2.5 py-1.5 border-b border-border/30 last:border-b-0 hover:bg-primary/10 transition-colors"
                  >
                    <span className="flex-shrink-0 text-[10px] font-mono text-textMuted min-w-[28px]">
                      {t('aiChat.pendingQueuePosition', { index: idx + 1 })}
                    </span>
                    <div className="flex-1 min-w-0 flex items-center gap-2">
                      {preview ? (
                        <span className="truncate text-[12px] text-textMain">{preview}</span>
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
                    <div className="flex-shrink-0 flex items-center gap-0.5 opacity-60 group-hover:opacity-100 transition-opacity">
                      <button
                        type="button"
                        onClick={() => handleSendPendingNow(pm.id)}
                        className="p-1 rounded text-primary hover:bg-primary/10 transition-colors"
                        title={t('aiChat.sendNow')}
                      >
                        <Zap size={12} />
                      </button>
                      <button
                        type="button"
                        onClick={() => handleCancelPending(pm.id)}
                        className="p-1 rounded text-textMuted hover:bg-primary/10 hover:text-textMain transition-colors"
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
      );
    };

    return {
    onSelectTab: (tab) => handleContentTabSelect(tab, paneId),
    onCloseTab: (tab) => handleContentTabClose(tab, paneId),
    onReorderTabs: (from, to) => handleContentTabReorder(from, to, paneId),
    onNewSession: () => {
      // New session only in this pane — other panes keep their tabs
      setFocusedPane(agentId, paneId);
      pendingTargetPaneIdRef.current = paneId;
      pendingOpenSessionTabRef.current = true;
      refreshWsSnap();
      handleNewSession(activeWorkspace?.rootPath || undefined);
    },
    onSplitRow: () => handleSplitPane(paneId, 'row'),
    onSplitCol: () => handleSplitPane(paneId, 'col'),
    onCloseAll: () => handleCloseAllInPane(paneId),
    onClosePane: () => handleClosePane(paneId),
    onFocus: () => {
      // Click = only change the anchor (focusedPaneId). Do NOT switch the global
      // live session — that remounted chatSlot/history and made sibling panes'
      // conversation content jump (see split-pane focus bug).
      if (focusedPaneId !== paneId) {
        setFocusedPane(agentId, paneId);
        refreshWsSnap();
      }
    },
    getSessionLiveTimeline: (sessionId: string) => {
      // Only this sid's live bucket — never borrow currentSessionId / global
      // timeline (that still holds another chat after a parallel spawn).
      // Missing bucket → null so ExecWorkflowView hydrates that sid from disk.
      const live = pickSessionLiveTimeline(liveTimelinesBySession, sessionId);
      return live != null ? flattenArchivedSections(live) : null;
    },
    getSessionTokenStats: (sessionId: string) =>
      tokenStatsBySession[sessionId] ?? null,
    isSessionBusy: (sessionId: string) => isSessionBusy(sessionId),
    sendToSessionStay: (sessionId, payload) =>
      handlePaneComposerSend(paneId, sessionId, payload, { stay: true }),
    stopSession: (sessionId: string) => handleStop(sessionId),
    renderSessionPendingPanel: renderPendingFor,
    ensureSessionWatched: (sessionId: string) => {
      const sid = (sessionId || '').trim();
      if (!sid) return;
      try {
        const ws = wsServiceRef.current || getAiWsService(agentId);
        ws.watchSession?.(sid);
        // Open / focus a session tab → load that session's token % immediately.
        ws.requestTokenStats?.(sid);
      } catch {
        /* ignore */
      }
      // If scheduled-task exec view attaches before any WS event created a live
      // bucket, seed once so the pane is not empty until refresh.
      const hasBucket = Object.prototype.hasOwnProperty.call(
        liveTimelinesBySessionRef.current,
        sid,
      );
      if (hasBucket) {
        const existing = liveTimelinesBySessionRef.current[sid];
        if (Array.isArray(existing) && existing.length > 0) return;
      }
      // Instant paint from timeline cache (no network) — avoids「加载中」on
      // every session tab switch when the session was viewed recently.
      const cached = getCachedSessionTimeline(agentId, sid);
      if (cached && cached.length > 0) {
        setLiveTimelinesBySession((prev) => {
          if (
            Object.prototype.hasOwnProperty.call(prev, sid)
            && (prev[sid]?.length || 0) > 0
          ) {
            return prev;
          }
          const out = { ...prev, [sid]: cached };
          liveTimelinesBySessionRef.current = out;
          return out;
        });
        return;
      }
      if (hasBucket) return;
      void (async () => {
        try {
          const resp = await agentSessionAPI.getSessionHistoryPaged(
            agentId,
            sid,
            0,
            SESSION_HISTORY_PAGE_SIZE,
          );
          if (
            Object.prototype.hasOwnProperty.call(liveTimelinesBySessionRef.current, sid)
            && (liveTimelinesBySessionRef.current[sid]?.length || 0) > 0
          ) {
            // Live WS / SessionChatPane already created a richer bucket — do not clobber.
            return;
          }
          const session = resp.session;
          const entries = buildTimelineFromSession(
            session?.messages || [],
            session?.events || [],
          );
          // Never seed an empty live bucket — [] still "has" a key and blocks
          // later fetch, and deliverMessage used to adopt that [] over the
          // optimistic user bubble on brand-new sessions.
          if (entries.length === 0) return;
          setLiveTimelinesBySession((prev) => {
            if (Object.prototype.hasOwnProperty.call(prev, sid) && (prev[sid]?.length || 0) > 0) {
              return prev;
            }
            const out = { ...prev, [sid]: entries };
            liveTimelinesBySessionRef.current = out;
            return out;
          });
          putCachedSessionTimeline(agentId, sid, entries, {
            complete: !(session?.has_more ?? false),
            messageCount: session?.messages?.length || 0,
            totalMessages: session?.total_messages,
          });
        } catch (err: any) {
          console.warn('[AIChatPage] ensureSessionWatched hydrate failed:', err?.message || err);
        }
      })();
    },
    renderSessionChat: (sessionId: string) => {
      const hasLiveBucket = Object.prototype.hasOwnProperty.call(liveTimelinesBySession, sessionId);
      const live = hasLiveBucket ? liveTimelinesBySession[sessionId] : null;
      return (
        <SessionChatPane
          key={`session-chat-${paneId}-${sessionId}`}
          agentId={agentId}
          sessionId={sessionId}
          liveTimeline={live != null && live.length > 0 ? flattenArchivedSections(live) : null}
          isSolo={isSolo}
          expandLevel={workflowExpandLevel}
          columnClass={soloColumnClass}
          userName={currentUser?.name || undefined}
          agentName={agentProfile?.agent_name || undefined}
          canWithdraw={!changesBusy}
          onWithdrawUserMessage={(entryUid, message) =>
            requestWithdrawUserMessage(entryUid, message, sessionId)
          }
          onFocus={() => {
            if (focusedPaneId !== paneId) {
              setFocusedPane(agentId, paneId);
              refreshWsSnap();
            }
          }}
        />
      );
    },
    renderComposer: (sessionId: string) => (
      <AgentWebComposer
        key={`composer-${paneId}-${sessionId}`}
        ref={(api) => {
          if (api) {
            composerApiByPaneRef.current.set(paneId, api);
            if (sessionId) composerApiBySessionRef.current.set(sessionId, api);
          } else {
            composerApiByPaneRef.current.delete(paneId);
            if (sessionId) composerApiBySessionRef.current.delete(sessionId);
          }
        }}
        agentId={agentId}
        columnClass={soloColumnClass}
        draftText={draftsBySession[sessionId] ?? ''}
        onDraftChange={(t) => {
          setDraftsBySession((prev) => {
            if ((prev[sessionId] ?? '') === t) return prev;
            return { ...prev, [sessionId]: t };
          });
        }}
        landing={isSessionComposerLanding(sessionId)}
        disabled={isLoadingSession || (!sessionBootstrapped && !composerLandingSessionsRef.current.has(sessionId))}
        busy={isSessionBusy(sessionId)}
        agentMode={agentModeBySession[sessionId] ?? agentMode}
        onModeChange={(mode) => {
          setAgentModeBySession((prev) => ({ ...prev, [sessionId]: mode }));
          wsServiceRef.current?.setAgentMode(mode, undefined, sessionId);
        }}
        approvalPanel={(() => {
          if (focusedPaneId !== paneId) return null;
          const pendingModes = modeApprovals.filter((a) => a.status === 'pending');
          const pendingOptions = optionsProposals.filter((p) => p.status === 'pending');
          if (pendingModes.length === 0 && pendingOptions.length === 0) return null;
          return (
            <>
              {pendingModes.map((req) => (
                <ModeSwitchApprovalCard
                  key={req.id}
                  request={req}
                  onApprove={(reqId, mode) => {
                    setModeApprovals((prev) =>
                      prev.map((a) => (a.id === reqId ? { ...a, status: 'approved' } : a)),
                    );
                    setAgentModeBySession((prev) => ({ ...prev, [sessionId]: mode }));
                    setAgentMode(mode);
                    wsServiceRef.current?.setAgentMode(mode, reqId, sessionId);
                  }}
                  onDeny={(reqId) => {
                    setModeApprovals((prev) =>
                      prev.map((a) => (a.id === reqId ? { ...a, status: 'denied' } : a)),
                    );
                    wsServiceRef.current?.denyModeSwitch(reqId);
                  }}
                />
              ))}
              {pendingOptions.map((proposal) => (
                <OptionsApprovalCard
                  key={proposal.id}
                  proposal={proposal}
                  onSubmit={(reqId, optionIds) => {
                    setOptionsProposals((prev) =>
                      prev.map((p) =>
                        p.id === reqId
                          ? {
                              ...p,
                              status: 'chosen',
                              chosen_option_id: optionIds[0],
                              chosen_option_ids: optionIds,
                            }
                          : p,
                      ),
                    );
                    wsServiceRef.current?.resolveProposedOptions(reqId, optionIds);
                  }}
                  onCustom={(reqId, answer) => {
                    setOptionsProposals((prev) =>
                      prev.map((p) =>
                        p.id === reqId ? { ...p, status: 'custom', custom_answer: answer } : p,
                      ),
                    );
                    wsServiceRef.current?.resolveProposedOptionsCustom(reqId, answer);
                  }}
                  onIgnore={(reqId) => {
                    setOptionsProposals((prev) =>
                      prev.map((p) => (p.id === reqId ? { ...p, status: 'ignored' } : p)),
                    );
                    wsServiceRef.current?.ignoreProposedOptions(reqId);
                  }}
                />
              ))}
            </>
          );
        })()}
        modelCards={modelCards}
        currentCardName={cardNameBySession[sessionId] ?? currentCardName}
        modelName={modelNameBySession[sessionId] ?? modelName ?? ''}
        fallbackLabel={agentProfile?.agent_name || agentId}
        switchingModel={!!switchingModelBySession[sessionId]}
        onRefreshModelCards={refreshModelCards}
        onSelectModel={(cardName) => {
          const prevCard = cardNameBySession[sessionId] ?? currentCardName;
          const prevModel = modelNameBySession[sessionId] ?? modelName ?? '';
          const cardMeta = modelCards.find((c) => c.name === cardName);
          const nextModel =
            (cardMeta && (cardMeta.title || cardMeta.model_name || cardMeta.name)) || cardName;
          modelSwitchRevertRef.current[sessionId] = {
            card: prevCard || null,
            model: String(prevModel || ''),
          };
          // Optimistic: update label immediately — never park on "Switching…".
          // Promote to agent-wide last pick so new/old chats share the same default.
          setCurrentCardName(cardName);
          setModelName(String(nextModel));
          setCardNameBySession((prev) => {
            const next: Record<string, string> = {};
            for (const k of Object.keys(prev)) next[k] = cardName;
            next[sessionId] = cardName;
            return next;
          });
          setModelNameBySession((prev) => {
            const next: Record<string, string> = {};
            for (const k of Object.keys(prev)) next[k] = String(nextModel);
            next[sessionId] = String(nextModel);
            return next;
          });
          setSwitchingModelBySession((prev) => ({ ...prev, [sessionId]: false }));
          saveLastModelPick(agentId, { card: cardName });
          console.info('[AIChatPage] switch_model', { sessionId, cardName });
          wsServiceRef.current?.switchModel(cardName, sessionId);
        }}
        reasoningEffort={reasoningBySession[sessionId] ?? reasoningEffort}
        onEffortChange={(effort) => {
          setReasoningEffort(effort);
          setReasoningBySession((prev) => {
            const next: Record<string, ReasoningEffort> = {};
            for (const k of Object.keys(prev)) next[k] = effort;
            next[sessionId] = effort;
            return next;
          });
          saveLastModelPick(agentId, { effort });
          wsServiceRef.current?.setReasoningEffort(effort, sessionId);
        }}
        cwd={agentCwd || defaultCwd}
        tokenStats={tokenStatsBySession[sessionId] ?? null}
        onViewReport={() => setShowContextViewer(true)}
        onCompressContext={handleCompressContext}
        compressing={isCompressingContext}
        compressDisabled={isLoadingSession || isCompressingContext}
        sessionChanges={
          isSolo && focusedPaneId === paneId && currentSessionId === sessionId
            ? sessionChanges
            : null
        }
        changesBusy={changesBusy}
        onOpenChanges={() => {
          setFilesPanelOpen(true);
          if (isCompactLayout) setSessionSidebarOpen(false);
          try {
            localStorage.setItem('opensquad.filesPanel.open', 'true');
          } catch {
            /* ignore */
          }
          setFocusChangedNonce(Date.now());
        }}
        onCommitPush={async () => {
          const root = projectRoot;
          const dirName = fsAgentName;
          setChangesBusy(true);
          try {
            if (dirName && root) {
              await adminAPI.commitSessionChanges(dirName, root).catch(() => {});
            }
            setSessionChanges({ additions: 0, deletions: 0, count: 0 });
            setFilesLiveChanges({
              nonce: Date.now(),
              additions: 0,
              deletions: 0,
              count: 0,
              files: [],
            });
            setFocusChangedNonce(Date.now());
            await handlePaneComposerSend(paneId, sessionId, {
              text: COMMIT_PUSH_MESSAGE,
              images: [],
              attachments: [],
            }, { stay: true });
          } catch (err) {
            console.warn('[SessionChanges] Commit & Push failed', err);
          } finally {
            setChangesBusy(false);
          }
        }}
        availableSkills={availableSkills}
        skillsLoading={skillsLoading}
        onOpenSkills={loadSkillsIfNeeded}
        onGoalAction={runGoalAction}
        autoSpeechEnabled={autoSpeechEnabled}
        onToggleAutoSpeech={toggleAutoSpeech}
        onActivate={() => {
          // Focus this pane only — do not switch global live session (avoids content jump).
          if (focusedPaneId !== paneId) {
            setFocusedPane(agentId, paneId);
            refreshWsSnap();
          }
        }}
        onSend={(payload) =>
          handlePaneComposerSend(paneId, sessionId, payload, { stay: true })
        }
        onStop={() => handleStop(sessionId)}
        voicePanelOpen={voicePanelOpen && focusedPaneId === paneId}
        voiceHost={focusedPaneId === paneId}
        onVoicePanelOpenChange={(open) => {
          if (focusedPaneId !== paneId) {
            setFocusedPane(agentId, paneId);
            refreshWsSnap();
          }
          setVoicePanelOpen(open);
        }}
        voiceRealtimeStatus={voiceRealtimeStatus}
        voiceRealtimeError={voiceRealtimeError}
        voiceTranscript={voiceTranscript}
        voiceBindings={voiceBindings}
        onVoiceBindingsChange={handleVoiceBindingsChange}
        onRealtimeStart={handleVoiceRealtimeStart}
        onRealtimeStop={handleVoiceRealtimeStop}
        onAudioChunk={handleVoiceAudioChunk}
        onMouthpieceUtterance={handleMouthpieceUtterance}
        onForceAskAgentChange={handleForceAskAgentChange}
        planPanel={
          sessionId === currentSessionId && effectivePlanSteps.length > 0 ? (
            <PlanBlock
              steps={effectivePlanSteps}
              defaultOpen={false}
              className="mb-0 border border-border/50 rounded-2xl overflow-hidden bg-bgLight"
            />
          ) : null
        }
        pendingPanel={renderPendingFor(sessionId)}
        statusHint={null}
      />
    ),
    isComposerLanding: isSessionComposerLanding,
    isSessionLoading: isSessionPaneLoading,
    sessionLoadingLabel: sessionLoadingLabel || t('aiChat.loadingSession'),
    onFileDirty: (relPath, dirty) => {
      setFileDirtyMap((prev) => {
        if (!!prev[relPath] === dirty) return prev;
        return { ...prev, [relPath]: dirty };
      });
    },
  };
  };

  const handleOpenSkills = () => {
    setLibraryView((cur) => {
      const next = cur === 'skills' ? null : 'skills';
      if (next) {
        setSessionSidebarOpen(true);
        if (isCompactLayout) setFilesPanelOpen(false);
      }
      return next;
    });
  };

  const handleOpenPlugins = () => {
    setLibraryView((cur) => {
      const next = cur === 'plugins' ? null : 'plugins';
      if (next) {
        setSessionSidebarOpen(true);
        if (isCompactLayout) setFilesPanelOpen(false);
      }
      return next;
    });
  };

  const handleOpenRoles = () => {
    setLibraryView((cur) => {
      const next = cur === 'roles' ? null : 'roles';
      if (next) {
        setSessionSidebarOpen(true);
        if (isCompactLayout) setFilesPanelOpen(false);
      }
      return next;
    });
  };

  const handleOpenScheduledTasks = () => {
    setLibraryView(null);
    if (isCompactLayout) {
      setSessionSidebarOpen(false);
      setFilesPanelOpen(false);
    }
    if (!activeWorkspace) return;
    const pane = focusedPaneId;
    openContentTab(agentId, activeWorkspace.id, { kind: 'scheduled-tasks', id: 'scheduled-tasks' }, pane);
    if (pane) setFocusedPane(agentId, pane);
    refreshWsSnap();
  };

  const toggleSessionSidebar = useCallback(() => {
    setSessionSidebarOpen((open) => {
      const next = !open;
      if (next && isCompactLayout) setFilesPanelOpen(false);
      return next;
    });
  }, [isCompactLayout]);

  const toggleFilesPanel = useCallback(() => {
    setFilesPanelOpen((open) => {
      const next = !open;
      if (next && isCompactLayout) setSessionSidebarOpen(false);
      try {
        localStorage.setItem('opensquad.filesPanel.open', String(next));
      } catch {
        /* ignore */
      }
      return next;
    });
  }, [isCompactLayout]);

  const closeCompactOverlays = useCallback(() => {
    setSessionSidebarOpen(false);
    setFilesPanelOpen(false);
  }, []);

  // Open a session content tab (e.g. from the Scheduled Tasks "task flow" button).
  useEffect(() => {
    const handler = (e: any) => {
      const sessionId: string | undefined = e?.detail?.sessionId;
      if (!sessionId || !activeWorkspace) return;
      setLibraryView(null);
      const pane = focusedPaneId;
      openContentTab(agentId, activeWorkspace.id, { kind: 'session', id: sessionId }, pane);
      if (pane) setFocusedPane(agentId, pane);
      refreshWsSnap();
    };
    window.addEventListener('opensquad-open-session-tab', handler as EventListener);
    return () => window.removeEventListener('opensquad-open-session-tab', handler as EventListener);
  }, [activeWorkspace, focusedPaneId, agentId, refreshWsSnap]);

  // Open in-chat Skill 库 / 插件 / 角色 from nested views.
  useEffect(() => {
    const openSkills = () => {
      setLibraryView('skills');
      setSessionSidebarOpen(true);
    };
    const openPlugins = () => {
      setLibraryView('plugins');
      setSessionSidebarOpen(true);
    };
    const openRoles = () => {
      setLibraryView('roles');
      setSessionSidebarOpen(true);
    };
    window.addEventListener('opensquad-open-skills', openSkills as EventListener);
    window.addEventListener('opensquad-open-plugins', openPlugins as EventListener);
    window.addEventListener('opensquad-open-roles', openRoles as EventListener);
    return () => {
      window.removeEventListener('opensquad-open-skills', openSkills as EventListener);
      window.removeEventListener('opensquad-open-plugins', openPlugins as EventListener);
      window.removeEventListener('opensquad-open-roles', openRoles as EventListener);
    };
  }, []);

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

  const isFileUploadDrag = useCallback((e: React.DragEvent) => {
    const types = Array.from(e.dataTransfer?.types || []);
    // Tab reorder uses a custom MIME; never treat it as a file drop.
    if (types.includes('application/x-opensquad-tab')) return false;
    // OS / browser file drags expose "Files"
    return types.includes('Files');
  }, []);

  const handleDragEnter = useCallback((e: React.DragEvent) => {
    if (!isFileUploadDrag(e)) return;
    e.preventDefault();
    e.stopPropagation();
    dragCounterRef.current++;
    if (dragCounterRef.current === 1) {
      setIsDragOver(true);
    }
  }, [isFileUploadDrag]);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    if (!isFileUploadDrag(e) && dragCounterRef.current === 0) return;
    e.preventDefault();
    e.stopPropagation();
    dragCounterRef.current = Math.max(0, dragCounterRef.current - 1);
    if (dragCounterRef.current === 0) {
      setIsDragOver(false);
    }
  }, [isFileUploadDrag]);

  const handleDragOver = useCallback((e: React.DragEvent) => {
    if (!isFileUploadDrag(e)) return;
    e.preventDefault();
    e.stopPropagation();
  }, [isFileUploadDrag]);

  const handleDrop = useCallback(async (e: React.DragEvent) => {
    // Always reset overlay counter; ignore non-file drops (e.g. tab reorder).
    const isFile = isFileUploadDrag(e);
    e.preventDefault();
    e.stopPropagation();
    dragCounterRef.current = 0;
    setIsDragOver(false);
    if (!isFile) return;

    const droppedFiles = e.dataTransfer.files;
    if (!droppedFiles || droppedFiles.length === 0) return;

    const fileArray = Array.from(droppedFiles) as File[];
    const focusedPid = focusedPaneIdRef.current;
    const composer =
      (focusedPid && composerApiByPaneRef.current.get(focusedPid)) ||
      resolveComposerApi(currentSessionIdRef.current);
    if (composer) {
      await composer.uploadFiles(fileArray);
      return;
    }

    // Legacy fallback if no composer is mounted yet
    setIsUploading(true);
    try {
      if (fileArray.length === 1) {
        const file = fileArray[0];
        const resp = await agentSessionAPI.uploadFile(agentId, file);
        if (resp.is_image) {
          setImages((prev) => [...prev, resp.url]);
        } else {
          setAttachments((prev) => [...prev, resp]);
        }
      } else {
        const resp = await agentSessionAPI.uploadFiles(agentId, fileArray);
        for (const f of resp.files) {
          if (f.is_image) {
            setImages((prev) => [...prev, f.url]);
          } else {
            setAttachments((prev) => [...prev, f]);
          }
        }
      }
    } catch (err: any) {
      console.error('[AIChatPage] Drop upload failed:', err);
    } finally {
      setIsUploading(false);
    }
  }, [agentId, isFileUploadDrag, resolveComposerApi]);

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

  // ---- Render ----
  return (
    <div
      className="flex-1 flex flex-col h-full w-full bg-stage overflow-hidden relative"
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
        <div className="absolute inset-0 z-40 flex items-center justify-center bg-stage/80 backdrop-blur-sm">
          <div className="flex flex-col items-center gap-3 p-6 bg-panel border border-border rounded-2xl shadow-xl">
            <OpenSquadLoader size={56} />
            <p className="text-base font-medium text-textMain">{t('chat.agentStarting')}</p>
            <p className="text-xs text-textMuted">{t('chat.agentStartingHint')}</p>
          </div>
        </div>
      )}

      {/* Tools still warming up after chat-ready: agent is usable, MCP/plugins loading */}
      {toolsStage === 'loading' && (
        <div className="flex items-center justify-center gap-2 px-3 py-1.5 text-xs text-yellow-600 bg-yellow-500/10 border-b border-yellow-500/20">
          <OpenSquadLoader size={14} />
          <span>工具加载中，可先开始对话（部分扩展工具就绪后自动启用）</span>
        </div>
      )}

      {/* Three columns from top: session | center chrome+content | files
          Compact/mobile: side rails overlay so the chat column stays full-width. */}
      <div className="relative flex-1 flex min-h-0 overflow-hidden p-1.5 sm:p-2 bg-stage max-md:pb-[max(0.375rem,env(safe-area-inset-bottom))]">
      {isCompactLayout && (sessionSidebarOpen || filesPanelOpen) ? (
        <button
          type="button"
          aria-label="关闭侧栏"
          className="absolute inset-0 z-40 bg-black/35 border-0 cursor-pointer"
          onClick={closeCompactOverlays}
        />
      ) : null}

      {/* Session Sidebar — desktop: in-flow rail; mobile: overlay drawer */}
      <div
        className={
          isCompactLayout
            ? `absolute inset-y-1.5 left-1.5 z-50 max-w-[min(100%-0.75rem,20rem)] ${
                sessionSidebarOpen ? '' : 'pointer-events-none'
              }`
            : 'relative z-0 h-full flex-shrink-0'
        }
      >
      <SessionSidebar
        agentId={agentId}
        currentSessionId={sidebarSelectedSessionId}
        workspaceRootPath={activeWorkspace?.rootPath || null}
        workspaceId={activeWorkspace?.id || null}
        onViewSession={handleSidebarViewSession}
        onNewSession={handleNewSessionInWorkspace}
        onSwitchAndReply={handleSwitchAndReply}
        onDeleteSession={handleDeleteSession}
        onOpenSkills={handleOpenSkills}
        onOpenPlugins={handleOpenPlugins}
        onOpenRoles={handleOpenRoles}
        onOpenScheduledTasks={handleOpenScheduledTasks}
        onOpenSearch={openSessionSearch}
        skillsActive={libraryView === 'skills'}
        pluginsActive={libraryView === 'plugins'}
        rolesActive={libraryView === 'roles'}
        isOpen={sessionSidebarOpen}
        sessionTitleUpdate={sessionTitleUpdate}
        agentBusy={
          isStreaming ||
          agentStatus === 'working' ||
          agentStatus === 'thinking' ||
          (!!currentSessionId && busySessions.includes(currentSessionId))
        }
        busySessionIds={busySessions}
        unseenCompleteSessionIds={unseenCompleteSessionIds}
        primarySessionId={primarySessionId}
        pendingPrimarySessionId={pendingPrimarySessionId}
        onSetPrimarySession={(sid) => {
          setPendingPrimarySessionId(sid);
          pendingPrimarySessionIdRef.current = sid;
          wsServiceRef.current?.setPrimarySession(sid);
        }}
        onSessionsChange={handleSessionsChange}
        uiMode={uiMode}
        onUiModeChange={setUiModePersisted}
        currentUser={currentUser}
        onOpenProfile={onOpenProfile}
        onOpenSettings={onOpenSettings}
      />
      </div>

      <SessionSearchModal
        open={sessionSearchOpen && !!activeWorkspace}
        agentId={agentId}
        sessions={sidebarSessions}
        workspaceRootPath={activeWorkspace?.rootPath || null}
        onCancel={closeSessionSearch}
        onPick={(sid) => {
          closeSessionSearch();
          handleSidebarViewSession(sid);
        }}
        onNewSession={() => {
          if (activeWorkspace?.rootPath) handleNewSessionInWorkspace(activeWorkspace.rootPath);
        }}
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

      {libraryView === 'skills' || libraryView === 'plugins' || libraryView === 'roles' ? (
        <div className="flex-1 min-w-0 min-h-0 overflow-hidden flex flex-col os-depth-panel relative z-0">
          <Suspense
            fallback={
              <div className="flex-1 flex items-center justify-center text-[12px] text-textMuted">
                <OpenSquadLoader size={32} />
              </div>
            }
          >
            {libraryView === 'skills' ? (
              <SkillManagerPage
                embedded
                initialAgentId={agentProfile?.dir_name || agentId}
                onBack={() => setLibraryView(null)}
              />
            ) : libraryView === 'plugins' ? (
              <PluginManagerPage
                embedded
                initialAgentId={agentProfile?.dir_name || agentId}
                onBack={() => setLibraryView(null)}
              />
            ) : (
              <RolesPage
                embedded
                onBack={() => setLibraryView(null)}
              />
            )}
          </Suspense>
        </div>
      ) : (
      <>
      <div className="flex-1 min-w-0 min-h-0 overflow-hidden flex flex-col os-depth-panel relative z-0">
        {/* L1 nest chrome — active workspace tab is panel and joins L2 with no seam */}
        <div className="flex-shrink-0 bg-nest">
          <div className="h-11 px-2 sm:px-2.5 box-border flex items-end gap-1.5 sm:gap-2 min-w-0 pb-0">
            <div className="flex h-8 items-center gap-1 sm:gap-1.5 min-w-0 shrink-0">
              <button
                type="button"
                onClick={toggleSessionSidebar}
                className="p-1 sm:p-1.5 hover:bg-primary/10 rounded-lg transition-colors flex-shrink-0"
                title={sessionSidebarOpen ? 'Close sessions' : 'Open sessions'}
              >
                {sessionSidebarOpen ? (
                  <PanelLeftClose size={16} className="text-textMuted" />
                ) : (
                  <PanelLeftOpen size={16} className="text-textMuted" />
                )}
              </button>
              <div className="flex min-w-0 max-w-[120px] sm:max-w-[180px] items-center gap-1.5">
                <StatusBadge status={agentStatus} />
                <h2 className="min-w-0 truncate text-sm font-bold leading-none text-textMain">
                  {agentProfile?.agent_name || modelName || agentId}
                  {switchingModel ? (
                    <span className="ml-1 text-[10px] font-normal text-textMuted animate-pulse">
                      switching…
                    </span>
                  ) : null}
                </h2>
              </div>
            </div>

            <div className="flex-1 min-w-0 overflow-visible self-stretch flex items-end">
              <WorkspaceTabBar
                workspaces={wsSnap.workspaces}
                openIds={wsSnap.chrome.openWorkspaceIds}
                activeId={wsSnap.chrome.activeWorkspaceId}
                onSelect={handleSelectWorkspace}
                onRequestClose={(id) => {
                  const ws = wsSnap.workspaces.find((w) => w.id === id);
                  if (ws) setCloseWorkspaceTarget(ws);
                }}
                onOpenExisting={handleOpenExistingWorkspace}
                onCreateNew={() => setCreateWorkspaceOpen(true)}
              />
            </div>

            <div className="flex h-8 items-center gap-0.5 sm:gap-1 shrink-0">
              <button
                type="button"
                onClick={toggleFilesPanel}
                className={`p-1 sm:p-1.5 rounded-lg transition-colors flex-shrink-0 ${
                  filesPanelOpen ? 'bg-primary/15 hover:bg-primary/20' : 'hover:bg-primary/10'
                }`}
                title={filesPanelOpen ? 'Hide project files' : 'Show project files'}
              >
                {filesPanelOpen ? (
                  <PanelRightClose size={16} className="text-primary" />
                ) : (
                  <PanelRightOpen size={16} className="text-textMuted" />
                )}
              </button>
            </div>
          </div>
        </div>

      <div className="os-depth-body flex-1 min-h-0 flex flex-col">
      {activeWorkspace && workspaceLayout ? (
        <>
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

        <PaneSplitLayout
          layout={workspaceLayout}
          focusedPaneId={focusedPaneId}
          liveSessionId={currentSessionId}
          agentId={agentProfile?.dir_name || agentId}
          sessionAgentId={agentId}
          rootPath={activeWorkspace.rootPath}
          tabTitles={tabSessionTitles}
          fileDirtyMap={fileDirtyMap}
          onResizeSplit={handleResizeSplit}
          handlers={{ makePaneHandlers }}
          renderChatSlot={() => (
      /* Main Chat Area — live messages only (agent chrome is above the split) */
      <div className="flex-1 flex flex-col h-full min-w-0">
        {/* Messages Area */}
        <div className="flex-1 relative min-h-0" style={{ minHeight: 0 }}>
        {/* Session loading overlay — panel-level (outside the scroll container)
            so it never overlaps/overlays timeline messages while loading a
            legacy full-pane history. z-40 > jump rail (z-30) and messages. */}
        {isLoadingSession && (
          <div className="absolute inset-0 z-40 flex flex-col items-center justify-center bg-panel/95 backdrop-blur-[1px] text-textMuted pointer-events-none">
            <OpenSquadLoader size={44} className="mb-3" />
            <p className="text-sm">{sessionLoadingLabel}</p>
          </div>
        )}
        {/* User-turn jump rail on panel far-right (outside padded scroll / max-w column) */}
        {soloUserNavNodes.length > 0 && (
          <div className="pointer-events-none absolute inset-y-0 right-0 z-30 flex items-center justify-end pr-1 overflow-visible">
            <div className="pointer-events-auto overflow-visible">
              <SoloUserNavRail
                nodes={soloUserNavNodes}
                activeId={soloUserNavNodes[soloUserNavNodes.length - 1]?.id}
                onJump={jumpToSoloUserMessage}
              />
            </div>
          </div>
        )}
        {/* Scroll buttons on outer panel edge */}
        {(showScrollTop || showScrollBottom) && (
          <div
            className="pointer-events-none absolute right-1 bottom-4 z-20 transition-opacity duration-300"
            style={{ opacity: scrollActive ? 1 : 0, pointerEvents: scrollActive ? undefined : 'none' }}
          >
            <div className="pointer-events-auto flex flex-col gap-2">
              {showScrollTop && (
                <button
                  onClick={scrollToTop}
                  className="w-8 h-8 bg-panel border border-border/70 rounded-full shadow-md flex items-center justify-center text-textMuted hover:text-primary hover:bg-primary/10 transition-colors"
                  title="Scroll to top"
                >
                  <ChevronUp size={18} />
                </button>
              )}
              {showScrollBottom && (
                <button
                  onClick={scrollToBottom}
                  className="w-8 h-8 bg-panel border border-border/70 rounded-full shadow-md flex items-center justify-center text-textMuted hover:text-primary hover:bg-primary/10 transition-colors"
                  title="Scroll to bottom"
                >
                  <ChevronDown size={18} />
                </button>
              )}
            </div>
          </div>
        )}
        <ChatTimeline
          scrollRef={messagesContainerRef}
          entries={displayTimeline}
          className="h-full overflow-y-auto px-2 sm:px-4 py-3 sm:py-4 relative"
          style={{ minHeight: 0 }}
          onScroll={handleMessagesScroll}
          columnClass={soloColumnClass}
          header={isLoadingMore ? (
            <div className="flex items-center justify-center py-3">
              <OpenSquadLoader size={18} className="mr-2" />
              <span className="text-xs text-textMuted">Loading earlier messages...</span>
            </div>
          ) : null}
          footer={(
            <>
          {(displayStreamingText || isStreaming) && (
            <StreamingMessage
              content={displayStreamingText}
              isComplete={!isStreaming}
              avatarSrc={resolveChatAvatar(agentProfile?.chat_profile) ?? undefined}
              variant={isSolo ? 'solo' : 'classic'}
              senderName={agentProfile?.agent_name}
            />
          )}
          <div ref={chatEndRef} />
            </>
          )}
          renderEntry={(entry, i, entryKey) => {
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
                agentId,
                canWithdraw:
                  entry.data.role === 'user' &&
                  !changesBusy,
                onWithdraw:
                  entry.data.role === 'user'
                    ? () => requestWithdrawUserMessage(entryKey, entry.data, currentSessionId || undefined)
                    : undefined,
              };
              // Classic: visualization iframes sit below the final assistant reply
              // (tool stream keeps the normal tool_call row only).
              const replyEmbeds: HtmlEmbedPayload[] =
                !isSolo && entry.data.role === 'assistant'
                  ? collectHtmlEmbedsPrecedingMessage(displayTimeline, i)
                  : [];
              if (replyEmbeds.length === 0) {
                return (
                  <TimelineRow key={entryKey}>
                    {isSolo
                      ? <SoloMessage {...msgProps} anchorId={entryKey} />
                      : <MessageBubble {...msgProps} anchorId={entryKey} />}
                  </TimelineRow>
                );
              }
              return (
                <TimelineRow key={entryKey}>
                  {isSolo
                    ? <SoloMessage {...msgProps} anchorId={entryKey} />
                    : <MessageBubble {...msgProps} anchorId={entryKey} />}
                  <div className="w-full mt-1 mb-4" data-html-embeds-below-reply="1">
                    {replyEmbeds.map((payload, ei) => (
                      <HtmlEmbedBlock
                        key={payload.id || payload.filename || `viz-${ei}`}
                        payload={payload}
                        variant="seamless"
                        className="my-0"
                      />
                    ))}
                  </div>
                </TimelineRow>
              );
            }
            if (entry.kind === 'workflow') {
              const lastIncompleteIdx = (() => {
                for (let j = displayTimeline.length - 1; j >= 0; j--) {
                  if (displayTimeline[j].kind === 'workflow' && !(displayTimeline[j] as { kind: 'workflow'; data: WorkflowBlock }).data.completed) return j;
                }
                return -1;
              })();
              // Classic + Solo: document-style activity rows (thinking / tools)
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
              const turnMs = groupHasIncomplete
                ? turnStartedMs
                : (!isSolo && i === lastIncompleteIdx ? turnStartedMs : undefined);
              return (
                <TimelineRow key={entryKey}>
                  <SoloActivityRow
                    block={merged}
                    expandLevel={workflowExpandLevel}
                    turnStartedMs={turnMs}
                    shellStreams={shellStreams}
                    onOpenFile={openProjectFile}
                    embedVisualizations={false}
                    uiMode={uiMode}
                  />
                </TimelineRow>
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
                <div key={entryKey} className="flex items-center gap-1.5 py-0.5 my-0.5 mx-0">
                  <div className="flex-1 h-px bg-border/25" />
                  {icon}
                  <span className="text-[10px] text-textMuted/45 font-mono shrink-0">{label}</span>
                  <div className="flex-1 h-px bg-border/25" />
                </div>
              );
            }
            if (entry.kind === 'task_fold') {
              const fold = entry.data;
              return (
                <TimelineRow key={entryKey}>
                <TaskFoldBlock
                  title={fold.title}
                  messageCount={fold.messageCount}
                  eventCount={fold.eventCount}
                  defaultCollapsed={fold.collapsed !== false}
                  isSolo={isSolo}
                >
                  {fold.entries.map((nested, ni) => {
                    const nestedKey = nested._uid || `${entryKey}-n${ni}`;
                    if (nested.kind === 'message') {
                      const msgProps = {
                        message: nested.data,
                        senderName:
                          nested.data.role === 'user'
                            ? (currentUser?.name || undefined)
                            : (agentProfile?.agent_name || undefined),
                        senderAvatar:
                          nested.data.role === 'user'
                            ? (currentUser?.avatar || null)
                            : (resolveChatAvatar(agentProfile?.chat_profile) || null),
                        agentId,
                        canWithdraw:
                          nested.data.role === 'user' &&
                          !changesBusy,
                        onWithdraw:
                          nested.data.role === 'user'
                            ? () => requestWithdrawUserMessage(nestedKey, nested.data, currentSessionId || undefined)
                            : undefined,
                      };
                      const replyEmbeds: HtmlEmbedPayload[] =
                        !isSolo && nested.data.role === 'assistant'
                          ? collectHtmlEmbedsPrecedingMessage(fold.entries, ni)
                          : [];
                      const bubble = isSolo
                        ? <SoloMessage key={nestedKey} {...msgProps} anchorId={nestedKey} />
                        : <MessageBubble key={nestedKey} {...msgProps} anchorId={nestedKey} />;
                      if (replyEmbeds.length === 0) return bubble;
                      return (
                        <React.Fragment key={nestedKey}>
                          {isSolo
                            ? <SoloMessage {...msgProps} anchorId={nestedKey} />
                            : <MessageBubble {...msgProps} anchorId={nestedKey} />}
                          <div className="w-full mt-1 mb-4" data-html-embeds-below-reply="1">
                            {replyEmbeds.map((payload, ei) => (
                              <HtmlEmbedBlock
                                key={payload.id || payload.filename || `viz-${ei}`}
                                payload={payload}
                                variant="seamless"
                                className="my-0"
                              />
                            ))}
                          </div>
                        </React.Fragment>
                      );
                    }
                    if (nested.kind === 'workflow') {
                      return (
                        <SoloActivityRow
                          key={nestedKey}
                          block={nested.data}
                          expandLevel={workflowExpandLevel}
                          turnStartedMs={undefined}
                          shellStreams={shellStreams}
                          onOpenFile={openProjectFile}
                          embedVisualizations={false}
                          uiMode={uiMode}
                        />
                      );
                    }
                    if (nested.kind === 'status_hint') {
                      const hint = nested.data;
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
                        <div key={nestedKey} className="flex items-center gap-1.5 py-0.5 my-0.5">
                          <div className="flex-1 h-px bg-border/25" />
                          {icon}
                          <span className="text-[10px] text-textMuted/45 font-mono shrink-0">{label}</span>
                          <div className="flex-1 h-px bg-border/25" />
                        </div>
                      );
                    }
                    return null;
                  })}
                </TaskFoldBlock>
                </TimelineRow>
              );
            }
            if (entry.kind === 'archived_section') {
              // Flattened by displayTimeline — should never reach here.
              return null;
            }
            return null;
          }}
        />
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
                       {att.type === 'voice' || att.is_audio ? 'VOICE' : 'FILE'} • {_formatFileSize(att.size)}
                       {typeof att.duration === 'number' && att.duration > 0 ? ` · ${att.duration}s` : ''}
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
                <OpenSquadLoader size={16} />
                <span className="text-xs text-textMuted">Uploading...</span>
              </div>
            )}
            </div>
          </div>
        )}

        {/* Classic only: scroll-to-bottom centered above composer */}
        {!isSolo && showScrollBottom && (
          <div className="relative flex-shrink-0 z-20 pointer-events-none h-0">
            <div className={`${soloColumnClass} relative`}>
              <button
                type="button"
                onClick={scrollToBottom}
                className="pointer-events-auto absolute left-1/2 -translate-x-1/2 -top-10 w-8 h-8 rounded-full bg-panel border border-border/70 shadow-[0_2px_10px_rgba(0,0,0,0.08)] flex items-center justify-center text-textMuted hover:text-primary hover:bg-primary/10 transition-opacity duration-300 cursor-pointer"
                style={{ opacity: scrollActive ? 1 : 0.55 }}
                title="滚动到底部"
              >
                <ChevronDown size={18} className="text-gray-500" />
              </button>
            </div>
          </div>
        )}

      </div>
          )}
        />
        </>
      ) : (
        <div className="flex-1 min-w-0 flex items-center justify-center text-[12px] text-textMuted px-4 text-center">
          打开或创建一个工作区以开始
        </div>
      )}
      </div>
      </div>

      <div
        className={
          isCompactLayout
            ? `absolute inset-y-1.5 right-1.5 z-50 max-w-[min(100%-0.75rem,22rem)] ${
                filesPanelOpen ? '' : 'pointer-events-none'
              }`
            : 'relative z-0 flex-shrink-0 h-full flex'
        }
      >
      <ProjectFilesPanel
        isOpen={filesPanelOpen}
        onClose={() => {
          setFilesPanelOpen(false);
          try {
            localStorage.setItem('opensquad.filesPanel.open', 'false');
          } catch {
            /* ignore */
          }
        }}
        agentId={agentProfile?.dir_name || agentId}
        rootPath={(activeWorkspace?.rootPath || agentCwd || defaultCwd || '').trim()}
        openRequest={fileOpenRequest}
        width={isCompactLayout ? Math.min(filesPanelWidth, isMobileViewport ? 300 : 340) : filesPanelWidth}
        onWidthChange={(w) => {
          if (isCompactLayout) return;
          setFilesPanelWidth(w);
          try {
            localStorage.setItem('opensquad.filesPanel.width', String(w));
          } catch {
            /* ignore */
          }
        }}
        focusChangedNonce={focusChangedNonce}
        liveChanges={filesLiveChanges}
        onSessionChanges={onSessionChangesStable}
        treeOnly
        onOpenFile={handleOpenFileInTab}
      />
      </div>
      </>
      )}
      </div>

      <RestoreCheckpointModal
        open={!!restoreConfirm}
        busy={changesBusy}
        onCancel={() => {
          if (!changesBusy) setRestoreConfirm(null);
        }}
        onConfirm={handleWithdrawUserMessage}
      />

      <CloseWorkspaceModal
        open={!!closeWorkspaceTarget}
        workspaceName={
          closeWorkspaceTarget ? workspaceDisplayName(closeWorkspaceTarget) : ''
        }
        onCancel={() => setCloseWorkspaceTarget(null)}
        onConfirm={handleConfirmCloseWorkspace}
      />

      <CreateWorkspaceModal
        open={createWorkspaceOpen}
        onCancel={() => setCreateWorkspaceOpen(false)}
        onCreate={handleCreateWorkspace}
      />
    </div>
  );
};
