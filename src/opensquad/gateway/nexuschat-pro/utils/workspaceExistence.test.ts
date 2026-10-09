/**
 * A workspace whose folder is gone must be flagged, never activated, and never
 * deleted.
 *
 * The registry only ever *unions* (`mergeWorkspaceSnapshots`), so a path the
 * launcher cannot see used to sit there forever and replicate to every origin.
 * Nothing surfaced it beyond a bare "Root not found" in the files panel, while
 * the agent — whose cwd write the launcher rejects for a non-existent directory
 * — silently kept running in the previous project.
 */
import fs from 'fs';
import path from 'path';
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

let store: Map<string, string>;

beforeAll(() => {
  store = new Map();
  vi.stubGlobal('localStorage', {
    getItem: (k: string) => (store.has(k) ? store.get(k)! : null),
    setItem: (k: string, v: string) => {
      store.set(k, String(v));
    },
    removeItem: (k: string) => {
      store.delete(k);
    },
    clear: () => store.clear(),
  });
  vi.stubGlobal('window', {
    localStorage: globalThis.localStorage,
    // `workspaceStore` reaches `services/api` through `cwdRecents`, and that
    // module reads window.location at import time.
    location: { hostname: '127.0.0.1', port: '5173', protocol: 'http:' },
    dispatchEvent: () => true,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
  });
});

beforeEach(() => {
  store.clear();
});

const load = () => import('./workspaceStore');

const AGENT = 'agent-demo';
const GONE = 'C:/sandbox/demo/nested';
const LIVE = 'C:/sandbox/demo';

describe('reconcileWorkspaceExistence', () => {
  it('flags a gone folder, keeps the entry, and moves the active tab off it', async () => {
    const { ensureWorkspace, openWorkspaceTab, reconcileWorkspaceExistence, loadWorkspaceStore } = await load();
    const gone = ensureWorkspace(AGENT, GONE);
    const live = ensureWorkspace(AGENT, LIVE);
    openWorkspaceTab(AGENT, gone.id); // the bad one is what the user is looking at
    openWorkspaceTab(AGENT, live.id);
    openWorkspaceTab(AGENT, gone.id);

    const changed = reconcileWorkspaceExistence(AGENT, [], { [GONE]: false, [LIVE]: true });

    expect(changed).toBe(true);
    const snap = loadWorkspaceStore(AGENT);
    // Kept — the folder may be a drive that is merely offline.
    expect(snap.workspaces.map((w) => w.rootPath).sort()).toEqual([GONE, LIVE].sort());
    expect(snap.workspaces.find((w) => w.rootPath === GONE)?.missing).toBe(true);
    expect(snap.workspaces.find((w) => w.rootPath === LIVE)?.missing).toBeFalsy();
    // Its tab is gone and nothing resolves through it any more.
    expect(snap.chrome.openWorkspaceIds).not.toContain(gone.id);
    expect(snap.chrome.activeWorkspaceId).toBe(live.id);
  });

  it('clears the flag when the folder is back, and reports no change otherwise', async () => {
    const { ensureWorkspace, reconcileWorkspaceExistence, loadWorkspaceStore } = await load();
    const gone = ensureWorkspace(AGENT, GONE);

    reconcileWorkspaceExistence(AGENT, [], { [GONE]: false });
    expect(loadWorkspaceStore(AGENT).workspaces.find((w) => w.id === gone.id)?.missing).toBe(true);
    // Second identical probe: nothing to write.
    expect(reconcileWorkspaceExistence(AGENT, [], { [GONE]: false })).toBe(false);

    expect(reconcileWorkspaceExistence(AGENT, [], { [GONE]: true })).toBe(true);
    expect(loadWorkspaceStore(AGENT).workspaces.find((w) => w.id === gone.id)?.missing).toBeUndefined();
  });

  it('leaves unprobed workspaces alone — not probed is not missing', async () => {
    const { ensureWorkspace, reconcileWorkspaceExistence, loadWorkspaceStore } = await load();
    const untouched = ensureWorkspace(AGENT, 'C:/sandbox/unprobed');
    expect(reconcileWorkspaceExistence(AGENT, [], { [GONE]: false })).toBe(false);
    expect(loadWorkspaceStore(AGENT).workspaces.find((w) => w.id === untouched.id)?.missing).toBeUndefined();
  });

  it('refuses to open a workspace that is flagged missing', async () => {
    const { ensureWorkspace, openWorkspaceTab, reconcileWorkspaceExistence, loadWorkspaceStore } = await load();
    const gone = ensureWorkspace(AGENT, GONE);
    const live = ensureWorkspace(AGENT, LIVE);
    reconcileWorkspaceExistence(AGENT, [], { [GONE]: false, [LIVE]: true });
    openWorkspaceTab(AGENT, live.id);
    const before = loadWorkspaceStore(AGENT).chrome;

    openWorkspaceTab(AGENT, gone.id);

    const after = loadWorkspaceStore(AGENT).chrome;
    expect(after.activeWorkspaceId).toBe(before.activeWorkspaceId);
    expect(after.openWorkspaceIds).not.toContain(gone.id);
  });
});

describe('the entry points validate before they register', () => {
  const read = (rel: string) => fs.readFileSync(path.resolve(__dirname, rel), 'utf8');
  const PAGE = read('../components/AIChatPage.tsx');
  const TAB_BAR = read('../components/ai-chat/WorkspaceTabBar.tsx');

  it.each([
    ['handleOpenExistingWorkspace', '目录不存在，无法作为工作区打开'],
    ['handleCreateWorkspace', '目录不存在，无法创建工作区'],
  ])('%s probes the folder before ensureWorkspace', (handler, message) => {
    const at = PAGE.indexOf(`const ${handler}`);
    expect(at).toBeGreaterThan(0);
    const body = PAGE.slice(at, at + 900);
    expect(body).toContain('await probeProjectRoot(fsAgentName, path)');
    expect(body).toContain(message);
    // The guard has to come first, or the bad path is registered anyway.
    expect(body.indexOf('probeProjectRoot')).toBeLessThan(body.indexOf('ensureWorkspace('));
  });

  it('surfaces a rejected cwd write instead of swallowing it', () => {
    // The launcher 400s a directory it cannot see; a bare console.error left the
    // agent answering about its previous project with nothing on screen.
    expect(PAGE).toContain('工作目录未生效：');
    expect(PAGE).toContain('setWorkspaceWarning');
  });

  it('offers a missing workspace in the menu but cannot activate it', () => {
    expect(TAB_BAR).toContain('目录不存在');
    expect(TAB_BAR).toContain('disabled={missing}');
  });
});
