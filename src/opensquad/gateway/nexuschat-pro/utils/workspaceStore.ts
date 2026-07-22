/**
 * Per-agent workspace registry + open chrome (L1 tabs + L2 split-pane tree).
 * Persisted in localStorage; closing a tab/pane never deletes registry or session data.
 */
import { folderLabel } from './cwdRecents';
import { loadSessionProjectMeta, saveSessionProjectMeta } from './sessionProjectMeta';

export type Workspace = {
  id: string;
  name: string;
  rootPath: string;
  createdAt: number;
};

export type ContentTabKind = 'session' | 'file' | 'scheduled-tasks';

export type ContentTab = {
  kind: ContentTabKind;
  /** sessionId or workspace-relative file path */
  id: string;
};

/** @deprecated alias — leaf tabs */
export type WorkspaceTabsState = PaneTabs;

export type PaneTabs = {
  open: ContentTab[];
  activeKey: string | null;
};

export type SplitDirection = 'row' | 'col';

export type SplitNode =
  | { type: 'leaf'; id: string; tabs: PaneTabs }
  | {
      type: 'split';
      id: string;
      direction: SplitDirection;
      /** 0..1 share for child a */
      ratio: number;
      a: SplitNode;
      b: SplitNode;
    };

export type OpenChromeState = {
  openWorkspaceIds: string[];
  activeWorkspaceId: string | null;
  focusedPaneId: string | null;
  layoutByWorkspace: Record<string, SplitNode>;
  /** Legacy flat tabs — migrated into layoutByWorkspace on load */
  tabsByWorkspace?: Record<string, PaneTabs>;
};

export type WorkspaceStoreSnapshot = {
  workspaces: Workspace[];
  chrome: OpenChromeState;
  migrated?: boolean;
};

/** Max nesting depth of the split tree (root leaf = 1). */
export const MAX_SPLIT_DEPTH = 3;

function uuid(): string {
  if (typeof crypto !== 'undefined' && crypto.randomUUID) return crypto.randomUUID();
  return `ws_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 9)}`;
}

function storageKey(agentId: string): string {
  return `opensquad.workspaces:${agentId}`;
}

export function normPath(p: string): string {
  return (p || '').replace(/\\/g, '/').replace(/\/+$/, '').trim();
}

export function pathsEqual(a: string, b: string): boolean {
  return normPath(a).toLowerCase() === normPath(b).toLowerCase();
}

export function contentTabKey(tab: ContentTab): string {
  return `${tab.kind}:${tab.id}`;
}

export function parseContentTabKey(key: string | null): ContentTab | null {
  if (!key) return null;
  const i = key.indexOf(':');
  if (i < 0) return null;
  const kind = key.slice(0, i) as ContentTabKind;
  const id = key.slice(i + 1);
  if ((kind !== 'session' && kind !== 'file' && kind !== 'scheduled-tasks') || !id) return null;
  return { kind, id };
}

export function emptyPaneTabs(): PaneTabs {
  return { open: [], activeKey: null };
}

export function createLeaf(tabs?: PaneTabs): SplitNode {
  return { type: 'leaf', id: uuid(), tabs: tabs || emptyPaneTabs() };
}

function emptyChrome(): OpenChromeState {
  return {
    openWorkspaceIds: [],
    activeWorkspaceId: null,
    focusedPaneId: null,
    layoutByWorkspace: {},
  };
}

function emptySnapshot(): WorkspaceStoreSnapshot {
  return { workspaces: [], chrome: emptyChrome(), migrated: false };
}

/** Read-only walk — never mints ids (safe for React render). */
export function collectLeaves(node: SplitNode): Array<{ id: string; tabs: PaneTabs }> {
  if (!node || typeof node !== 'object') return [];
  if (node.type === 'leaf') {
    if (!node.id) return [];
    return [{ id: node.id, tabs: node.tabs || emptyPaneTabs() }];
  }
  if (node.type === 'split' && node.a && node.b) {
    return [...collectLeaves(node.a), ...collectLeaves(node.b)];
  }
  return [];
}

