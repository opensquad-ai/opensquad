/**
 * Task-plan decoding contract — source-level guard across the wire boundary.
 *
 * The blank screen was a *cross-language* type lie: Python serialised `{"plan":
 * {}}` for every plain task, TypeScript declared all ten `TaskPlan` fields
 * required, and the panel dereferenced the placeholder as if it were a
 * checkpoint. Types cannot check that boundary, so this file pins it
 * structurally:
 *
 *   S1  `task.plan` is never consumed raw anywhere in the frontend — it is
 *       handed to `normalizeTaskPlan`, nothing else. (`task.plan.budget`,
 *       `task.plan || null`, `task.plan?.budget` are all the same mistake.)
 *   S2  the wire type stays honest: field optional, value partial, never the
 *       fully-populated `TaskPlan`;
 *   S3  the decoder guarantees a complete `budget` object — the exact value
 *       whose absence threw;
 *   S4  an `ErrorBoundary` wraps every L2 panel and the app root, so no future
 *       render throw can unmount the whole product again;
 *   S5  the Python producer emits `None`, not `{}`, when there is no checkpoint.
 *
 * Mutations verified — 11/11 killed; the ones this file catches
 * (script `C:/tmp/prov/mutate_taskplan.py`, report
 * `C:/tmp/prov/mutation_report.json`):
 *   MT1  TaskPanelPage reads `task.plan || null` again      → S1 (2 assertions)
 *   MT4  drop the ErrorBoundary around TaskPanelPage        → S4
 *   MT5  `to_dict` emits `"plan": self.plan` again          → S5
 *   MT6  api.ts declares `plan?: TaskPlan | null`           → S2
 *   MT7  stop mirroring `Budget.from_dict` for attempts     → S3
 *   MT10 drop the app-root boundary                         → S4
 *   MT11 `const plan = task.plan as never`                  → S1
 */
import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';

const ROOT = path.resolve(__dirname, '..');
const read = (rel: string) => fs.readFileSync(path.join(ROOT, rel), 'utf8');

/** .../src/opensquad — the Python tree sits three levels up from the frontend. */
const PY_ROOT = path.resolve(ROOT, '../../..');
const py = (rel: string) => fs.readFileSync(path.join(PY_ROOT, 'opensquad', rel), 'utf8');

/** Source with comments stripped: these rules are about code, not prose. */
const code = (src: string) => src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');

const SKIP_DIRS = new Set(['node_modules', 'dist', 'build', 'resources', 'assets', '.os-worktrees']);

/** First-party `.ts` / `.tsx` sources under the frontend root. */
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

const TASK_PANEL = read('components/ai-chat/TaskPanelPage.tsx');
const API = read('services/api.ts');
const DECODER = read('utils/taskPlan.ts');
const PANE_SHELL = read('components/ai-chat/WorkspacePaneShell.tsx');
const ENTRY = read('index.tsx');
const BOUNDARY = read('components/ErrorBoundary.tsx');
const PY_SCHEDULER = py('tasks/task_scheduler.py');

/**
 * Consuming the raw field: any member access, optional chain, default or
 * conditional. Passing it *whole* to the decoder (`normalizeTaskPlan(task.plan)`)
 * is the one legal use, so a following `)`, `,` or `}` is fine.
 */
const RAW_TASK_PLAN = /task\.plan\s*(?=[.?!&|])/;

describe('S1 — the raw `task.plan` is only ever handed to the decoder', () => {
  it('TaskDetail decodes the plan instead of using it directly', () => {
    expect(code(TASK_PANEL)).toContain('normalizeTaskPlan(task.plan)');
    expect(code(TASK_PANEL)).not.toMatch(RAW_TASK_PLAN);
  });

  it('the `plan && …` guard that opened the budget grid on a placeholder is gone', () => {
    // It used to be `const plan = task.plan || null;` followed by `{plan && …}`.
    expect(code(TASK_PANEL)).not.toMatch(/task\.plan\s*\|\|/);
    expect(code(TASK_PANEL)).not.toContain('plan.budget &&');
  });

  it('no other frontend source dereferences `task.plan`', () => {
    const offenders: string[] = [];
    for (const file of FRONTEND_SOURCES) {
      if (rel(file) === 'components/ai-chat/TaskPanelPage.tsx') continue;
      if (RAW_TASK_PLAN.test(code(fs.readFileSync(file, 'utf8')))) offenders.push(rel(file));
    }
    expect(offenders).toEqual([]);
  });
});

