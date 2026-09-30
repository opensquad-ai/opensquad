/**
 * UpdateNotification — the always-on update card. Unlike the old full-screen
 * overlay it never blocks the app: the download runs in the background and this
 * card carries the **progress UI under the version line** (bar + percent +
 * bytes). Once the installer is ready it becomes the "restart & install" entry.
 *
 * The install phases (preparing / launching / shutting-down) hand off to the
 * full-screen `DesktopUpdateOverlay`, so this card hides then.
 */
import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Download, RefreshCw, RotateCcw, Sparkles, X } from 'lucide-react';
import {
  subscribeDesktopUpdateOverlay,
  setDesktopUpdateProgress,
  setDesktopUpdatePhase,
  markDesktopUpdateDownloaded,
  type DesktopUpdateOverlayState,
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

interface Props {
  onDownloadBackground: () => void;
  onInstallNow: () => void;
  onOpenChangelog: () => void;
  onDismiss: () => void;
}

export const UpdateNotification: React.FC<Props> = ({
  onDownloadBackground,
  onInstallNow,
  onOpenChangelog,
  onDismiss,
}) => {
  const { t } = useTranslation();
  const [update, setUpdate] = useState<DesktopUpdateOverlayState | null>(null);

  useEffect(() => subscribeDesktopUpdateOverlay(setUpdate), []);

  // Install-phase progress from the main process.
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
      } else if (status.phase === 'downloaded') {
        markDesktopUpdateDownloaded();
      } else {
        setDesktopUpdatePhase(status.phase);
      }
    });
  }, []);

  if (!update) return null;
  const { phase, progress, version, error } = update;
  // The full-screen overlay owns the install phases.
  if (phase === 'idle' || phase === 'preparing' || phase === 'launching' || phase === 'shutting-down') {
    return null;
  }

  const showDeterminate = phase === 'downloading' && progress.total > 0;
  const barWidth = showDeterminate
    ? Math.max(progress.percent, 4)
    : phase === 'downloading' && progress.transferred > 0
      ? Math.min(96, 8 + (progress.transferred % 20))
      : phase === 'downloaded'
        ? 100
        : undefined;

  return (
    <div className="fixed bottom-4 right-4 z-[9997] w-[340px] max-w-[calc(100vw-2rem)] rounded-xl border border-border bg-panel shadow-[0_12px_40px_rgba(0,0,0,0.18)]">
      <div className="flex items-start gap-2 px-3.5 pt-3.5">
        <span className="mt-0.5 shrink-0 text-primary">
          {phase === 'downloaded' ? <Sparkles size={15} /> : <Download size={15} />}
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-[12px] font-semibold text-textMain truncate">
            {phase === 'downloaded'
              ? t('systemConfig.about.updateNotifyDownloaded', { version: version ?? '' })
              : t('systemConfig.about.updateNotifyTitle', { version: version ?? '' })}
          </p>
          <p className="text-[11px] text-textMuted mt-0.5">
            {phase === 'downloading'
              ? t('systemConfig.about.updateNotifyDownloading')
              : phase === 'downloaded'
                ? t('systemConfig.about.updateNotifyReadyHint')
                : t('systemConfig.about.updateNotifyAvailableHint')}
          </p>
        </div>
        <button
          type="button"
          onClick={onDismiss}
          className="p-0.5 rounded text-textMuted hover:text-textMain hover:bg-black/5 dark:hover:bg-white/10 shrink-0"
          aria-label={t('common.close')}
        >
          <X size={13} />
        </button>
      </div>

      {/* Progress UI — sits directly under the notification text. */}
      {(phase === 'downloading' || phase === 'downloaded') && (
        <div className="px-3.5 pt-2.5 space-y-1.5">
          <div className="h-1.5 overflow-hidden rounded-full bg-black/10 dark:bg-white/10">
            {barWidth !== undefined ? (
              <div
                className="h-full rounded-full bg-primary transition-all duration-300 ease-out"
                style={{ width: `${barWidth}%` }}
              />
            ) : (
              <div className="h-full w-1/3 rounded-full bg-primary animate-[updateNotifyIndeterminate_1.4s_ease-in-out_infinite]" />
            )}
          </div>
          <div className="text-[10px] text-textMuted tabular-nums">
            {phase === 'downloaded'
              ? t('systemConfig.about.updateNotifyDownloadedHint')
              : showDeterminate
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
        </div>
      )}

      {error && (
        <p className="px-3.5 pt-2.5 text-[11px] text-rose-500 break-words">
          {t('systemConfig.about.updateNotifyFailed', { error })}
        </p>
      )}

      <div className="flex items-center justify-end gap-2 px-3.5 py-3">
        {phase === 'downloaded' ? (
          <>
            <button
              type="button"
              onClick={onDismiss}
              className="px-2.5 py-1 rounded-lg text-[11px] font-medium text-textMuted hover:bg-black/5 dark:hover:bg-white/10"
            >
              {t('systemConfig.about.updateLater')}
            </button>
            <button
              type="button"
              onClick={onInstallNow}
              className="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg text-[11px] font-medium text-white bg-primary hover:bg-primary/90"
            >
              <RotateCcw size={11} />
              {t('systemConfig.about.updateNotifyInstallNow')}
            </button>
          </>
        ) : phase === 'downloading' ? (
          <span className="text-[11px] text-textMuted inline-flex items-center gap-1">
            <RefreshCw size={11} className="animate-spin" />
            {t('systemConfig.about.updateNotifyDownloadingBg')}
          </span>
        ) : (
          <>
            <button
              type="button"
              onClick={onOpenChangelog}
              className="px-2.5 py-1 rounded-lg text-[11px] font-medium text-textMuted hover:bg-black/5 dark:hover:bg-white/10"
            >
              {t('systemConfig.about.updateNotifyViewChangelog')}
            </button>
            <button
              type="button"
              onClick={onDownloadBackground}
              className="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg text-[11px] font-medium text-white bg-primary hover:bg-primary/90"
            >
              <Download size={11} />
              {error
                ? t('systemConfig.about.updateNotifyRetry')
                : t('systemConfig.about.updateDownloadInBackground')}
            </button>
          </>
        )}
      </div>

      <style>{`
        @keyframes updateNotifyIndeterminate {
          0% { transform: translateX(-120%); }
          100% { transform: translateX(320%); }
        }
      `}</style>
    </div>
  );
};

export default UpdateNotification;