export function findLeaf(node: SplitNode, paneId: string): SplitNode | null {
  if (!node || typeof node !== 'object') return null;
  if (node.type === 'leaf') return node.id === paneId ? node : null;
  if (node.type === 'split' && node.a && node.b) {
    return findLeaf(node.a, paneId) || findLeaf(node.b, paneId);
  }
  return null;
}

export function leafDepth(node: SplitNode, paneId: string, depth = 1): number | null {
  if (!node || typeof node !== 'object') return null;
  if (node.type === 'leaf') return node.id === paneId ? depth : null;
  if (node.type === 'split' && node.a && node.b) {
    const da = leafDepth(node.a, paneId, depth + 1);
    if (da != null) return da;
    return leafDepth(node.b, paneId, depth + 1);
  }
  return null;
}

export function treeDepth(node: SplitNode): number {
  if (!node || typeof node !== 'object') return 1;
  if (node.type === 'leaf') return 1;
  if (node.type === 'split' && node.a && node.b) {
    return 1 + Math.max(treeDepth(node.a), treeDepth(node.b));
  }
  return 1;
}

/** Coerce legacy / partial nodes into a valid SplitNode (preserves ids when present). */
export function coerceSplitNode(node: any): SplitNode {
  if (!node || typeof node !== 'object') return createLeaf();
  if (node.type === 'split' && node.a && node.b) {
    return {
      type: 'split',
      id: typeof node.id === 'string' && node.id ? node.id : uuid(),
      direction: node.direction === 'col' ? 'col' : 'row',
      ratio: typeof node.ratio === 'number' ? node.ratio : 0.5,
      a: coerceSplitNode(node.a),
      b: coerceSplitNode(node.b),
    };
  }
  // Proper leaf, or legacy { open, activeKey } mistakenly stored as layout root
  if (node.type === 'leaf' || Array.isArray(node.open) || node.tabs) {
    const tabs: PaneTabs = node.tabs
      ? {
          open: Array.isArray(node.tabs.open) ? node.tabs.open : [],
          activeKey: node.tabs.activeKey ?? null,
        }
      : {
          open: Array.isArray(node.open) ? node.open : [],
          activeKey: node.activeKey ?? null,
        };
    return {
      type: 'leaf',
      id: typeof node.id === 'string' && node.id ? node.id : uuid(),
      tabs,
    };
  }
  return createLeaf();
}

function updateLeafTabs(
  node: SplitNode,
  paneId: string,
  updater: (tabs: PaneTabs) => PaneTabs,
): SplitNode {
  if (node.type === 'leaf') {
    if (node.id !== paneId) return node;
    return { ...node, tabs: updater(node.tabs || emptyPaneTabs()) };
  }
  return {
    ...node,
    a: updateLeafTabs(node.a, paneId, updater),
    b: updateLeafTabs(node.b, paneId, updater),
  };
}

/** Replace a leaf with a split; returns new tree or null if not found / depth exceeded. */
function splitLeafInTree(
  node: SplitNode,
  paneId: string,
  direction: SplitDirection,
  depth: number,
): { tree: SplitNode; newLeafId: string } | null {
  if (node.type === 'leaf') {
    if (node.id !== paneId) return null;
    if (depth >= MAX_SPLIT_DEPTH) return null;
    const newLeaf = createLeaf();
    return {
      newLeafId: newLeaf.id,
      tree: {
        type: 'split',
        id: uuid(),
        direction,
        ratio: 0.5,
        a: node,
        b: newLeaf,
      },
    };
  }
  const left = splitLeafInTree(node.a, paneId, direction, depth + 1);
  if (left) return { newLeafId: left.newLeafId, tree: { ...node, a: left.tree } };
  const right = splitLeafInTree(node.b, paneId, direction, depth + 1);
  if (right) return { newLeafId: right.newLeafId, tree: { ...node, b: right.tree } };
  return null;
}

