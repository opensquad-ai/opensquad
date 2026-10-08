/**
 * WorkspacePaneShell — one split leaf: L2 tab bar + content (chat / file / preview) + composer.
 */
import React, { useMemo, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Plus } from 'lucide-react';
import { OpenSquadLoader } from '../OpenSquadLoader';
import { ContentTabBar, type ContentTabLabel } from './ContentTabBar';
import { WorkspaceFileEditor } from './WorkspaceFileEditor';
import { ComposerLandingDock } from './ComposerLandingDock';
import { ScheduledTasksPage } from './ScheduledTasksPage';
import { TaskPanelPage } from './TaskPanelPage';
import { ErrorBoundary } from '../ErrorBoundary';
import type { ComposerSendPayload } from './AgentWebComposer';
import type { SoloTokenStats } from './SoloContextFooter';
import type { TimelineEntry } from '../../utils/aiChatTimeline';
import type { ContentTab, PaneTabs } from '../../utils/workspaceStore';
import { parseContentTabKey } from '../../utils/workspaceStore';
import { PANE_VIEWS, type PaneViewId } from '../../utils/paneViews';
import { BrowserPanel } from './BrowserPanel';
import { ModPaneView } from './ModPaneView';
import { TerminalPanel } from './TerminalPanel';

/** Optional Agent Web session bridge for scheduled-task exec UI (stay on scheduled-tasks tab). */
export type PaneSessionBridge = {
  getSessionLiveTimeline?: (sessionId: string) => TimelineEntry[] | null;
  getSessionTokenStats?: (sessionId: string) => SoloTokenStats | null;
  isSessionBusy?: (sessionId: string) => boolean;
  /** Send WITHOUT opening/switching to a session L2 tab. Same queue logic as pane composer send. */
  sendToSessionStay?: (sessionId: string, payload: ComposerSendPayload) => void | Promise<void>;
  stopSession?: (sessionId: string) => void;
  renderSessionPendingPanel?: (sessionId: string) => React.ReactNode;
  ensureSessionWatched?: (sessionId: string) => void;
};

export type PaneShellHandlers = {
  onSelectTab: (tab: ContentTab) => void;
  onCloseTab: (tab: ContentTab) => void;
  onReorderTabs?: (from: ContentTab, to: ContentTab) => void;
  onNewSession: () => void;
  onSplitRow: () => void;
  onSplitCol: () => void;
  onCloseAll: () => void;
  onClosePane: () => void;
  onFocus: () => void;
  onFileDirty?: (relPath: string, dirty: boolean) => void;
  /** Full Agent Web composer for this pane's active session (independent instance). */
  renderComposer?: (sessionId: string) => React.ReactNode;
  /** Rich timeline for session tabs that do not host the live chatSlot. */
  renderSessionChat?: (sessionId: string) => React.ReactNode;
  /** Empty live session → center composer + greeting (smooth dock on first send). */
  isComposerLanding?: (sessionId: string) => boolean;
  /** True while hydrating after refresh/connect — show loading, not New Chat. */
  isSessionLoading?: (sessionId: string) => boolean;
  sessionLoadingLabel?: string;
  /**
   * The welcome rows (shown while the pane has no tab open) and the header's view entries.
   * The two file-ish rows open the right-hand files rail; terminal and browser open a tab
   * in this pane. Optional so the shell still renders in tests and in drawers.
   */
  onOpenChanges?: () => void;
  onOpenFiles?: () => void;
  onOpenTerminal?: () => void;
  onOpenBrowser?: () => void;
} & PaneSessionBridge;

/**
 * The welcome rows' shortcuts come from utils/paneViews, so the rows, the tab bar's "open"
 * menu and the shortcut binder all offer exactly the same set.
 */