describe('S2 — the wire type stays honest', () => {
  it('the field is optional and typed as the partial wire shape', () => {
    expect(API).toMatch(/plan\?:\s*TaskPlanWire/);
    expect(API).toMatch(/export type TaskPlanWire\s*=\s*Partial<TaskPlan>\s*\|\s*null/);
  });

  it('never claims the field is a fully-populated checkpoint', () => {
    // This declaration is what let `task.plan || null` type-check.
    expect(API).not.toMatch(/plan\?:\s*TaskPlan\s*\|\s*null/);
  });

  it('tells the reader to decode, next to the declaration', () => {
    // A documentation rule, so it asserts on the raw source: `code()` strips
    // comments, and the warning only lives in the doc comment. Without it the
    // next consumer re-invents `task.plan || null`.
    expect(API).toContain('normalizeTaskPlan');
    expect(API).toMatch(/Decode with `?normalizeTaskPlan`?/);
  });
});

describe('S3 — the decoder always returns a complete budget', () => {
  it('builds the budget as an object literal with all three keys', () => {
    expect(DECODER).toMatch(/budget:\s*\{\s*max_tokens:/);
    expect(DECODER).toMatch(/max_seconds:/);
    expect(DECODER).toMatch(/max_attempts:/);
  });

  it('treats the all-empty placeholder as "no checkpoint"', () => {
    // The discriminator itself: no goal, no status, no reason, no budget, no
    // milestone ⇒ null. The behavioural half lives in taskPlan.test.ts.
    expect(code(DECODER)).toMatch(/if\s*\(!goal\s*&&\s*!status\s*&&\s*!blockedReason/);
  });

  it('mirrors Budget.from_dict for max_attempts', () => {
    expect(code(DECODER)).toMatch(/max_attempts:\s*attempts\(/);
  });
});

describe('S4 — a render throw cannot blank the app', () => {
  it('the boundary is a real boundary (both hooks present)', () => {
    expect(BOUNDARY).toContain('getDerivedStateFromError');
    expect(BOUNDARY).toContain('componentDidCatch');
  });

  it('every L2 panel is wrapped', () => {
    expect(PANE_SHELL).toMatch(/<ErrorBoundary[^>]*>\s*<TaskPanelPage/);
    expect(PANE_SHELL).toMatch(/<ErrorBoundary[^>]*>\s*<ScheduledTasksPage/);
  });

  it('the app root is wrapped as a last resort', () => {
    expect(ENTRY).toMatch(/<ErrorBoundary[^>]*full[^>]*>\s*<App\s*\/>/);
  });

  it('the fallback text exists in both locales', () => {
    for (const lng of ['zh', 'en']) {
      const locale = JSON.parse(read(`locales/${lng}.json`)) as Record<string, unknown>;
      const section = locale.errorBoundary as Record<string, string> | undefined;
      expect(section, `${lng}: errorBoundary section`).toBeTruthy();
      for (const key of ['title', 'hint', 'retry', 'reload']) {
        expect(section![key], `${lng}: errorBoundary.${key}`).toBeTruthy();
      }
    }
  });
});

describe('S5 — the Python producer sends null for an absent checkpoint', () => {
  it('to_dict emits None rather than the empty placeholder', () => {
    expect(PY_SCHEDULER).toMatch(/"plan":\s*self\.plan\s+or\s+None/);
    expect(PY_SCHEDULER).not.toMatch(/"plan":\s*self\.plan\s*,/);
  });

  it('the attribute still normalises a non-dict back to a dict on load', () => {
    // ``None`` round-trips through persistence because __init__ guards it;
    // without that guard a reload of a plain task would set plan=None.
    expect(PY_SCHEDULER).toMatch(/self\.plan\s*=\s*dict\(plan\) if isinstance\(plan,\s*dict\) else \{\}/);
  });
});