/** Remove a leaf; promote sibling. Returns null if would remove the only leaf. */
function removeLeafFromTree(
  node: SplitNode,
  paneId: string,
): { tree: SplitNode; focusId: string } | null {
  if (node.type === 'leaf') return null;
  if (node.a.type === 'leaf' && node.a.id === paneId) {
    const leaves = collectLeaves(node.b);
    return { tree: node.b, focusId: leaves[0]?.id || node.b.id };
  }
  if (node.b.type === 'leaf' && node.b.id === paneId) {
    const leaves = collectLeaves(node.a);
    return { tree: node.a, focusId: leaves[0]?.id || node.a.id };
  }
  const left = removeLeafFromTree(node.a, paneId);
  if (left) return { tree: { ...node, a: left.tree }, focusId: left.focusId };
  const right = removeLeafFromTree(node.b, paneId);
  if (right) return { tree: { ...node, b: right.tree }, focusId: right.focusId };
  return null;
}

function setSplitRatioInTree(node: SplitNode, splitId: string, ratio: number): SplitNode {
  if (node.type === 'leaf') return node;
  if (node.id === splitId) {
    return { ...node, ratio: Math.min(0.85, Math.max(0.15, ratio)) };
  }
  return {
    ...node,
    a: setSplitRatioInTree(node.a, splitId, ratio),
    b: setSplitRatioInTree(node.b, splitId, ratio),
  };
}

/** Migrate legacy tabsByWorkspace → layoutByWorkspace; ensure layouts for open workspaces. */
export function normalizeChrome(chrome: OpenChromeState): OpenChromeState {
  const next: OpenChromeState = {
    openWorkspaceIds: chrome.openWorkspaceIds || [],
    activeWorkspaceId: chrome.activeWorkspaceId ?? null,
    focusedPaneId: chrome.focusedPaneId ?? null,
    layoutByWorkspace: { ...(chrome.layoutByWorkspace || {}) },
    tabsByWorkspace: chrome.tabsByWorkspace,
  };

  const legacy = chrome.tabsByWorkspace || {};
  for (const [wsId, tabs] of Object.entries(legacy)) {
    if (!next.layoutByWorkspace[wsId]) {
      const leaf = createLeaf({
        open: Array.isArray(tabs?.open) ? tabs.open : [],
        activeKey: tabs?.activeKey ?? null,
      });
      next.layoutByWorkspace[wsId] = leaf;
      if (!next.focusedPaneId) next.focusedPaneId = leaf.id;
    }
  }

  for (const wsId of next.openWorkspaceIds) {
    if (!next.layoutByWorkspace[wsId]) {
      const leaf = createLeaf();
      next.layoutByWorkspace[wsId] = leaf;
      if (!next.focusedPaneId) next.focusedPaneId = leaf.id;
    }
  }

  // Stabilize malformed / legacy layout nodes (assign ids once, keep thereafter)
  for (const wsId of Object.keys(next.layoutByWorkspace)) {
    next.layoutByWorkspace[wsId] = coerceSplitNode(next.layoutByWorkspace[wsId]);
  }

  const active = next.activeWorkspaceId;
  if (active && next.layoutByWorkspace[active]) {
    const leaves = collectLeaves(next.layoutByWorkspace[active]);
    if (!next.focusedPaneId || !leaves.some((l) => l.id === next.focusedPaneId)) {
      next.focusedPaneId = leaves[0]?.id ?? null;
    }
  }

  return next;
}

export function loadWorkspaceStore(agentId: string): WorkspaceStoreSnapshot {
  if (!agentId) return emptySnapshot();
  try {
    const raw = localStorage.getItem(storageKey(agentId));
    if (!raw) return emptySnapshot();
    const parsed = JSON.parse(raw) as WorkspaceStoreSnapshot;
    if (!parsed || !Array.isArray(parsed.workspaces)) return emptySnapshot();
    const chrome = normalizeChrome(parsed.chrome || emptyChrome());
    const snap: WorkspaceStoreSnapshot = {
      workspaces: parsed.workspaces,
      chrome,
      migrated: !!parsed.migrated,
    };
    // Persist coerced ids / migrated layouts so paneId stays stable across loads
    try {
      const hadLegacyTabs = !!(parsed.chrome && parsed.chrome.tabsByWorkspace);
      const before = JSON.stringify(parsed.chrome?.layoutByWorkspace || {});
      const after = JSON.stringify(chrome.layoutByWorkspace);
      if (hadLegacyTabs || before !== after) {
        saveWorkspaceStore(agentId, snap);
      }
    } catch {
      /* ignore */
    }
    return snap;
  } catch {
    return emptySnapshot();
  }
}

