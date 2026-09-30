/**
 * The git status bar must follow the UI language, and say only words it owns.
 *
 * A hardcoded label does not throw — it silently stops following the locale,
 * which is how the attach menu ended up half-English in a Chinese UI. The same
 * trap is easier to fall into here because most of the git wording ("Push",
 * "Fetch", "Already up to date") is legitimately English in git's own output.
 *
 *   R1  no CJK anywhere in the new git sources outside comments — a Chinese
 *       label that never went through t() fails here
 *   R2  none of the English labels this feature used to be tempted to inline
 *   R3  every `git.*` key these files name exists in zh.json AND en.json
 *   R4  the zh values carry real Chinese, not the English text copied over
 *
 * Mutations verified:
 *   MR1  hardcode 推送 as the sync button's label              → R1
 *   MR2  use 'Switch branch' as a title attribute              → R2
 *   MR3  reference git.branch.rename without adding the key    → R3
 *   MR4  copy the English value into zh.json                   → R4
 */
import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';

const ROOT = path.resolve(__dirname, '..', '..');
const read = (rel: string) => fs.readFileSync(path.join(ROOT, rel), 'utf8');
/** Source with comments stripped — these files document themselves in English. */
const code = (src: string) => src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');

const SOURCES = [
  'components/ai-chat/RepoStatusBar.tsx',
  'components/ai-chat/BranchPicker.tsx',
  'components/ai-chat/GitRepoBar.tsx',
  'components/ai-chat/GitConfirmModal.tsx',
  'components/ai-chat/GitChangesPanel.tsx',
  'utils/gitRepoState.ts',
  'utils/gitChanges.ts',
].map((rel) => ({ rel, src: code(read(rel)) }));

const zh = JSON.parse(read('locales/zh.json'));
const en = JSON.parse(read('locales/en.json'));

/** English labels that belong in the locale files, not in the components. */
const LITERALS = [
  'Switch branch',
  'Search branches',
  'New branch',
  'Delete branch',
  'Force push',
  'Not a Git repository',
  'Initialize repository',
  'Abort merge',
  'View changes',
  'Repositories',
  'Select all',
  'Nothing to commit',
  'Undo last commit',
];

function lookup(dict: Record<string, unknown>, dotted: string): unknown {
  let cur: unknown = dict;
  for (const part of dotted.split('.')) {
    if (typeof cur !== 'object' || cur === null) return undefined;
    cur = (cur as Record<string, unknown>)[part];
  }
  return cur;
}

/** Locale keys named as string literals in one source file. */
function keysIn(src: string): string[] {
  return [...src.matchAll(/'(git\.[A-Za-z.]+)'/g)].map((m) => m[1]);
}

describe('the git bar follows the UI language', () => {
  it('R1: no Chinese literal outside the locale files', () => {
    for (const { rel, src } of SOURCES) {
      const match = src.match(/[\u4e00-\u9fff]+/);
      expect(match?.[0], `${rel} hardcodes "${match?.[0]}" — it will not follow the locale`).toBeUndefined();
    }
  });

  it('R2: none of these labels is inlined in the sources', () => {
    for (const { rel, src } of SOURCES) {
      for (const literal of LITERALS) {
        expect(src, `${rel} hardcodes "${literal}"`).not.toContain(literal);
      }
    }
  });

  it('R3: every git.* key it names exists in both locales', () => {
    const keys = new Set(SOURCES.flatMap(({ src }) => keysIn(src)));
    expect(keys.size, 'the git sources stopped naming locale keys').toBeGreaterThan(15);
    for (const key of keys) {
      expect(lookup(zh, key), `zh.json is missing ${key}`).toBeTruthy();
      expect(lookup(en, key) as string, `en.json is missing ${key}`).toBeTruthy();
    }
  });

  it('R4: the zh values are Chinese, not the English text', () => {
    for (const key of new Set(SOURCES.flatMap(({ src }) => keysIn(src)))) {
      const chinese = lookup(zh, key) as string;
      const english = lookup(en, key) as string;
      expect(chinese, `${key} in zh.json is not translated`).toMatch(/[\u4e00-\u9fff]/);
      expect(chinese, `${key} carries the English string in zh.json`).not.toBe(english);
    }
  });

  it('R5: the fallback error key and the cancel label are translated too', () => {
    expect(lookup(zh, 'git.error.generic')).toBeTruthy();
    expect(lookup(en, 'git.error.generic')).toBeTruthy();
    expect(lookup(zh, 'common.cancel')).toBeTruthy();
    expect(lookup(en, 'common.cancel')).toBeTruthy();
  });
});
