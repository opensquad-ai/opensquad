/**
 * Which session row shows the working animation.
 *
 * Reported from the live app: a session whose task had finished long ago kept
 * pulsing with the 进行中 orbit while a *different* session ran.  The row keyed its
 * busy state off the backend's ``session.current`` flag, which is the disk "current
 * session pointer" — not "this session is running" (the same file already refuses
 * to trust it for the highlight).
 *
 * Mutations verified (applied, run, reverted):
 *   M1 bring back the ``session.current`` heuristic   -> R2 fails
 *   M2 let the agent-wide flag light up any row       -> R2/R4 fail
 *   M3 ignore the authoritative list                  -> R1 fails
 */
import { describe, expect, it } from 'vitest';
import { isSessionRowBusy } from './sessionBusy';

const RUNNING = 'B-running';

describe('isSessionRowBusy', () => {
  it('the running session pulses, whoever is looking', () => {
    expect(
      isSessionRowBusy({
        sessionId: RUNNING,
        currentSessionId: 'A-finished',
        busySessionIds: [RUNNING],
      }),
    ).toBe(true);
  });

  it('a finished session does NOT pulse while another one runs', () => {
    // The reported bug: agent busy (for B) + A is the backend current pointer.
    expect(
      isSessionRowBusy({
        sessionId: 'A-finished',
        currentSessionId: 'A-finished',
        agentBusy: true,
        busySessionIds: [RUNNING],
      }),
    ).toBe(false);
    // But the session the user is driving lights up before the snapshot lands.
    expect(
      isSessionRowBusy({
        sessionId: 'A-finished',
        currentSessionId: 'A-finished',
        agentBusy: true,
        busySessionIds: [],
      }),
    ).toBe(true);
  });

  it('the agent-wide flag alone never lights an idle row', () => {
    expect(
      isSessionRowBusy({
        sessionId: 'A-finished',
        currentSessionId: 'B-other',
        agentBusy: true,
        busySessionIds: [],
      }),
    ).toBe(false);
  });

  it('nothing running, nothing pulsing', () => {
    expect(
      isSessionRowBusy({ sessionId: 'A', currentSessionId: 'A', agentBusy: false, busySessionIds: [] }),
    ).toBe(false);
    expect(isSessionRowBusy({ sessionId: '' })).toBe(false);
  });
});
