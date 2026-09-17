/**
 * `uiEvents` contract — the producer/consumer pairing, pinned structurally.
 *
 * The bug this locks out is invisible to every other kind of test. A panel
 * cannot open an L2 tab, so it asks `AIChatPage` through a window `CustomEvent`.
 * `AIChatPage` listened for `opensquad-open-session-tab` (its own comment
 * promised "from the Scheduled Tasks 'task flow' button") and **nothing ever
 * dispatched it**, so the feature simply did nothing — no throw, no warning, no
 * code path a test would touch. `window.dispatchEvent` only runs if someone
 * calls it.
 *
 * Rules:
 *   E1 the event name is spelled in exactly one file (`utils/uiEvents.ts`), so
 *      the two sides cannot drift apart;
 *   E2 both listener registrations use the constant, and panels dispatch
 *      through the helper instead of hand-rolling the event;
 *   E3 **no dead listener**: every `opensquad-*` name a first-party source
 *      listens for has a producer somewhere. Two pre-existing dead listeners
 *      are recorded in `KNOWN_UNPRODUCED` — they are the same defect class and
 *      are deliberately *not* silently tolerated: the assertion demands the
 *      list match exactly, so adding a producer (or a new dead listener) fails
 *      the test until the entry is updated.
 *
 * Mutations verified — see `C:/tmp/prov/mutate_uievents.py`:
 *   ME1 drop `openSessionTab`'s dispatch          → E2, `uiEvents.test.ts`
 *   ME2 TaskPanelPage hand-dispatches the literal → E1
 *   ME3 reverts AIChatPage to the literal         → E1, E2
 *   ME4 remove the `task.session_id` guard        → E4
 *   ME5 add a new listener with no producer       → E3
 */
import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';

const ROOT = path.resolve(__dirname, '..');
const read = (rel: string) => fs.readFileSync(path.join(ROOT, rel), 'utf8');

/** Source with comments stripped — these rules are about code, not prose. */
const code = (src: string) => src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');

const SKIP_DIRS = new Set(['node_modules', 'dist', 'build', 'resources', 'assets', '.os-worktrees']);

const FRONTEND_SOURCES: string[] = (function walk(dir: string, acc: string[] = []): string[] {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (entry.isDirectory()) {
      if (SKIP_DIRS.has(entry.name)) continue;
      walk(path.join(dir, entry.name), acc);
    } else if (/\.tsx?$/.test(entry.name) && !/\.test\.tsx?$/.test(entry.name)) {
      acc.push(path.join(dir, entry.name));
    }
  }
  return acc;
})(ROOT);

const rel = (abs: string) => path.relative(ROOT, abs).replace(/\\/g, '/');

const UI_EVENTS = read('utils/uiEvents.ts');
const AI_CHAT = read('components/AIChatPage.tsx');
const TASK_PANEL = read('components/ai-chat/TaskPanelPage.tsx');

const EVENT_NAME = 'opensquad-open-session-tab';

/** Listeners that exist with no dispatcher anywhere — documented, not hidden. */
const KNOWN_UNPRODUCED: Record<string, string> = {
  'opensquad-open-plugins':
    'AIChatPage listens; no producer in this bundle. Same defect class as the '
    + 'session-tab event fixed here — left as-is because the intended trigger is unknown.',
  'opensquad-open-roles': 'AIChatPage listens; no producer in this bundle. Same as above.',
};

describe('E1 — the event name has exactly one home', () => {
  it('the literal appears only in utils/uiEvents.ts', () => {
    const offenders: string[] = [];
    for (const file of FRONTEND_SOURCES) {
      if (rel(file) === 'utils/uiEvents.ts') continue;
      if (code(fs.readFileSync(file, 'utf8')).includes(`'${EVENT_NAME}'`)) offenders.push(rel(file));
    }
    expect(offenders).toEqual([]);
  });

  it('and it is declared there as the exported constant', () => {
    expect(UI_EVENTS).toMatch(/export const OPEN_SESSION_TAB_EVENT = 'opensquad-open-session-tab'/);
  });
});

describe('E2 — both sides go through the shared name', () => {
  it('the listener registers and unregisters with the constant', () => {
    expect(code(AI_CHAT)).toMatch(/addEventListener\(OPEN_SESSION_TAB_EVENT/);
    expect(code(AI_CHAT)).toMatch(/removeEventListener\(OPEN_SESSION_TAB_EVENT/);
  });

  it('the consumer reads `detail.sessionId`', () => {
    // The shape the producer writes; a rename on one side only would make the
    // listener treat every request as empty.
    expect(code(AI_CHAT)).toMatch(/detail\?\.sessionId/);
    expect(code(UI_EVENTS)).toMatch(/detail: \{ sessionId: id \}/);
  });

  it('no source hand-rolls the dispatch', () => {
    const offenders: string[] = [];
    for (const file of FRONTEND_SOURCES) {
      if (rel(file) === 'utils/uiEvents.ts') continue;
      if (/new CustomEvent\(\s*OPEN_SESSION_TAB_EVENT/.test(code(fs.readFileSync(file, 'utf8')))) {
        offenders.push(rel(file));
      }
    }
    expect(offenders).toEqual([]);
  });

  it('the task panel asks through the helper', () => {
    expect(code(TASK_PANEL)).toContain('openSessionTab(');
  });
});

describe('E3 — no dead listener', () => {
  it('every listened-for opensquad-* event has a producer', () => {
    const consumers = new Set<string>();
    const producers = new Set<string>();

    for (const file of FRONTEND_SOURCES) {
      const src = code(fs.readFileSync(file, 'utf8'));
      for (const m of src.matchAll(/addEventListener\(\s*'(opensquad[-\w:]+)'/g)) consumers.add(m[1]);
      for (const m of src.matchAll(/new CustomEvent\(\s*'(opensquad[-\w:]+)'/g)) producers.add(m[1]);
    }
    // A name exported from uiEvents.ts counts as produced when that file
    // actually dispatches — the helper hides the literal from the scanner.
    if (/dispatchEvent\(/.test(UI_EVENTS)) {
      for (const m of UI_EVENTS.matchAll(/export const [A-Z0-9_]+\s*=\s*'([^']+)'/g)) producers.add(m[1]);
    }

    const dead = [...consumers].filter((n) => !producers.has(n)).sort();
    expect(
      dead,
      'a listener with no dispatcher looks exactly like a broken feature — '
        + 'wire a producer, or record it in KNOWN_UNPRODUCED with a reason',
    ).toEqual(Object.keys(KNOWN_UNPRODUCED).sort());
  });
});

describe('E4 — the task panel offers the way in', () => {
  it('the affordance is gated on a bound session', () => {
    // A queued or failed-before-first-turn task has no session: the button must
    // not appear, and the cell must stay inert rather than open a blank tab.
    expect(code(TASK_PANEL)).toMatch(/\{task\.session_id && \(/);
    expect(code(TASK_PANEL)).toMatch(
      /onClick=\{task\.session_id \? \(\) => openSessionTab\(task\.session_id\) : undefined\}/,
    );
  });

  it('both locales carry its label', () => {
    for (const lng of ['zh', 'en']) {
      const locale = JSON.parse(read(`locales/${lng}.json`)) as {
        taskPanel?: Record<string, string>;
      };
      expect(locale.taskPanel?.viewFlow, `${lng}: taskPanel.viewFlow`).toBeTruthy();
      expect(locale.taskPanel?.viewFlowHint, `${lng}: taskPanel.viewFlowHint`).toBeTruthy();
    }
  });
});
