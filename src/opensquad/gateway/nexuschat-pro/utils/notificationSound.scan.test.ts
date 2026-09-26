/**
 * 结束提示音 — one chime per turn, and only for a turn that had something to say.
 *
 * The trap this file exists for: a turn is NOT one frame. A long task emits a
 * `message` per LLM round that speaks to the user (each persisted assistant
 * message in a real session — `20260923_051204_ylsx` has 6 of them in one user
 * turn, plus the terminal `to_user_end_task`), because every round with a
 * non-empty `user_msg` emits its own `to_user_final`. Ringing on the frame
 * therefore rings per step: the 过程输出 the user hears instead of one 完成 提示.
 *
 * `turn_elapsed` is the turn's closing frame (the runner emits it once, after
 * the round loop breaks), so that is where the chime lives. The rules below are
 * all about not letting it drift back onto a per-round frame, and about not
 * ringing for turns that produced no reply at all — 撤回 / 新建会话 also emit a
 * (zero-length) `turn_elapsed`.
 *
 *   R1  exactly one chime call site, in the `turn_elapsed` handler — never in
 *       `handleFinal` (per-round `message`/`response`) nor in `to_user_end_task`;
 *   R2  it is gated on the turn having displayed a reply, and on the page being
 *       in the background and the sid not user-stopped;
 *   R3  both terminal-frame handlers record the reply, and a workflow start
 *       clears the record, so a reply-less turn cannot inherit the previous one;
 *   R4  the record is consumed — cleared right after the ring — so a second
 *       `turn_elapsed` for the same sid cannot ring twice.
 *
 * Mutations verified:
 *   MA1 move the chime back into `handleFinal`      → R1
 *   MA2 drop the `repliedBySidRef` gate             → R2
 *   MA3 drop the reset in `turn_start`              → R3
 *   MA4 drop the `= false` after ringing            → R4
 */
import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';

const ROOT = path.resolve(__dirname, '..');
const WS = fs.readFileSync(path.join(ROOT, 'hooks/useAgentWebSocket.ts'), 'utf8');
/** Source with comments stripped — these rules are about code, not prose. */
const code = (src: string) => src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
const SRC = code(WS);

const slice = (from: string, to: string) => {
  const src = code(WS);
  const start = src.indexOf(from);
  return start < 0 ? '' : src.slice(start, src.indexOf(to, start));
};

/** `message` + `response` + `to_user_reply` all land here, once per round. */
const handleFinalBody = slice('const handleFinal', 'const unsubMessage');
/** The terminal frame of an end-task turn — still one frame, not one chime. */
const endTaskBody = slice('const unsubToUserEndTask', 'const unsubThought');
/** The workflow-start frame. */
const turnStartBody = slice('const unsubTurnStart', '// Turn elapsed —');
/** The turn's closing frame. */
const turnElapsedBody = slice('const unsubTurnElapsed', 'const unsubTurnUsage');

const CHIME = /playGentleNotificationSound\(\)/g;

/**
 * The predicate the chime call sits under, up to its opening brace. Asserting on
 * the handler body alone is not enough: the `= false` line right after the call
 * also mentions `repliedBySidRef`, so a body-wide match hides a dropped gate.
 */
const chimeGuard = (() => {
  const at = turnElapsedBody.indexOf('playGentleNotificationSound');
  if (at < 0) return '';
  const from = turnElapsedBody.lastIndexOf('if (', at);
  return from < 0 ? '' : turnElapsedBody.slice(from, turnElapsedBody.indexOf('{', from) + 1);
})();

describe('R1 — the chime is on the end-of-turn frame, not on a reply frame', () => {
  it('rings exactly once in the whole hook', () => {
    expect(SRC.match(CHIME)?.length).toBe(1);
  });

  it('and that one call is in the turn_elapsed handler', () => {
    expect(turnElapsedBody).toMatch(CHIME);
    expect(turnElapsedBody).toMatch(/onWs\('turn_elapsed'/);
  });

  it('no longer rings per round on the reply frames', () => {
    // `handleFinal` runs for every `message` — one per LLM round of a long task.
    expect(handleFinalBody).not.toMatch(/playGentleNotificationSound/);
    expect(endTaskBody).not.toMatch(/playGentleNotificationSound/);
    // ...and the slices have to actually cover the handlers, or this passes vacuously.
    expect(handleFinalBody).toMatch(/setTimeline/);
    expect(endTaskBody).toMatch(/finalizeWorkflowAndAddMessage/);
    expect(turnElapsedBody).toMatch(/setTurnStartedMs\(undefined\)/);
  });

  it('the import is the only other mention', () => {
    expect(SRC).toMatch(/import \{ playGentleNotificationSound \} from '\.\.\/utils\/sounds'/);
  });
});

describe('R2 — it only rings when there is something to announce', () => {
  it('requires a reply to have landed this turn', () => {
    expect(chimeGuard).toMatch(/repliedBySidRef\.current\[chimeSid\]/);
  });

  it('keeps the background-only and not-stopped guards', () => {
    expect(chimeGuard).toMatch(/!pageActiveRef\.current/);
    expect(chimeGuard).toMatch(/!isSidStopped\(chimeSid\)/);
  });

  it('guards on the event sid, so a parallel pane cannot ring for another', () => {
    expect(turnElapsedBody).toMatch(/const chimeSid = eventSidKey\(\)/);
  });
});

describe('R3 — the reply record follows the turn lifecycle', () => {
  it('is recorded by both terminal-frame handlers', () => {
    expect(handleFinalBody).toMatch(/repliedBySidRef\.current\[finalSid \|\| ''\] = true/);
    expect(endTaskBody).toMatch(/repliedBySidRef\.current\[endSid \|\| ''\] = true/);
  });

  it('starts every workflow cleared, so a quiet turn cannot inherit it', () => {
    // 撤回 / 新建会话 also emit turn_elapsed; without this reset the previous
    // turn's reply would make those ring.
    expect(turnStartBody).toMatch(/if \(isFirstTurn\) repliedBySidRef\.current\[turnSid \|\| ''\] = false/);
  });

  it('is per sid, not one global flag', () => {
    const page = code(fs.readFileSync(path.join(ROOT, 'components/AIChatPage.tsx'), 'utf8'));
    expect(page).toMatch(/const repliedBySidRef = useRef<Record<string, boolean>>\(\{\}\)/);
  });

  it('is threaded from the page, like the other per-sid refs it sits beside', () => {
    const page = code(fs.readFileSync(path.join(ROOT, 'components/AIChatPage.tsx'), 'utf8'));
    expect(page).toMatch(/^ {4}repliedBySidRef,$/m);
    expect(SRC).toMatch(/^ {6}repliedBySidRef,$/m);
  });
});

describe('R4 — one ring per turn', () => {
  it('consumes the record as it rings', () => {
    const at = turnElapsedBody.indexOf('playGentleNotificationSound');
    const cleared = turnElapsedBody.indexOf("repliedBySidRef.current[chimeSid] = false");
    expect(at).toBeGreaterThan(-1);
    expect(cleared).toBeGreaterThan(at);
  });
});
