import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { OpenSquadLoader } from './OpenSquadLoader';
import {
  subscribeDesktopUpdateOverlay,
  setDesktopUpdatePhase,
  setDesktopUpdateProgress,
  type DesktopUpdateOverlayState,
  type DesktopUpdatePhase,
} from '../services/desktopUpdateOverlay';

function formatBytes(bytes: number): string {
  if (!bytes || bytes <= 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB'];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(unit === 0 ? 0 : 1)} ${units[unit]}`;
}

function phaseTitleKey(phase: DesktopUpdatePhase): string {
  switch (phase) {
    case 'downloading':
      return 'systemConfig.about.desktopUpdateOverlayDownloading';
    case 'preparing':
      return 'systemConfig.about.desktopUpdateOverlayPreparing';
    case 'launching':
      return 'systemConfig.about.desktopUpdateOverlayLaunching';
    case 'shutting-down':
      return 'systemConfig.about.desktopUpdateOverlayShuttingDown';
    default:
      return 'systemConfig.about.desktopUpdateOverlayDownloading';
  }
}

function phaseHintKey(phase: DesktopUpdatePhase): string {
  switch (phase) {
    case 'downloading':
      return 'systemConfig.about.desktopUpdateOverlayDownloadingHint';
    case 'preparing':
      return 'systemConfig.about.desktopUpdateOverlayPreparingHint';
    case 'launching':
      return 'systemConfig.about.desktopUpdateOverlayLaunchingHint';
    case 'shutting-down':
      return 'systemConfig.about.desktopUpdateOverlayShuttingDownHint';
    default:
      return 'systemConfig.about.desktopUpdateOverlayDownloadingHint';
  }
}

export const DesktopUpdateOverlay: React.FC = () => {
  const { t } = useTranslation();
  const [overlay, setOverlay] = useState<DesktopUpdateOverlayState>(() => ({
    phase: 'idle',
    progress: { percent: 0, transferred: 0, total: 0 },
    error: null,
    version: null,
  }));

  useEffect(() => subscribeDesktopUpdateOverlay(setOverlay), []);

  useEffect(() => {
    const env = window.electronEnv;
    if (!env?.onUpdateStatus) return;

    return env.onUpdateStatus((status) => {
      if (status.phase === 'downloading') {
        setDesktopUpdateProgress({
          percent: status.percent ?? 0,
          transferred: status.transferred ?? 0,
          total: status.total ?? 0,
        });
        return;
      }
      setDesktopUpdatePhase(status.phase);
    });
  }, []);

  if (overlay.phase === 'idle') {
    return null;
  }

  const { phase, progress, version } = overlay;
  const showDeterminate = phase === 'downloading' && progress.total > 0;
  const barWidth = showDeterminate
    ? Math.max(progress.percent, 4)
    : phase === 'downloading' && progress.transferred > 0
      ? Math.min(96, 8 + (progress.transferred % 20))
      : undefined;

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

        <div className="space-y-2">
          <div className="h-2 overflow-hidden rounded-full bg-slate-800">
            {barWidth !== undefined ? (
              <div
                className="h-full rounded-full bg-primary transition-all duration-300 ease-out"
                style={{ width: `${barWidth}%` }}
              />
            ) : (
              <div className="h-full w-1/3 rounded-full bg-primary animate-[desktopUpdateIndeterminate_1.4s_ease-in-out_infinite]" />
            )}
          </div>

          {phase === 'downloading' && (
            <div className="text-xs text-slate-400 tabular-nums">
              {showDeterminate
                ? t('systemConfig.about.desktopUpdateOverlayProgress', {
                    percent: Math.round(progress.percent),
                    transferred: formatBytes(progress.transferred),
                    total: formatBytes(progress.total),
                  })
                : progress.transferred > 0
                  ? t('systemConfig.about.desktopUpdateOverlayTransferred', {
                      transferred: formatBytes(progress.transferred),
                    })
                  : t('systemConfig.about.desktopUpdateOverlayStarting')}
            </div>
          )}
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
