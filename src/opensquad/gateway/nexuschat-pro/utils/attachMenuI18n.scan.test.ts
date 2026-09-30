/**
 * The "+" attach menu must follow the UI language.
 *
 * Reported 2026-09-30 with a screenshot of the ZH interface: the popup showed
 * "Upload files / Upload folder / Upload images / Auto speech / Skills" next to
 * a translated 语音 row, and its header still read "Add agents, context, tools…".
 * Every label in that file was a string literal, so switching the language
 * changed nothing — the exact complaint ("怎么中文语言还是英文?").
 *
 * Nothing throws when a label goes back to being hardcoded: it just silently
 * stops following the locale, which is why this is scanned rather than tested.
 *
 *   R1  no user-visible label is left as a literal in SoloAttachMenu.tsx
 *       (comments stripped: the file documents itself in English)
 *   R2  every `aiChat.attach.*` key it uses exists in zh.json AND en.json
 *   R3  those keys carry real Chinese in zh.json (not the English text copied
 *       over) — the failure this file exists to catch
 *
 * Mutations verified:
 *   MR1 put "Upload files" back as a literal                    → R1
 *   MR2 reference aiChat.attach.uploadFiles without adding it   → R2
 *   MR3 copy the English value into zh.json                     → R3
 */
import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';

const ROOT = path.resolve(__dirname, '..');
const read = (rel: string) => fs.readFileSync(path.join(ROOT, rel), 'utf8');
/** Source with comments stripped — the file's own prose is English. */
const code = (src: string) =>
  src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');

const MENU = code(read('components/ai-chat/SoloAttachMenu.tsx'));
const zh = JSON.parse(read('locales/zh.json'));
const en = JSON.parse(read('locales/en.json'));

/** Labels the user sees in this popup, as they were hardcoded before. */
const LITERALS = [
  'Upload files',
  'Upload folder',
  'Upload images',
  'Auto speech',
  'Add agents, context, tools',
  'Loading skills',
  'No skills installed',
  'Goal mode',
  'Attach (',
  '录音消息',
];

function lookup(dict: Record<string, unknown>, dotted: string): unknown {
  let cur: unknown = dict;
  for (const part of dotted.split('.')) {
    if (typeof cur !== 'object' || cur === null) return undefined;
    cur = (cur as Record<string, unknown>)[part];
  }
  return cur;
}

describe('attach menu follows the UI language', () => {
  it('R1: renders every label through t(), not as a literal', () => {
    for (const literal of LITERALS) {
      expect(MENU, `"${literal}" is hardcoded again — it will not follow the locale`).not.toContain(
        literal,
      );
    }
  });

  it('R2: uses aiChat.attach.* keys that exist in both locales', () => {
    const keys = [...MENU.matchAll(/'(aiChat\.attach\.[A-Za-z]+)'/g)].map((m) => m[1]);
    expect(keys.length, 'the menu stopped using the aiChat.attach namespace').toBeGreaterThan(5);
    for (const key of new Set(keys)) {
      expect(lookup(zh, key), `zh.json is missing ${key}`).toBeTruthy();
      expect(lookup(en, key), `en.json is missing ${key}`).toBeTruthy();
    }
  });

  it('R3: the zh values are Chinese, not the English text', () => {
    for (const key of [...new Set([...MENU.matchAll(/'(aiChat\.attach\.[A-Za-z]+)'/g)].map((m) => m[1]))]) {
      const chinese = lookup(zh, key) as string;
      const english = lookup(en, key) as string;
      expect(chinese, `${key} in zh.json is not translated`).toMatch(/[\u4e00-\u9fff]/);
      expect(chinese, `${key} carries the English string in zh.json`).not.toBe(english);
    }
  });

  it('keeps the Skills row on the shared nav label', () => {
    expect(MENU).toContain("t('nav.skills')");
    expect(lookup(zh, 'nav.skills')).toBeTruthy();
    expect(lookup(en, 'nav.skills')).toBeTruthy();
  });
});