export function saveWorkspaceStore(agentId: string, snap: WorkspaceStoreSnapshot): void {
  if (!agentId) return;
  try {
    // Drop legacy flat map when saving so layout is the source of truth
    const chrome = { ...snap.chrome };
    delete chrome.tabsByWorkspace;
    localStorage.setItem(
      storageKey(agentId),
      JSON.stringify({ ...snap, chrome }),
    );
  } catch {
    /* ignore */
  }
  try {
    window.dispatchEvent(
      new CustomEvent('opensquad-workspaces-changed', { detail: { agentId } }),
    );
  } catch {
    /* ignore */
  }
}

export const WORKSPACES_CHANGED_EVENT = 'opensquad-workspaces-changed';

export function ensureWorkspace(
  agentId: string,
  rootPath: string,
  name?: string,
): Workspace {
  const path = rootPath.trim();
  if (!path) throw new Error('rootPath required');
  const snap = loadWorkspaceStore(agentId);
  const existing = snap.workspaces.find((w) => pathsEqual(w.rootPath, path));
  if (existing) {
    if (name && name.trim() && existing.name !== name.trim()) {
      existing.name = name.trim();
      saveWorkspaceStore(agentId, snap);
    }
    return existing;
  }
  const ws: Workspace = {
    id: uuid(),
    name: (name && name.trim()) || folderLabel(path) || 'Workspace',
    rootPath: path,
    createdAt: Date.now(),
  };
  snap.workspaces.push(ws);
  saveWorkspaceStore(agentId, snap);
  return ws;
}

export function listWorkspaces(agentId: string): Workspace[] {
  return loadWorkspaceStore(agentId).workspaces.slice();
}

export function getWorkspace(agentId: string, id: string): Workspace | null {
  return loadWorkspaceStore(agentId).workspaces.find((w) => w.id === id) || null;
}

export function findWorkspaceByPath(agentId: string, rootPath: string): Workspace | null {
  const n = normPath(rootPath);
  if (!n) return null;
  return loadWorkspaceStore(agentId).workspaces.find((w) => pathsEqual(w.rootPath, n)) || null;
}

function ensureWorkspaceLayout(chrome: OpenChromeState, workspaceId: string): SplitNode {
  if (!chrome.layoutByWorkspace[workspaceId]) {
    const leaf = createLeaf();
    chrome.layoutByWorkspace[workspaceId] = leaf;
    if (!chrome.focusedPaneId) chrome.focusedPaneId = leaf.id;
  } else {
    chrome.layoutByWorkspace[workspaceId] = coerceSplitNode(
      chrome.layoutByWorkspace[workspaceId],
    );
  }
  return chrome.layoutByWorkspace[workspaceId];
}

export function getWorkspaceLayout(agentId: string, workspaceId: string): SplitNode | null {
  const chrome = loadWorkspaceStore(agentId).chrome;
  return chrome.layoutByWorkspace[workspaceId] || null;
}

export function getFocusedPaneId(agentId: string): string | null {
  return loadWorkspaceStore(agentId).chrome.focusedPaneId;
}

export function setFocusedPane(agentId: string, paneId: string): OpenChromeState {
  const snap = loadWorkspaceStore(agentId);
  const chrome = snap.chrome;
  const wsId = chrome.activeWorkspaceId;
  if (wsId) {
    const layout = chrome.layoutByWorkspace[wsId];
    if (layout && findLeaf(layout, paneId)) {
      chrome.focusedPaneId = paneId;
      snap.chrome = chrome;
      saveWorkspaceStore(agentId, snap);
    }
  }
  return snap.chrome;
}

