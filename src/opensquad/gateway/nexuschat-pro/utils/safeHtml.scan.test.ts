// @vitest-environment node
/**
 * Static guard against unsanitized HTML injection.
 *
 * WHY THIS EXISTS
 * ---------------
 * `marked` passes raw HTML through, and this app injects the result with
 * `dangerouslySetInnerHTML` in 25 places across 17 files. Auditing on
 * 2026-09-13 found 11 of those paths were *never* sanitized — an obvious
 * stored-XSS hole that no behavioural test caught, because each component
 * looked locally correct. The sanitizer lived inside one renderer
 * (`fencedMarkdown.ts`) and every other renderer was trusted to remember.
 *
 * This test turns "remember to sanitize" into a build failure:
 *
 *   R1  no `marked.parse` / bare `parse(` inside a `__html:` expression unless
 *       the same expression also calls `sanitizeHtml`
 *   R2  every `__html:` expression must be produced by a known-safe producer
 *       (or a known-safe local variable) — a new/renamed producer fails
 *   R3  each known-safe producer's *definition* must actually sanitize
 *   R4  the exact set of injection sites is pinned, so adding one forces a
 *       human to classify it
 *
 * Mutations verified against the security test set
 * (this file + safeHtml.test.ts + fencedMarkdown.test.ts + highlightText.test.ts).
 * All nine are caught; the baseline is green:
 *
 *   M1 AgentManagerPage: `renderedRole` back to raw `marked.parse`   → R6
 *   M2 highlightText: drop `escapeHtml(text)`                        → highlightText.test
 *   M3 MessageInput: `parseContent` returns `parsed`                 → R3
 *   M4 fencedMarkdown: drop the final `sanitizeHtml`                 → R3 + behaviour
 *   M5 StreamingMessage: `renderMarkdownSafe` returns `raw`          → R7
 *   M6 ToolCallBlock: `renderMarkdown` back to raw `marked.parse`     → R3
 *   M7 ToolCallBlock: catch falls back to `text`                     → R7
 *   M8 a new `dangerouslySetInnerHTML` site                          → R4
 *   M9 sanitizeHtml becomes a no-op                                  → behaviour
 *
 * M2/M6 are why the checks are phrased as bans on the *producer call* rather
 * than "does the body mention a sanitizer": in both cases a legitimate-looking
 * `escapeHtml(query)` / `catch { return escapeHtml(x) }` sat next to the
 * unsanitized branch and satisfied a keyword search.
 *
 * Known limits — do NOT over-trust this file:
 *   - It is a brace-balancing source scanner, not a type-aware dataflow
 *     analysis. A value laundered through an unlisted helper or a module-level
 *     mutable variable will not be traced.
 *   - R2 accepts any top-level call in SAFE_PRODUCERS; correctness of those
 *     functions themselves rests on R3/R7 plus the behavioural tests.
 *   - Untrusted HTML built with `innerHTML`/`insertAdjacentHTML` instead of
 *     `dangerouslySetInnerHTML` is out of scope (none today; see
 *     mermaidHydrate.ts, which is safe via `securityLevel: 'strict'`).
 */
import { describe, expect, it } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const APP_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const SKIP_DIRS = new Set(['node_modules', 'dist', 'dist-electron', 'resources', 'e2e', 'scripts']);

// ---------------------------------------------------------------- file walk

function walk(dir: string, out: string[] = []): string[] {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (SKIP_DIRS.has(entry.name)) continue;
      walk(full, out);
    } else if (/\.tsx?$/.test(entry.name) && !/\.test\.tsx?$/.test(entry.name)) {
      out.push(full);
    }
  }
  return out;
}

const FILES = walk(APP_ROOT).map((f) => ({ abs: f, rel: path.relative(APP_ROOT, f).split(path.sep).join('/') }));

// ------------------------------------------------------- brace-aware slicing

