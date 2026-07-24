/**
 * PaneSplitLayout — recursive row/col split tree with draggable dividers.
 */
import React, { useCallback, useRef } from 'react';
import type { SplitNode } from '../../utils/workspaceStore';
import { collectLeaves, MAX_SPLIT_DEPTH, parseContentTabKey } from '../../utils/workspaceStore';
import { WorkspacePaneShell, type PaneShellHandlers } from './WorkspacePaneShell';
import type { PaneTabs } from '../../utils/workspaceStore';

export type PaneLayoutHandlers = {
  makePaneHandlers: (paneId: string) => PaneShellHandlers;
};

interface PaneSplitLayoutProps {
  layout: SplitNode;
  focusedPaneId: string | null;
  /** Currently live WS session — chatSlot only mounts on the leaf that owns this tab */
  liveSessionId?: string | null;
  /** Agent dir_name for workspace file IO */
  agentId: string;
  /** Agent id for session history API (may differ from dir_name) */
  sessionAgentId?: string;
  rootPath: string;
  tabTitles: Record<string, string>;
  fileDirtyMap: Record<string, boolean>;
  /** Render live chat into the leaf whose active session === liveSessionId */
  renderChatSlot: (paneId: string) => React.ReactNode;
  onResizeSplit: (splitId: string, ratio: number) => void;
  handlers: PaneLayoutHandlers;
}

const SplitDivider: React.FC<{
  direction: 'row' | 'col';
  onDrag: (deltaPx: number, containerSize: number) => void;
}> = ({ direction, onDrag }) => {
  const startRef = useRef<{ pos: number; size: number } | null>(null);

  const onPointerDown = (e: React.PointerEvent) => {
    e.preventDefault();
    const parent = (e.currentTarget.parentElement as HTMLElement) || null;
    if (!parent) return;
    const rect = parent.getBoundingClientRect();
    const size = direction === 'row' ? rect.width : rect.height;
    startRef.current = {
      pos: direction === 'row' ? e.clientX : e.clientY,
      size,
    };
    e.currentTarget.setPointerCapture(e.pointerId);
    document.body.style.cursor = direction === 'row' ? 'col-resize' : 'row-resize';
    document.body.style.userSelect = 'none';
  };

  const onPointerMove = (e: React.PointerEvent) => {
    if (!startRef.current) return;
    const pos = direction === 'row' ? e.clientX : e.clientY;
    const delta = pos - startRef.current.pos;
    startRef.current.pos = pos;
    onDrag(delta, startRef.current.size);
  };

  const onPointerUp = (e: React.PointerEvent) => {
    if (!startRef.current) return;
    startRef.current = null;
    try {
      e.currentTarget.releasePointerCapture(e.pointerId);
    } catch {
      /* */
    }
    document.body.style.cursor = '';
    document.body.style.userSelect = '';
  };

  return (
    <div
      role="separator"
      aria-orientation={direction === 'row' ? 'vertical' : 'horizontal'}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      className={
        direction === 'row'
          ? 'w-1.5 flex-shrink-0 cursor-col-resize hover:bg-primary/40 bg-border/60 z-10'
          : 'h-1.5 flex-shrink-0 cursor-row-resize hover:bg-primary/40 bg-border/60 z-10'
      }
    />
  );
};

/** Stable leaf that should host the live chatSlot for liveSessionId.
 * Prefer the leaf whose active tab is that session; otherwise any leaf that
 * still has it in open tabs (so switching L2 tabs does not unmount live chat). */
function findLiveOwnerPaneId(
  layout: SplitNode,
  liveSessionId: string | null | undefined,
): string | null {
  if (!liveSessionId) return null;
  let fallback: string | null = null;
  for (const leaf of collectLeaves(layout)) {
    const active = parseContentTabKey(leaf.tabs.activeKey);
    if (active?.kind === 'session' && active.id === liveSessionId) {
      return leaf.id;
    }
    if (
      !fallback
      && (leaf.tabs.open || []).some((t) => t.kind === 'session' && t.id === liveSessionId)
    ) {
      fallback = leaf.id;
    }
  }
  return fallback;
}

