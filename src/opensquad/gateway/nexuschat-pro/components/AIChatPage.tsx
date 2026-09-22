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
  Moon, Zap, Bell,
  RefreshCw,
  Clock,
  Reply, Pencil, Trash2,
} from 'lucide-react';

import { useTranslation } from 'react-i18next';
import { getAiWsService, AIWebSocketStatus } from '../services/aiWebSocket';
import { agentSessionAPI, authAPI, adminAPI, AdminAgent, modelCardAPI, ModelCardInfo, skillAPI, SkillInfo } from '../services/api';
import type { AgentSession } from '../services/api';
import { resolveChatAvatar, toAbsoluteMediaUrl } from '../utils/image';
import { OpenSquadLoader } from './OpenSquadLoader';
import {
  appendWorkflowEvent,
  composeAssistantDisplayContent,
  buildTimelineFromSession,
  demoteIntermediateAssistantMessages,
  dropEntriesAlreadyPresent,
  formatUserSkillDisplayContent,
  genTimelineUID,
  mergeAdjacentWorkflowEntries,
  sessionMessageIdentity,
  timelineHasVisibleChatContent,
  sealIncompleteWorkflows,
  sealPendingCompression,
  sealWorkflowAndAppendAssistantMessage,
  toWebMediaUrl,
  type TimelineEntry,
  type WorkflowBlock,
  type WorkflowEvent,
} from '../utils/aiChatTimeline';
import { pushCwdRecent } from '../utils/cwdRecents';
import {
  clearComposerDraft,
  getComposerDraft,
  setComposerDraft,
} from '../utils/composerDraftStore';
import {
  putCachedSessionTimeline,
  getCachedSessionTimeline,
  getCachedSessionTimelineMeta,
  SESSION_HISTORY_PAGE_SIZE,
} from '../utils/sessionTimelineCache';
import { pickSessionLiveTimeline } from '../utils/sessionLiveTimeline';
import {
  mergeSessionTokenStats,
} from '../utils/sessionTokenStats';
import { useTextSelectionFreeze } from '../hooks/useTextSelectionFreeze';
import { useIsCompactAgentWeb, useIsMobileViewport } from '../hooks/useMatchMedia';
import { useAgentWebVoice } from '../hooks/useAgentWebVoice';
import { useAgentWebSocket } from '../hooks/useAgentWebSocket';
import {
  flattenArchivedSections,
  logMediaDebug,
  type UploadedFile,
} from '../utils/agentWebChatHelpers';
import { loadLastModelPick, saveLastModelPick } from '../utils/agentWebModelPick';
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
  ensureActiveWorkspaceFromRoot,
  resolveSessionWorkspaceId,
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
// Cross-surface event names — shared with the panels that ask for a tab.
import { OPEN_SESSION_TAB_EVENT } from '../utils/uiEvents';

