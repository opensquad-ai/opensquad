/**
 * WorkingDirectoryModal — 设置 Agent 工作目录（浏览器无法解析绝对路径时的回退 UI）
 */
import React, { useEffect, useState } from 'react';
import { X, FolderOpen } from 'lucide-react';
import { useTranslation } from 'react-i18next';

export interface WorkingDirectoryModalProps {
  open: boolean;
  initialPath: string;
  folderNameHint?: string | null;
  onClose: () => void;
  onConfirm: (path: string) => void | Promise<void>;
  onBrowse?: () => void;
  saving?: boolean;
}

export const WorkingDirectoryModal: React.FC<WorkingDirectoryModalProps> = ({
  open,
  initialPath,
  folderNameHint,
  onClose,
  onConfirm,
  onBrowse,
  saving = false,
}) => {
  const { t } = useTranslation();
  const [path, setPath] = useState(initialPath);

  useEffect(() => {
    if (open) setPath(initialPath);
  }, [open, initialPath]);

  if (!open) return null;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = path.trim();
    if (!trimmed) return;
    await onConfirm(trimmed);
  };

  return (
    <div className="fixed inset-0 bg-black/50 z-[160] flex items-center justify-center backdrop-blur-sm p-4">
      <div
        className="bg-panel rounded-2xl shadow-2xl w-full max-w-lg border border-border animate-in fade-in duration-200"
        role="dialog"
        aria-modal="true"
        aria-labelledby="working-dir-modal-title"
      >
        <div className="bg-primary px-5 py-3.5 flex justify-between items-center text-white rounded-t-2xl">
          <h3 id="working-dir-modal-title" className="font-semibold text-base">
            {t('aiChat.workingDirModal.title')}
          </h3>
          <button
            type="button"
            onClick={onClose}
            disabled={saving}
            className="hover:text-white/80 disabled:opacity-50"
            aria-label={t('common.close')}
          >
            <X size={18} />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="p-5 space-y-4">
          <p className="text-sm text-textMuted leading-relaxed">
            {t('aiChat.workingDirModal.description')}
          </p>

          {folderNameHint && (
            <div className="rounded-lg bg-blue-50 dark:bg-blue-950/30 border border-blue-200 dark:border-blue-800 px-3 py-2 text-xs text-blue-800 dark:text-blue-200">
              {t('aiChat.workingDirModal.folderSelected', { name: folderNameHint })}
            </div>
          )}

          <div>
            <label className="block text-xs font-semibold text-textMuted uppercase mb-2">
              {t('aiChat.workingDirModal.pathLabel')}
            </label>
            <div className="flex gap-2">
              <input
                type="text"
                value={path}
                onChange={(e) => setPath(e.target.value)}
                placeholder={t('aiChat.workingDirModal.pathPlaceholder')}
                className="flex-1 px-3 py-2.5 bg-bgLight border border-border rounded-lg text-sm font-mono focus:outline-none focus:ring-2 focus:ring-primary/50"
                autoFocus
                disabled={saving}
              />
              {onBrowse && (
                <button
                  type="button"
                  onClick={onBrowse}
                  disabled={saving}
                  className="px-3 py-2.5 bg-bgLight border border-border rounded-lg hover:bg-panel transition-colors disabled:opacity-50"
                  title={t('aiChat.workingDirModal.browse')}
                >
                  <FolderOpen size={18} className="text-primary" />
                </button>
              )}
            </div>
          </div>

          <div className="flex gap-3 pt-1">
            <button
              type="button"
              onClick={onClose}
              disabled={saving}
              className="flex-1 px-4 py-2.5 bg-bgLight border border-border rounded-lg text-sm hover:bg-panel transition-colors disabled:opacity-50"
            >
              {t('common.cancel')}
            </button>
            <button
              type="submit"
              disabled={saving || !path.trim()}
              className="flex-1 px-4 py-2.5 bg-primary text-white rounded-lg text-sm hover:opacity-90 transition-colors disabled:opacity-50"
            >
              {saving ? t('aiChat.workingDirModal.saving') : t('common.confirm')}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};
