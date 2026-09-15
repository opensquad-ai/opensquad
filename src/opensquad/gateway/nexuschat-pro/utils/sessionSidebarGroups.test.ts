import { describe, expect, it } from 'vitest';
import {
  ensureCommsSessionVisible,
  groupSessionsForSidebar,
  resolveCommsSessionId,
  type SidebarSession,
} from './sessionSidebarGroups';

function s(id: string, extra: Partial<SidebarSession> = {}): SidebarSession {
  return { id, last_updated: extra.last_updated ?? id, ...extra };
}

describe('resolveCommsSessionId', () => {
  it('prefers pending, then primary prop, then list flag', () => {
    const list = [s('a'), s('b', { primary: true })];
    expect(resolveCommsSessionId(list, 'a', 'pending')).toBe('pending');
    expect(resolveCommsSessionId(list, 'a', null)).toBe('a');
    expect(resolveCommsSessionId(list, null, null)).toBe('b');
    expect(resolveCommsSessionId([s('a')], null, null)).toBeNull();
  });
});

describe('ensureCommsSessionVisible', () => {
  it('keeps workspace list when comms is already in it', () => {
    const all = [s('ws'), s('comms')];
    expect(ensureCommsSessionVisible(all, all, 'comms').map((x) => x.id)).toEqual(['ws', 'comms']);
  });

  it('prepends comms from the unfiltered list', () => {
    const ws = [s('ws')];
    const all = [s('ws'), s('comms')];
    expect(ensureCommsSessionVisible(ws, all, 'comms').map((x) => x.id)).toEqual(['comms', 'ws']);
  });

  it('stubs a row when comms is missing from pagination', () => {
    const ws = [s('ws')];
    const next = ensureCommsSessionVisible(ws, ws, 'old-primary');
    expect(next[0].id).toBe('old-primary');
    expect(next[0].primary).toBe(true);
  });
});

describe('groupSessionsForSidebar', () => {
  it('puts the comms session only in 通讯, even if pinned or archived', () => {
    const sessions = [
      s('pin', { last_updated: '2026-01-02' }),
      s('comms', { last_updated: '2026-01-03' }),
      s('recent', { last_updated: '2026-01-01' }),
      s('arch', { last_updated: '2026-01-04' }),
    ];
    const grouped = groupSessionsForSidebar(
      sessions,
      {
        pin: { pinned: true },
        comms: { pinned: true, archived: true },
        arch: { archived: true },
      },
      'comms',
    );
    expect(grouped.comms.map((x) => x.id)).toEqual(['comms']);
    expect(grouped.pinned.map((x) => x.id)).toEqual(['pin']);
    expect(grouped.recent.map((x) => x.id)).toEqual(['pin', 'recent']);
    expect(grouped.archive.map((x) => x.id)).toEqual(['arch']);
  });
});
