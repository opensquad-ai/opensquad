// @vitest-environment jsdom
/**
 * The update card's copy must match what the shell can actually do.
 *
 * "Download in background — keep working as usual" is a lie on a pre-0.9.0 shell: that action
 * downloads, installs and quits. The label and the hint follow the capability flag, and the click
 * reaches whatever action the host wired for this shell.
 *
 * `React.createElement` rather than JSX: vitest's include glob is `**\/*.test.ts`.
 */
import { act } from 'react';
import React from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

import i18n from '../i18n';
import { UpdateNotification } from './UpdateNotification';
import {
  failDesktopUpdate,
  offerDesktopUpdate,
  resetDesktopUpdateOverlay,
} from '../services/desktopUpdateOverlay';

const OFFER = {
  version: '0.9.1',
  releaseNotes: 'notes',
  downloadUrl: 'https://github.com/opensquad-ai/opensquad/releases/download/v0.9.1/OpenSquad-win.exe',
  fileName: 'OpenSquad-win.exe',
};

let container: HTMLDivElement;
let root: Root;

function renderCard(onDownload: () => void) {
  act(() => {
    root.render(
      React.createElement(UpdateNotification, {
        onDownload,
        onInstallNow: () => {},
        onOpenChangelog: () => {},
        onDismiss: () => {},
      }),
    );
  });
}

function buttonWith(text: string): HTMLButtonElement | undefined {
  return Array.from(container.querySelectorAll('button')).find(
    (b) => (b.textContent || '').includes(text),
  );
}

beforeEach(async () => {
  await i18n.changeLanguage('zh');
  resetDesktopUpdateOverlay();
  offerDesktopUpdate(OFFER);
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  delete window.electronEnv;
  resetDesktopUpdateOverlay();
});

describe('UpdateNotification on a shell without background download', () => {
  it('offers the combined action and says the app will restart', () => {
    window.electronEnv = {
      isElectron: true,
      platform: 'win32',
      arch: 'x64',
      downloadAndInstallUpdate: async () => ({ ok: true as const }),
    };
    const onDownload = vi.fn();
    renderCard(onDownload);

    const label = i18n.t('systemConfig.about.updateDownloadAndInstall');
    expect(container.textContent).toContain(i18n.t('systemConfig.about.updateNotifyAvailableHintLegacy'));
    expect(container.textContent).not.toContain(i18n.t('systemConfig.about.updateNotifyAvailableHint'));

    const primary = buttonWith(label);
    expect(primary).toBeTruthy();
    act(() => {
      primary!.click();
    });
    expect(onDownload).toHaveBeenCalledTimes(1);
  });

  it('keeps the background copy when the shell supports it', () => {
    window.electronEnv = {
      isElectron: true,
      platform: 'win32',
      arch: 'x64',
      downloadUpdate: async () => ({ ok: true as const }),
      installUpdate: async () => ({ ok: true as const }),
    };
    renderCard(vi.fn());

    expect(container.textContent).toContain(i18n.t('systemConfig.about.updateNotifyAvailableHint'));
    expect(buttonWith(i18n.t('systemConfig.about.updateDownloadInBackground'))).toBeTruthy();
    expect(buttonWith(i18n.t('systemConfig.about.updateDownloadAndInstall'))).toBeUndefined();
  });

  it('shows the reason on the card, not just the button being dead', () => {
    // The failure the *card* has to surface: a click that produced no visible change.
    window.electronEnv = { isElectron: true, platform: 'win32', arch: 'x64' };
    failDesktopUpdate('shell too old');
    renderCard(vi.fn());

    expect(container.textContent).toContain(
      i18n.t('systemConfig.about.updateNotifyFailed', { error: 'shell too old' }),
    );
  });
});