/** Open workspace in L1 chrome (idempotent). */
export function openWorkspaceTab(agentId: string, workspaceId: string): OpenChromeState {
  const snap = loadWorkspaceStore(agentId);
  if (!snap.workspaces.some((w) => w.id === workspaceId)) return snap.chrome;
  const chrome = snap.chrome;
  if (!chrome.openWorkspaceIds.includes(workspaceId)) {
    chrome.openWorkspaceIds = [...chrome.openWorkspaceIds, workspaceId];
  }
  chrome.activeWorkspaceId = workspaceId;
  const layout = ensureWorkspaceLayout(chrome, workspaceId);
  const leaves = collectLeaves(layout);
  if (!chrome.focusedPaneId || !leaves.some((l) => l.id === chrome.focusedPaneId)) {
    chrome.focusedPaneId = leaves[0]?.id ?? null;
  }
  snap.chrome = chrome;
  saveWorkspaceStore(agentId, snap);
  return chrome;
}

export function closeWorkspaceTab(agentId: string, workspaceId: string): OpenChromeState {
  const snap = loadWorkspaceStore(agentId);
  const chrome = snap.chrome;
  const ids = chrome.openWorkspaceIds.filter((id) => id !== workspaceId);
  chrome.openWorkspaceIds = ids;
  if (chrome.activeWorkspaceId === workspaceId) {
    chrome.activeWorkspaceId = ids.length ? ids[ids.length - 1] : null;
    if (chrome.activeWorkspaceId) {
      const layout = ensureWorkspaceLayout(chrome, chrome.activeWorkspaceId);
      chrome.focusedPaneId = collectLeaves(layout)[0]?.id ?? null;
    } else {
      chrome.focusedPaneId = null;
    }
  }
  snap.chrome = chrome;
  saveWorkspaceStore(agentId, snap);
  return chrome;
}

export function setActiveWorkspace(agentId: string, workspaceId: string): OpenChromeState {
  return openWorkspaceTab(agentId, workspaceId);
}

export function getChrome(agentId: string): OpenChromeState {
  return loadWorkspaceStore(agentId).chrome;
}

function resolvePaneId(chrome: OpenChromeState, workspaceId: string, paneId?: string | null): string | null {
  const layout = ensureWorkspaceLayout(chrome, workspaceId);
  const leaves = collectLeaves(layout);
  if (paneId && leaves.some((l) => l.id === paneId)) return paneId;
  if (chrome.focusedPaneId && leaves.some((l) => l.id === chrome.focusedPaneId)) {
    return chrome.focusedPaneId;
  }
  return leaves[0]?.id ?? null;
}

export function openContentTab(
  agentId: string,
  workspaceId: string,
  tab: ContentTab,
  paneId?: string | null,
): PaneTabs {
  const snap = loadWorkspaceStore(agentId);
  const chrome = snap.chrome;
  if (!chrome.openWorkspaceIds.includes(workspaceId)) {
    chrome.openWorkspaceIds = [...chrome.openWorkspaceIds, workspaceId];
  }
  chrome.activeWorkspaceId = workspaceId;
  ensureWorkspaceLayout(chrome, workspaceId);
  const pid = resolvePaneId(chrome, workspaceId, paneId);
  if (!pid) return emptyPaneTabs();
  chrome.focusedPaneId = pid;
  chrome.layoutByWorkspace[workspaceId] = updateLeafTabs(
    chrome.layoutByWorkspace[workspaceId],
    pid,
    (tabs) => {
      const key = contentTabKey(tab);
      const open = tabs.open.some((t) => contentTabKey(t) === key)
        ? tabs.open
        : [...tabs.open, tab];
      return { open, activeKey: key };
    },
  );
  snap.chrome = chrome;
  saveWorkspaceStore(agentId, snap);
  const leaf = findLeaf(chrome.layoutByWorkspace[workspaceId], pid);
  return leaf && leaf.type === 'leaf' ? leaf.tabs : emptyPaneTabs();
}

