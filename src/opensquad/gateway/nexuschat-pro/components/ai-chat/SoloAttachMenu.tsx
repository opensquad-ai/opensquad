/**
 * SoloAttachMenu — Cursor-style "+" button that expands attachment actions.
 * Includes a Skills flyout that lists available agent skills.
 */
import React, { useEffect, useRef, useState } from 'react';
import { BookOpen, Check, ChevronRight, Image as ImageIcon, Paperclip, Plus, Upload, Volume2 } from 'lucide-react';
import type { SkillInfo } from '../../services/api';

export interface SoloAttachMenuProps {
  disabled?: boolean;
  skills?: SkillInfo[];
  skillsLoading?: boolean;
  onUploadFiles: () => void;
  onUploadFolder: () => void;
  onUploadImages: () => void;
  onSelectSkill: (skill: SkillInfo) => void;
  onOpenSkills?: () => void;
  /** When true, agent final replies are spoken via TTS automatically. */
  autoSpeechEnabled?: boolean;
  onToggleAutoSpeech?: (enabled: boolean) => void;
}

export const SoloAttachMenu: React.FC<SoloAttachMenuProps> = ({
  disabled = false,
  skills = [],
  skillsLoading = false,
  onUploadFiles,
  onUploadFolder,
  onUploadImages,
  onSelectSkill,
  onOpenSkills,
  autoSpeechEnabled = false,
  onToggleAutoSpeech,
}) => {
  const [open, setOpen] = useState(false);
  const [skillsOpen, setSkillsOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) {
      setSkillsOpen(false);
      return;
    }
    const onDoc = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) {
        setOpen(false);
        setSkillsOpen(false);
      }
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setOpen(false);
        setSkillsOpen(false);
      }
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
    setSkillsOpen(false);
    fn();
  };

  const attachItems = [
    { key: 'files', label: 'Upload files', icon: Paperclip, onClick: onUploadFiles },
    { key: 'folder', label: 'Upload folder', icon: Upload, onClick: onUploadFolder },
    { key: 'images', label: 'Upload images', icon: ImageIcon, onClick: onUploadImages },
  ] as const;

  return (
    <div ref={rootRef} className="relative shrink-0">
      <button
        type="button"
        disabled={disabled}
        onClick={() => {
          const next = !open;
          setOpen(next);
          if (next) onOpenSkills?.();
        }}
        className={`w-7 h-7 rounded-full flex items-center justify-center transition-colors border-0 cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed ${
          open
            ? 'bg-primary/15 text-primary'
            : autoSpeechEnabled
              ? 'bg-primary/10 text-primary'
              : 'bg-black/[0.05] dark:bg-white/[0.08] text-textMuted hover:bg-primary/15 hover:text-textMain'
        }`}
        title={autoSpeechEnabled ? 'Attach (Auto speech on)' : 'Attach'}
      >
        <Plus
          size={16}
          className={`transition-transform duration-150 ${open ? 'rotate-45' : ''}`}
        />
      </button>

      {open && (
        <div className="absolute bottom-[calc(100%+8px)] left-0 z-50 flex items-end gap-1">
          <div className="min-w-[200px] rounded-xl border border-border bg-white dark:bg-[#2a2a2c] shadow-[0_8px_30px_rgba(0,0,0,0.12)] overflow-hidden py-1">
            <div className="px-3 py-1.5 text-[11px] text-textMuted/70 truncate">
              Add agents, context, tools…
            </div>
            {attachItems.map((item) => {
              const Icon = item.icon;
              return (
                <button
                  key={item.key}
                  type="button"
                  onClick={() => run(item.onClick)}
                  className="w-full flex items-center gap-2.5 px-3 py-2 text-left text-[13px] text-textMain hover:bg-primary/10 transition-colors border-0 bg-transparent cursor-pointer"
                >
                  <Icon size={15} className="text-textMuted" />
                  <span>{item.label}</span>
                </button>
              );
            })}
            {onToggleAutoSpeech && (
              <>
                <div className="my-1 h-px bg-border/60" />
                <button
                  type="button"
                  onClick={() => onToggleAutoSpeech(!autoSpeechEnabled)}
                  className={`w-full flex items-center gap-2.5 px-3 py-2 text-left text-[13px] transition-colors border-0 cursor-pointer ${
                    autoSpeechEnabled
                      ? 'bg-primary/10 text-primary'
                      : 'bg-transparent text-textMain hover:bg-primary/10'
                  }`}
                  title="Automatically speak each final agent reply"
                >
                  <Volume2 size={15} className={autoSpeechEnabled ? 'text-primary' : 'text-textMuted'} />
                  <span className="flex-1">Auto speech</span>
                  {autoSpeechEnabled ? <Check size={14} className="text-primary" /> : null}
                </button>
              </>
            )}
            <div className="my-1 h-px bg-border/60" />
            <button
              type="button"
              onMouseEnter={() => {
                setSkillsOpen(true);
                onOpenSkills?.();
              }}
              onClick={() => {
                setSkillsOpen((v) => !v);
                onOpenSkills?.();
              }}
              className={`w-full flex items-center gap-2.5 px-3 py-2 text-left text-[13px] transition-colors border-0 cursor-pointer ${
                skillsOpen
                  ? 'bg-black/[0.06] dark:bg-white/[0.08] text-textMain'
                  : 'bg-transparent text-textMain hover:bg-primary/10'
              }`}
            >
              <BookOpen size={15} className="text-textMuted" />
              <span className="flex-1">Skills</span>
              <ChevronRight size={14} className="text-textMuted" />
            </button>
          </div>

          {skillsOpen && (
            <div className="min-w-[260px] max-w-[320px] max-h-[320px] overflow-y-auto rounded-xl border border-border bg-white dark:bg-[#2a2a2c] shadow-[0_8px_30px_rgba(0,0,0,0.12)] py-1">
              {skillsLoading && skills.length === 0 ? (
                <div className="px-3 py-3 text-[12px] text-textMuted">Loading skills…</div>
              ) : skills.length === 0 ? (
                <div className="px-3 py-3 text-[12px] text-textMuted">No skills installed</div>
              ) : (
                skills.map((skill) => {
                  const id = skill.dir || skill.name;
                  const title = skill.display_name || skill.name || id;
                  const desc = (skill.description || '').trim();
                  return (
                    <button
                      key={id}
                      type="button"
                      onClick={() => run(() => onSelectSkill(skill))}
                      className="w-full text-left px-3 py-2 hover:bg-primary/10 transition-colors border-0 bg-transparent cursor-pointer"
                      title={desc || title}
                    >
                      <div className="text-[13px] font-medium text-textMain truncate">{title}</div>
                      {desc ? (
                        <div className="text-[11px] text-textMuted truncate mt-0.5">{desc}</div>
                      ) : null}
                    </button>
                  );
                })
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
};