/** Slice `src` from the first `{` at/after `idx` to its matching `}`. */
function balancedFrom(src: string, idx: number, max = 8000): string {
  const start = src.indexOf('{', idx);
  if (start < 0) return src.slice(idx, idx + max);
  let depth = 0;
  let quote: string | null = null;
  let escaped = false;
  for (let i = start; i < src.length && i < start + max; i++) {
    const ch = src[i];
    if (quote) {
      if (escaped) escaped = false;
      else if (ch === '\\') escaped = true;
      else if (ch === quote) quote = null;
      continue;
    }
    if (ch === '"' || ch === "'" || ch === '`') { quote = ch; continue; }
    if (ch === '{') depth++;
    else if (ch === '}') {
      depth--;
      if (depth === 0) return src.slice(start, i + 1);
    }
  }
  return src.slice(start, start + max);
}

// ------------------------------------------------------------- extraction

/** Every `__html: <expr>` expression in a source file. */
function htmlExpressions(src: string): string[] {
  const out: string[] = [];
  const re = /__html\s*:/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(src)) !== null) {
    const start = m.index + m[0].length;
    let depth = 0;
    let quote: string | null = null;
    let escaped = false;
    let i = start;
    for (; i < src.length; i++) {
      const ch = src[i];
      if (quote) {
        if (escaped) escaped = false;
        else if (ch === '\\') escaped = true;
        else if (ch === quote) quote = null;
        continue;
      }
      if (ch === '"' || ch === "'" || ch === '`') { quote = ch; continue; }
      if (ch === '(' || ch === '[' || ch === '{') depth++;
      else if (ch === ')' || ch === ']') depth--;
      else if (ch === '}') {
        if (depth === 0) break;
        depth--;
      }
    }
    out.push(src.slice(start, i));
  }
  return out;
}

/** Non-member call names invoked at the expression's top nesting level. */
function topLevelCalls(expr: string): string[] {
  const names: string[] = [];
  let depth = 0;
  let quote: string | null = null;
  let escaped = false;
  for (let i = 0; i < expr.length; i++) {
    const ch = expr[i];
    if (quote) {
      if (escaped) escaped = false;
      else if (ch === '\\') escaped = true;
      else if (ch === quote) quote = null;
      continue;
    }
    if (ch === '"' || ch === "'" || ch === '`') { quote = ch; continue; }
    if (ch === '(') {
      let j = i - 1;
      while (j >= 0 && /\s/.test(expr[j])) j--;
      let k = j;
      while (k >= 0 && /[A-Za-z0-9_$]/.test(expr[k])) k--;
      const name = expr.slice(k + 1, j + 1);
      const isMember = k >= 0 && expr[k] === '.';
      if (depth === 0 && name && !isMember) names.push(name);
      depth++;
      continue;
    }
    if (ch === ')') { depth--; continue; }
    if (ch === '{' || ch === '[') { depth++; continue; }
    if (ch === '}' || ch === ']') { depth--; continue; }
  }
  return names;
}

function rootIdentifier(expr: string): string {
  const m = /^\s*([A-Za-z_$][\w$]*)/.exec(expr);
  return m ? m[1] : '';
}

/**
 * Right-hand side of a declaration: from `from` up to the first `;` at nesting
 * depth 0 (so `useMemo(() => { …; … }, [dep]);` is captured whole, and a
 * following statement is not).
 */
function rhsUntilStatementEnd(src: string, from: number, max = 2000): string {
  let depth = 0;
  let quote: string | null = null;
  let escaped = false;
  for (let i = from; i < src.length && i < from + max; i++) {
    const ch = src[i];
    if (quote) {
      if (escaped) escaped = false;
      else if (ch === '\\') escaped = true;
      else if (ch === quote) quote = null;
      continue;
    }
    if (ch === '"' || ch === "'" || ch === '`') { quote = ch; continue; }
    if (ch === '(' || ch === '[' || ch === '{') depth++;
    else if (ch === ')' || ch === ']' || ch === '}') depth--;
    else if (ch === ';' && depth === 0) return src.slice(from, i);
  }
  return src.slice(from, from + max);
}

