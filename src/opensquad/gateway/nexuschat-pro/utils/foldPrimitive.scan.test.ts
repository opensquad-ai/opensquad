/**
 * Fold primitive guard — every collapsible surface in the app must fold through
 * `components/Collapse.tsx`, and no surface may regress to the shape that
 * cannot animate: a body that is mounted only while its state is true.
 *
 * Why this file exists (2026-09-14): the sidebar group fold landed first
 * (`.os-collapse` + a rotating chevron). The tool flow still collapsed by
 * unmounting (`{isOpen && (<div>…body…</div>)}`), which snaps — there is no
 * "from" box to interpolate. The fix was to funnel every fold through one
 * primitive; this test is what keeps a future edit from quietly going back.
 *
 * The assertions are deliberately DOM/structural, not snapshot-ish: the body
 * must be wrapped in `.os-collapse` > `.os-collapse-body`, the closed wrapper
 * must be `inert`, and the indicator must be ONE icon that rotates.
 *
 * Mutations verified (each one makes this file fail):
 *   MF1 ToolCallBlock body back to `{isOpen && (…)}`            → R2/R3
 *   MF2 ThoughtBlock body back to `{isOpen && (…)}`             → R2/R3
 *   MF3 TaskFoldBlock back to `{open && (…)}`                   → R2/R3
 *   MF4 WorkflowContainer body back to `className={… 'hidden'}` → R4
 *   MF5 Collapse drops `inert={!open}`                          → R1
 *   MF6 `.os-collapse-body` loses `overflow: hidden`            → R5
 *   MF7 ToolCallBlock swaps `<FoldChevron>` back to a
 *       ChevronDown/ChevronRight ternary                        → R6
 *   MF8 a pinned file stops importing the primitive             → R3
 */
import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';

const ROOT = path.resolve(__dirname, '..');
const read = (rel: string) => fs.readFileSync(path.join(ROOT, rel), 'utf8');

const CSS = read('index.css');
const PRIMITIVE = read('components/Collapse.tsx');

/**
 * Every fold in the app. Pinned on purpose: adding a new collapsible surface
 * means adding its file here (and routing it through the primitive) — that is
 * the whole point, an unpinned fold is an unguarded fold.
 */
const FOLD_SURFACES: Array<{ file: string; uses: string }> = [
  { file: 'components/ai-chat/ToolCallBlock.tsx', uses: '<Collapse open={isOpen}>' },
  { file: 'components/ai-chat/ThoughtBlock.tsx', uses: '<Collapse open={isOpen}>' },
  { file: 'components/ai-chat/PlanBlock.tsx', uses: '<Collapse open={isOpen}>' },
  { file: 'components/ai-chat/TaskFoldBlock.tsx', uses: '<Collapse open={open}>' },
  { file: 'components/ai-chat/ShellJobFold.tsx', uses: '<Collapse open={open}>' },
  { file: 'components/ai-chat/FileDiffBlock.tsx', uses: '<Collapse open={isOpen}>' },
  { file: 'components/ai-chat/WorkflowContainer.tsx', uses: '<Collapse open={isOpen}>' },
  { file: 'components/ai-chat/SubAgentPanel.tsx', uses: '<Collapse open={open}>' },
  { file: 'components/ai-chat/SessionSidebar.tsx', uses: '<Collapse open={open}>' },
  { file: 'components/ai-chat/SoloActivityRow.tsx', uses: '<Collapse open={outerOpen}>' },
  { file: 'components/ai-chat/ContextViewer.tsx', uses: '<ControlledFold open={isExpanded}>' },
  { file: 'components/ai-chat/ProjectFilesPanel.tsx', uses: '<ControlledFold open={isOpenRow}>' },
];

/**
 * `{open && (…)}` / `{isOpen ? (…)}` — a body that only exists while open.
 * The lookbehind excludes the two shapes that are not folds:
 *   `=` → a prop ternary (`title={isOpenRow ? a : b}`);
 *   `$` → a template-literal interpolation (`${open ? ' is-open' : ''}`), which
 *         is exactly what the primitive's own rotating chevron does.
 */
