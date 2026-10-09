// @vitest-environment jsdom
/**
 * The update actions must never be a silent no-op.
 *
 * Field report 2026-10-09 (installed 0.8.46 shell, newer renderer): "clicked download in background,
 * nothing happened" — `startBackgroundDownload` saw no `env.downloadUpdate` and *returned*, leaving
 * the button, the card and the overlay state exactly as they were. Every branch below used to be
 * that same silent return.
 *
 * `React.createElement` rather than JSX: vitest's include glob is `**\/*.test.ts`.
 */
import { act } from 'react';
import React from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

import '../i18n';
import { useDesktopUpdate } from './useDesktopUpdate';
import {
  getDesktopUpdateOverlayState,
  offerDesktopUpdate,
  resetDesktopUpdateOverlay,
} from '../services/desktopUpdateOverlay';

const OFFER = {
  version: '0.9.1',
  releaseNotes: 'notes',
  downloadUrl: 'https://github.com/opensquad-ai/opensquad/releases/download/v0.9.1/OpenSquad-win.exe',
  fileName: 'OpenSquad-win.exe',
};

/** Shells before 0.9.0: they can report an update, and only install one as a whole action. */
const LEGACY_SHELL = {
  isElectron: true,
  platform: 'win32' as NodeJS.Platform,
  arch: 'x64',
  downloadAndInstallUpdate: async () => ({ ok: true as const }),
};

type Actions = ReturnType<typeof useDesktopUpdate>;

let container: HTMLDivElement;
let root: Root;
let actions: Actions;

function Host() {
  actions = useDesktopUpdate();
  return null;
}

beforeEach(() => {
  resetDesktopUpdateOverlay();
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  act(() => {
    root.render(React.createElement(Host));
  });
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  delete window.electronEnv;
});

describe('useDesktopUpdate', () => {
  it('explains itself when the shell has no background download', async () => {
    window.electronEnv = { isElectron: true, platform: 'win32', arch: 'x64' };
    offerDesktopUpdate(OFFER);

    await act(async () => {
      await actions.startBackgroundDownload();
    });

    const state = getDesktopUpdateOverlayState();
    expect(state.error).toBeTruthy();
    // The offer survives so the card can still show what is available / retry.
    expect(state.phase).toBe('available');
    expect(state.version).toBe('0.9.1');
  });

  it('falls back to the combined action on a pre-0.9.0 shell', async () => {
    const legacy = vi.fn(async () => ({ ok: true as const }));
    window.electronEnv = { ...LEGACY_SHELL, downloadAndInstallUpdate: legacy };
    offerDesktopUpdate(OFFER);

    await act(async () => {
      await actions.downloadAndInstall();
    });

    expect(legacy).toHaveBeenCalledWith({ url: OFFER.downloadUrl, fileName: OFFER.fileName });
    expect(getDesktopUpdateOverlayState().phase).toBe('downloading');
    expect(getDesktopUpdateOverlayState().error).toBeNull();
  });

  it('reports a failed download instead of staying quiet', async () => {
    window.electronEnv = {
      isElectron: true,
      platform: 'win32',
      arch: 'x64',
      downloadUpdate: async () => ({ ok: false as const, error: 'network down' }),
    };
    offerDesktopUpdate(OFFER);

    await act(async () => {
      await actions.startBackgroundDownload();
    });

    expect(getDesktopUpdateOverlayState().error).toBe('network down');
  });

  it('does not offer to install on a shell that cannot install', async () => {
    window.electronEnv = { ...LEGACY_SHELL };
    offerDesktopUpdate(OFFER);

    await act(async () => {
      await actions.installNow();
    });

    expect(getDesktopUpdateOverlayState().error).toBeTruthy();
  });

  it('reports a release without an installer asset for this platform', async () => {
    window.electronEnv = {
      isElectron: true,
      platform: 'win32',
      arch: 'x64',
      downloadUpdate: vi.fn(async () => ({ ok: true as const })),
    };
    offerDesktopUpdate({ ...OFFER, downloadUrl: '', fileName: '' });

    await act(async () => {
      await actions.startBackgroundDownload();
    });

    expect(getDesktopUpdateOverlayState().error).toBeTruthy();
  });
});
