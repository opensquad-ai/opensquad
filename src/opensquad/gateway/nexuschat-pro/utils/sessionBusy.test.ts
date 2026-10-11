/**
 * Which session row shows the working animation.
 *
 * Reported from the live app, round 1: a session whose task had finished long ago
 * kept pulsing with the 进行中 orbit while a *different* session ran.  The row keyed
 * its busy state off the backend's ``session.current`` flag, which is the disk
 * "current session pointer" — not "this session is running" (the same file already
 * refuses to trust it for the highlight).
 *
 * Reported from the live app, round 2 (2026-09-28): 「会话列表怎么点击哪个会话，哪个
 * 会话就任务流进度动画」.  The stopgap for the window before ``busy_sessions`` lands
 * was ``agentBusy && sessionId === currentSessionId`` — "whichever row the user has
 * selected, if the agent is busy anywhere".  Clicking a session is not driving it,
 * so every row the user touched got the animation; a stale agent-wide flag (a
 * session's final frame arriving after the user switched away never clears the
 * global ``isStreaming``) then kept it there.  Busy is now decided per row:
 * the authoritative snapshot, or this client's stream evidence for that same sid.
 *
 * Mutations verified (applied, run, reverted):
 *   M1 bring back the ``session.current`` heuristic      -> R2 fails
 *   M2 let the agent-wide flag light up the selected row -> R3 fails
 *   M3 ignore the authoritative list                      -> R1 fails
 *   M4 ignore the client-side stream evidence             -> R4 fails
 */
import { describe, expect, it } from 'vitest';
import { isSessionRowBusy, mergeBusySnapshot } from './sessionBusy';

const RUNNING = 'B-running';
const FINISHED = 'A-finished';

describe('isSessionRowBusy', () => {
  it('R1 the running session pulses, whoever is looking', () => {
    expect(isSessionRowBusy({ sessionId: RUNNING, busySessionIds: [RUNNING] })).toBe(true);
  });

  it('R2 a finished session does NOT pulse while another one runs', () => {
    expect(
      isSessionRowBusy({
        sessionId: FINISHED,
        busySessionIds: [RUNNING],
        streamingSessionIds: [RUNNING],
      }),
    ).toBe(false);
  });

  it('R3 selecting a session never lights it up by itself', () => {
    // The reported bug: the row the user just clicked is the selected one, and the
    // agent is busy elsewhere. Selection is not busy evidence.
    expect(isSessionRowBusy({ sessionId: FINISHED, busySessionIds: [] })).toBe(false);
    expect(isSessionRowBusy({ sessionId: FINISHED, streamingSessionIds: [] })).toBe(false);
    // …but that session's own stream evidence does light it up.
    expect(isSessionRowBusy({ sessionId: FINISHED, streamingSessionIds: [FINISHED] })).toBe(true);
  });

  it('R4 the client-side evidence is per session too', () => {
    expect(
      isSessionRowBusy({
        sessionId: FINISHED,
        busySessionIds: [],
        streamingSessionIds: [RUNNING],
      }),
    ).toBe(false);
    expect(isSessionRowBusy({ sessionId: RUNNING, streamingSessionIds: [RUNNING] })).toBe(true);
  });

  it('nothing running, nothing pulsing', () => {
    expect(isSessionRowBusy({ sessionId: 'A', busySessionIds: [] })).toBe(false);
    expect(isSessionRowBusy({ sessionId: '' })).toBe(false);
  });
});

/**
 * The snapshot the `connected` frame carries (2026-10-10).
 *
 * Reported: a turn was running, the user switched sessions and came back (or reloaded) and
 * the Stop button was gone — because the live event only fires on *changes*, so the client's
 * set came back empty. These pin what the snapshot does to that set.
 */
describe('mergeBusySnapshot', () => {
  it('R1 takes the snapshot as the authority — a turn that ended while we were away is not red', () => {
    expect(mergeBusySnapshot({ snapshot: ['s2'], streamingSessionIds: [] })).toEqual(['s2']);
  });

  it('R2 keeps this client\'s own stream evidence, which is fresher than the frame', () => {
    expect(mergeBusySnapshot({ snapshot: [], streamingSessionIds: ['s1'] })).toEqual(['s1']);
  });

  it('R3 never resurrects a session the user just stopped', () => {
    expect(
      mergeBusySnapshot({ snapshot: ['s1', 's2'], streamingSessionIds: ['s1'], stopped: { s1: true } }),
    ).toEqual(['s2']);
  });

  it('R4 no snapshot in the frame → nothing to apply', () => {
    expect(mergeBusySnapshot({})).toBeNull();
    expect(mergeBusySnapshot({ snapshot: 's1' })).toBeNull();
  });

  it('blanks and duplicates are dropped', () => {
    expect(mergeBusySnapshot({ snapshot: [' s1 ', '', null, 's1'] })).toEqual(['s1']);
  });
});