/**
 * True when `text` builds HTML from Markdown without sanitizing it.
 *
 * Used for both `__html:` expressions (R1) and local declarations (R6). The
 * mutation evidence showed that a plain "does it mention a sanitizer anywhere"
 * check is defeated by a `catch { return escapeHtml(x) }` fallback sitting next
 * to an unsanitized `try` branch — so the ban is on the *producer call*.
 */
function hasUnsanitizedMarkdown(text: string): boolean {
  const buildsMarkdown = /\bmarked\.parse\s*\(|\bparse\s*\(/.test(text);
  return buildsMarkdown && !text.includes('sanitizeHtml(');
}

/** Parameter names of a function/arrow signature whose match ends at `from`. */
function parameterNames(src: string, from: number): string[] {
  const open = src.indexOf('(', from);
  if (open < 0) return [];
  let depth = 0;
  let close = -1;
  for (let i = open; i < src.length; i++) {
    if (src[i] === '(') depth++;
    else if (src[i] === ')') {
      depth--;
      if (depth === 0) { close = i; break; }
    }
  }
  if (close < 0) return [];
  return src
    .slice(open + 1, close)
    .split(',')
    .map((p) => p.split(/[:=]/)[0].trim())
    .filter((p) => /^[A-Za-z_$][\w$]*$/.test(p));
}

// ------------------------------------------------------------- policy data

/** Functions whose return value is safe for innerHTML by construction. */
const SAFE_PRODUCERS = new Set([
  'sanitizeHtml',
  'escapeHtml',
  'renderFencedMarkdown',
  'renderMarkdownSafe',
  'highlightLine',
  'highlightText',
  'parseContent',
  'parseMessageContent',
  'renderMarkdown',
  'renderReadMarkdown',
]);

/** Locals derived from a safe producer (name → resolved by R3/R4 review). */
const SAFE_LOCALS = new Set([
  'html',
  'summaryHtml',
  'renderedHtml',
  'renderedMd',
  'renderedRole',
  'highlightedHtml',
  'mdHtml',
]);

/**
 * Producers that are not the primitive themselves must call one of these.
 * Derived from SAFE_PRODUCERS so adding a producer cannot leave this stale —
 * R3 is what guarantees an indirect producer is itself sanitizing.
 */
const SANITIZING_MARKERS = [...SAFE_PRODUCERS].map((n) => `${n}(`);

/**
 * Canonical home of each producer. R3 only inspects these modules, so an
 * unrelated same-named helper elsewhere (e.g. LogsManagerPage's local
 * `highlightLine`, which returns JSX and never touches innerHTML) is not
 * mistaken for the sanitizing renderer.
 */
const PRODUCER_MODULES: Record<string, string[]> = {
  renderFencedMarkdown: ['utils/fencedMarkdown.ts'],
  renderMarkdownSafe: ['components/ai-chat/StreamingMessage.tsx'],
  highlightLine: ['utils/codeHighlight.ts'],
  highlightText: ['utils/highlightText.ts'],
  parseContent: ['components/MessageInput.tsx', 'components/RichTextEditor.tsx'],
  parseMessageContent: ['components/ChatWindow.tsx'],
  renderMarkdown: ['components/ai-chat/ToolCallBlock.tsx'],
  renderReadMarkdown: ['components/ai-chat/FileDiffBlock.tsx'],
};

/**
 * Producers that live in a shared module and must be *imported* — a local
 * function that happens to share the name must not be treated as the
 * sanitizing one.
 */
const SHARED_PRODUCER_MODULE: Record<string, string> = {
  sanitizeHtml: 'utils/safeHtml',
  escapeHtml: 'utils/safeHtml',
  renderFencedMarkdown: 'utils/fencedMarkdown',
  highlightLine: 'utils/codeHighlight',
  highlightText: 'utils/highlightText',
};

const PRODUCERS_TO_VERIFY = [...SAFE_PRODUCERS].filter(
  (n) => n !== 'sanitizeHtml' && n !== 'escapeHtml',
);

/**
 * Exact inventory of `dangerouslySetInnerHTML` sites.
 * A count change means a site was added or removed → re-classify it here.
 */
const EXPECTED_SITES: Record<string, number> = {
  'components/AgentManagerPage.tsx': 1,
  'components/ChatList.tsx': 1,
  'components/ChatWindow.tsx': 2,
  'components/CollabBoardPage.tsx': 1,
  'components/MessageInput.tsx': 1,
  'components/RichTextEditor.tsx': 1,
  'components/RightPanel.tsx': 1,
  'components/RolesPage.tsx': 1,
  'components/SkillManagerPage.tsx': 2,
  'components/ai-chat/FileDiffBlock.tsx': 3,
  'components/ai-chat/FileDocumentEditor.tsx': 3,
  'components/ai-chat/MarkdownScrollBody.tsx': 1,
  'components/ai-chat/MessageBubble.tsx': 2,
  'components/ai-chat/ProjectFilesPanel.tsx': 2,
  'components/ai-chat/StreamingMessage.tsx': 1,
  'components/ai-chat/ToolCallBlock.tsx': 1,
  'components/ai-chat/UnifiedDiffView.tsx': 1,
};

// ------------------------------------------------------------------ helpers

const SITES = FILES.flatMap(({ abs, rel }) => {
  const src = fs.readFileSync(abs, 'utf8');
  return htmlExpressions(src).map((expr) => ({ rel, expr }));
});

// ---------------------------------------------------------------- the rules

describe('R4 — injection-site inventory is pinned', () => {
  it('matches the expected file → count map', () => {
    const actual: Record<string, number> = {};
    for (const { rel } of SITES) actual[rel] = (actual[rel] ?? 0) + 1;
    expect(actual).toEqual(EXPECTED_SITES);
  });

  it('finds a non-trivial number of sites (guards against a broken scanner)', () => {
    expect(SITES.length).toBeGreaterThanOrEqual(25);
  });
});

describe('R1 — no unsanitized markdown reaches innerHTML', () => {
  it('wraps every marked.parse / bare parse( occurrence in sanitizeHtml', () => {
    const offenders: string[] = [];
    for (const { rel, expr } of SITES) {
      if (hasUnsanitizedMarkdown(expr)) {
        offenders.push(`${rel}: ${expr.replace(/\s+/g, ' ').trim().slice(0, 120)}`);
      }
    }
    expect(offenders).toEqual([]);
  });
});

describe('R2 — every site is produced by a known-safe source', () => {
  it('uses only allowlisted producers or allowlisted locals', () => {
    const offenders: string[] = [];
    for (const { rel, expr } of SITES) {
      const calls = topLevelCalls(expr);
      if (calls.length > 0) {
        for (const name of calls) {
          if (!SAFE_PRODUCERS.has(name)) {
            offenders.push(`${rel}: unknown producer "${name}(" in ${expr.replace(/\s+/g, ' ').trim().slice(0, 100)}`);
          }
        }
      } else {
        const root = rootIdentifier(expr);
        if (!SAFE_LOCALS.has(root)) {
          offenders.push(`${rel}: unreviewed local "${root}" in ${expr.replace(/\s+/g, ' ').trim().slice(0, 100)}`);
        }
      }
    }
    expect(offenders).toEqual([]);
  });
});

describe('R3 — every known-safe producer actually sanitizes', () => {
  it('maps every producer to a module that exists', () => {
    const known = new Set(FILES.map((f) => f.rel));
    const missing: string[] = [];
    for (const [name, mods] of Object.entries(PRODUCER_MODULES)) {
      expect(PRODUCERS_TO_VERIFY, `${name} is not in PRODUCERS_TO_VERIFY`).toContain(name);
      for (const mod of mods) {
        if (!known.has(mod)) missing.push(`${name} → ${mod}`);
      }
    }
    expect(missing).toEqual([]);
  });

  it('covers every producer', () => {
    expect(Object.keys(PRODUCER_MODULES).sort()).toEqual([...PRODUCERS_TO_VERIFY].sort());
  });

  it.each(PRODUCERS_TO_VERIFY)('definition of %s calls a sanitizer', (name) => {
    const modules = PRODUCER_MODULES[name];
    const re = new RegExp(`(?:export\\s+)?(?:async\\s+)?function\\s+${name}\\b|(?:export\\s+)?const\\s+${name}\\s*=`, 'g');
    let checked = 0;
    for (const mod of modules) {
      const src = fs.readFileSync(path.join(APP_ROOT, mod), 'utf8');
      let m: RegExpExecArray | null;
      while ((m = re.exec(src)) !== null) {
        const body = balancedFrom(src, m.index + m[0].length);
        checked++;
        expect(
          SANITIZING_MARKERS.some((marker) => body.includes(marker)),
          `${mod} → ${name}() does not sanitize. Body:\n${body.slice(0, 400)}`,
        ).toBe(true);
        // Mutation M6: flipping the `try` branch back to a raw `marked.parse`
        // left `catch { return escapeHtml(x) }` in place, so the marker check
        // above still passed. Ban the unsanitized producer outright instead of
        // looking for the presence of a sanitizer.
        expect(
          hasUnsanitizedMarkdown(body),
          `${mod} → ${name}() builds HTML from unsanitized markdown. Body:\n${body.slice(0, 400)}`,
        ).toBe(false);
      }
    }
    expect(checked, `no definition of ${name} found in ${modules.join(', ')}`).toBeGreaterThan(0);
  });
});

describe('R5 — shared producers are imported, not shadowed', () => {
  it('imports safeHtml/fencedMarkdown/codeHighlight helpers from their module', () => {
    const offenders: string[] = [];
    for (const { abs, rel } of FILES) {
      const src = fs.readFileSync(abs, 'utf8');
      const namesUsed = topLevelCalls(
        htmlExpressions(src).join('\n'),
      );
      for (const name of namesUsed) {
        const mod = SHARED_PRODUCER_MODULE[name];
        if (!mod) continue;
        const importRe = new RegExp(`from\\s+['"][^'"]*${mod.replace(/[/]/g, '\\/')}['"]`);
        if (!importRe.test(src)) {
          offenders.push(`${rel}: uses ${name}() in __html but does not import it from ${mod}`);
        }
      }
    }
    expect(offenders).toEqual([]);
  });
});

describe('R6 — allowlisted locals are actually sanitized where they are built', () => {
  // SAFE_LOCALS ("renderedRole", "mdHtml", …) is a trust list. Without this rule
  // it is a hole: reverting `renderedRole` to a raw `marked.parse` keeps the
  // `__html:` expression itself unchanged (`renderedRole`), so R1/R2 stay green.
  // Verified by mutation: that exact revert is what this catches.
  it('every declaration of an allowlisted local calls a sanitizing producer', () => {
    const offenders: string[] = [];
    for (const { abs, rel } of FILES) {
      const src = fs.readFileSync(abs, 'utf8');
      const used = new Set(topLevelCalls('') .length ? [] : []); // placeholder, filled below
      void used;
      const localsUsed = new Set<string>();
      for (const expr of htmlExpressions(src)) {
        if (topLevelCalls(expr).length === 0) {
          const root = rootIdentifier(expr);
          if (SAFE_LOCALS.has(root)) localsUsed.add(root);
        }
      }
      for (const name of localsUsed) {
        const declRe = new RegExp(
          // const name = … | const [name, setX] = … (ends at the `=` so the RHS
          // starts at the right offset and bracket depth stays balanced)
          `(?:const|let|var)\\s+${name}\\s*=|(?:const|let|var)\\s*\\[\\s*${name}\\s*,[^\\]]*\\]\\s*=`,
          'g',
        );
        let m: RegExpExecArray | null;
        let seen = 0;
        while ((m = declRe.exec(src)) !== null) {
          seen++;
          const rhs = rhsUntilStatementEnd(src, m.index + m[0].length);
          if (!SANITIZING_MARKERS.some((marker) => rhs.includes(marker))) {
            offenders.push(`${rel}: ${name} built without a sanitizer → ${rhs.replace(/\s+/g, ' ').trim().slice(0, 120)}`);
          }
          if (hasUnsanitizedMarkdown(rhs)) {
            offenders.push(`${rel}: ${name} built from unsanitized markdown → ${rhs.replace(/\s+/g, ' ').trim().slice(0, 120)}`);
          }
        }
        if (seen === 0) {
          offenders.push(`${rel}: ${name} used in __html but no declaration found`);
        }
      }
    }
    expect(offenders).toEqual([]);
  });
});

describe('R7 — a producer must not return its own argument unwrapped', () => {
  // `renderMarkdownSafe(raw) { try { return renderFencedMarkdown(raw) } catch
  // { return raw } }` — the `return raw` fallback injects untrusted text, and a
  // keyword check cannot see it because `escapeHtml(` also appears nearby.
  it('no producer has a bare `return <param>` escape hatch', () => {
    const offenders: string[] = [];
    for (const [name, mods] of Object.entries(PRODUCER_MODULES)) {
      const re = new RegExp(`(?:export\\s+)?(?:async\\s+)?function\\s+${name}\\b|(?:export\\s+)?const\\s+${name}\\s*=`, 'g');
      for (const mod of mods) {
        const src = fs.readFileSync(path.join(APP_ROOT, mod), 'utf8');
        let m: RegExpExecArray | null;
        while ((m = re.exec(src)) !== null) {
          const params = parameterNames(src, m.index + m[0].length);
          const body = balancedFrom(src, m.index + m[0].length);
          for (const p of params) {
            if (new RegExp(`\\breturn\\s+${p}\\s*;`).test(body)) {
              offenders.push(`${mod}: ${name}() returns its own argument "${p}" unwrapped`);
            }
          }
        }
      }
    }
    expect(offenders).toEqual([]);
  });
});

describe('safeHtml.ts is the single source of truth', () => {
  it('exports sanitizeHtml and escapeHtml', () => {
    const src = fs.readFileSync(path.join(APP_ROOT, 'utils/safeHtml.ts'), 'utf8');
    expect(src).toMatch(/export function sanitizeHtml\b/);
    expect(src).toMatch(/export function escapeHtml\b/);
    expect(src).toMatch(/\.sanitize\(/);
  });

  // The point of the rule is to stop *ad-hoc* sanitizing: every string handed to
  // `dangerouslySetInnerHTML` must go through `safeHtml.ts`, which owns the HTML
  // profile and the link/target handling.  `mermaidHydrate.ts` is the one
  // sanctioned exception -- it sanitizes **agent-authored SVG** for the mermaid
  // fullscreen viewer and needs the script-free SVG profile
  // (`USE_PROFILES: {svg, svgFilters}`), which the HTML profile would strip
  // instead of cleaning.  A third importer still has to justify itself here.
  const ALLOWED_DOMPURIFY_IMPORTERS = ['utils/mermaidHydrate.ts', 'utils/safeHtml.ts'];

  it('is the only module importing dompurify directly', () => {
    const importers = FILES.filter(({ abs }) =>
      /from\s+['"]dompurify['"]/.test(fs.readFileSync(abs, 'utf8')),
    ).map(({ rel }) => rel);
    expect([...importers].sort()).toEqual([...ALLOWED_DOMPURIFY_IMPORTERS].sort());
  });
});