export function closeContentTab(
  agentId: string,
  workspaceId: string,
  tab: ContentTab,
  paneId?: string | null,
): PaneTabs {
  const snap = loadWorkspaceStore(agentId);
  const chrome = snap.chrome;
  ensureWorkspaceLayout(chrome, workspaceId);
  const pid = resolvePaneId(chrome, workspaceId, paneId);
  if (!pid) return emptyPaneTabs();
  chrome.layoutByWorkspace[workspaceId] = updateLeafTabs(
    chrome.layoutByWorkspace[workspaceId],
    pid,
    (tabs) => {
      const key = contentTabKey(tab);
      const nextOpen = tabs.open.filter((t) => contentTabKey(t) !== key);
      let activeKey = tabs.activeKey;
      if (activeKey === key) {
        const idx = tabs.open.findIndex((t) => contentTabKey(t) === key);
        const neighbor = nextOpen[Math.min(idx, nextOpen.length - 1)] || null;
        activeKey = neighbor ? contentTabKey(neighbor) : null;
      }
      return { open: nextOpen, activeKey };
    },
  );
  snap.chrome = chrome;
  saveWorkspaceStore(agentId, snap);
  const leaf = findLeaf(chrome.layoutByWorkspace[workspaceId], pid);
  return leaf && leaf.type === 'leaf' ? leaf.tabs : emptyPaneTabs();
}

export function setActiveContentTab(
  agentId: string,
  workspaceId: string,
  tab: ContentTab,
  paneId?: string | null,
): PaneTabs {
  return openContentTab(agentId, workspaceId, tab, paneId);
}

/** Reorder tabs within a pane by moving `fromKey` before/after `toKey`. */
export function reorderContentTabs(
  agentId: string,
  workspaceId: string,
  fromKey: string,
  toKey: string,
  paneId?: string | null,
): PaneTabs {
  if (!fromKey || !toKey || fromKey === toKey) {
    const chrome = loadWorkspaceStore(agentId).chrome;
    ensureWorkspaceLayout(chrome, workspaceId);
    const pid = resolvePaneId(chrome, workspaceId, paneId);
    if (!pid) return emptyPaneTabs();
    const leaf = findLeaf(chrome.layoutByWorkspace[workspaceId], pid);
    return leaf && leaf.type === 'leaf' ? leaf.tabs : emptyPaneTabs();
  }
  const snap = loadWorkspaceStore(agentId);
  const chrome = snap.chrome;
  ensureWorkspaceLayout(chrome, workspaceId);
  const pid = resolvePaneId(chrome, workspaceId, paneId);
  if (!pid) return emptyPaneTabs();
  chrome.layoutByWorkspace[workspaceId] = updateLeafTabs(
    chrome.layoutByWorkspace[workspaceId],
    pid,
    (tabs) => {
      const fromIdx = tabs.open.findIndex((t) => contentTabKey(t) === fromKey);
      const toIdx = tabs.open.findIndex((t) => contentTabKey(t) === toKey);
      if (fromIdx < 0 || toIdx < 0 || fromIdx === toIdx) return tabs;
      const next = [...tabs.open];
      const [moved] = next.splice(fromIdx, 1);
      next.splice(toIdx, 0, moved);
      return { ...tabs, open: next };
    },
  );
  snap.chrome = chrome;
  saveWorkspaceStore(agentId, snap);
  const leaf = findLeaf(chrome.layoutByWorkspace[workspaceId], pid);
  return leaf && leaf.type === 'leaf' ? leaf.tabs : emptyPaneTabs();
}

export function closeAllTabsInPane(
  agentId: string,
  workspaceId: string,
  paneId: string,
): PaneTabs {
  const snap = loadWorkspaceStore(agentId);
  const chrome = snap.chrome;
  ensureWorkspaceLayout(chrome, workspaceId);
  chrome.layoutByWorkspace[workspaceId] = updateLeafTabs(
    chrome.layoutByWorkspace[workspaceId],
    paneId,
    () => emptyPaneTabs(),
  );
  chrome.focusedPaneId = paneId;
  snap.chrome = chrome;
  saveWorkspaceStore(agentId, snap);
  return emptyPaneTabs();
}

