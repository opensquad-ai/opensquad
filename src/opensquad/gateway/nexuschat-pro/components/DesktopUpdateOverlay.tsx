/**
 * DesktopUpdateOverlay — full-screen install-time overlay.
 *
 * The download no longer blocks (see `UpdateNotification`); this overlay only
 * appears for the brief window where the installer is being launched and the
 * app is about to quit, so the user understands the window is closing on
 * purpose.
 */
import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { OpenSquadLoader } from './OpenSquadLoader';
import {
  subscribeDesktopUpdateOverlay,
  type DesktopUpdateOverlayState,
} from '../services/desktopUpdateOverlay';

function phaseTitleKey(phase: DesktopUpdateOverlayState['phase']): string {
  switch (phase) {
    case 'preparing':
      return 'systemConfig.about.desktopUpdateOverlayPreparing';
    case 'launching':
      return 'systemConfig.about.desktopUpdateOverlayLaunching';
    case 'shutting-down':
      return 'systemConfig.about.desktopUpdateOverlayShuttingDown';
    default:
      return 'systemConfig.about.desktopUpdateOverlayPreparing';
  }
}

function phaseHintKey(phase: DesktopUpdateOverlayState['phase']): string {
  switch (phase) {
    case 'preparing':
      return 'systemConfig.about.desktopUpdateOverlayPreparingHint';
    case 'launching':
      return 'systemConfig.about.desktopUpdateOverlayLaunchingHint';
    case 'shutting-down':
      return 'systemConfig.about.desktopUpdateOverlayShuttingDownHint';
    default:
      return 'systemConfig.about.desktopUpdateOverlayPreparingHint';
  }
}

const INSTALL_PHASES = new Set(['preparing', 'launching', 'shutting-down']);

export const DesktopUpdateOverlay: React.FC = () => {
  const { t } = useTranslation();
  const [overlay, setOverlay] = useState<DesktopUpdateOverlayState | null>(null);

  useEffect(() => subscribeDesktopUpdateOverlay(setOverlay), []);

  if (!overlay || !INSTALL_PHASES.has(overlay.phase)) {
    return null;
  }

  const { phase, version } = overlay;

  return (
    <div
      className="fixed inset-0 z-[9999] flex items-center justify-center bg-slate-950/75 backdrop-blur-sm px-6"
      role="dialog"
      aria-modal="true"
      aria-busy="true"
      aria-label={t(phaseTitleKey(phase))}
    >
      <div className="w-full max-w-md rounded-2xl border border-white/10 bg-slate-900/95 p-8 shadow-2xl text-center">
        <div className="mx-auto mb-5 flex h-16 w-16 items-center justify-center rounded-full bg-primary/15">
          <OpenSquadLoader size={40} />
        </div>

        <h2 className="text-lg font-semibold text-white mb-1">
          {t(phaseTitleKey(phase))}
        </h2>

        {version && (
          <p className="text-sm text-slate-400 mb-4">
            {t('systemConfig.about.desktopUpdateOverlayVersion', { version })}
          </p>
        )}

        <p className="text-sm text-slate-300 mb-6 leading-relaxed">
          {t(phaseHintKey(phase))}
        </p>

        <div className="h-2 overflow-hidden rounded-full bg-slate-800">
          <div className="h-full w-1/3 rounded-full bg-primary animate-[desktopUpdateIndeterminate_1.4s_ease-in-out_infinite]" />
        </div>
      </div>

      <style>{`
        @keyframes desktopUpdateIndeterminate {
          0% { transform: translateX(-120%); }
          100% { transform: translateX(320%); }
        }
      `}</style>
    </div>
  );
};
