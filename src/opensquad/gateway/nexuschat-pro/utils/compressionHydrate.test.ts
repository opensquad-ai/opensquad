/**
 * Compression hydration target — a manual compression must re-hydrate THE
 * session it compressed, never the agent's `/current` pointer.
 *
 * Why this file exists (2026-09-27): pressing "压缩上下文" on a session that was
 * not the agent's current one repainted the pane with the CURRENT session's
 * chat (串页) — the compressed view flipped to another session's content and only
 * a later send restored it. `history_sync {reason:'compression'}` names the
 * compressed session in `session_id`, but the handler discarded it and ran the
 * generic `/current` hydrate. `/current` answers with the agent's pointer, so in
 * a multi-tab / parallel layout it is a *different* session.
 *
 * Mutations verified (each one makes this file fail):
 *   MF1 compression branch drops the frame's sid           → R1
 *   MF2 targeted hydrate falls back to /current            → R2
 *   MF3 targeted hydrate writes agentCurrentSessionIdRef   → R3
 *   MF4 boot-restore hijacks a targeted hydrate            → R4
 *   MF5 compressionHydrateSid keeps untrimmed/non-strings  → B1..B5
 */
import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import { compressionHydrateSid } from './compressionHydrate';

const ROOT = path.resolve(__dirname, '..');
const read = (rel: string) => fs.readFileSync(path.join(ROOT, rel), 'utf8');

/**
 * Drop comments before searching, so a doc line that merely mentions a call is
 * not mistaken for one. `//` is skipped when it follows `:` so `https://…` in a
 * string survives the strip (same idiom as sessionExport.scan.test.ts).
 */
const withoutComments = (src: string): string =>
  src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:\w/])\/\/[^\n]*/g, '$1');

const sliceBetween = (src: string, from: string, to: string): string => {
  const a = src.indexOf(from);
  if (a < 0) return '';
  const b = src.indexOf(to, a + from.length);
  return b < 0 ? '' : src.slice(a, b);
};

const HOOK = 'hooks/useAgentWebSocket.ts';
const hook = withoutComments(read(HOOK));

/** One `if (reason === '<x>') { … }` branch of the history_sync handler. */
const branchOf = (src: string, reason: string): string => {
  const marker = `if (reason === '${reason}') {`;
  const start = src.indexOf(marker);
  if (start < 0) return '';
  const end = src.indexOf('\n      }', start);
  return end < 0 ? '' : src.slice(start, end);
};

const hydrateBody = sliceBetween(
  hook,
  'const hydrateCurrentSession = (opts?',
  'const scheduleCurrentSessionHydration = (',
);
const scheduleBody = sliceBetween(
  hook,
  'const scheduleCurrentSessionHydration = (',
  'hydrateCurrentSession({ showLoading: true })',
);

describe('compressionHydrateSid (pure)', () => {
  it('B1: takes the frame\'s session_id', () => {
    expect(compressionHydrateSid({ session_id: 'sess-aaa', reason: 'compression' })).toBe('sess-aaa');
  });

  it('B2: trims padding so a padded sid still matches the live session', () => {
    expect(compressionHydrateSid({ session_id: '  sess-aaa \n' })).toBe('sess-aaa');
  });

  it('B3: falls back to sessionId / id (older frame shapes)', () => {
    expect(compressionHydrateSid({ sessionId: 'sess-bravo' })).toBe('sess-bravo');
    expect(compressionHydrateSid({ id: 'sess-charlie' })).toBe('sess-charlie');
  });

  it('B4: no session named -> "" so callers keep the legacy /current behaviour', () => {
    expect(compressionHydrateSid(null)).toBe('');
    expect(compressionHydrateSid(undefined)).toBe('');
    expect(compressionHydrateSid('sess-aaa')).toBe('');
    expect(compressionHydrateSid({})).toBe('');
    expect(compressionHydrateSid({ session_id: '   ' })).toBe('');
  });

  it('B5: non-string ids are not coerced into a bogus target', () => {
    expect(compressionHydrateSid({ session_id: 42 })).toBe('');
    expect(compressionHydrateSid({ session_id: { sid: 'sess-aaa' } })).toBe('');
  });
});

describe('history_sync compression → hydration wiring', () => {
  it('R1: the compression branch hydrates the session named in the frame', () => {
    const branch = branchOf(hook, 'compression');
    expect(branch, 'compression branch not found in the history_sync handler').not.toBe('');
    expect(
      branch,
      'MF1: the compressed session id must be forwarded as the hydrate target',
    ).toMatch(/scheduleCurrentSessionHydration\(\s*80\s*,\s*compressionHydrateSid\(data\)\s*\)/);
    // The untargeted call is what caused the 串页 — it must be gone.
    expect(branch, 'MF2: no bare /current refresh after compression').not.toMatch(
      /scheduleCurrentSessionHydration\(\s*80\s*\)/,
    );
    // Still required: the merge path keeps in-flight tools alive during reload.
    expect(branch).toMatch(/compressionHydrationPendingRef\.current = true/);
    expect(hook).toContain("import { compressionHydrateSid } from '../utils/compressionHydrate'");
  });

  it('R2: a targeted hydrate reads that sid instead of /current', () => {
    expect(hydrateBody).toMatch(/const targetSid = \(opts\?\.targetSid \|\| ''\)\.trim\(\)/);
    expect(
      hydrateBody,
      'MF2: the targeted branch must fetch the named session',
    ).toContain('getSessionHistoryPaged(agentId, targetSid, 0, SESSION_HISTORY_PAGE_SIZE)');
    // `/current` may only be used when no target was given.
    expect(hydrateBody).toMatch(/targetSid\s*\?\s*agentSessionAPI\s*\.\s*getSessionHistoryPaged/);
    expect(hydrateBody).toMatch(
      /:\s*agentSessionAPI\.getCurrentSession\(agentId, 0, SESSION_HISTORY_PAGE_SIZE\)/,
    );
  });

  it('R3/R4: a targeted hydrate never moves the agent-current pointer', () => {
    expect(
      hydrateBody,
      'MF3: a session-scoped refresh must not claim to be the agent-current session',
    ).toMatch(/if \(currentSid && !targetSid\)/);
    expect(
      hydrateBody,
      'MF4: the boot-restore fallback must not hijack an explicit target',
    ).toMatch(/if \(!targetSid && restoreSid && restoreSid !== currentSid\)/);
  });

  it('R5: the scheduler forwards the target into the hydrate call', () => {
    expect(scheduleBody).toMatch(/targetSid: string = ''/);
    expect(scheduleBody).toMatch(/hydrateCurrentSession\(\{ showLoading: false, targetSid \}\)/);
  });
});
