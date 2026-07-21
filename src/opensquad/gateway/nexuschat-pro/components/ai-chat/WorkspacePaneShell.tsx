/**
 * WorkspacePaneShell — one split leaf: L2 tab bar + content (chat / file / preview) + composer.
 */
import React, { useMemo } from 'react';
import { ContentTabBar, type ContentTabLabel } from './ContentTabBar';
import { WorkspaceFileEditor } from './WorkspaceFileEditor';
import type { ContentTab, PaneTabs } from '../../utils/workspaceStore';
import { parseContentTabKey } from '../../utils/workspaceStore';

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
};

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
  chatSlot,
  handlers,
}) => {
  const labels: ContentTabLabel[] = useMemo(() => {
    return tabs.open.map((tab) => {
      if (tab.kind === 'file') {
        const name = tab.id.replace(/\\/g, '/').split('/').pop() || tab.id;
        return { tab, title: name, dirty: !!fileDirtyMap[tab.id] };
      }
      const title = tabTitles[tab.id]?.trim() || tab.id;
      return { tab, title };
    });
  }, [tabs.open, tabTitles, fileDirtyMap]);

  const active = parseContentTabKey(tabs.activeKey);

  return (
    <div
      className={`flex flex-col min-w-0 min-h-0 flex-1 h-full border ${
        focused ? 'border-primary/40 ring-1 ring-primary/40' : 'border-transparent'
      }`}
      data-pane-id={paneId}
      data-pane-focused={focused ? '1' : '0'}
    >
      <div
        className="flex-shrink-0 border-b border-border bg-stage"
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
            无打开的标签 — 点击 + 新建对话，或从右侧打开文件
          </div>
        ) : active.kind === 'file' ? (
          <WorkspaceFileEditor
            agentId={agentId}
            rootPath={rootPath}
            relPath={active.id}
            onDirtyChange={(dirty) => handlers.onFileDirty?.(active.id, dirty)}
          />
        ) : (
          <>
            <div className="flex-1 min-h-0 flex flex-col overflow-hidden">
              {chatSlot
                ? chatSlot
                : handlers.renderSessionChat
                  ? handlers.renderSessionChat(active.id)
                  : (
                    <div
                      className="flex-1 flex items-center justify-center text-[12px] text-textMuted px-4 text-center"
                      onClick={handlers.onFocus}
                    >
                      会话内容不可用
                    </div>
                  )}
            </div>
            {handlers.renderComposer ? handlers.renderComposer(active.id) : null}
          </>
        )}
      </div>
    </div>
  );
};
