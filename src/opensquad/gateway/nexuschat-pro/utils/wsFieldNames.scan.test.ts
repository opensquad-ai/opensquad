/**
 * `is_final` spelling guard — source-level contract across the WS boundary.
 *
 * The field is produced on the Python side (`opensquad/protocol_version.py`,
 * `FIELD_IS_FINAL`) and read by several browser consumers. Nothing type-checks
 * across that boundary and the timeline content type is a loose record, so a
 * camelCase `isFinal` write compiled cleanly and silently broke
 * `isWorkflowSettled()` — the compression fold never settled.
 *
 * This file pins the invariant structurally:
 *   R1  the TS mirror declares the snake_case value;
 *   R2  the only `content` object literal producing `compression_progress`
 *       payloads is the shared helper (no inline object with an `isFinal` key);
 *   R3  the spelling `is_final` appears as a string literal in exactly one
 *       frontend file (`wsFieldNames.ts`), so readers cannot drift either;
 *   R4  the Python producer uses the constant, not a literal.
 *
 * Mutations verified (each makes this file fail):
 *   MF1 wsFieldNames.ts flips to `isFinal: 'isFinal'`            → R1 + R3
 *   MF2 hook inlines `content: { text, isFinal, trace_id }`      → R2
 *   MF3 a second file hard-codes `'is_final'`                    → R3
 *   MF4 `_manual_compress.py` writes `{"is_final": final}`        → R4
 */
import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';

const ROOT = path.resolve(__dirname, '..');
const read = (rel: string) => fs.readFileSync(path.join(ROOT, rel), 'utf8');

/** .../src — the Python tree lives next to the frontend at ../../../ */
const PY_ROOT = path.resolve(ROOT, '../../..');
const py = (rel: string) => fs.readFileSync(path.join(PY_ROOT, 'opensquad', rel), 'utf8');

const FIELD_NAMES = read('utils/wsFieldNames.ts');
const HOOK = read('hooks/useAgentWebSocket.ts');
const TIMELINE = read('utils/aiChatTimeline.ts');
const PY_PROTOCOL = py('protocol_version.py');
const PY_PRODUCER = py('_runner/_manual_compress.py');

/**
 * Source with comments stripped.
 *
 * The spelling rules below are about *code*: a docstring that mentions
 * `content.is_final` documents the wire format and must not trip the scan (the
 * sibling `followupSuggestions.scan.test.ts` strips comments for the same
 * reason).
 */
const code = (src: string) => src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');

/** Directories that hold vendored/generated code, never first-party sources. */
const SKIP_DIRS = new Set(['node_modules', 'dist', 'build', 'resources', 'assets']);

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

describe('R1 — the TS field mirror declares the snake_case wire value', () => {
  it('wsFieldNames.ts maps isFinal -> "is_final"', () => {
    expect(FIELD_NAMES).toMatch(/isFinal:\s*'is_final'/);
  });

  it('the Python source of truth agrees', () => {
    expect(PY_PROTOCOL).toMatch(/FIELD_IS_FINAL\s*=\s*"is_final"/);
  });
});

describe('R2 — compression_progress payloads come from the shared helper', () => {
  it('the hook never builds a compression_progress content object inline', () => {
    // An object literal whose `content` carries an `isFinal` key — shorthand
    // (`{ text, isFinal, trace_id }`) or explicit (`{ isFinal: x }`).
    const inlineCamel = /content:\s*\{[^}]*\bisFinal\s*[,:}]/;
    expect(code(HOOK)).not.toMatch(inlineCamel);
    expect(code(TIMELINE)).not.toMatch(inlineCamel);
  });

  it('the hook routes every compression_progress payload through the helper', () => {
    // The hook has three emission sites: append, merge-into-existing, and push.
    // Only two of them name the type literally (the merge spreads an existing
    // event), so count helper calls, not type literals.
    const viaHelper = HOOK.match(/compressionProgressContent\(/g) ?? [];
    expect(viaHelper.length).toBeGreaterThanOrEqual(3);
    // ...and no emission builds its content with a bare object literal at all:
    // every `content:` inside a compression_progress emission must be a call.
    expect(HOOK).not.toMatch(/type:\s*'compression_progress',\s*\n\s*content:\s*\{/);
  });

  it('every consumer reads the flag through the shared accessor', () => {
    expect(HOOK).toContain('isFinalFlag(');
    expect(code(HOOK)).not.toMatch(/\.is_final\b/);
    expect(code(TIMELINE)).not.toMatch(/\.is_final\b/);
    // Three readers in aiChatTimeline alone (isWorkflowSettled,
    // shouldTreatWorkflowComplete) plus the hook and SoloActivityRow — the
    // accessor is the only place allowed to touch the key.
    expect(code(TIMELINE)).toContain('isFinalFlag(');
  });

  it('the accessor refuses the camelCase alias (a stale payload must not settle)', () => {
    // The behavioural half lives in aiChatTimeline.test.ts; this is the source
    // contract: the accessor must go through WS_FIELD_IS_FINAL.
    expect(code(TIMELINE)).toMatch(/\[WS_FIELD_IS_FINAL\]\s*===\s*true/);
  });
});

describe("R3 — 'is_final' is spelled in exactly one frontend file", () => {
  it('no first-party source hard-codes the literal except wsFieldNames.ts', () => {
    const offenders: string[] = [];
    for (const file of FRONTEND_SOURCES) {
      if (rel(file) === 'utils/wsFieldNames.ts') continue;
      const src = code(fs.readFileSync(file, 'utf8'));
      if (/['"]is_final['"]/.test(src)) offenders.push(rel(file));
    }
    expect(offenders).toEqual([]);
  });

  it('SoloActivityRow reads through the accessor too', () => {
    const row = code(read('components/ai-chat/SoloActivityRow.tsx'));
    expect(row).toContain('isFinalFlag(');
    expect(row).not.toMatch(/\.is_final\b/);
  });

  it('the mirror is the file that carries it', () => {
    expect(FIELD_NAMES).toMatch(/['"]is_final['"]/);
  });
});

describe('R4 — the Python producer uses the shared constant', () => {
  it('_manual_compress.py writes FIELD_IS_FINAL rather than a literal key', () => {
    expect(PY_PRODUCER).toContain('FIELD_IS_FINAL');
    expect(PY_PRODUCER).not.toMatch(/["']is_final["']\s*:/);
  });
});
