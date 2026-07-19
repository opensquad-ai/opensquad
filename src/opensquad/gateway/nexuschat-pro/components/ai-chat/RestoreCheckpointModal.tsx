/**
 * RestoreCheckpointModal — Cursor-style confirm before restoring a checkpoint.
 */
import React, { useEffect } from 'react';
import { useTranslation } from 'react-i18next';

export interface RestoreCheckpointModalProps {
  open: boolean;
  busy?: boolean;
  onCancel: () => void;
  onConfirm: () => void | Promise<void>;
}

export const RestoreCheckpointModal: React.FC<RestoreCheckpointModalProps> = ({
  open,
  busy = false,
  onCancel,
  onConfirm,
}) => {
  const { t } = useTranslation();

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (busy) return;
      if (e.key === 'Escape') {
        e.preventDefault();
        onCancel();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, busy, onCancel]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-[170] flex items-center justify-center bg-black/50 p-4 backdrop-blur-sm"
      role="presentation"
      onMouseDown={(e) => {
        if (busy) return;
        if (e.target === e.currentTarget) onCancel();
      }}
    >
      <div
        className="w-full max-w-md rounded-xl border border-border bg-panel shadow-2xl animate-in fade-in zoom-in-95 duration-150"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="restore-checkpoint-title"
        aria-describedby="restore-checkpoint-desc"
      >
        <div className="px-5 pt-5 pb-2">
          <h3
            id="restore-checkpoint-title"
            className="text-[15px] font-semibold text-text tracking-tight"
          >
            {t('aiChat.restoreCheckpoint.title')}
          </h3>
          <p
            id="restore-checkpoint-desc"
            className="mt-2 text-sm leading-relaxed text-textMuted"
          >
            {t('aiChat.restoreCheckpoint.description')}
          </p>
        </div>
        <div className="flex items-center justify-end gap-2 px-5 py-4">
          <button
            type="button"
            disabled={busy}
            onClick={onCancel}
            className="rounded-lg border border-border bg-transparent px-3.5 py-1.5 text-sm text-text hover:bg-surface disabled:opacity-50"
          >
            {t('common.cancel')}
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={() => void onConfirm()}
            className="rounded-lg bg-primary px-3.5 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50"
          >
            {busy
              ? t('aiChat.restoreCheckpoint.restoring')
              : t('aiChat.restoreCheckpoint.confirm')}
          </button>
        </div>
      </div>
    </div>
  );
};
