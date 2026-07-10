/**
 * SoloAttachMenu — Cursor-style "+" button that expands attachment actions.
 */
import React, { useEffect, useRef, useState } from 'react';
import { FolderOpen, Image as ImageIcon, Paperclip, Plus, Upload } from 'lucide-react';

export interface SoloAttachMenuProps {
  disabled?: boolean;
  cwdActive?: boolean;
  onUploadFiles: () => void;
  onUploadFolder: () => void;
  onUploadImages: () => void;
  onSetWorkingDir: () => void;
}

export const SoloAttachMenu: React.FC<SoloAttachMenuProps> = ({
  disabled = false,
  cwdActive = false,
  onUploadFiles,
  onUploadFolder,
  onUploadImages,
  onSetWorkingDir,
}) => {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', onDoc);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDoc);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  const run = (fn: () => void) => {
    setOpen(false);
    fn();
  };

  const items = [
    { key: 'files', label: 'Upload files', icon: Paperclip, onClick: onUploadFiles },
    { key: 'folder', label: 'Upload folder', icon: Upload, onClick: onUploadFolder },
    { key: 'images', label: 'Upload images', icon: ImageIcon, onClick: onUploadImages },
    {
      key: 'cwd',
      label: cwdActive ? 'Working directory' : 'Set working directory',
      icon: FolderOpen,
      onClick: onSetWorkingDir,
      active: cwdActive,
    },
  ] as const;

  return (
    <div ref={rootRef} className="relative shrink-0">
      <button
        type="button"
        disabled={disabled}
        onClick={() => setOpen((v) => !v)}
        className={`w-7 h-7 rounded-full flex items-center justify-center transition-colors border-0 cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed ${
          open
            ? 'bg-primary/15 text-primary'
            : 'bg-black/[0.05] dark:bg-white/[0.08] text-textMuted hover:bg-black/[0.08] dark:hover:bg-white/[0.12] hover:text-textMain'
        }`}
        title="Attach"
      >
        <Plus
          size={16}
          className={`transition-transform duration-150 ${open ? 'rotate-45' : ''}`}
        />
      </button>

      {open && (
        <div className="absolute bottom-[calc(100%+8px)] left-0 z-50 min-w-[200px] rounded-xl border border-border bg-white dark:bg-[#2a2a2c] shadow-[0_8px_30px_rgba(0,0,0,0.12)] overflow-hidden py-1">
          {items.map((item) => {
            const Icon = item.icon;
            const active = 'active' in item && item.active;
            return (
              <button
                key={item.key}
                type="button"
                onClick={() => run(item.onClick)}
                className="w-full flex items-center gap-2.5 px-3 py-2 text-left text-[13px] text-textMain hover:bg-black/[0.04] dark:hover:bg-white/[0.06] transition-colors border-0 bg-transparent cursor-pointer"
              >
                <Icon size={15} className={active ? 'text-primary' : 'text-textMuted'} />
                <span className={active ? 'text-primary font-medium' : ''}>{item.label}</span>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
};
