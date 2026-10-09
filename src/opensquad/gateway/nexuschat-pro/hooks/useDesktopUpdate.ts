/**
 * useDesktopUpdate — the desktop-update actions the UI triggers.
 *
 * `startBackgroundDownload` kicks off a non-blocking download (progress flows
 * through `services/desktopUpdateOverlay` via the Electron status channel).
 * `downloadAndInstall` is the same offer for shells that predate 0.9.0: they
 * only ship one combined action that downloads, runs the installer and quits.
 * `installNow` relaunches the installer and quits — but first warns (warn-only,
 * no block) when agent work is still in flight, so an update never silently
 * interrupts a running turn or parallel task.
 *
 * Every entry point answers: when the shell cannot do what was asked (too old
 * for the action, a release without an installer asset) the reason lands in the
 * overlay state the update card renders. Returning silently made a visible
 * button look like a no-op — the 2026-10-09 "clicked download, nothing
 * happened" report was an old shell with no such IPC behind the button.
 */
import { useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import {
  beginDesktopUpdate,
  failDesktopUpdate,
  getDesktopUpdateOverlayState,
} from '../services/desktopUpdateOverlay';
import { getActiveAgentWorkCount } from '../utils/agentActivity';

const KEY_ASSET_MISSING = 'systemConfig.about.desktopUpdateAssetMissing';
const KEY_SHELL_TOO_OLD = 'systemConfig.about.desktopUpdateShellTooOld';
const KEY_FAILED = 'systemConfig.about.desktopUpdateFailed';

/** The offer behind the current UI, or null when the release has no asset for us. */
function currentOffer(): { url: string; fileName: string; version: string | null } | null {
  const state = getDesktopUpdateOverlayState();
  if (!state.downloadUrl || !state.fileName) return null;
  return { url: state.downloadUrl, fileName: state.fileName, version: state.version };
}

export function useDesktopUpdate() {
  const { t } = useTranslation();

  /** Warn (warn-only, no block) before an action that restarts the app. */
  const confirmedBusy = useCallback(() => {
    const active = getActiveAgentWorkCount();
    if (active <= 0) return true;
    return window.confirm(
      t('systemConfig.about.desktopUpdateBusyWarning', { count: active }),
    );
  }, [t]);

  const startBackgroundDownload = useCallback(async () => {
    const env = window.electronEnv;
    const offer = currentOffer();
    if (!offer) {
      failDesktopUpdate(t(KEY_ASSET_MISSING));
      return;
    }
    if (!env?.downloadUpdate) {
      failDesktopUpdate(t(KEY_SHELL_TOO_OLD));
      return;
    }

    beginDesktopUpdate(offer.version);
    try {
      const res = await env.downloadUpdate({
        url: offer.url,
        fileName: offer.fileName,
        version: offer.version ?? undefined,
      });
      if (!res.ok) failDesktopUpdate(res.error);
    } catch (e: any) {
      failDesktopUpdate(e?.message || t(KEY_FAILED));
    }
  }, [t]);

  const downloadAndInstall = useCallback(async () => {
    const env = window.electronEnv;
    const offer = currentOffer();
    if (!offer) {
      failDesktopUpdate(t(KEY_ASSET_MISSING));
      return;
    }
    if (!env?.downloadAndInstallUpdate) {
      failDesktopUpdate(t(KEY_SHELL_TOO_OLD));
      return;
    }
    // Unlike the background download this one quits to install, so it takes the
    // same warning as installNow.
    if (!confirmedBusy()) return;

    beginDesktopUpdate(offer.version);
    try {
      const res = await env.downloadAndInstallUpdate({ url: offer.url, fileName: offer.fileName });
      if (!res.ok) failDesktopUpdate(res.error);
    } catch (e: any) {
      failDesktopUpdate(e?.message || t(KEY_FAILED));
    }
  }, [confirmedBusy, t]);

  const installNow = useCallback(async () => {
    const env = window.electronEnv;
    if (!env?.installUpdate) {
      failDesktopUpdate(t(KEY_SHELL_TOO_OLD));
      return;
    }
    if (!confirmedBusy()) return;

    try {
      const res = await env.installUpdate();
      if (!res.ok) failDesktopUpdate(res.error);
    } catch (e: any) {
      failDesktopUpdate(e?.message || t(KEY_FAILED));
    }
  }, [confirmedBusy, t]);

  return { startBackgroundDownload, installNow, downloadAndInstall };
}
