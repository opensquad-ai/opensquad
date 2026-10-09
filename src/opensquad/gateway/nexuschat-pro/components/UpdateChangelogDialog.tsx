/**
 * UpdateChangelogDialog — the small "what's new" popup shown when a new
 * desktop build is discovered (and reopenable from the update notification).
 *
 * It only *offers* the update: the primary button hands off to the download the
 * shell supports (background on 0.9.0+, the older combined action otherwise).
 * On the background path the app keeps running; on the older one it installs
 * and restarts. Nothing here restarts the app by itself.
 *
 * Failures from that button are rendered here as well as on the card: this
 * dialog is a modal (`z-[9998]`) and would otherwise hide the card's reason
 * behind its own backdrop — a click that looks like it did nothing.
 */
import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { marked } from 'marked';
import { Download, ExternalLink, X } from 'lucide-react';
import { subscribeDesktopUpdateOverlay } from '../services/desktopUpdateOverlay';
import { sanitizeHtml } from '../utils/safeHtml';
import { desktopUpdateCapabilities } from '../utils/desktopUpdateCapability';

interface Props {
  version: string | null;
  releaseNotes: string | null;
  releaseUrl: string | null;
  isBeta?: boolean;
  /** The primary download action for the shell this dialog runs in. */
  onDownload: () => void;
  onLater: () => void;
}

export const UpdateChangelogDialog: React.FC<Props> = ({
  version,
  releaseNotes,
  releaseUrl,
  isBeta,
  onDownload,
  onLater,
}) => {
  const { t } = useTranslation();
  const [error, setError] = useState<string | null>(null);
  const backgroundDownload = desktopUpdateCapabilities().backgroundDownload;

  useEffect(
    () => subscribeDesktopUpdateOverlay((next) => setError(next.error)),
    [],
  );

  const notes = (releaseNotes || '').trim();

  return (
    <div
      className="fixed inset-0 z-[9998] flex items-center justify-center bg-black/45 backdrop-blur-sm px-4"
      role="dialog"
      aria-modal="true"
      aria-label={t('systemConfig.about.updateChangelogTitle', { version: version ?? '' })}
    >
      <div className="w-full max-w-lg max-h-[80vh] flex flex-col rounded-2xl border border-border bg-panel shadow-2xl">
        <div className="flex items-start gap-3 px-5 py-4 border-b border-border shrink-0">
          <div className="min-w-0 flex-1">
            <h2 className="text-base font-semibold text-textMain truncate">
              {t('systemConfig.about.updateChangelogTitle', { version: version ?? '' })}
            </h2>
            {isBeta && (
              <span className="inline-block mt-1 px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-600 text-[10px] font-medium">
                {t('systemConfig.about.channel.pre-release')}
              </span>
            )}
          </div>
          <button
            type="button"
            onClick={onLater}
            className="p-1 rounded-lg text-textMuted hover:text-textMain hover:bg-black/5 dark:hover:bg-white/10 shrink-0"
            aria-label={t('common.close')}
          >
            <X size={16} />
          </button>
        </div>

        <div className="flex-1 min-h-0 overflow-y-auto px-5 py-4">
          <p className="text-[11px] font-bold text-textMuted uppercase mb-2">
            {t('systemConfig.about.updateChangelogHeading')}
          </p>
          {notes ? (
            <div
              className="update-changelog-body text-[12px] leading-relaxed text-textMain break-words"
              dangerouslySetInnerHTML={{
                // Release notes come from the GitHub release body; sanitized at
                // the single HTML-injection entry point (utils/safeHtml).
                __html: sanitizeHtml(marked.parse(notes, { breaks: true }) as string),
              }}
            />
          ) : (
            <p className="text-[12px] text-textMuted">
              {t('systemConfig.about.updateChangelogEmpty')}
            </p>
          )}
          {releaseUrl && (
            <a
              href={releaseUrl}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1 mt-3 text-[11px] text-primary hover:underline"
            >
              <ExternalLink size={11} />
              {t('systemConfig.about.updateChangelogViewFull')}
            </a>
          )}
        </div>

        <div className="flex items-center justify-end gap-2 px-5 py-3 border-t border-border shrink-0">
          {error && (
            <p className="flex-1 min-w-0 text-[11px] text-rose-500 break-words">
              {t('systemConfig.about.updateNotifyFailed', { error })}
            </p>
          )}
          <button
            type="button"
            onClick={onLater}
            className="px-3 py-1.5 rounded-lg text-[12px] font-medium text-textMuted hover:bg-black/5 dark:hover:bg-white/10"
          >
            {t('systemConfig.about.updateLater')}
          </button>
          <button
            type="button"
            onClick={onDownload}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[12px] font-medium text-white bg-primary hover:bg-primary/90"
          >
            <Download size={13} />
            {backgroundDownload
              ? t('systemConfig.about.updateDownloadInBackground')
              : t('systemConfig.about.updateDownloadAndInstall')}
          </button>
        </div>
      </div>
    </div>
  );
};

export default UpdateChangelogDialog;
