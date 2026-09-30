/**
 * GitConfirmModal — the confirmation surface for the git actions that cannot be
 * undone (discarding work, force-pushing, deleting a branch) or that need an
 * explicit choice (stashing before a switch).
 *
 * It exists as its own component so a force push can be a *second* confirmation
 * of the same shape as the first, rather than a `window.confirm` that blocks the
 * page and cannot show the hint line saying what `--force-with-lease` will do.
 */
import React from 'react';
import { useTranslation } from 'react-i18next';
import { AlertTriangle, X } from 'lucide-react';
import { SoftOverlay } from '../SoftOverlay';

export interface GitConfirmSpec {
  title: string;
  body: string;
  /** Extra line under the body: the caveat that makes the action acceptable. */
  hint?: string;
  confirmLabel: string;
  /** `danger` fills the confirm button red — for discards and forced operations. */
  tone?: 'danger' | 'primary';
  /** Test hook for the action under confirmation (`discard`, `force-push`, …). */
  testId: string;
  onConfirm: () => void;
}

interface GitConfirmModalProps {
  spec: GitConfirmSpec | null;
  busy?: boolean;
  onCancel: () => void;
}

export const GitConfirmModal: React.FC<GitConfirmModalProps> = ({ spec, busy = false, onCancel }) => {
  const { t } = useTranslation();
  const danger = (spec?.tone || 'primary') === 'danger';

  return (
    <SoftOverlay
      open={!!spec}
      onBackdrop={onCancel}
      dismissDisabled={busy}
      panelClassName="w-[min(440px,92vw)] rounded-xl bg-bgLight border border-border shadow-2xl"
    >
      {spec ? (
        <div role="dialog" aria-modal="true" data-testid={`git-confirm-${spec.testId}`}>
          <div className="flex items-center justify-between px-4 pt-4 pb-2">
            <h3 className="inline-flex items-center gap-2 text-[15px] font-semibold text-textMain">
              {danger ? <AlertTriangle size={15} className="text-rose-500 shrink-0" /> : null}
              {spec.title}
            </h3>
            <button type="button" onClick={onCancel} className="p-1 rounded-md hover:bg-primary/10" title={t('common.cancel')}>
              <X size={16} className="text-textMuted" />
            </button>
          </div>
          <div className="px-4 pb-3 text-[13px] text-textMuted leading-relaxed whitespace-pre-line">{spec.body}</div>
          {spec.hint ? <div className="px-4 pb-3 text-[12px] text-textMuted/80">{spec.hint}</div> : null}
          <div className="flex justify-end gap-2 px-4 pb-4">
            <button
              type="button"
              disabled={busy}
              onClick={onCancel}
              className="px-3 py-1.5 rounded-lg text-[13px] border border-border text-textMain hover:bg-primary/10 disabled:opacity-50"
            >
              {t('common.cancel')}
            </button>
            <button
              type="button"
              data-testid={`git-confirm-${spec.testId}-go`}
              disabled={busy}
              onClick={() => spec.onConfirm()}
              className={`px-3 py-1.5 rounded-lg text-[13px] font-medium text-white disabled:opacity-50 ${
                danger ? 'bg-rose-600 hover:bg-rose-500' : 'bg-primary hover:bg-primary/90'
              }`}
            >
              {spec.confirmLabel}
            </button>
          </div>
        </div>
      ) : null}
    </SoftOverlay>
  );
};

export default GitConfirmModal;
