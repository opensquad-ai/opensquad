/**
 * WorkspacePaneShell — one split leaf: L2 tab bar + content (chat / file / preview) + composer.
 */
import React, { useMemo, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { OpenSquadLoader } from '../OpenSquadLoader';
import { ContentTabBar, type ContentTabLabel } from './ContentTabBar';
import { WorkspaceFileEditor } from './WorkspaceFileEditor';
import { ComposerLandingDock } from './ComposerLandingDock';
import { ScheduledTasksPage } from './ScheduledTasksPage';
import type { ComposerSendPayload } from './AgentWebComposer';
import type { SoloTokenStats } from './SoloContextFooter';
import type { TimelineEntry } from '../../utils/aiChatTimeline';
import type { ContentTab, PaneTabs } from '../../utils/workspaceStore';
import { parseContentTabKey } from '../../utils/workspaceStore';

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
} & PaneSessionBridge;

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
        />
      </div>
      <div
        className="flex-1 min-h-0 flex flex-col overflow-hidden"
        onMouseDown={() => handlers.onFocus()}
      >
        {!active ? (
          <div
            className="flex-1 flex items-center justify-center text-[12px] text-textMuted px-4 text-center"
            onClick={handlers.onFocus}
          >
            {t('aiChat.noOpenTabsHint')}
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

        {showScheduled ? (
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
