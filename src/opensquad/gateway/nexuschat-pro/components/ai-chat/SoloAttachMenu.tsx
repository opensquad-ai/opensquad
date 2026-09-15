/**
 * SoloAttachMenu — Cursor-style "+" button that expands attachment actions.
 * Includes a Skills flyout that lists available agent skills.
 */
import React, { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { BookOpen, Check, ChevronRight, Image as ImageIcon, Paperclip, Plus, Upload, Volume2 } from 'lucide-react';
import type { SkillInfo } from '../../services/api';
import { POPOVER_SURFACE_CLASS, usePopMenuMounted } from './popoverSurface';

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
  const [menuPos, setMenuPos] = useState<{ bottom: number; left: number } | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  /**
   * The menu opens upward (bottom-anchored), so the Skills row lands right
   * under the cursor that just clicked "+" — without this, the hover handler
   * immediately expands the L2 skill list. Suppress hover-expand briefly.
   */
  const suppressHoverRef = useRef(false);

  useEffect(() => {
    if (!open) return;
    suppressHoverRef.current = true;
    const t = window.setTimeout(() => {
      suppressHoverRef.current = false;
    }, 350);
    return () => window.clearTimeout(t);
  }, [open]);

  useLayoutEffect(() => {
    if (!open) return; // keep last position while the exit animation plays
    const sync = () => {
      const trigger = triggerRef.current;
      if (!trigger) return;
      const rect = trigger.getBoundingClientRect();
      setMenuPos({
        bottom: Math.max(8, window.innerHeight - rect.top + 8),
        left: Math.max(8, rect.left),
      });
    };
    sync();
    window.addEventListener('resize', sync);
    window.addEventListener('scroll', sync, true);
    return () => {
      window.removeEventListener('resize', sync);
      window.removeEventListener('scroll', sync, true);
    };
  }, [open]);

  useEffect(() => {
    if (!open) {
      setSkillsOpen(false);
      return;
    }
    const onDoc = (e: MouseEvent) => {
      const t = e.target as Node;
      if (rootRef.current?.contains(t)) return;
      if (menuRef.current?.contains(t)) return;
      setOpen(false);
      setSkillsOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setOpen(false);
        setSkillsOpen(false);
      }
    };
    document.addEventListener('mousedown', onDoc, true);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDoc, true);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  const run = (fn: () => void) => {
    setOpen(false);
    setSkillsOpen(false);
    fn();
  };

  // Stay mounted briefly after close so the pop-out animation can play.
  const menuMounted = usePopMenuMounted(open);
  const skillsMounted = usePopMenuMounted(skillsOpen);

  const attachItems = [
    { key: 'files', label: 'Upload files', icon: Paperclip, onClick: onUploadFiles },
    { key: 'folder', label: 'Upload folder', icon: Upload, onClick: onUploadFolder },
    { key: 'images', label: 'Upload images', icon: ImageIcon, onClick: onUploadImages },
  ] as const;

  const menu = menuMounted ? (
    <div
      ref={menuRef}
      className="fixed z-[220] flex items-end gap-1"
      style={menuPos ? { bottom: menuPos.bottom, left: menuPos.left } : { visibility: 'hidden' }}
      onMouseLeave={() => setSkillsOpen(false)}
    >
      {/* Flush rows (no py-1): hover/selected fill must reach the panel edges.
          The header row is the only non-interactive strip and keeps breathing room. */}
      <div
        className={`min-w-[184px] origin-bottom-left rounded-xl border border-border ${POPOVER_SURFACE_CLASS} overflow-hidden ${
          open ? 'os-pop-menu' : 'os-pop-menu-out'
        }`}
      >
        <div className="px-3 py-1 text-[11px] text-textMuted/70 truncate">
          Add agents, context, tools…
        </div>
        {attachItems.map((item) => {
          const Icon = item.icon;
          return (
            <button
              key={item.key}
              type="button"
              onClick={() => run(item.onClick)}
              className="w-full flex items-center gap-2 px-3 py-1.5 text-left text-[12px] text-textMain hover:bg-black/[0.06] dark:hover:bg-white/[0.10] transition-colors border-0 bg-transparent cursor-pointer"
            >
              <span className="w-4 shrink-0 flex items-center justify-center">
                <Icon size={14} className="text-textMuted" />
              </span>
              <span className="flex-1 min-w-0 truncate font-medium">{item.label}</span>
            </button>
          );
        })}
        {onToggleAutoSpeech && (
          <>
            <div className="my-0.5 h-px bg-border/60" />
            <button
              type="button"
              onClick={() => onToggleAutoSpeech(!autoSpeechEnabled)}
              className={`w-full flex items-center gap-2 px-3 py-1.5 text-left text-[12px] transition-colors border-0 cursor-pointer ${
                autoSpeechEnabled
                  ? 'bg-black/[0.06] dark:bg-white/[0.08] text-blue-600 dark:text-blue-400'
                  : 'bg-transparent text-textMain hover:bg-black/[0.06] dark:hover:bg-white/[0.10]'
              }`}
              title="Automatically speak each final agent reply"
            >
              <span className="w-4 shrink-0 flex items-center justify-center">
                <Volume2 size={14} className={autoSpeechEnabled ? 'text-blue-600 dark:text-blue-400' : 'text-textMuted'} />
              </span>
              <span className="flex-1 min-w-0 truncate font-medium">Auto speech</span>
              {autoSpeechEnabled ? <Check size={13} className="text-blue-600 dark:text-blue-400" /> : null}
            </button>
          </>
        )}
        <div className="my-0.5 h-px bg-border/60" />
        <button
          type="button"
          onMouseEnter={() => {
            if (suppressHoverRef.current) return;
            setSkillsOpen(true);
            onOpenSkills?.();
          }}
          onClick={() => {
            setSkillsOpen((v) => !v);
            onOpenSkills?.();
          }}
          className={`w-full flex items-center gap-2 px-3 py-1.5 text-left text-[12px] transition-colors border-0 cursor-pointer ${
            skillsOpen
              ? 'bg-black/[0.06] dark:bg-white/[0.08] text-textMain'
              : 'bg-transparent text-textMain hover:bg-black/[0.06] dark:hover:bg-white/[0.10]'
          }`}
        >
          <span className="w-4 shrink-0 flex items-center justify-center">
            <BookOpen size={14} className="text-textMuted" />
          </span>
          <span className="flex-1 min-w-0 truncate font-medium">Skills</span>
          <ChevronRight size={13} className="text-textMuted/50" />
        </button>
      </div>

      {skillsMounted && (
        <div
          className={`min-w-[240px] max-w-[320px] max-h-[320px] overflow-y-auto origin-left rounded-xl border border-border ${POPOVER_SURFACE_CLASS} ${
            skillsOpen ? 'os-pop-menu' : 'os-pop-menu-out'
          }`}
        >
          {skillsLoading && skills.length === 0 ? (
            <div className="px-3 py-4 text-[12px] text-textMuted text-center">Loading skills…</div>
          ) : skills.length === 0 ? (
            <div className="px-3 py-4 text-[12px] text-textMuted text-center">No skills installed</div>
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
                  className="w-full text-left px-3 py-1.5 hover:bg-black/[0.06] dark:hover:bg-white/[0.10] transition-colors border-0 bg-transparent cursor-pointer"
                  title={desc || title}
                >
                  <div className="text-[12px] font-medium text-textMain truncate">{title}</div>
                  {desc ? (
                    <div className="text-[10px] text-textMuted truncate mt-0.5">{desc}</div>
                  ) : null}
                </button>
              );
            })
          )}
        </div>
      )}
    </div>
  ) : null;

  return (
    <div ref={rootRef} className="relative shrink-0">
      <button
        ref={triggerRef}
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

      {typeof document !== 'undefined' && menu ? createPortal(menu, document.body) : null}
    </div>
  );
};
