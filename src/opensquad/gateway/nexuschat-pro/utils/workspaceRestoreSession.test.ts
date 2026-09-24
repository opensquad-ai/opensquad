// @vitest-environment jsdom
/**
 * A page reload must come back to the conversation this browser was showing.
 *
 * Bug (2026-09-23 user report, agent305): refresh sometimes painted an empty
 * chat. The pane hydrated `GET /agent-sessions/{agent}/current`, and
 * `current_session.json` only follows agent-side rotations — a parallel session
 * (what a session tab holds) is persisted to `history/{sid}.json` only. The
 * agent's current was an empty draft (`{draft: true, messages: []}`) while the
 * conversation the user was reading lived in a tab, so `/current` answered with
 * nothing to show and the pane came up blank with no error.
 *
 * Locked here:
 *   R1  the focused pane's active *session* tab is the restore target;
 *   R2  a non-session tab (file / scheduled-tasks / tasks) never restores;
 *   R3  a split workspace restores the FOCUSED pane's tab, not the first leaf;
 *   R4  no chrome (fresh browser) → '' so the caller keeps the backend current;
 *   R5  the hydrate consults the resolver, and `/current` stays the fallback
 *       (`agentCurrentSessionIdRef` still comes from the backend response).
 *
 * Why fences for R5 rather than a mount: AIChatPage needs the api layer, i18n,
 * the WS event bus and a populated chrome; a jsdom mount would spend its budget
 * on mocks and still would not see which session the hydrate painted.
 */

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { beforeEach, describe, expect, it } from 'vitest';

import {
  ensureWorkspace,
  getFocusedPaneId,
  getRestorableSessionId,
  openContentTab,
  splitPane,
} from './workspaceStore';

beforeEach(() => {
  localStorage.clear();
});

describe('getRestorableSessionId', () => {
  it('restores the session tab the focused pane is on', () => {
    const ws = ensureWorkspace('agent1', 'C:/proj');
    openContentTab('agent1', ws.id, { kind: 'session', id: 's1' });
    openContentTab('agent1', ws.id, { kind: 'session', id: 's2' });
    expect(getRestorableSessionId('agent1')).toBe('s2');
  });

  it('never restores a non-session tab', () => {
    const ws = ensureWorkspace('agent2', 'C:/proj');
    openContentTab('agent2', ws.id, { kind: 'session', id: 's1' });
    openContentTab('agent2', ws.id, { kind: 'file', id: 'src/a.ts' });
    expect(getRestorableSessionId('agent2')).toBe('');
  });

  it('restores the focused pane in a split, not the first leaf', () => {
    const ws = ensureWorkspace('agent3', 'C:/proj');
    openContentTab('agent3', ws.id, { kind: 'session', id: 'left' });
    const split = splitPane('agent3', ws.id, getFocusedPaneId('agent3')!, 'row');
    expect(split?.newLeafId).toBeTruthy();
    // splitPane focuses the new leaf; its tab is the one to restore.
    openContentTab('agent3', ws.id, { kind: 'session', id: 'right' });
    expect(getRestorableSessionId('agent3')).toBe('right');
  });

  it('returns nothing when this browser has no chrome', () => {
    expect(getRestorableSessionId('agent-without-chrome')).toBe('');
    expect(getRestorableSessionId('')).toBe('');
  });
});

describe('boot restore wiring (source fence)', () => {
  const HOOK_SRC = readFileSync(
    resolve(__dirname, '..', 'hooks', 'useAgentWebSocket.ts'),
    'utf8',
  );
  const STORE_SRC = readFileSync(resolve(__dirname, 'workspaceStore.ts'), 'utf8');

  it('R5a: the hydrate resolves a restore session and falls back to /current', () => {
    // The resolver is consulted inside hydrateCurrentSession…
    expect(HOOK_SRC).toMatch(/getBootRestoreSessionId/);
    expect(HOOK_SRC).toMatch(/getSessionHistoryPaged\(\s*agentId,\s*restoreSid/);
    // …and the backend current still seeds the agent-side pointer + the
    // new-session guard, so a rotation is never overridden by a stale tab.
    expect(HOOK_SRC).toMatch(/agentCurrentSessionIdRef\.current = currentSid/);
    expect(HOOK_SRC).toMatch(/currentSid !== guard\.sid/);
  });

  it('R5b: the viewed session — not the agent current — drives the pane', () => {
    expect(HOOK_SRC).toMatch(/setCurrentSessionId\(viewedSid\)/);
    expect(HOOK_SRC).toMatch(/setActiveSession\(viewedSid\)/);
    expect(HOOK_SRC).toMatch(/putCachedSessionTimeline\(agentId, viewedSid/);
  });

  it('R5c: a failed restore keeps the agent current instead of blanking', () => {
    expect(HOOK_SRC).toMatch(/restore of last viewed session failed, keeping agent current/);
  });

  it('R5d: the resolver reads the focused pane and only session tabs', () => {
    expect(STORE_SRC).toMatch(/export function getRestorableSessionId/);
    expect(STORE_SRC).toMatch(/tab\?\.kind === 'session'/);
  });
});