interface WorkspacePaneShellProps {
  paneId: string;
  tabs: PaneTabs;
  focused: boolean;
  canSplit: boolean;
  canClosePane: boolean;
  agentId: string;
  sessionAgentId?: string;
  rootPath: string;
  tabTitles: Record<string, string>;
  fileDirtyMap: Record<string, boolean>;
  /** Live WS session id hosted by this pane (may differ from the active L2 tab). */
  liveSessionId?: string | null;
  /** Live chat UI for the focused session pane (messages + header; no composer) */
  chatSlot?: React.ReactNode;
  handlers: PaneShellHandlers;
  /**
   * 聊天版面：整个标签/窗格那一行都不画。那里是「多个会话标签 + 分屏」的
   * 入口，而聊天版面一个联系人就是一个窗口 —— 留着它就不像在和人聊天了。
   */
  hideTabBar?: boolean;
}

export const WorkspacePaneShell: React.FC<WorkspacePaneShellProps> = ({
  paneId,
  tabs,
  focused,
  canSplit,
  canClosePane,
  agentId,
  sessionAgentId: _sessionAgentId,
  rootPath,
  tabTitles,
  fileDirtyMap,
  liveSessionId = null,
  chatSlot,
  handlers,
  hideTabBar = false,
}) => {
  const { t } = useTranslation();
  const labels: ContentTabLabel[] = useMemo(() => {
    return tabs.open.map((tab) => {
      if (tab.kind === 'file') {
        const name = tab.id.replace(/\\/g, '/').split('/').pop() || tab.id;
        return { tab, title: name, dirty: !!fileDirtyMap[tab.id] };
      }
      if (tab.kind === 'scheduled-tasks') {
        return { tab, title: tabTitles[tab.id]?.trim() || t('aiChat.scheduledTasks') };
      }
      if (tab.kind === 'tasks') {
        return { tab, title: tabTitles[tab.id]?.trim() || t('taskPanel.title') };
      }
      // A terminal and a browser view are singletons per pane, so their tab title is just
      // what they are (the reference shows `cmd` / `Browser`).
      if (tab.kind === 'terminal') {
        return { tab, title: tabTitles[tab.id]?.trim() || t('aiChat.panelTabs.terminal') };
      }
      if (tab.kind === 'browser') {
        return { tab, title: tabTitles[tab.id]?.trim() || t('aiChat.panelTabs.browser') };
      }
      const title = tabTitles[tab.id]?.trim() || tab.id;
      return { tab, title };
    });
  }, [tabs.open, tabTitles, fileDirtyMap, t]);

  const active = parseContentTabKey(tabs.activeKey);
  const openSessionTabs = useMemo(
    () => tabs.open.filter((t) => t.kind === 'session'),
    [tabs.open],
  );
  const openFileTabs = useMemo(
    () => tabs.open.filter((t) => t.kind === 'file'),
    [tabs.open],
  );
  // Lazy keep-alive: only mount file editors after the user has opened them
  // once in this pane. Revisit = instant (no TipTap remount). Closing a tab
  // drops it from the set so we do not keep stale editors forever.
  const [mountedFileIds, setMountedFileIds] = useState<string[]>([]);
  useEffect(() => {
    if (!active || active.kind !== 'file') return;
    setMountedFileIds((prev) => (prev.includes(active.id) ? prev : [...prev, active.id]));
  }, [active?.kind, active?.id]);
  useEffect(() => {
    const open = new Set(openFileTabs.map((t) => t.id));
    setMountedFileIds((prev) => {
      const next = prev.filter((id) => open.has(id));
      return next.length === prev.length ? prev : next;
    });
  }, [openFileTabs]);
  const keptFileTabs = useMemo(() => {
    const ids = new Set(mountedFileIds);
    // Include active file synchronously so first open does not wait a frame
    // for the mount-tracking effect (would otherwise flash empty).
    if (active?.kind === 'file') ids.add(active.id);
    return openFileTabs.filter((t) => ids.has(t.id));
  }, [openFileTabs, mountedFileIds, active]);
  const sessionLoading =
    !!active &&
    active.kind === 'session' &&
    (handlers.isSessionLoading?.(active.id) ?? false);
  const landing =
    !sessionLoading &&
    !!active &&
    active.kind === 'session' &&
    !!handlers.renderComposer &&
    (handlers.isComposerLanding?.(active.id) ?? false);
  const showSessions = !!active && active.kind === 'session';
  const showFiles = !!active && active.kind === 'file';
  const showScheduled = !!active && active.kind === 'scheduled-tasks';
  const showTasks = !!active && active.kind === 'tasks';
  const showTerminal = !!active && active.kind === 'terminal';
  const showBrowser = !!active && active.kind === 'browser';
  const showMod = !!active && active.kind === 'mod';

  // One place from which both the welcome rows and the tab bar's menu take their action.
  const viewHandlers: Record<PaneViewId, (() => void) | undefined> = {
    changes: handlers.onOpenChanges,
    files: handlers.onOpenFiles,
    terminal: handlers.onOpenTerminal,
    browser: handlers.onOpenBrowser,
  };

  // A terminal is a live process, not a view: it stays mounted once opened, hidden when
  // another tab is active. Without this, switching tabs would kill the shell and start a
  // new one on the way back (losing the cwd, the history and anything still running).
  const [terminalMounted, setTerminalMounted] = useState(false);
  useEffect(() => {
    if (showTerminal) setTerminalMounted(true);
  }, [showTerminal]);
  // Closing the terminal tab ends the process: nothing should keep a hidden shell alive.
  useEffect(() => {
    if (terminalMounted && !tabs.open.some((t) => t.kind === 'terminal')) setTerminalMounted(false);
  }, [tabs.open, terminalMounted]);

  // The browser is kept mounted for the same reason: remounting a webview reloads the page
  // (losing a login, a form, a scroll position) every time the user glances at another tab.
  const [browserMounted, setBrowserMounted] = useState(false);
  useEffect(() => {
    if (showBrowser) setBrowserMounted(true);
  }, [showBrowser]);
  useEffect(() => {
    if (browserMounted && !tabs.open.some((t) => t.kind === 'browser')) setBrowserMounted(false);
  }, [tabs.open, browserMounted]);

  // Active session tab → claim watch + refresh token stats for this sid.
  useEffect(() => {
    if (!active || active.kind !== 'session') return;
    handlers.ensureSessionWatched?.(active.id);
    // intentionally only when the active session tab changes
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active?.kind, active?.id]);

  return (
    <div
      className={`flex flex-col min-w-0 min-h-0 flex-1 h-full border ${
        focused ? 'border-primary/40 ring-1 ring-primary/40' : 'border-transparent'
      }`}
      data-pane-id={paneId}
      data-pane-focused={focused ? '1' : '0'}
    >
      {!hideTabBar ? (
      <div
        className="flex-shrink-0 bg-panel border-0 border-b border-border/40"
        onMouseDown={(e) => {
          e.stopPropagation();
          handlers.onFocus();
        }}
      >
        <ContentTabBar
          tabs={labels}
          activeKey={tabs.activeKey}
          onSelect={(tab) => {
            handlers.onFocus();
            handlers.onSelectTab(tab);
          }}
          onClose={handlers.onCloseTab}
          onReorder={
            handlers.onReorderTabs
              ? (from, to) => {
                  handlers.onFocus();
                  handlers.onReorderTabs?.(from, to);
                }
              : undefined
          }
          onNewSession={() => {
            handlers.onFocus();
            handlers.onNewSession();
          }}
          onSplitRow={handlers.onSplitRow}
          onSplitCol={handlers.onSplitCol}
          canSplit={canSplit}
          onCloseAll={handlers.onCloseAll}
          onClosePane={handlers.onClosePane}
          canClosePane={canClosePane}
          onOpenView={(view) => viewHandlers[view]?.()}
        />
      </div>
      ) : null}
      <div
        className="flex-1 min-h-0 flex flex-col overflow-hidden"
        onMouseDown={() => handlers.onFocus()}
      >
        {!active ? (
          /* The pane's welcome: what can be opened here, with the shortcut that opens it.
             The rows are the entry point the reference shows, so nothing is reachable
             only by knowing a key. */
          <div
            className="flex-1 min-h-0 overflow-auto px-4 py-5"
            data-testid="pane-welcome"
            onClick={handlers.onFocus}
          >
            <div className="text-[13px] font-medium text-textMain">{t('aiChat.welcome.title')}</div>
            <div className="mt-0.5 text-[11px] text-textMuted">{t('aiChat.welcome.subtitle')}</div>
            <div className="mt-3 space-y-0.5" data-testid="pane-welcome-rows">
              {/* Starting a session is not a view, so it is a row of its own here rather than an
                  entry in PANE_VIEWS — the tab bar and the files panel render that table too, and a
                  session has no business appearing among the views they list. */}
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  handlers.onFocus();
                  handlers.onNewSession();
                }}
                data-testid="pane-welcome-new-session"
                className="flex w-full items-center gap-2.5 rounded-lg px-2 py-1.5 text-left hover:bg-primary/10"
              >
                <Plus size={13} className="shrink-0 text-textMuted" />
                <span className="min-w-0 flex-1 truncate text-[12px] text-textMain">
                  {t('aiChat.views.newSession')}
                </span>
                <span className="min-w-0 flex-[2] truncate text-[11px] text-textMuted">
                  {t('aiChat.views.newSessionHint')}
                </span>
              </button>
              {PANE_VIEWS.map((view) => {
                const Icon = view.Icon;
                const onClick = viewHandlers[view.id];
                return (
                  <button
                    key={view.id}
                    type="button"
                    disabled={!onClick}
                    onClick={(e) => {
                      e.stopPropagation();
                      onClick?.();
                    }}
                    data-testid={`pane-view-${view.id}`}
                    className="flex w-full items-center gap-2.5 rounded-lg px-2 py-1.5 text-left hover:bg-primary/10 disabled:opacity-40"
                  >
                    <Icon size={13} className="shrink-0 text-textMuted" />
                    <span className="min-w-0 flex-1 truncate text-[12px] text-textMain">
                      {t(view.labelKey)}
                    </span>
                    <span className="min-w-0 flex-[2] truncate text-[11px] text-textMuted">
                      {t(view.hintKey)}
                    </span>
                    {view.shortcut ? (
                      <kbd className="shrink-0 rounded border border-border px-1 py-0.5 font-mono text-[10px] text-textMuted">
                        {view.shortcut}
                      </kbd>
                    ) : null}
                  </button>
                );
              })}
            </div>
          </div>
        ) : null}

        {/* Terminal (hidden while another tab is active — the shell keeps running). */}
        {terminalMounted ? (
          <div
            className={showTerminal ? 'flex-1 min-h-0 flex flex-col' : 'hidden'}
            aria-hidden={!showTerminal}
            data-testid="pane-terminal"
          >
            <ErrorBoundary label="terminal" resetKey={`${agentId}:terminal`}>
              <TerminalPanel agentId={agentId} rootPath={rootPath} />
            </ErrorBoundary>
          </div>
        ) : null}

        {browserMounted ? (
          <div
            className={showBrowser ? 'flex-1 min-h-0 flex flex-col' : 'hidden'}
            aria-hidden={!showBrowser}
            data-testid="pane-browser"
          >
            <ErrorBoundary label="browser" resetKey={`${agentId}:browser`}>
              <BrowserPanel agentId={agentId} />
            </ErrorBoundary>
          </div>
        ) : null}

        {/* Keep every open file tab mounted (hidden when inactive) — same
            pattern as sessions. Remounting TipTap/FileDocumentEditor on each
            L2 switch was the main “wait to load” cost even with content cache. */}
        {keptFileTabs.map((tab) => {
          const isActive = showFiles && !!active && active.id === tab.id;
          return (
            <div
              key={`keep-file-${paneId}-${tab.id}`}
              className={isActive ? 'flex-1 min-h-0 flex flex-col' : 'hidden'}
              aria-hidden={!isActive}
            >
              <WorkspaceFileEditor
                agentId={agentId}
                rootPath={rootPath}
                relPath={tab.id}
                onDirtyChange={(dirty) => handlers.onFileDirty?.(tab.id, dirty)}
              />
            </div>
          );
        })}

        {/* An L2 panel is an independent surface: a render throw inside one must
            cost the user that pane, not the whole workspace. */}
        {showScheduled ? (
          <ErrorBoundary label="scheduled-tasks" resetKey={`${agentId}:scheduled`}>
            <ScheduledTasksPage
              agentName={agentId}
              rootPath={rootPath}
              sessionBridge={{
                getSessionLiveTimeline: handlers.getSessionLiveTimeline,
                getSessionTokenStats: handlers.getSessionTokenStats,
                isSessionBusy: handlers.isSessionBusy,
                sendToSessionStay: handlers.sendToSessionStay,
                stopSession: handlers.stopSession,
                renderSessionPendingPanel: handlers.renderSessionPendingPanel,
                ensureSessionWatched: handlers.ensureSessionWatched,
              }}
            />
          </ErrorBoundary>
        ) : null}

        {showTasks ? (
          <ErrorBoundary label="tasks" resetKey={`${agentId}:tasks`}>
            <TaskPanelPage agentName={agentId} rootPath={rootPath} />
          </ErrorBoundary>
        ) : null}

        {/* A mod's pane: the id is the one the mod passed to `$.ui.open`, and the
            body is the same validated tree every other slot renders. */}
        {showMod ? (
          <ErrorBoundary label="mod-pane" resetKey={`${agentId}:${active?.id}`}>
            <ModPaneView paneId={String(active?.id || '')} />
          </ErrorBoundary>
        ) : null}

        {/* Session shell stays mounted while any session tab is open, so
            file ↔ session ↔ file also stays warm. */}
        {openSessionTabs.length > 0 || showSessions ? (
          <div
            className={
              showSessions
                ? `os-chat-session-shell relative flex-1 min-h-0 ${landing ? 'is-landing' : 'is-docked'}`
                : 'hidden'
            }
            aria-hidden={!showSessions}
          >
            {sessionLoading ? (
              <div
                className="absolute inset-0 z-30 flex flex-col items-center justify-center gap-3 bg-stage/90 text-textMuted"
                role="status"
                aria-live="polite"
              >
                <OpenSquadLoader size={28} />
                <p className="text-sm">
                  {handlers.sessionLoadingLabel || t('aiChat.loadingSession')}
                </p>
              </div>
            ) : null}
            <div className="os-chat-session-messages relative min-h-0 flex-1">
              {sessionLoading ? null : openSessionTabs.map((tab) => {
                const isActive = showSessions && active.id === tab.id;
                const useLiveSlot = !!chatSlot && !!liveSessionId && tab.id === liveSessionId;
                return (
                  <div
                    key={`keep-${paneId}-${tab.id}`}
                    className={isActive ? 'h-full min-h-0 flex flex-col' : 'hidden'}
                    aria-hidden={!isActive}
                  >
                    {useLiveSlot
                      ? chatSlot
                      : handlers.renderSessionChat
                        ? handlers.renderSessionChat(tab.id)
                        : (
                          <div
                            className="flex-1 flex items-center justify-center text-[12px] text-textMuted px-4 text-center"
                            onClick={handlers.onFocus}
                          >
                            {t('aiChat.sessionUnavailable')}
                          </div>
                        )}
                  </div>
                );
              })}
              {!sessionLoading && showSessions && openSessionTabs.length === 0 ? (
                <div
                  className="flex-1 flex items-center justify-center text-[12px] text-textMuted px-4 text-center"
                  onClick={handlers.onFocus}
                >
                  {t('aiChat.sessionUnavailable')}
                </div>
              ) : null}
            </div>
            {handlers.renderComposer && showSessions && !sessionLoading ? (
              <ComposerLandingDock landing={landing} seedKey={active.id}>
                {handlers.renderComposer(active.id)}
              </ComposerLandingDock>
            ) : null}
          </div>
        ) : null}
      </div>
    </div>
  );
};