/**
 * Split a leaf inside an in-memory layout tree (does not touch localStorage).
 * Falls back to focused/first leaf when paneId is stale.
 */
export function applySplitToLayout(
  layout: SplitNode,
  paneId: string,
  direction: SplitDirection,
  preferredFocusId?: string | null,
): { tree: SplitNode; newLeafId: string; splitPaneId: string } | null {
  const root = coerceSplitNode(layout);
  const leaves = collectLeaves(root);
  if (!leaves.length) return null;

  let targetId = paneId;
  if (!leaves.some((l) => l.id === targetId)) {
    if (preferredFocusId && leaves.some((l) => l.id === preferredFocusId)) {
      targetId = preferredFocusId;
    } else {
      targetId = leaves[0].id;
    }
  }

  const result = splitLeafInTree(root, targetId, direction, 1);
  if (!result) return null;
  return { ...result, splitPaneId: targetId };
}

/** Persist a layout tree for a workspace (e.g. after UI-driven split). */
export function commitWorkspaceLayout(
  agentId: string,
  workspaceId: string,
  tree: SplitNode,
  focusedPaneId?: string | null,
): OpenChromeState {
  const snap = loadWorkspaceStore(agentId);
  const chrome = snap.chrome;
  if (!chrome.openWorkspaceIds.includes(workspaceId)) {
    chrome.openWorkspaceIds = [...chrome.openWorkspaceIds, workspaceId];
  }
  chrome.activeWorkspaceId = workspaceId;
  chrome.layoutByWorkspace[workspaceId] = coerceSplitNode(tree);
  if (focusedPaneId) chrome.focusedPaneId = focusedPaneId;
  snap.chrome = chrome;
  saveWorkspaceStore(agentId, snap);
  return chrome;
}

/**
 * Split a leaf pane. Returns new leaf id, or null if depth limit / not found.
 * Direction: row = left-right, col = up-down.
 */
export function splitPane(
  agentId: string,
  workspaceId: string,
  paneId: string,
  direction: SplitDirection,
): { newLeafId: string; chrome: OpenChromeState } | null {
  const snap = loadWorkspaceStore(agentId);
  const chrome = snap.chrome;
  const layout = ensureWorkspaceLayout(chrome, workspaceId);
  const result = applySplitToLayout(layout, paneId, direction, chrome.focusedPaneId);
  if (!result) return null;
  chrome.layoutByWorkspace[workspaceId] = result.tree;
  chrome.focusedPaneId = result.newLeafId;
  chrome.activeWorkspaceId = workspaceId;
  snap.chrome = chrome;
  saveWorkspaceStore(agentId, snap);
  return { newLeafId: result.newLeafId, chrome };
}

export function canSplitPane(agentId: string, workspaceId: string, paneId: string): boolean {
  const layout = loadWorkspaceStore(agentId).chrome.layoutByWorkspace[workspaceId];
  if (!layout) return true;
  const d = leafDepth(layout, paneId);
  return d != null && d < MAX_SPLIT_DEPTH;
}

export function closePane(
  agentId: string,
  workspaceId: string,
  paneId: string,
): OpenChromeState | null {
  const snap = loadWorkspaceStore(agentId);
  const chrome = snap.chrome;
  const layout = chrome.layoutByWorkspace[workspaceId];
  if (!layout || layout.type === 'leaf') return null;
  const result = removeLeafFromTree(layout, paneId);
  if (!result) return null;
  chrome.layoutByWorkspace[workspaceId] = result.tree;
  chrome.focusedPaneId = result.focusId;
  snap.chrome = chrome;
  saveWorkspaceStore(agentId, snap);
  return chrome;
}

