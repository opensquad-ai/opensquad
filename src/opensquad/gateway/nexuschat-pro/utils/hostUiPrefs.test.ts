import { beforeAll, describe, expect, it, vi } from 'vitest';
import {
  isEmptyWorkspaceSnapshot,
  mergeWorkspaceSnapshots,
  type WorkspaceStoreSnapshot,
} from './workspaceStore';
import type { HostUiPrefs } from './hostUiPrefs';

function snap(
  partial: Partial<WorkspaceStoreSnapshot> & { workspaces: WorkspaceStoreSnapshot['workspaces'] },
): WorkspaceStoreSnapshot {
  return {
    chrome: {
      openWorkspaceIds: [],
      activeWorkspaceId: null,
      focusedPaneId: null,
      layoutByWorkspace: {},
      ...(partial.chrome || {}),
    },
    migrated: true,
    savedAt: 1,
    ...partial,
  };
}

describe('mergeWorkspaceSnapshots', () => {
  it('unions extra local folders onto remote chrome', () => {
    const remote = snap({
      savedAt: 10,
      workspaces: [{ id: 'r1', name: 'proj', rootPath: 'C:/proj', createdAt: 1 }],
      chrome: {
        openWorkspaceIds: ['r1'],
        activeWorkspaceId: 'r1',
        focusedPaneId: 'p1',
        layoutByWorkspace: {
          r1: { type: 'leaf', id: 'p1', tabs: { open: [{ kind: 'session', id: 's1' }], activeKey: 'session:s1' } },
        },
      },
    });
    const local = snap({
      savedAt: 99,
      workspaces: [
        { id: 'l1', name: 'proj', rootPath: 'C:/proj', createdAt: 2 },
        { id: 'l2', name: 'other', rootPath: 'C:/other', createdAt: 3 },
      ],
    });
    const merged = mergeWorkspaceSnapshots(local, remote);
    expect(merged.workspaces.map((w) => w.rootPath).sort()).toEqual(['C:/other', 'C:/proj']);
    expect(merged.chrome.activeWorkspaceId).toBe('r1');
    expect(merged.workspaces.find((w) => w.rootPath === 'C:/proj')?.id).toBe('r1');
  });

  it('keeps remote chrome when local is an empty seed with newer savedAt', () => {
    const remote = snap({
      savedAt: 10,
      workspaces: [{ id: 'r1', name: 'proj', rootPath: 'C:/proj', createdAt: 1 }],
      chrome: {
        openWorkspaceIds: ['r1'],
        activeWorkspaceId: 'r1',
        focusedPaneId: 'p1',
        layoutByWorkspace: {
          r1: { type: 'leaf', id: 'p1', tabs: { open: [{ kind: 'session', id: 's1' }], activeKey: 'session:s1' } },
        },
      },
    });
    const local = snap({
      savedAt: 999,
      workspaces: [{ id: 'seed', name: 'proj', rootPath: 'C:/proj', createdAt: 2 }],
    });
    const merged = mergeWorkspaceSnapshots(local, remote);
    expect(merged.chrome.activeWorkspaceId).toBe('r1');
    expect(merged.workspaces).toHaveLength(1);
    expect(merged.workspaces[0].id).toBe('r1');
  });
});

describe('isEmptyWorkspaceSnapshot', () => {
  it('treats missing or empty lists as empty', () => {
    expect(isEmptyWorkspaceSnapshot(null)).toBe(true);
    expect(isEmptyWorkspaceSnapshot(snap({ workspaces: [] }))).toBe(true);
    expect(
      isEmptyWorkspaceSnapshot(
        snap({ workspaces: [{ id: 'a', name: 'a', rootPath: '/a', createdAt: 1 }] }),
      ),
    ).toBe(false);
  });
});

describe('pickHydratePrefs', () => {
  beforeAll(() => {
    const store = new Map<string, string>();
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
      location: { hostname: '127.0.0.1', port: '5173', protocol: 'http:' },
      dispatchEvent: () => true,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
      matchMedia: () => ({
        matches: false,
        addEventListener: () => undefined,
        removeEventListener: () => undefined,
        addListener: () => undefined,
        removeListener: () => undefined,
      }),
    });
  });

  it('lets host theme win over a fresh origin', async () => {
    const { pickHydratePrefs } = await import('./hostUiPrefs');
    const host: HostUiPrefs = {
      savedAt: 5,
      theme: { preset: 'pure-white', mode: 'light' },
      lang: 'zh',
    };
    const local: HostUiPrefs = { savedAt: 1, lang: 'en' };
    const picked = pickHydratePrefs(host, local);
    expect(picked.theme?.preset).toBe('pure-white');
    expect(picked.lang).toBe('zh');
  });

  it('keeps local theme when host has none', async () => {
    const { pickHydratePrefs } = await import('./hostUiPrefs');
    const local: HostUiPrefs = {
      savedAt: 1,
      theme: { preset: 'pure-white', mode: 'light' },
      lang: 'zh',
    };
    const picked = pickHydratePrefs({ savedAt: 0 }, local);
    expect(picked.theme?.preset).toBe('pure-white');
  });
});
