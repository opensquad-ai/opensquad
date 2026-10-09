// @vitest-environment jsdom
/**
 * The shell under the renderer decides which update actions exist.
 *
 * An installed 0.8.46 app served a 0.9 frontend showed "download in background"; its preload
 * has no `electron:downloadUpdate`, so the click was a silent no-op. These flags are what the
 * UI gates on instead.
 */
import { afterEach, describe, expect, it } from 'vitest';

import { desktopUpdateCapabilities } from './desktopUpdateCapability';

const FULL_ENV = {
  isElectron: true,
  platform: 'win32' as NodeJS.Platform,
  arch: 'x64',
  downloadUpdate: async () => ({ ok: true as const }),
  installUpdate: async () => ({ ok: true as const }),
  downloadAndInstallUpdate: async () => ({ ok: true as const }),
};

afterEach(() => {
  delete window.electronEnv;
});

describe('desktopUpdateCapabilities', () => {
  it('reports nothing for a plain browser', () => {
    expect(desktopUpdateCapabilities()).toEqual({
      backgroundDownload: false,
      installDownloaded: false,
      downloadAndInstall: false,
    });
  });

  it('reports only the combined action for a pre-0.9.0 shell', () => {
    window.electronEnv = {
      isElectron: true,
      platform: 'win32',
      arch: 'x64',
      downloadAndInstallUpdate: async () => ({ ok: true as const }),
    };
    expect(desktopUpdateCapabilities()).toEqual({
      backgroundDownload: false,
      installDownloaded: false,
      downloadAndInstall: true,
    });
  });

  it('reports the granular surface for a current shell', () => {
    window.electronEnv = { ...FULL_ENV };
    expect(desktopUpdateCapabilities()).toEqual({
      backgroundDownload: true,
      installDownloaded: true,
      downloadAndInstall: true,
    });
  });
});