export function resizeSplit(
  agentId: string,
  workspaceId: string,
  splitId: string,
  ratio: number,
): void {
  const snap = loadWorkspaceStore(agentId);
  const chrome = snap.chrome;
  const layout = chrome.layoutByWorkspace[workspaceId];
  if (!layout) return;
  chrome.layoutByWorkspace[workspaceId] = setSplitRatioInTree(layout, splitId, ratio);
  snap.chrome = chrome;
  saveWorkspaceStore(agentId, snap);
}

export function countLeaves(agentId: string, workspaceId: string): number {
  const layout = loadWorkspaceStore(agentId).chrome.layoutByWorkspace[workspaceId];
  if (!layout) return 1;
  return collectLeaves(layout).length;
}

/**
 * Migrate legacy session projectPath folders into workspace registry once.
 */
export function migrateProjectPathsToWorkspaces(
  agentId: string,
  defaultRoot: string | null | undefined,
): WorkspaceStoreSnapshot {
  const snap = loadWorkspaceStore(agentId);
  if (snap.migrated) return snap;

  const meta = loadSessionProjectMeta(agentId);
  const paths = new Set<string>();
  for (const m of Object.values(meta)) {
    const p = (m.projectPath || '').trim();
    if (p) paths.add(normPath(p));
  }
  const def = (defaultRoot || '').trim();
  if (def) paths.add(normPath(def));

  for (const p of paths) {
    if (!p) continue;
    if (!snap.workspaces.some((w) => pathsEqual(w.rootPath, p))) {
      snap.workspaces.push({
        id: uuid(),
        name: folderLabel(p) || 'Workspace',
        rootPath: p,
        createdAt: Date.now(),
      });
    }
  }

  let metaChanged = false;
  for (const [sid, m] of Object.entries(meta)) {
    const p = (m.projectPath || '').trim() || def;
    if (!p) continue;
    const ws = snap.workspaces.find((w) => pathsEqual(w.rootPath, p));
    if (ws && (m as { workspaceId?: string }).workspaceId !== ws.id) {
      (m as { workspaceId?: string }).workspaceId = ws.id;
      if (!m.projectPath?.trim() && def) m.projectPath = def;
      meta[sid] = m;
      metaChanged = true;
    }
  }
  if (metaChanged) saveSessionProjectMeta(agentId, meta);

  if (snap.chrome.openWorkspaceIds.length === 0 && snap.workspaces.length > 0) {
    const prefer =
      (def && snap.workspaces.find((w) => pathsEqual(w.rootPath, def))) || snap.workspaces[0];
    snap.chrome.openWorkspaceIds = [prefer.id];
    snap.chrome.activeWorkspaceId = prefer.id;
    if (!snap.chrome.layoutByWorkspace[prefer.id]) {
      const leaf = createLeaf();
      snap.chrome.layoutByWorkspace[prefer.id] = leaf;
      snap.chrome.focusedPaneId = leaf.id;
    } else if (!snap.chrome.focusedPaneId) {
      snap.chrome.focusedPaneId =
        collectLeaves(snap.chrome.layoutByWorkspace[prefer.id])[0]?.id ?? null;
    }
  }

  snap.chrome = normalizeChrome(snap.chrome);
  snap.migrated = true;
  saveWorkspaceStore(agentId, snap);
  return snap;
}

export function workspaceDisplayName(ws: Workspace): string {
  return ws.name || folderLabel(ws.rootPath) || ws.rootPath;
}

/** Sync focused pane tabs into legacy-shaped object for simple readers. */
export function getFocusedPaneTabs(agentId: string, workspaceId: string): PaneTabs {
  const chrome = loadWorkspaceStore(agentId).chrome;
  const layout = chrome.layoutByWorkspace[workspaceId];
  if (!layout) return emptyPaneTabs();
  const pid = resolvePaneId(chrome, workspaceId, chrome.focusedPaneId);
  if (!pid) return emptyPaneTabs();
  const leaf = findLeaf(layout, pid);
  return leaf && leaf.type === 'leaf' ? leaf.tabs : emptyPaneTabs();
}