const UNMOUNT_TOGGLE = /(?<![$=])\{\s*(?:isOpen|open|isExpanded|isOpenRow)\s*(?:&&|\?)/;

describe('fold primitive guard', () => {
  it('R1: the primitive keeps the box mounted, clips it, and hides it from a11y', () => {
    expect(PRIMITIVE).toContain('os-collapse');
    expect(PRIMITIVE).toContain('os-collapse-body');
    // `0fr` alone still paints the children — the inner wrapper must clip…
    expect(PRIMITIVE).toContain('className="os-collapse-body"');
    // …and a closed fold must not be reachable by Tab / screen readers.
    expect(PRIMITIVE).toContain('inert={!open}');
    expect(PRIMITIVE).toContain('aria-hidden={!open || undefined}');
    // Lazy mounting must still animate the FIRST expansion.
    expect(PRIMITIVE).toContain('requestAnimationFrame');
    expect(PRIMITIVE).toContain('useControlledFold');
  });

  it('R2: no fold surface conditionally unmounts its body', () => {
    const offenders = FOLD_SURFACES.filter(({ file }) => UNMOUNT_TOGGLE.test(read(file))).map(
      ({ file }) => file,
    );
    expect(
      offenders,
      'A body that only exists while open cannot animate — wrap it in <Collapse>/<ControlledFold> instead',
    ).toEqual([]);
  });

  it('R3: every pinned fold surface imports and uses the primitive', () => {
    const problems: string[] = [];
    for (const { file, uses } of FOLD_SURFACES) {
      const src = read(file);
      if (!/from '\.\.\/Collapse'/.test(src)) problems.push(`${file}: does not import ../Collapse`);
      if (!src.includes(uses)) problems.push(`${file}: missing \`${uses}\``);
    }
    expect(problems).toEqual([]);
  });

  it('R4: no fold surface hides its body with display:none', () => {
    // `display:none` cannot interpolate a height, so `isOpen ? '' : 'hidden'`
    // is a snap in disguise. (Scoped to the fold-state ternary — a panel that
    // hides itself for an unrelated reason is not a fold.)
    const DISPLAY_NONE_FOLD =
      /(?:isOpen|open|isExpanded|isOpenRow)\s*\?\s*['"`]\s*['"`]\s*:\s*['"`]hidden['"`]/;
    const offenders = FOLD_SURFACES.filter(({ file }) => DISPLAY_NONE_FOLD.test(read(file))).map(
      ({ file }) => file,
    );
    expect(offenders).toEqual([]);
  });

  it('R5: the CSS contract — animatable grid track + real clipping + reduced motion', () => {
    const block = (sel: string) => {
      const i = CSS.indexOf(sel);
      expect(i, `${sel} missing from index.css`).toBeGreaterThan(-1);
      return CSS.slice(i, CSS.indexOf('}', i));
    };
    const collapse = block('.os-collapse {');
    expect(collapse).toContain('grid-template-rows: 1fr');
    expect(collapse).toMatch(/transition:[\s\S]*grid-template-rows/);
    expect(block('.os-collapse.is-closed')).toContain('grid-template-rows: 0fr');
    expect(block('.os-collapse-body {')).toContain('overflow: hidden');
    expect(block('.os-collapse-body {')).toContain('min-height: 0');
    expect(block('.os-fold-chevron.is-open {')).toContain('rotate(90deg)');

    // Same duration/easing as the left/right rails — one motion language.
    expect(block('.os-fold-chevron {')).toContain('var(--duration-panel)');
    expect(block('.os-fold-chevron {')).toContain('var(--ease-soft)');
    expect(collapse).toContain('var(--duration-panel)');
    expect(collapse).toContain('var(--ease-soft)');

    // Opt-out list must carry every new class. (index.css has several
    // reduced-motion blocks — take the one that actually names the fold.)
    const marker = CSS.indexOf('.os-collapse', CSS.indexOf('@media (prefers-reduced-motion: reduce) {'));
    const rm = CSS.slice(CSS.lastIndexOf('@media (prefers-reduced-motion: reduce) {', marker));
    for (const cls of ['.os-fold-chevron', '.os-collapse']) {
      expect(rm, `${cls} missing from the reduced-motion opt-out`).toContain(cls);
    }
    expect(rm).toContain('transition: none !important');
  });

  it('R6: the indicator is one rotating icon, never a swapped pair', () => {
    // A ChevronRight/ChevronDown ternary changes the DOM node identity, which
    // can never transition.
    const offenders = FOLD_SURFACES.filter(({ file }) =>
      /ChevronDown[\s\S]{0,200}ChevronRight/.test(read(file)),
    ).map(({ file }) => file);
    expect(offenders).toEqual([]);
    expect(PRIMITIVE).toContain('os-fold-chevron');
    expect(PRIMITIVE).toContain("open && 'is-open'");
  });
});
