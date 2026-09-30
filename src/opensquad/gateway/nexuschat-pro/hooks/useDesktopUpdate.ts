/**
 * useDesktopUpdate — the two desktop-update actions the UI triggers.
 *
 * `startBackgroundDownload` kicks off a non-blocking download (progress flows
 * through `services/desktopUpdateOverlay` via the Electron status channel).
 * `installNow` relaunches the installer and quits — but first warns (warn-only,
 * no block) when agent work is still in flight, so an update never silently
 * interrupts a running turn or parallel task.
 */
import { useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import {
  beginDesktopUpdate,
  failDesktopUpdate,
  getDesktopUpdateOverlayState,
} from '../services/desktopUpdateOverlay';
import { getActiveAgentWorkCount } from '../utils/agentActivity';

export function useDesktopUpdate() {
  const { t } = useTranslation();

  const startBackgroundDownload = useCallback(async () => {
    const env = window.electronEnv;
    const state = getDesktopUpdateOverlayState();
    if (!env?.downloadUpdate || !state.downloadUrl || !state.fileName) return;

    beginDesktopUpdate(state.version);
    try {
      const res = await env.downloadUpdate({
        url: state.downloadUrl,
        fileName: state.fileName,
        version: state.version ?? undefined,
      });
      if (!res.ok) failDesktopUpdate(res.error);
    } catch (e: any) {
      failDesktopUpdate(e?.message || t('systemConfig.about.desktopUpdateFailed'));
    }
  }, [t]);

  const installNow = useCallback(async () => {
    const env = window.electronEnv;
    if (!env?.installUpdate) return;

    const active = getActiveAgentWorkCount();
    if (active > 0) {
      // Warn-only by design: the user may still proceed.
      const proceed = window.confirm(
        t('systemConfig.about.desktopUpdateBusyWarning', { count: active }),
      );
      if (!proceed) return;
    }

    try {
      const res = await env.installUpdate();
      if (!res.ok) failDesktopUpdate(res.error);
    } catch (e: any) {
      failDesktopUpdate(e?.message || t('systemConfig.about.desktopUpdateFailed'));
    }
  }, [t]);

  return { startBackgroundDownload, installNow };
}
