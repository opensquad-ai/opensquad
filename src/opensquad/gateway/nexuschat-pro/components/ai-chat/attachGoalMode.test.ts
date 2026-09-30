// @vitest-environment jsdom
/**
 * "+" menu → 目标模式 must be the same entry point as typing `/goal `.
 *
 * Reported 2026-09-30: goal mode was reachable only by hand-typing the slash
 * command, so it stayed invisible to anyone who never guessed it existed. The
 * row must not grow a second, parallel way into goal mode — it inserts the very
 * text `/goal ` and lets the composer's existing parser take over, so the arg
 * picker, the send path and `/goal`'s lifecycle words all keep working.
 *
 * A regression here is silent (the row simply stops doing anything, or drifts
 * into a second code path): R1 drives the row for real, R2 pins the equivalence
 * on the parser and the wiring in the composer.
 *
 *   R1  clicking the row fires onGoalMode once, and the row's label/中文 come
 *       from i18n in both locales;
 *   R2  the inserted text parses back to `{kind:'goal'}` — identical to typing
 *       `/goal ` — and the composer routes the row through the slash picker.
 *
 * Mutations verified:
 *   MR1 drop `onClick={() => run(onGoalMode)}` from the row   → R1
 *   MR2 hardcode the English label instead of t()             → R1 (locale half)
 *   MR3 point the composer row at `toggleVoicePanel`          → R2
 *   MR4 insert `/goal` without the trailing space             → R2
 *
 * `React.createElement` on purpose: the vitest `include` glob is `**\/*.test.ts`.
 */
import fs from 'node:fs';
import path from 'node:path';
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import i18n from '../../i18n';
import { SoloAttachMenu } from './SoloAttachMenu';
import { parseSlashInput, SLASH_COMMANDS, slashCommandTriggerText } from './slashCommands';

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}

const h = React.createElement;
const ROOT = path.resolve(__dirname, '..', '..');
const read = (rel: string) => fs.readFileSync(path.join(ROOT, rel), 'utf8');
/** Source with comments stripped — these rules are about code, not prose. */
const code = (src: string) => src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
const COMPOSER = code(read('components/ai-chat/AgentWebComposer.tsx'));
const MENU = code(read('components/ai-chat/SoloAttachMenu.tsx'));

const goalDef = SLASH_COMMANDS.find((cmd) => cmd.id === 'goal')!;

let container: HTMLDivElement;
let root: Root;
let onGoalMode: ReturnType<typeof vi.fn>;

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  onGoalMode = vi.fn();
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  void i18n.changeLanguage('zh');
});

/** Open the popup, click the goal row labelled `label`, return the popup text. */
function clickGoalRow(label: string): string {
  act(() => {
    root.render(
      h(SoloAttachMenu, {
        skills: [],
        onUploadFiles: () => undefined,
        onUploadFolder: () => undefined,
        onUploadImages: () => undefined,
        onSelectSkill: () => undefined,
        onGoalMode,
      }),
    );
  });
  const trigger = container.querySelector('button');
  act(() => {
    trigger!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });
  const text = document.body.textContent || '';
  const row = [...document.querySelectorAll('button')].find((b) => b.textContent === label);
  expect(row, `the "${label}" row did not render`).toBeTruthy();
  act(() => {
    row!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });
  return text;
}

describe('R1 — the row opens goal mode', () => {
  it('fires onGoalMode once, and only for this row', async () => {
    await act(async () => {
      await i18n.changeLanguage('zh');
    });
    clickGoalRow('目标模式');
    expect(onGoalMode).toHaveBeenCalledTimes(1);
  });

  it('labels the row from i18n in both locales', async () => {
    await act(async () => {
      await i18n.changeLanguage('zh');
    });
    const zhText = clickGoalRow('目标模式');
    expect(zhText).toContain('目标模式');
    expect(zhText).not.toContain('Goal mode');

    onGoalMode.mockClear();
    await act(async () => {
      await i18n.changeLanguage('en');
    });
    const enText = clickGoalRow('Goal mode');
    expect(enText).toContain('Goal mode');
    expect(enText).not.toContain('目标模式');
  });

  it('hides the row when the composer does not offer goal mode', async () => {
    await act(async () => {
      await i18n.changeLanguage('zh');
    });
    act(() => {
      root.render(
        h(SoloAttachMenu, {
          skills: [],
          onUploadFiles: () => undefined,
          onUploadFolder: () => undefined,
          onUploadImages: () => undefined,
          onSelectSkill: () => undefined,
        }),
      );
    });
    const trigger = container.querySelector('button');
    act(() => {
      trigger!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    expect(document.body.textContent || '').not.toContain('目标模式');
  });
});

describe('R2 — that is the same entry point as typing /goal', () => {
  it('the inserted text parses back into goal mode', () => {
    const inserted = slashCommandTriggerText(goalDef);
    expect(inserted).toBe('/goal ');
    expect(parseSlashInput(inserted)).toEqual({ kind: 'goal', query: '' });
    expect(parseSlashInput(inserted)).toEqual(parseSlashInput('/goal '));
  });

  it('the menu exposes an optional onGoalMode row', () => {
    expect(MENU).toMatch(/onGoalMode\?: \(\) => void;/);
    expect(MENU).toMatch(/onClick=\{\(\) => run\(onGoalMode\)\}/);
    expect(MENU).toMatch(/t\('aiChat\.attach\.goalMode'\)/);
  });

  it('the composer routes it through the slash-command picker', () => {
    expect(COMPOSER).toMatch(/onGoalMode=\{enterGoalMode\}/);
    expect(COMPOSER).toMatch(/const enterGoalMode = useCallback/);
    expect(COMPOSER).toMatch(/SLASH_COMMANDS\.find\(\(cmd\) => cmd\.id === 'goal'\)/);
    expect(COMPOSER).toMatch(/selectSlashCommand\(goal\)/);
  });

  it('the labels exist in both locales, with Chinese in zh', () => {
    const zh = JSON.parse(read('locales/zh.json'));
    const en = JSON.parse(read('locales/en.json'));
    for (const key of ['goalMode', 'goalModeHint'] as const) {
      expect(zh.aiChat.attach[key], `zh.json is missing aiChat.attach.${key}`).toBeTruthy();
      expect(en.aiChat.attach[key], `en.json is missing aiChat.attach.${key}`).toBeTruthy();
    }
    expect(zh.aiChat.attach.goalMode).toMatch(/[\u4e00-\u9fff]/);
    expect(zh.aiChat.attach.goalMode).not.toBe(en.aiChat.attach.goalMode);
    expect(zh.aiChat.attach.goalModeHint).toContain('/goal');
  });
});