// AI Chat sub-components
import { MessageBubble, ChatMessage, FileAttachment } from './ai-chat/MessageBubble';
import { StreamingMessage } from './ai-chat/StreamingMessage';
import { SoloMessage } from './ai-chat/SoloMessage';
import { SoloActivityRow, mergeWorkflowBlocks } from './ai-chat/SoloActivityRow';
import {
  indexHtmlEmbedsByAssistantMessage,
  HtmlEmbedBlock,
  type HtmlEmbedPayload,
} from './ai-chat/HtmlEmbedBlock';
import { ProjectFilesPanel, type ProjectFileOpenRequest } from './ai-chat/ProjectFilesPanel';
import { TurnChangedFilesCard, collectTurnChangedFilesBefore } from './ai-chat/TurnChangedFilesCard';
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
import { ShellTerminalsBar } from './ai-chat/ShellTerminalsBar';
import { useWorkflowExpandLevel } from '../utils/workflowExpandPref';
import { CHAT_DOCUMENT_COLUMN_CLASS } from '../utils/chatLayout';
import {
  SoloUserNavRail,
  buildUserNavNodesFromTimeline,
  userNavAnchorDomId,
} from './ai-chat/SoloUserNavRail';
import { TaskFoldBlock } from './ai-chat/TaskFoldBlock';
import { TimelineRow } from './ai-chat/TimelineRow';
import { ChatTimeline } from './ai-chat/ChatTimeline';
import { ChatScrollComposerHint, ChatScrollHud } from './ai-chat/ChatScrollHud';
import { SoloModelPicker } from './ai-chat/SoloModelPicker';
import { EffortPicker, type ReasoningEffort } from './ai-chat/EffortPicker';
import { ModePicker, type AgentMode } from './ai-chat/ModePicker';
import { ModeSwitchApprovalCard, type ModeSwitchApproval } from './ai-chat/ModeSwitchApprovalCard';
import { FollowupSuggestions, type FollowupSuggestion } from './ai-chat/FollowupSuggestions';
import { OptionsApprovalCard, type OptionsProposal } from './ai-chat/OptionsApprovalCard';
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
  rebuildShellStreamsFromTimeline,
  collectRunningShellJobs,
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

  const wsServiceRef = useRef<ReturnType<typeof getAiWsService> | null>(null);
  const [agentProfile, setAgentProfile] = useState<AdminAgent | null>(null);
  const {
    autoSpeechEnabled,
    autoSpeechEnabledRef,
    toggleAutoSpeech,
    stopAutoTts,
    feedAutoTtsFromStreamRef,
    speakFinalReplyRef,
    lastAutoSpokenRef,
    voicePanelOpen,
    setVoicePanelOpen,
    voiceRealtimeStatus,
    setVoiceRealtimeStatus,
    voiceTranscript,
    setVoiceTranscript,
    voiceRealtimeError,
    setVoiceRealtimeError,
    voiceBindings,
    setVoiceBindings,
    voiceCaptionRef,
    voiceResumeProbeRef,
    voicePageHideRef,
    voiceRealtimeStatusRef,
    clearVoiceConnectTimer,
    armVoiceConnectTimeout,
    handleVoiceBindingsChange,
    handleVoiceRealtimeStart,
    handleVoiceRealtimeStop,
    handleVoiceAudioChunk,
    handleMouthpieceUtterance,
    handleForceAskAgentChange,
    unlockAutoTtsAudio,
  } = useAgentWebVoice({
    agentId,
    agentDirName: agentProfile?.dir_name,
    wsServiceRef,
  });
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
    const snap = loadWorkspaceStore(agentId);
    const wsId = snap.chrome.activeWorkspaceId;
    const ws = wsId ? snap.workspaces.find((w) => w.id === wsId) : null;
    if (wsId && ws) {
      // Workspace exists → show the file ONCE, as a workspace content tab.
      // (Opening the files-panel preview here too would render the same file twice.)
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
    } else {
      // No active workspace → fall back to the files panel preview.
      setFileOpenRequest({ path: p, nonce: Date.now() });
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
  const finalizingBySidRef = useRef<Record<string, boolean>>({});
  /** After user hits Stop on a pane, ignore late stream/tool/thought for THAT sid. */
  const userStoppedBySidRef = useRef<Record<string, boolean>>({});
  const eventSidKey = (explicit?: string | null) =>
    String(explicit || eventSidRef.current || '').trim();
  const isSidStopped = (sid?: string | null) => {
    const key = eventSidKey(sid);
    return !!(key && userStoppedBySidRef.current[key]);
  };
  const isSidFinalizing = (sid?: string | null) => {
    const key = eventSidKey(sid);
    return !!(key && finalizingBySidRef.current[key]);
  };
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
  const displayStreamingText = composeAssistantDisplayContent(selectFrozenView.streaming || '').trim();

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

  // Agent chat profile (avatar + name) — agentProfile state is declared with voice hook
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
  /** Agent-offered follow-up chips (suggest_followups tool) — cleared on the next user turn. */
  const [followupSuggestions, setFollowupSuggestions] = useState<FollowupSuggestion[]>([]);
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
  // 上下文详情面板跟随「发起查看的那条会话」。tab 模式下切换会话标签只更新
  // pane/tab 状态、刻意不回写 currentSessionId（见 handleContentTabSelect——
  // 回写会重挂载 chatSlot / 触发全局加载），重启后 currentSessionId 停在启动
  // 会话上，于是无论看哪个标签，详情都显示同一次会话。null = 跟随焦点会话。
  const [contextViewerSessionId, setContextViewerSessionId] = useState<string | null>(null);
  const [isCompressingContext, setIsCompressingContext] = useState(false);

  // Lazy loading state
  const [hasMoreHistory, setHasMoreHistory] = useState(false);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const historyOffsetRef = useRef(0);        // how many messages already loaded (from the end)
  /**
   * Identity of the oldest message currently loaded FROM THE PAGED ENDPOINT.
   * `historyOffsetRef` counts from the tail, so it silently re-aims backwards
   * every time the live turn appends messages — the next page then overlaps
   * what is already painted and the user's own bubbles render twice. This
   * anchor pins the window to a message instead, so pages can never overlap.
   * Kept separate from the timeline head: the head may be archived content,
   * which the paged endpoint's `messages` array does not contain.
   */
  const pagedAnchorIdRef = useRef<string | null>(null);
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

  const userScrolledRef = useRef(false); // true when user manually scrolled away from bottom

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
    /** 引导注入：已发往后端注入队列，等当前工具轮结束后的下一轮进模型上下文。 */
    steered?: boolean;
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
      if (key) delete finalizingBySidRef.current[key];
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
    // 引导消息随回合结束兜底清理：后端要么已注入、要么把残留插话开成了新
    // 回合；消费回执（steer_consumed）丢失时避免条目滞留在引导队列里。
    setPendingMessages((prevQ) => {
      const nextQ = prevQ.filter(
        (m) => !(m.steered && m.sessionId && finished.includes(m.sessionId)),
      );
      return nextQ.length === prevQ.length ? prevQ : nextQ;
    });
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
  /** Background terminals still running across the visible timeline (composer-top bar). */
  const runningShellJobs = useMemo(
    () => collectRunningShellJobs(displayTimeline, shellStreams),
    [displayTimeline, shellStreams],
  );

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
  const htmlEmbedsByAssistantIndex = useMemo(
    () => (isSolo ? null : indexHtmlEmbedsByAssistantMessage(displayTimeline)),
    [isSolo, displayTimeline],
  );
  const setUiModePersisted = useCallback((mode: AiChatUiMode) => {
    setUiMode(mode);
    try { localStorage.setItem('ai_chat_ui_mode', mode); } catch {}
    void import('../utils/hostUiPrefs').then((m) => m.schedulePushHostUiPrefs()).catch(() => undefined);
  }, []);

  // Document column for both classic + solo (classic: user bubble + agent doc stream)
  const soloColumnClass = CHAT_DOCUMENT_COLUMN_CLASS;

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
  // 抽成独立函数：详情面板可能查看「非焦点会话」（tab 模式），solo timeline
  // 与 per-session bucket 必须走同一条映射路径，避免两份实现漂移。
  const flattenTimelineToContextEntries = useCallback(
    (source: TimelineEntry[], sid: string | null): ContextEntry[] => {
      const result: ContextEntry[] = [];
      let msgIdx = 0, evtIdx = 0, promptIdx = 0;
      for (const entry of source) {
        if (entry.kind === 'message') {
          result.push({
            id: `${sid || 'local'}-msg-${msgIdx++}`,
            kind: entry.data.role as 'user' | 'assistant',
            content: entry.data.content,
            timestamp: entry.data.timestamp,
            elapsed_ms: (entry.data as any).elapsed_ms,
          });
        } else if (entry.kind === 'workflow') {
          for (const evt of entry.data.events) {
            result.push({
              id: `${sid || 'local'}-evt-${evtIdx++}`,
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
            id: `${sid || 'local'}-prompt-${promptIdx++}`,
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
            id: `${sid || 'local'}-hint-${evtIdx++}`,
            kind: hintKind,
            content: label,
            timestamp: new Date(hint.timestamp).toISOString(),
          });
        }
      }
      return result;
    },
    [t],
  );

  const contextEntries = useMemo(
    () => flattenTimelineToContextEntries(timeline, currentSessionId),
    [flattenTimelineToContextEntries, timeline, currentSessionId],
  );

  // 详情面板的数据源跟随 contextViewerSessionId，而不是焦点会话：非焦点会话
  // 按 tokenStats 同一套优先级解析（live bucket → 磁盘缓存），保证面板里的
  // 「上下文构成」与「原始消息」永远来自同一条会话。
  const contextViewerEntries = useMemo((): ContextEntry[] => {
    const sid = contextViewerSessionId;
    if (!sid || sid === currentSessionId) return contextEntries;
    const live = pickSessionLiveTimeline(liveTimelinesBySession, sid);
    const source = (live && live.length > 0 ? live : null)
      ?? getCachedSessionTimeline(agentId, sid)
      ?? [];
    return flattenTimelineToContextEntries(source, sid);
  }, [
    contextViewerSessionId,
    currentSessionId,
    contextEntries,
    liveTimelinesBySession,
    agentId,
    flattenTimelineToContextEntries,
  ]);

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
  const markUnpinnedFromBottom = useCallback((away: boolean) => {
    userScrolledRef.current = away;
  }, []);

  // Load earlier messages (prepend to timeline)
  const loadMoreHistory = useCallback(async () => {
    const sid = loadingSessionIdRef.current;
    if (!sid || isLoadingMore || !hasMoreHistory) return;

    setIsLoadingMore(true);
    const el = messagesContainerRef.current;
    const prevScrollHeight = el ? el.scrollHeight : 0;
    // Snapshot BEFORE this page is counted; the state updater below runs after
    // the synchronous body, so reading the ref there would double-count.
    const loadedBeforePage = historyOffsetRef.current;

    try {
      const resp = await agentSessionAPI.getSessionHistoryPaged(
        agentId, sid, historyOffsetRef.current, 50,
        pagedAnchorIdRef.current || undefined,
      );
      const session = resp.session;
      if (session && (session.messages?.length > 0 || session.events?.length > 0)) {
        const olderEntries = buildTimelineFromSession(
          session.messages || [],
          session.events || [],
        );
        // The anchor of the NEXT page is this page's oldest message — it is
        // what "strictly older than" must mean, regardless of how much the
        // live turn has appended in the meantime.
        pagedAnchorIdRef.current =
          sessionMessageIdentity((session.messages || [])[0]) || pagedAnchorIdRef.current;
        if (olderEntries.length > 0) {
          setTimeline(prev => {
            // Cross-seam renormalization: each page is rebuilt independently,
            // so a turn split across the page boundary ends up as "tool folds
            // stacked above, text bubbles below" (page-local demote cannot see
            // the workflows of the other page). Re-run demote + merge over the
            // combined array so the seam stitches back into one interleaved
            // turn.
            //
            // `dropEntriesAlreadyPresent` first: prepending is the only path
            // that concatenates two independently deduped arrays, so it is the
            // only place a message already on screen can slip back in.
            const next = mergeAdjacentWorkflowEntries(
              demoteIntermediateAssistantMessages([
                ...dropEntriesAlreadyPresent(olderEntries, prev),
                ...prev,
              ]),
            );
            putCachedSessionTimeline(agentId, sid, next, {
              complete: !(session.has_more ?? false),
              // NOTE: computed from the ref's pre-increment value below —
              // reading it here would already include this page (React runs
              // updaters after the synchronous body), inflating the count and
              // desyncing `historyOffsetRef` on the next cache restore.
              messageCount: loadedBeforePage,
              totalMessages: session.total_messages,
              oldestMessageId: pagedAnchorIdRef.current || undefined,
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

  // Stick-to-bottom is owned by ChatTimeline (unpinRef + overflow-anchor: none).

  // Timeline helpers live in utils/aiChatTimeline.ts


  /**
   * Mark the last workflow block as completed, then append the
   * assistant's final message.
   */
  function finalizeWorkflowAndAddMessage(
    prev: TimelineEntry[],
    msg: ChatMessage,
  ): TimelineEntry[] {
    return sealWorkflowAndAppendAssistantMessage(prev, msg);
  }

  useAgentWebSocket(agentId, {
    SUMMARY_STREAM_DEBUG,
    agentCurrentSessionIdRef,
    agentStatus,
    applyTokenStats,
    busySessionsRef,
    cancelStreamFlush,
    clearOutboundTurnPending,
    clearSessionRunState,
    compressionHydrationPendingRef,
    currentCardName,
    currentSessionId,
    currentSessionIdRef,
    diskSessionLoadedRef,
    eventSidKey,
    eventSidRef,
    filePushDedupRef,
    finalizeWorkflowAndAddMessage,
    finalizingBySidRef,
    historyOffsetRef,
    hydrateCurrentSessionRef,
    isHydratingSessionRef,
    isSidFinalizing,
    isSidStopped,
    isStreamingBySessionRef,
    loadMoreHistory,
    loadingSessionIdRef,
    modelSwitchRevertRef,
    newSessionGuardRef,
    newSessionPendingRef,
    pageActiveRef,
    pendingFilePushesRef,
    pendingHydrationFinalsRef,
    pendingHydrationMediaRef,
    pendingHydrationWorkflowEventsRef,
    pendingPrimarySessionIdRef,
    pendingSessionTitleRef,
    pinComposerLanding,
    reasoningEffort,
    refreshSessionChangesRef,
    scheduleRefreshSessionChanges,
    scheduleStreamFlush,
    sessionBootstrapDoneRef,
    sessionBootstrapped,
    sessionReloadSeqRef,
    sessionReloadTimerRef,
    setActiveGoal,
    setAgentMode,
    setAgentModeBySession,
    setAgentStatus,
    setBusySessions,
    setCardNameBySession,
    setCurrentCardName,
    setCurrentSessionId,
    setFollowupSuggestions,
    setHasMoreHistory,
    setIsCompressingContext,
    setIsLoadingSession,
    setIsStreaming,
    setIsStreamingBySession,
    setModeApprovals,
    setModelName,
    setModelNameBySession,
    setOptionsProposals,
    setPendingPrimarySessionId,
    setPlanSteps,
    setPrimarySessionId,
    setReasoningBySession,
    setReasoningEffort,
    setSessionBootstrapped,
    setSessionExpired,
    setSessionLoadingLabel,
    setSessionTitleUpdate,
    setShellStreams,
    setStreamingText,
    setStreamingTextBySession,
    setSwitchingModel,
    setSwitchingModelBySession,
    setTimeline,
    setToolsStage,
    setTurnStartedMs,
    setViewingHistorySession,
    setWsStatus,
    streamUiFlushTimerRef,
    streamingTextBySessionRef,
    streamingTextRef,
    summaryStreamCacheRef,
    turnStartedMs,
    turnStartedMsRef,
    userStoppedBySidRef,
    viewingHistorySessionRef,
    wsServiceRef,
    voicePageHideRef,
    voiceResumeProbeRef,
    voiceCaptionRef,
    speakFinalReplyRef,
    lastAutoSpokenRef,
    clearVoiceConnectTimer,
    armVoiceConnectTimeout,
    setVoiceBindings,
    setVoicePanelOpen,
    setVoiceRealtimeStatus,
    setVoiceRealtimeError,
    setVoiceTranscript,
    stopAutoTts,
    t,
  });

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

  /**
   * A new user turn consumes the agent's follow-up offer (`suggest_followups`).
   *
   * Fired from both send entry points (`handleSend`, `handlePaneComposerSend`)
   * the moment the user commits a message, instead of waiting for the server's
   * `turn_start` echo — that echo is a whole round-trip away (the chips visibly
   * outlived the Enter keypress) and never arrives at all for a message parked
   * in the pending queue. Once consumed the offer is gone for good; only a
   * fresh `suggest_followups` from the next answer may bring chips back.
   *
   * The `turn_start` clear in `useAgentWebSocket` stays as the backstop for
   * turns this UI does not originate (scheduled tasks, self-continuation).
   */
  const consumeFollowupOffer = useCallback(() => setFollowupSuggestions([]), []);

  const deliverMessage = useCallback((
    payload: {
      text: string;
      images: string[];
      attachments: UploadedFile[];
      skillDir?: string;
      skillName?: string;
      /** Target session for parallel multi-session sends */
      sessionId?: string;
      /** steer 消费回执对账 id（= PendingMessage.id），普通发送不用 */
      clientId?: string;
    },
    opts?: { clearInputState?: boolean; salvageStream?: boolean; steer?: boolean },
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

    // ---- Steer（引导注入）分支 ----
    // 会话忙时的插话：照常构建 wsText（skill 标签 / 文件 / 图片标记），立即经
    // WS 发往后端注入队列。runner 会在当前工具轮结束后的下一轮把它塞进模型
    // 上下文，不打断连续工具流；消费后回发 steer_consumed，这里不入时间线、
    // 不抢焦点、不做 checkpoint（撤回由 cancel_steer 命令负责）。
    if (opts?.steer) {
      const steerUid = payload.clientId || genUID();
      const steerCard =
        (targetSessionId && cardNameBySessionRef.current[targetSessionId]) ||
        currentCardNameRef.current ||
        undefined;
      wsServiceRef.current?.sendMessage(
        wsText,
        allImages.length > 0 ? allImages : undefined,
        nonImageAttachments,
        {
          client_id: steerUid,
          session_id: targetSessionId || undefined,
          model_card: steerCard,
        },
      );
      console.info('[AIChatPage] deliverMessage → WS steer', {
        targetSessionId,
        clientId: steerUid,
        textHead: wsText.slice(0, 80),
      });
      return;
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

    logMediaDebug('handleSend-payload', {
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
    if (targetSessionId) delete userStoppedBySidRef.current[targetSessionId];
    const salvageSrc = targetSessionId
      ? (streamingTextBySessionRef.current[targetSessionId]
        || (targetSessionId === (currentSessionIdRef.current || '') ? streamingTextRef.current : ''))
      : streamingTextRef.current;
    if (salvageStream && salvageSrc && !isSidFinalizing(targetSessionId)) {
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
    // NOTE: Do NOT clear this sid's finalizing flag here. If the previous turn's
    // handleFinal is still within its 300ms guard window, clearing it would let
    // late-arriving debounced stream chunks from turn N bleed into turn N+1.
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
        // Registering is not opening. The user is provably working in this
        // folder right now, so its workspace must be open — otherwise it only
        // ever appears in the `+` menu while the tab strip and the sidebar keep
        // showing some other project. (No refresh call here: `openWorkspaceTab`
        // saves through the store, which emits WORKSPACES_CHANGED_EVENT and the
        // listener below refreshes the snapshot.)
        openWorkspaceTab(agentId, ws.id);
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

  // 引导注入（steer）：忙时会话的排队消息在入队的同时立即经 WS 发往后端，
  // 由 runner 在当前工具轮/普通输出结束后的下一轮塞进模型上下文——不打断
  // 连续工具流，但模型能看见这条消息。本地队列条目仅作展示（steered=true），
  // 消费回执（steer_consumed）到达后挪进时间线。
  const steerPendingSnapshot = useCallback((snapshot: PendingMessage) => {
    setPendingMessages((prev) => prev.map((m) => (m.id === snapshot.id ? { ...m, steered: true } : m)));
    deliverMessage(
      {
        text: snapshot.text,
        images: snapshot.images,
        attachments: snapshot.attachments,
        skillDir: snapshot.skillDir,
        skillName: snapshot.skillName,
        sessionId: snapshot.sessionId,
        clientId: snapshot.id,
      },
      { clearInputState: false, salvageStream: false, steer: true },
    );
  }, [deliverMessage]);

  const handleSend = () => {
    // A user send consumes the agent's follow-up offer (see
    // `consumeFollowupOffer`), whether the message goes out now or is parked.
    consumeFollowupOffer();
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
        steerPendingSnapshot(snapshot);
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
        steerPendingSnapshot(snapshot);
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
      steerPendingSnapshot(snapshot);
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
          pagedAnchorIdRef.current = sessionMessageIdentity(messages[0]) || null;
          putCachedSessionTimeline(agentId, sid, entries, {
            complete: !hasMore,
            messageCount: messages.length,
            totalMessages: session.total_messages,
            oldestMessageId: pagedAnchorIdRef.current || undefined,
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
        // Same owner rule as every other session-tab site (see
        // resolveSessionWorkspaceId), falling back to the active workspace.
        const wsId =
          resolveSessionWorkspaceId(snap.workspaces, getSessionMeta(agentId, sid))
          || snap.chrome.activeWorkspaceId;
        if (wsId) {
          const sameWorkspace = wsId === snap.chrome.activeWorkspaceId;
          if (!sameWorkspace) openWorkspaceTab(agentId, wsId);
          const pane = sameWorkspace ? target.paneId : null;
          if (pane) setFocusedPane(agentId, pane);
          openContentTab(agentId, wsId, { kind: 'session', id: sid }, pane);
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

  // 引导消息撤回：从注入队列撤回到输入框重新编辑（后端尚未消费时同步移除；
  // 已被模型消费的条目此前已随 steer_consumed 移出队列，不存在该入口）。
  const handleEditPending = useCallback((id: string) => {
    const target = pendingMessagesRef.current.find((m) => m.id === id);
    if (!target) return;
    setPendingMessages((prev) => prev.filter((m) => m.id !== id));
    wsServiceRef.current?.cancelSteer((target.sessionId || '').trim() || undefined, target.id);
    // 回填输入框：文本追加/还原，媒体与技能一并恢复，便于重新编辑后再发送。
    setInputText((prev) => {
      const t = (target.text || '').trim();
      return prev.trim() ? (t ? `${prev}\n${t}` : prev) : t;
    });
    if (target.skillDir) {
      setPendingSkill({ dir: target.skillDir, name: target.skillName || target.skillDir });
    }
    if (target.images.length > 0) setImages((prev) => [...prev, ...target.images]);
    if (target.attachments.length > 0) {
      setAttachments((prev) => [...prev, ...target.attachments.map((a) => ({ ...a }))]);
    }
    if (inputRef.current) inputRef.current.style.height = 'auto';
    inputRef.current?.focus();
  }, []);

  // 引导消息删除：直接丢弃（后端同步从注入队列移除）。
  const handleDeletePending = useCallback((id: string) => {
    const target = pendingMessagesRef.current.find((m) => m.id === id);
    if (!target) return;
    setPendingMessages((prev) => prev.filter((m) => m.id !== id));
    wsServiceRef.current?.cancelSteer((target.sessionId || '').trim() || undefined, target.id);
  }, []);

  // Header "Send now": release only the first queued message (sequential drain).
  // 已引导注入的条目不重发（后端注入队列里已在排队）。
  const handleSendNextPending = useCallback(() => {
    const queue = pendingMessagesRef.current;
    if (queue.length === 0) return;
    const next = queue.find((m) => !m.steered);
    if (!next) return;
    setPendingMessages(prev => prev.filter(m => m.id !== next.id));
    void flushPendingMessage(next);
  }, [flushPendingMessage]);

  // Clear the entire queue without sending anything. 已引导条目需同步撤销后端注入。
  const handleCancelAllPending = useCallback(() => {
    for (const m of pendingMessagesRef.current) {
      if (m.steered) {
        wsServiceRef.current?.cancelSteer((m.sessionId || '').trim() || undefined, m.id);
      }
    }
    setPendingMessages([]);
    clearOutboundTurnPending();
  }, [clearOutboundTurnPending]);

  // 引导消息被模型消费（随本轮工具结果进入上下文）：把用户气泡插入时间线，
  // 并从引导队列移除对应条目（按入队时的 client_id 对账）。
  useEffect(() => {
    const svc = wsServiceRef.current;
    if (!svc) return;
    return svc.on('steer_consumed', (raw: any) => {
      const inner = raw?.content ?? raw?.data ?? {};
      const payload = typeof inner === 'object' && inner !== null ? inner : {};
      const messageId = String(payload.message_id || '').trim();
      const text = String(payload.content ?? '').trim();
      const sid = String(raw?.sid || payload.session_id || '').trim();
      if (messageId) {
        setPendingMessages((prev) => prev.filter((m) => !(m.steered && m.id === messageId)));
      }
      if (!text) return;
      const uid = genUID();
      const userMsg: ChatMessage = {
        role: 'user',
        content: formatUserSkillDisplayContent(text),
        timestamp: new Date().toISOString().replace(/\.\d{3}Z$/, 'Z'),
        message_id: uid,
      };
      const prevBucket = sid
        ? (liveTimelinesBySessionRef.current[sid] ?? [])
        : (timelineRef.current ?? []);
      const nextEntries: TimelineEntry[] = [
        ...sealIncompleteWorkflows(prevBucket, { fallbackStartedMs: turnStartedMsRef.current }),
        { kind: 'message', data: userMsg, _uid: uid },
      ];
      if (sid) {
        const out = { ...liveTimelinesBySessionRef.current, [sid]: nextEntries };
        liveTimelinesBySessionRef.current = out;
        setLiveTimelinesBySession(out);
      }
      if (!sid || sid === (currentSessionIdRef.current || '')) {
        setTimelineState(nextEntries);
      }
    });
  }, [agentId]);

  // Auto-drain: when idle, release exactly ONE pending message (any session),
  // switching without stop_task, then wait for that turn before the next.
  useEffect(() => {
    // Drain per-session queues independently — do not block on other sessions.
    if (isFlushingPendingRef.current) return;
    const queue = pendingMessagesRef.current;
    if (queue.length === 0) return;
    const next = queue.find((m) => {
      // 已引导注入的条目由后端消费（steer_consumed / 回合结束清理），绝不重发。
      if (m.steered) return false;
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
    if (sid) {
      userStoppedBySidRef.current[sid] = true;
    } else {
      for (const b of busySessionsRef.current) {
        if (b) userStoppedBySidRef.current[b] = true;
      }
      const focused = String(currentSessionIdRef.current || '').trim();
      if (focused) userStoppedBySidRef.current[focused] = true;
    }
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
    // Stamp elapsed so the stopped message shows the 消耗 badge (duration
    // only — billed tokens are unknown at abort; a real turn_usage would
    // overwrite if the backend ever sends one).
    const stopStartedMs = turnStartedMsRef.current || 0;
    const stopElapsedMs = stopStartedMs > 0 ? Math.max(0, Date.now() - stopStartedMs) : 0;
    const stoppedMsg: ChatMessage = {
      role: 'assistant',
      // No "[Stopped]" text in the body — MessageBubble renders the styled
      // 任务已取消 badge from the `stopped` flag instead of raw marker text.
      content: currentText.replace(/\s+$/, ''),
      stopped: true,
      timestamp: new Date().toISOString(),
      ...(stopElapsedMs > 0
        ? {
            usage: {
              input_tokens: 0,
              output_tokens: 0,
              total_tokens: 0,
              elapsed_ms: stopElapsedMs,
            },
          }
        : {}),
    };
    // Seal every incomplete workflow on this sid (not just the last block).
    setTimeline((prev) => {
      const sealed = sealIncompleteWorkflows(prev, {
        cancelOpenTools: 'Cancelled: still running when the turn stopped',
        fallbackStartedMs: turnStartedMsRef.current,
      });
      return finalizeWorkflowAndAddMessage(sealed, stoppedMsg);
    });
    setTurnStartedMs(undefined);
    clearSessionRunState(sid);
    eventSidRef.current = '';
  };

  const handleCompressContext = (paneSessionId?: string) => {
    if (isCompressingContext || isLoadingSession) return;
    setIsCompressingContext(true);
    // Compress the pane the user is looking at — same rule as onViewReport:
    // tab 模式下 composer 所属会话往往不是焦点会话，切 tab 不回写焦点。
    const compressSid = (paneSessionId || currentSessionIdRef.current || '').trim();
    // Optimistic feedback must land in the clicked pane's timeline. setTimeline
    // routes by eventSidRef — empty means "focused pane", which would silently
    // dump the compress indicator into a different session (看起来毫无反应).
    const prevSid = eventSidRef.current;
    eventSidRef.current = compressSid;
    try {
      setTimeline(prev => appendWorkflowEvent(prev, {
        type: 'summary_stream',
        content: { id: 'compress_pending', text: 'Generating context summary...', done: false, pending: true },
        timestamp: Date.now(),
      }, 'Summarizing...'));
    } finally {
      eventSidRef.current = prevSid;
    }
    wsServiceRef.current?.compressContext(compressSid || undefined);
    // Hard fallback: the backend always emits a terminal frame (done / skipped),
    // so this only fires when the request never reached the agent (agent died,
    // socket dropped). Seal the optimistic block — leaving it pending is what
    // makes the button look stuck on "进度中" forever.
    window.setTimeout(() => {
      setIsCompressingContext(false);
      const prevSidTo = eventSidRef.current;
      eventSidRef.current = compressSid;
      try {
        setTimeline(prev => sealPendingCompression(prev, t('aiChat.compressNoResponse', { defaultValue: '压缩没有响应，请重试' })));
      } finally {
        eventSidRef.current = prevSidTo;
      }
    }, 120000);
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
        delete userStoppedBySidRef.current[draftSid];
        delete finalizingBySidRef.current[draftSid];
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
        setFollowupSuggestions([]);
        setModeApprovals([]);
        setActiveGoal(null);
        setPendingSkill(null);
        setSessionChanges(null);
        setFocusChangedNonce(Date.now());
        streamingTextRef.current = '';
        setStreamingText('');
        diskSessionLoadedRef.current = false;
        sessionBootstrapDoneRef.current = true;
        setPlanSteps([]);
        setImages([]);
        setAttachments([]);
        setPendingMessages([]);
        clearOutboundTurnPending();
        pinComposerLanding(draftSid);
        if (activeWorkspace) {
          // A folder-scoped new session targets `boundPath`, which is not
          // necessarily the active workspace — file the draft tab under the
          // workspace that owns it (see resolveSessionWorkspaceId).
          const ownerId =
            (boundPath
              ? resolveSessionWorkspaceId(wsSnap.workspaces, { projectPath: boundPath })
              : null) || activeWorkspace.id;
          const sameWorkspace = ownerId === activeWorkspace.id;
          // A pane id from the workspace we are leaving does not resolve in the
          // new layout — let openContentTab pick that workspace's own pane.
          const targetPane = sameWorkspace
            ? (pendingTargetPaneIdRef.current || focusedPaneId || null)
            : null;
          pendingTargetPaneIdRef.current = null;
          pendingOpenSessionTabRef.current = false;
          if (!sameWorkspace) openWorkspaceTab(agentId, ownerId);
          openContentTab(
            agentId,
            ownerId,
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
      userStoppedBySidRef.current[previousSid] = true;
      wsServiceRef.current?.stopTask({ session_id: previousSid });
    }
    if (previousSid) delete finalizingBySidRef.current[previousSid];
    clearSessionRunState(previousSid);
    newSessionPendingRef.current = true;
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
    setFollowupSuggestions([]);
    setModeApprovals([]);
    setActiveGoal(null);
    setPendingSkill(null);
    setSessionChanges(null);
    setFocusChangedNonce(Date.now());
    streamingTextRef.current = '';
    setStreamingText('');
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
    pagedAnchorIdRef.current = null;
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
      pagedAnchorIdRef.current = sessionMessageIdentity(session?.messages?.[0]) || null;
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
      // This page replaces the timeline, so it also re-anchors scroll-up: the
      // oldest message of the page is the boundary everything older must come
      // strictly before.
      pagedAnchorIdRef.current = sessionMessageIdentity(messages[0]) || null;
      putCachedSessionTimeline(agentId, sessionId, entries, {
        complete: !hasMore,
        messageCount: messages.length,
        totalMessages: session.total_messages,
        oldestMessageId: pagedAnchorIdRef.current || undefined,
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
          // Restore the paged anchor so scroll-up resumes from the exact
          // message the cached window ended on instead of the tail-relative
          // offset (which drifts once the live turn appends messages).
          pagedAnchorIdRef.current = meta.oldestMessageId || null;
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
    clearComposerDraft(sessionId);
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
    if (def) ensureActiveWorkspaceFromRoot(agentId, def);
    wsMigratedRef.current = true;
    refreshWsSnap();
  }, [agentId, defaultCwd, agentCwd, refreshWsSnap]);

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
    // A session belongs to exactly one workspace, and its tab has to live in
    // that workspace's layout. Filing it under whichever tab happens to be
    // active leaves the workspace it actually works in unopened — visible in
    // the `+` menu, absent from the tab strip (and its sessions filtered out of
    // the sidebar). Bring the owner forward first.
    const ownerId =
      resolveSessionWorkspaceId(
        wsSnap.workspaces,
        getSessionMeta(agentId, currentSessionId),
      ) || activeWorkspace.id;
    const sameWorkspace = ownerId === activeWorkspace.id;
    if (!sameWorkspace) openWorkspaceTab(agentId, ownerId);
    // A pane id from the workspace we are leaving does not resolve in the new
    // layout — let openContentTab pick that workspace's own focused pane.
    const targetPane = sameWorkspace
      ? (pendingTargetPaneIdRef.current || focusedPaneId || null)
      : null;
    pendingTargetPaneIdRef.current = null;
    openContentTab(
      agentId,
      ownerId,
      { kind: 'session', id: currentSessionId },
      targetPane,
    );
    pinComposerLanding(currentSessionId);
    if (targetPane) setFocusedPane(agentId, targetPane);
    refreshWsSnap();
  }, [
    currentSessionId,
    activeWorkspace?.id,
    agentId,
    wsSnap.workspaces,
    refreshWsSnap,
    focusedPaneId,
    pinComposerLanding,
  ]);

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
    // The session's own workspace owns the tab (see resolveSessionWorkspaceId):
    // a sidebar click must reveal the project the session works in, not file it
    // under whichever workspace happens to be active.
    const ownerId = resolveSessionWorkspaceId(
      wsSnap.workspaces,
      getSessionMeta(agentId, sessionId),
    );
    const wsId = ownerId || activeWorkspace?.id || null;
    if (wsId) {
      const sameWorkspace = wsId === activeWorkspace?.id;
      if (!sameWorkspace) openWorkspaceTab(agentId, wsId);
      // A pane id from the workspace we are leaving does not resolve in the new
      // layout — let openContentTab pick that workspace's own focused pane.
      const pane = sameWorkspace ? focusedPaneId : null;
      openContentTab(
        agentId,
        wsId,
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
    // A user send consumes the agent's follow-up offer (see
    // `consumeFollowupOffer`), whether the message goes out now or is parked.
    consumeFollowupOffer();
    // First real send leaves the centered new-session landing and promotes
    // the draft into the sidebar session list.
    unpinComposerLanding(sessionId);
    requestSessionListRefresh(agentId, sessionId);
    setFocusedPane(agentId, paneId);
    if (!opts?.stay) {
      // Same owner rule as every other session-tab site: a send in a session
      // whose workspace is not the active one must not file the tab under the
      // active workspace (see resolveSessionWorkspaceId).
      const ownerId =
        resolveSessionWorkspaceId(wsSnap.workspaces, getSessionMeta(agentId, sessionId))
        || activeWorkspace.id;
      const sameWorkspace = ownerId === activeWorkspace.id;
      if (!sameWorkspace) openWorkspaceTab(agentId, ownerId);
      openContentTab(
        agentId,
        ownerId,
        { kind: 'session', id: sessionId },
        sameWorkspace ? paneId : null,
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
      steerPendingSnapshot(snapshot);
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

  const resolveTokenStatsForSession = (sessionId: string | null | undefined) => {
    const sid = (sessionId || '').trim();
    const ws = (sid && tokenStatsBySession[sid]) || agentTokenStats;
    const live = sid ? pickSessionLiveTimeline(liveTimelinesBySession, sid) : null;
    const fromLive = live != null && live.length > 0 ? live : null;
    const cached = sid && !fromLive ? getCachedSessionTimeline(agentId, sid) : null;
    const raw = fromLive
      || cached
      || (sid && sid === currentSessionId ? timeline : []);
    return mergeSessionTokenStats(ws, flattenArchivedSections(raw || []));
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
              · ↗ {t('aiChat.pendingSteerHint')}
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
                        onClick={(e) => e.stopPropagation()}
                        className="p-1 rounded text-primary hover:bg-primary/10 transition-colors"
                        title={t('aiChat.steerHint')}
                      >
                        <Reply size={12} />
                      </button>
                      <button
                        type="button"
                        onClick={() => handleEditPending(pm.id)}
                        className="p-1 rounded text-textMuted hover:bg-primary/10 hover:text-textMain transition-colors"
                        title={t('aiChat.editPending')}
                      >
                        <Pencil size={12} />
                      </button>
                      <button
                        type="button"
                        onClick={() => handleDeletePending(pm.id)}
                        className="p-1 rounded text-textMuted hover:bg-primary/10 hover:text-textMain transition-colors"
                        title={t('aiChat.deletePending')}
                      >
                        <Trash2 size={12} />
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
      resolveTokenStatsForSession(sessionId),
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
        draftText={getComposerDraft(sessionId)}
        onDraftChange={(t) => {
          setComposerDraft(sessionId, t);
        }}
        landing={isSessionComposerLanding(sessionId)}
        disabled={isLoadingSession || (!sessionBootstrapped && !composerLandingSessionsRef.current.has(sessionId))}
        busy={isSessionBusy(sessionId)}
        terminalsPanel={
          runningShellJobs.length > 0 ? (
            <ShellTerminalsBar
              jobs={runningShellJobs}
              onStopJob={(job) => {
                if (job.jobId) {
                  wsServiceRef.current?.stopSessionJob(job.jobId, job.sessionId || sessionId);
                } else if (job.sessionId) {
                  // Sync/persistent shell call — no job_id; kill its shell session.
                  wsServiceRef.current?.stopSessionJob(undefined, sessionId, job.sessionId);
                }
              }}
            />
          ) : null
        }
        agentMode={agentModeBySession[sessionId] ?? agentMode}
        onModeChange={(mode) => {
          setAgentModeBySession((prev) => ({ ...prev, [sessionId]: mode }));
          wsServiceRef.current?.setAgentMode(mode, undefined, sessionId);
        }}
        approvalPanel={(() => {
          if (focusedPaneId !== paneId) return null;
          const pendingModes = modeApprovals.filter((a) => a.status === 'pending');
          const pendingOptions = optionsProposals.filter((p) => p.status === 'pending');
          if (pendingModes.length === 0 && pendingOptions.length === 0) {
            return null;
          }
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
        tokenStats={resolveTokenStatsForSession(sessionId)}
        onViewReport={() => {
          // 详情面板跟随「这个 composer 所属的会话」——tab 模式下它往往不是
          // currentSessionId（切 tab 不回写焦点会话），重启后更是停在启动会话。
          setContextViewerSessionId(sessionId);
          setShowContextViewer(true);
          // 非焦点会话可能还没有 per-session 统计；不请求的话
          // resolveTokenStatsForSession 会回退到 agentTokenStats——
          // 那是上一次活跃会话的数字，恰好复刻本 bug 的另一半。
          if (sessionId && sessionId !== currentSessionIdRef.current) {
            requestSessionTokenStats(sessionId);
          }
        }}
        onCompressContext={() => handleCompressContext(sessionId)}
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

  const handleOpenTasks = () => {
    setLibraryView(null);
    if (isCompactLayout) {
      setSessionSidebarOpen(false);
      setFilesPanelOpen(false);
    }
    if (!activeWorkspace) return;
    const pane = focusedPaneId;
    openContentTab(agentId, activeWorkspace.id, { kind: 'tasks', id: 'tasks' }, pane);
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

  // Open a session content tab (e.g. from the parallel-task / scheduled-task
  // "执行过程" button). The panels cannot open tabs themselves, so they ask
  // through `utils/uiEvents.openSessionTab`.
  useEffect(() => {
    const handler = (e: any) => {
      const sessionId: string | undefined = e?.detail?.sessionId;
      if (!sessionId) return;
      // Same rule as a sidebar click: the tab belongs to the session's own
      // workspace (a scheduled/parallel run may well live in another one).
      const ownerId = resolveSessionWorkspaceId(
        wsSnap.workspaces,
        getSessionMeta(agentId, sessionId),
      );
      const wsId = ownerId || activeWorkspace?.id || null;
      if (!wsId) return;
      setLibraryView(null);
      const sameWorkspace = wsId === activeWorkspace?.id;
      if (!sameWorkspace) openWorkspaceTab(agentId, wsId);
      const pane = sameWorkspace ? focusedPaneId : null;
      openContentTab(agentId, wsId, { kind: 'session', id: sessionId }, pane);
      if (pane) setFocusedPane(agentId, pane);
      refreshWsSnap();
    };
    window.addEventListener(OPEN_SESSION_TAB_EVENT, handler as EventListener);
    return () => window.removeEventListener(OPEN_SESSION_TAB_EVENT, handler as EventListener);
  }, [activeWorkspace, wsSnap.workspaces, focusedPaneId, agentId, refreshWsSnap]);

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
        workspaceRootPath={activeWorkspace?.rootPath || defaultCwd || agentCwd || null}
        workspaceId={activeWorkspace?.id || null}
        onViewSession={handleSidebarViewSession}
        onNewSession={handleNewSessionInWorkspace}
        onSwitchAndReply={handleSwitchAndReply}
        onDeleteSession={handleDeleteSession}
        onOpenSkills={handleOpenSkills}
        onOpenPlugins={handleOpenPlugins}
        onOpenRoles={handleOpenRoles}
        onOpenScheduledTasks={handleOpenScheduledTasks}
        onOpenTasks={handleOpenTasks}
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
        open={sessionSearchOpen}
        agentId={agentId}
        sessions={sidebarSessions}
        workspaceRootPath={activeWorkspace?.rootPath || defaultCwd || agentCwd || null}
        onCancel={closeSessionSearch}
        onPick={(sid) => {
          closeSessionSearch();
          handleSidebarViewSession(sid);
        }}
        onNewSession={() => {
          const path = (activeWorkspace?.rootPath || defaultCwd || agentCwd || '').trim();
          if (path) handleNewSessionInWorkspace(path);
        }}
      />

      {showContextViewer && (
        <ContextViewer
          agentId={agentId}
          agentName={agentProfile?.agent_name || agentId}
          sessionId={contextViewerSessionId ?? currentSessionId}
          provider={agentProvider}
          apiProtocol={agentApiProtocol}
          model={modelName}
          cwd={agentCwd}
          tokenStats={resolveTokenStatsForSession(contextViewerSessionId ?? currentSessionId)}
          entries={contextViewerEntries}
          onClose={() => {
            setShowContextViewer(false);
            setContextViewerSessionId(null);
          }}
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
          renderChatSlot={(slotPaneId) => (
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
        <ChatScrollHud
          scrollRef={messagesContainerRef}
          onNearTop={loadMoreHistory}
          nearTopEnabled={hasMoreHistory && !isLoadingMore}
          onUnpin={markUnpinnedFromBottom}
        />
        <ChatTimeline
          scrollRef={messagesContainerRef}
          entries={displayTimeline}
          revealKey={currentSessionId}
          className="h-full overflow-y-auto px-2 sm:px-4 py-3 sm:py-4 relative"
          style={{ minHeight: 0 }}
          columnClass={soloColumnClass}
          unpinRef={userScrolledRef}
          freezeRef={textSelectFrozenRef}
          header={isLoadingMore ? (
            <div className="flex items-center justify-center py-3">
              <OpenSquadLoader size={18} className="mr-2" />
              <span className="text-xs text-textMuted">Loading earlier messages...</span>
            </div>
          ) : null}
          footer={(
            <>
          {(displayStreamingText) && (
            <StreamingMessage
              content={displayStreamingText}
              isComplete={!isStreaming}
              avatarSrc={resolveChatAvatar(agentProfile?.chat_profile) ?? undefined}
              variant={isSolo ? 'solo' : 'classic'}
              // 流式文本前若是工作流组，名字已在工作流上方显示，避免重复
              senderName={
                displayTimeline.length > 0
                && displayTimeline[displayTimeline.length - 1].kind === 'workflow'
                  ? undefined
                  : agentProfile?.agent_name
              }
              // 只传 undefined 不够 —— 组件会退化成兜底文案「Agent」。
              hideSenderLabel={
                displayTimeline.length > 0
                && displayTimeline[displayTimeline.length - 1].kind === 'workflow'
              }
            />
          )}
          {/* 对话后续预期：贴在「最终输出」末尾，而不是输入框上方。
              仅在回合结束后出现（流式/进行中一律不渲染），因此新的工具流或
              新的消息输出一旦开始，它就先被隐藏、随后由 hook 清空。 */}
          {followupSuggestions.length > 0
            && currentSessionId
            && !displayStreamingText
            && !isSessionBusy(currentSessionId) && (
            <div className="mt-2" data-testid="followup-suggestions-tail">
              <FollowupSuggestions
                suggestions={followupSuggestions}
                onPick={(text) => {
                  // Tapping is just another send: `handlePaneComposerSend`
                  // consumes the offer on every send path (single owner — see
                  // `consumeFollowupOffer`), so there is no local clear here.
                  void handlePaneComposerSend(
                    slotPaneId,
                    currentSessionId,
                    { text, images: [], attachments: [] },
                    { stay: true },
                  );
                }}
              />
            </div>
          )}
          <div ref={chatEndRef} />
            </>
          )}
          renderEntry={(entry, i, entryKey, revealStyle) => {
            const lockLayout =
              i >= displayTimeline.length - 8
              || (entry.kind === 'workflow' && !entry.data.completed);
            if (entry.kind === 'message') {
              const msgProps = {
                message: entry.data,
                senderName:
                  entry.data.role === 'user'
                    ? (currentUser?.name || undefined)
                    : (agentProfile?.agent_name || undefined),
                // 助手回复紧跟工作流组时，名字已在工作流上方显示 —— 整行隐藏。
                // 只把 senderName 传 undefined 是不够的：MessageBubble 会退化成
                // 兜底文案「Agent」，于是统计行和正文之间夹出一行幽灵签名。
                hideSenderLabel:
                  entry.data.role === 'assistant'
                  && i > 0
                  && displayTimeline[i - 1].kind === 'workflow',
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
              // Files created/modified by the workflow that produced this reply
              // (shown below the reply once the turn completes).
              const turnChangedFiles =
                entry.data.role === 'assistant'
                  ? collectTurnChangedFilesBefore(displayTimeline, i)
                  : [];
              // Classic: visualization iframes sit below the final assistant reply
              // (tool stream keeps the normal tool_call row only).
              const replyEmbeds: HtmlEmbedPayload[] =
                !isSolo && entry.data.role === 'assistant'
                  ? (htmlEmbedsByAssistantIndex?.get(i) ?? [])
                  : [];
              const turnFilesCard =
                turnChangedFiles.length > 0 ? (
                  <TurnChangedFilesCard
                    files={turnChangedFiles}
                    onOpenFile={openProjectFile}
                    onViewAll={() => {
                      setFilesPanelOpen(true);
                      if (isCompactLayout) setSessionSidebarOpen(false);
                      try {
                        localStorage.setItem('opensquad.filesPanel.open', 'true');
                      } catch {
                        /* ignore */
                      }
                      setFocusChangedNonce(Date.now());
                    }}
                    viewAllLabel={t('aiChat.turnFiles.viewAll')}
                  />
                ) : null;
              if (replyEmbeds.length === 0 && !turnFilesCard) {
                return (
                  <TimelineRow key={entryKey} lockLayout={lockLayout} style={revealStyle}>
                    {isSolo
                      ? <SoloMessage {...msgProps} anchorId={entryKey} />
                      : <MessageBubble {...msgProps} anchorId={entryKey} />}
                  </TimelineRow>
                );
              }
              return (
                <TimelineRow key={entryKey} lockLayout={lockLayout} style={revealStyle}>
                  {isSolo
                    ? <SoloMessage {...msgProps} anchorId={entryKey} />
                    : <MessageBubble {...msgProps} anchorId={entryKey} />}
                  {(replyEmbeds.length > 0 || turnFilesCard) && (
                    <div className="w-full mt-1 mb-4" data-html-embeds-below-reply={replyEmbeds.length > 0 ? '1' : undefined}>
                      {replyEmbeds.map((payload, ei) => (
                        <HtmlEmbedBlock
                          key={payload.id || payload.filename || `viz-${ei}`}
                          payload={payload}
                          variant="seamless"
                          className="my-0"
                        />
                      ))}
                      {turnFilesCard}
                    </div>
                  )}
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
              // 'prompt' entries render as null — they must not split the
              // workflow group into separate fold rows.
              let prevIdx = i - 1;
              while (prevIdx >= 0 && displayTimeline[prevIdx].kind === 'prompt') prevIdx -= 1;
              if (prevIdx >= 0 && displayTimeline[prevIdx].kind === 'workflow') {
                return null;
              }
              const blocks: WorkflowBlock[] = [curBlock];
              let j = i + 1;
              while (j < displayTimeline.length && (displayTimeline[j].kind === 'workflow' || displayTimeline[j].kind === 'prompt')) {
                if (displayTimeline[j].kind === 'workflow') {
                  blocks.push((displayTimeline[j] as { kind: 'workflow'; data: WorkflowBlock }).data);
                }
                j += 1;
              }
              const merged = blocks.length > 1 ? mergeWorkflowBlocks(blocks) : curBlock;
              const groupHasIncomplete = !merged.completed;
              // 任务已交付判定：该工作流组之后紧跟 assistant 最终回复 →
              // 即使块未密封/有未闭合工具，也停止"执行中"动画与流光。
              const nextAfterGroup = displayTimeline[j];
              const turnDelivered =
                !!nextAfterGroup
                && nextAfterGroup.kind === 'message'
                && (nextAfterGroup.data as ChatMessage).role === 'assistant'
                && typeof (nextAfterGroup.data as ChatMessage).content === 'string'
                && !!(nextAfterGroup.data as ChatMessage).content.trim();
              const turnMs = groupHasIncomplete
                ? turnStartedMs
                : (!isSolo && i === lastIncompleteIdx ? turnStartedMs : undefined);
              return (
                <TimelineRow
                  key={entryKey}
                  lockLayout={lockLayout || (groupHasIncomplete && !turnDelivered)}
                  style={revealStyle}
                >
                  <div className="w-full min-w-0">
                    {/* 尾部"幽灵签名"守卫：组里只剩无正文 info 事件（如回合结束后的
                        suggest_followups 回执）时 SoloActivityRow 什么都不画，caption
                        也必须跟着消失，否则推荐追问上方会悬一行孤立的 agent 名。 */}
                    {agentProfile?.agent_name
                      && merged.events.some((e) => {
                        if (e.type !== 'info') return true;
                        const c = e.content as any;
                        const s = (typeof e.content === 'string' ? e.content : c?.text || c?.message || '').trim();
                        return !!s
                          && !/^New session started$/i.test(s)
                          && !/^Workflow started$/i.test(s);
                      }) && (
                        <div className="text-[11px] font-medium text-textMuted/70 mb-2">
                          {agentProfile.agent_name}
                        </div>
                      )}
                    <SoloActivityRow
                      block={merged}
                      turnDelivered={turnDelivered}
                      expandLevel={workflowExpandLevel}
                      turnStartedMs={turnMs}
                      shellStreams={shellStreams}
                      onOpenFile={openProjectFile}
                      embedVisualizations={false}
                      uiMode={uiMode}
                    />
                  </div>
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
            if (entry.kind === 'model_switch') {
              // 模型切换提示（无工作流时的独立轻量条目，不属于工作流统计）。
              const sw = entry.data;
              const label = sw.model
                ? t('aiChat.modelSwitched', { model: sw.model })
                : sw.text;
              return (
                <TimelineRow key={entryKey} style={revealStyle}>
                  <div className="flex items-center gap-1.5 py-0.5 my-0.5 mx-0">
                    <div className="flex-1 h-px bg-border/25" />
                    <RefreshCw size={11} className="text-textMuted/50 shrink-0" />
                    <span className="text-[10px] text-textMuted/45 font-mono shrink-0">{label}</span>
                    <div className="flex-1 h-px bg-border/25" />
                  </div>
                </TimelineRow>
              );
            }
            if (entry.kind === 'task_fold') {
              const fold = entry.data;
              const foldEmbedIndex = !isSolo ? indexHtmlEmbedsByAssistantMessage(fold.entries) : null;
              return (
                <TimelineRow key={entryKey} lockLayout={lockLayout} style={revealStyle}>
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
                          ? (foldEmbedIndex?.get(ni) ?? [])
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
                          turnDelivered
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

        {!isSolo && (
          <ChatScrollComposerHint
            scrollRef={messagesContainerRef}
            columnClass={soloColumnClass}
            onUnpin={markUnpinnedFromBottom}
          />
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
