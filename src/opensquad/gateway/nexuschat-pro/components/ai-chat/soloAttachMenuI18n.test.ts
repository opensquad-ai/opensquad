// @vitest-environment jsdom
/**
 * The "+" attach menu must render in the UI language (screenshot report,
 * 2026-09-30: the ZH interface showed "Upload files / Upload folder /
 * Upload images / Auto speech / Skills" around a translated 语音 row).
 *
 * `utils/attachMenuI18n.scan.test.ts` pins the source; this one renders the
 * real component through the app's i18n instance and reads the popup text, so a
 * wrong namespace or a key that exists but never resolves still fails here.
 *
 *   L1  in zh the popup shows 上传文件/上传文件夹/上传图片/自动朗读/技能/目标模式 and
 *       none of the English labels
 *   L2  switching to en switches the same rows back
 *
 * `React.createElement` on purpose: the vitest `include` glob is `**\/*.test.ts`.
 */
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import i18n from '../../i18n';
import { SoloAttachMenu } from './SoloAttachMenu';

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}

const h = React.createElement;

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  void i18n.changeLanguage('zh');
});

/** Open the popup and return the text the user actually sees. */
function openPopup(): string {
  act(() => {
    root.render(
      h(SoloAttachMenu, {
        skills: [],
        skillsLoading: false,
        onUploadFiles: () => undefined,
        onUploadFolder: () => undefined,
        onUploadImages: () => undefined,
        onSelectSkill: () => undefined,
        onGoalMode: () => undefined,
        voiceEnabled: true,
        onOpenVoice: () => undefined,
        autoSpeechEnabled: true,
        onToggleAutoSpeech: () => undefined,
      }),
    );
  });
  const trigger = container.querySelector('button');
  expect(trigger, 'the + trigger did not render').toBeTruthy();
  act(() => {
    trigger!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });
  return document.body.textContent || '';
}

describe('attach menu language', () => {
  it('L1: renders Chinese in the zh interface', async () => {
    await act(async () => {
      await i18n.changeLanguage('zh');
    });
    const text = openPopup();
    for (const label of ['添加智能体', '上传文件', '上传文件夹', '上传图片', '语音', '自动朗读', '技能', '目标模式']) {
      expect(text, `the popup is missing ${label}`).toContain(label);
    }
    for (const leaked of ['Upload files', 'Upload folder', 'Upload images', 'Auto speech', 'Add agents', 'Goal mode']) {
      expect(text, `the popup leaked the English label "${leaked}"`).not.toContain(leaked);
    }
  });

  it('L2: switching to English switches the same rows', async () => {
    await act(async () => {
      await i18n.changeLanguage('en');
    });
    const text = openPopup();
    for (const label of ['Upload files', 'Upload folder', 'Upload images', 'Auto speech', 'Skills', 'Goal mode']) {
      expect(text, `the popup is missing ${label}`).toContain(label);
    }
    expect(text).not.toContain('上传文件');
    expect(text).not.toContain('目标模式');
  });
});