function NodeView({
  node,
  depth,
  focusedPaneId,
  liveSessionId,
  liveOwnerPaneId,
  agentId,
  sessionAgentId,
  rootPath,
  tabTitles,
  fileDirtyMap,
  renderChatSlot,
  onResizeSplit,
  handlers,
  leafCount,
}: {
  node: SplitNode;
  depth: number;
  focusedPaneId: string | null;
  liveSessionId?: string | null;
  liveOwnerPaneId: string | null;
  agentId: string;
  sessionAgentId: string;
  rootPath: string;
  tabTitles: Record<string, string>;
  fileDirtyMap: Record<string, boolean>;
  renderChatSlot: (paneId: string) => React.ReactNode;
  onResizeSplit: (splitId: string, ratio: number) => void;
  handlers: PaneLayoutHandlers;
  leafCount: number;
}) {
  const ratioRef = useRef(node.type === 'split' ? node.ratio : 0.5);
  if (node.type === 'split') ratioRef.current = node.ratio;

  const onDrag = useCallback(
    (deltaPx: number, containerSize: number) => {
      if (node.type !== 'split' || containerSize <= 0) return;
      const next = ratioRef.current + deltaPx / containerSize;
      ratioRef.current = next;
      onResizeSplit(node.id, next);
    },
    [node, onResizeSplit],
  );

  if (node.type === 'leaf') {
    const canSplit = depth < MAX_SPLIT_DEPTH;
    const tabs: PaneTabs = node.tabs;
    // Keep live chatSlot mounted on this leaf as long as the live session tab
    // is still open here — even when another L2 tab is active.
    const hostsLiveSession =
      !!liveOwnerPaneId
      && node.id === liveOwnerPaneId
      && !!liveSessionId;
    return (
      <WorkspacePaneShell
        paneId={node.id}
        tabs={tabs}
        focused={focusedPaneId === node.id}
        canSplit={canSplit}
        canClosePane={leafCount > 1}
        agentId={agentId}
        sessionAgentId={sessionAgentId}
        rootPath={rootPath}
        tabTitles={tabTitles}
        fileDirtyMap={fileDirtyMap}
        liveSessionId={hostsLiveSession ? liveSessionId : null}
        chatSlot={hostsLiveSession ? renderChatSlot(node.id) : undefined}
        handlers={handlers.makePaneHandlers(node.id)}
      />
    );
  }

  const isRow = node.direction === 'row';
  return (
    <div
      className={`flex min-w-0 min-h-0 flex-1 overflow-hidden ${isRow ? 'flex-row' : 'flex-col'}`}
    >
      <div
        className="min-w-0 min-h-0 flex flex-col overflow-hidden"
        style={
          isRow
            ? { width: `${node.ratio * 100}%`, flex: 'none' }
            : { height: `${node.ratio * 100}%`, flex: 'none' }
        }
      >
        <NodeView
          node={node.a}
          depth={depth + 1}
          focusedPaneId={focusedPaneId}
          liveSessionId={liveSessionId}
          liveOwnerPaneId={liveOwnerPaneId}
          agentId={agentId}
          sessionAgentId={sessionAgentId}
          rootPath={rootPath}
          tabTitles={tabTitles}
          fileDirtyMap={fileDirtyMap}
          renderChatSlot={renderChatSlot}
          onResizeSplit={onResizeSplit}
          handlers={handlers}
          leafCount={leafCount}
        />
      </div>
      <SplitDivider direction={node.direction} onDrag={onDrag} />
      <div className="min-w-0 min-h-0 flex-1 flex flex-col overflow-hidden">
        <NodeView
          node={node.b}
          depth={depth + 1}
          focusedPaneId={focusedPaneId}
          liveSessionId={liveSessionId}
          liveOwnerPaneId={liveOwnerPaneId}
          agentId={agentId}
          sessionAgentId={sessionAgentId}
          rootPath={rootPath}
          tabTitles={tabTitles}
          fileDirtyMap={fileDirtyMap}
          renderChatSlot={renderChatSlot}
          onResizeSplit={onResizeSplit}
          handlers={handlers}
          leafCount={leafCount}
        />
      </div>
    </div>
  );
}

export const PaneSplitLayout: React.FC<PaneSplitLayoutProps> = ({
  layout,
  focusedPaneId,
  liveSessionId,
  agentId,
  sessionAgentId,
  rootPath,
  tabTitles,
  fileDirtyMap,
  renderChatSlot,
  onResizeSplit,
  handlers,
}) => {
  const leafCount = collectLeaves(layout).length;
  const sid = sessionAgentId || agentId;
  const liveOwnerPaneId = findLiveOwnerPaneId(layout, liveSessionId);
  return (
    <div className="flex-1 min-w-0 min-h-0 flex flex-col overflow-hidden">
      <NodeView
        node={layout}
        depth={1}
        focusedPaneId={focusedPaneId}
        liveSessionId={liveSessionId}
        liveOwnerPaneId={liveOwnerPaneId}
        agentId={agentId}
        sessionAgentId={sid}
        rootPath={rootPath}
        tabTitles={tabTitles}
        fileDirtyMap={fileDirtyMap}
        renderChatSlot={renderChatSlot}
        onResizeSplit={onResizeSplit}
        handlers={handlers}
        leafCount={leafCount}
      />
    </div>
  );
};
