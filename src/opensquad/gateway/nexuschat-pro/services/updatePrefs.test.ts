import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  getUpdatePrefs,
  loadUpdatePrefs,
  resetUpdatePrefsForTest,
  saveUpdatePrefs,
} from './updatePrefs';

beforeEach(() => {
  resetUpdatePrefsForTest();
  (globalThis as any).window = { electronEnv: {} };
});

describe('updatePrefs', () => {
  it('defaults to auto-download and install-on-quit, idle install off', () => {
    expect(getUpdatePrefs()).toEqual({
      autoDownload: true,
      installOnQuit: true,
      autoInstallWhenIdle: false,
    });
  });

  it('loads values from the electron bridge', async () => {
    (globalThis as any).window = {
      electronEnv: {
        getUpdatePrefs: async () => ({
          autoDownload: false,
          installOnQuit: false,
          autoInstallWhenIdle: true,
        }),
      },
    };

    await loadUpdatePrefs();

    expect(getUpdatePrefs()).toEqual({
      autoDownload: false,
      installOnQuit: false,
      autoInstallWhenIdle: true,
    });
  });

  it('saves through the bridge and reflects the stored result', async () => {
    const set = vi.fn(async (p: any) => ({
      autoDownload: true,
      installOnQuit: true,
      autoInstallWhenIdle: false,
      ...p,
    }));
    (globalThis as any).window = { electronEnv: { setUpdatePrefs: set } };

    await saveUpdatePrefs({ autoDownload: false });

    expect(set).toHaveBeenCalledWith({ autoDownload: false });
    expect(getUpdatePrefs().autoDownload).toBe(false);
  });
});
