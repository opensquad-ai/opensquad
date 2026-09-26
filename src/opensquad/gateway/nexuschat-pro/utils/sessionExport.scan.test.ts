/**
 * Session export guard — the transcript exporter must have exactly ONE flow.
 *
 * Why this file exists (2026-09-26): "export this session as Markdown" used to
 * live only in the sidebar (a hover-only per-row icon plus a nav row). It moved
 * into the context panel, under compress — which would have left two call sites
 * for "read the session → render it → download it". Two hand-rolled copies is
 * how this codebase already drifted once (the usage counters), and the failure
 * mode here is silent: a stale copy exports a TRUNCATED transcript and the
 * caller has no way to notice.
 *
 * So the flow is pinned to `utils/sessionExport.ts`, both entry points must go
 * through it, and the panel must not drift in the labels/locales it needs.
 *
 * Mutations verified (each one makes this file fail):
 *   MF1 sidebar hand-rolls the flow again (getSessionHistory + download)  → R1
 *   MF2 a second module starts calling downloadTextFile(                  → R1
 *   MF3 panel drops the export row / stops wiring onExportContext         → R2
 *   MF4 panel moves export above compress                                 → R3
 *   MF5 sidebar re-adds the "session you are reading" nav row              → R4
 *   MF6 exportContext keys added to zh.json only                          → R5
 */
import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';

const ROOT = path.resolve(__dirname, '..');
const read = (rel: string) => fs.readFileSync(path.join(ROOT, rel), 'utf8');

const FLOW_MODULE = 'utils/sessionExport.ts';
/** Defines the primitives; the point of the rule is that nothing else CALLS them. */
const DEFINER = 'utils/sessionMarkdown.ts';
const SKIP_DIRS = ['node_modules', 'dist', 'dist-electron', 'resources', '.vite'];

/**
 * Every source file under the frontend, minus tests (a test legitimately imports
 * the primitives) and build output. Walked with pruning rather than
 * `readdirSync(recursive)` — that would descend into node_modules first and only
 * then filter, which is thousands of files of pure latency.
 */
const sourceFiles = (): string[] => {
  const out: string[] = [];
  const walk = (dir: string, prefix: string) => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const name = entry.name;
      if (entry.isDirectory()) {
        if (SKIP_DIRS.includes(name) || name.startsWith('.')) continue;
        walk(path.join(dir, name), `${prefix}${name}/`);
        continue;
      }
      const rel = `${prefix}${name}`;
      if (!/\.(ts|tsx)$/.test(rel)) continue;
      if (/\.test\.|\.spec\./.test(rel)) continue;
      out.push(rel);
    }
  };
  walk(ROOT, '');
  return out.sort();
};

/**
 * Drop comments before searching, so a doc line that merely mentions a call is
 * not mistaken for one. `//` is skipped when it follows `:` so `https://…` in a
 * string survives the strip.
 */
const withoutComments = (src: string): string =>
  src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:\w/])\/\/[^\n]*/g, '$1');

const callersOf = (needle: string): string[] =>
  sourceFiles()
    .filter((f) => f !== DEFINER)
    .filter((f) => withoutComments(read(f)).includes(needle))
    .sort();

describe('session export guard', () => {
  it('R1: exactly one module runs the export flow', () => {
    expect(
      callersOf('downloadTextFile('),
      'the transcript download must have one owner — route it through utils/sessionExport',
    ).toEqual([FLOW_MODULE]);
    expect(
      callersOf('sessionToMarkdown('),
      'rendering a session to Markdown must have one owner — route it through utils/sessionExport',
    ).toEqual([FLOW_MODULE]);
    // The flow itself must keep preferring a COMPLETE cache over the API, or a
    // partially loaded session exports a truncated transcript.
    const flow = read(FLOW_MODULE);
    expect(flow).toMatch(/cached\?\.complete/);
    expect(flow).toContain('buildTimelineFromSession(');
  });

  it('R2: both entry points go through the shared flow', () => {
    // Sidebar: the remaining per-row affordance calls the shared exporter.
    const sidebar = read('components/ai-chat/SessionSidebar.tsx');
    expect(sidebar).toContain('exportSessionToMarkdown(');

    // Panel → composer → page: the row must be wired end to end, not just drawn.
    expect(read('components/ai-chat/SoloContextFooter.tsx')).toContain('onExportContext');
    expect(read('components/ai-chat/AgentWebComposer.tsx')).toContain('onExportContext');
    const page = read('components/AIChatPage.tsx');
    expect(page).toContain('onExportContext={');
    expect(page).toContain('exportSessionToMarkdown(');
  });

  it('R3: the panel puts export under compress', () => {
    const panel = read('components/ai-chat/SoloContextFooter.tsx');
    const compress = panel.indexOf('<Scissors');
    const exportIcon = panel.indexOf('<FileDown');
    expect(compress, 'compress row missing from the panel').toBeGreaterThan(-1);
    expect(exportIcon, 'export row missing from the panel').toBeGreaterThan(-1);
    expect(
      compress < exportIcon,
      'export must render after compress: details → compress → export',
    ).toBe(true);
  });

  it('R4: the sidebar keeps no second "session you are reading" export row', () => {
    // A re-added nav row means the panel entry has a duplicate again — one of
    // them will silently fall behind.
    expect(read('components/ai-chat/SessionSidebar.tsx')).not.toContain('currentSessionForExport');
  });

  it('R5: the new panel labels exist in both locales', () => {
    for (const locale of ['locales/zh.json', 'locales/en.json']) {
      const parsed = JSON.parse(read(locale)) as { aiChat?: Record<string, unknown> };
      expect(parsed.aiChat?.exportContext, `${locale} is missing aiChat.exportContext`).toBeTruthy();
      expect(parsed.aiChat?.exportContextHint, `${locale} is missing aiChat.exportContextHint`).toBeTruthy();
    }
  });
});
