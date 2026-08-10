/**
 * WorkspaceTabBar — L1 workspace tabs + open/create menu (图二).
 */
import React, { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Check, Folder, FolderOpen, Plus, X } from 'lucide-react';
import type { Workspace } from '../../utils/workspaceStore';
import { workspaceDisplayName } from '../../utils/workspaceStore';
import { pickFolder } from '../../utils/cwdRecents';

interface WorkspaceTabBarProps {
  workspaces: Workspace[];
  openIds: string[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onRequestClose: (id: string) => void;
  onOpenExisting: (rootPath: string) => void;
  onCreateNew: () => void;
}

export const WorkspaceTabBar: React.FC<WorkspaceTabBarProps> = ({
  workspaces,
  openIds,
  activeId,
  onSelect,
  onRequestClose,
  onOpenExisting,
  onCreateNew,
}) => {
  const [menuOpen, setMenuOpen] = useState(false);
  const [menuPos, setMenuPos] = useState<{ top: number; left: number } | null>(null);
  const plusBtnRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const byId = new Map(workspaces.map((w) => [w.id, w]));
  const openList = openIds.map((id) => byId.get(id)).filter(Boolean) as Workspace[];

  // Prefer: currently open workspaces first, then the rest (closed / not in L1).
  const menuWorkspaces = (() => {
    const openSet = new Set(openIds);
    const open = openIds.map((id) => byId.get(id)).filter(Boolean) as Workspace[];
    const rest = workspaces.filter((w) => !openSet.has(w.id));
    return [...open, ...rest];
  })();

  const updateMenuPos = () => {
    const btn = plusBtnRef.current;
    if (!btn) return;
    const r = btn.getBoundingClientRect();
    const width = 320;
    let left = r.left;
    if (left + width > window.innerWidth - 8) {
      left = Math.max(8, window.innerWidth - width - 8);
    }
    setMenuPos({ top: r.bottom + 4, left });
  };

  useLayoutEffect(() => {
    if (!menuOpen) {
      setMenuPos(null);
      return;
    }
    updateMenuPos();
  }, [menuOpen]);

  useEffect(() => {
    if (!menuOpen) return;
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node;
      if (menuRef.current?.contains(t)) return;
      if (plusBtnRef.current?.contains(t)) return;
      setMenuOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setMenuOpen(false);
    };
    const onReposition = () => updateMenuPos();
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    window.addEventListener('resize', onReposition);
    window.addEventListener('scroll', onReposition, true);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
      window.removeEventListener('resize', onReposition);
      window.removeEventListener('scroll', onReposition, true);
    };
  }, [menuOpen]);

  const handleOpenFolder = async () => {
    setMenuOpen(false);
    const result = await pickFolder(null);
    if (result.cancelled || !result.path) return;
    onOpenExisting(result.path);
  };

  const menu =
    menuOpen && menuPos
      ? createPortal(
          <div
            ref={menuRef}
            role="menu"
            className="fixed z-[9999] min-w-[280px] max-w-[360px] w-[320px] py-1 rounded-lg bg-bgLight border border-border shadow-xl text-[12px]"
            style={{ top: menuPos.top, left: menuPos.left }}
          >
            {menuWorkspaces.length > 0 ? (
              <div className="py-1 max-h-56 overflow-y-auto border-b border-border/60">
                {menuWorkspaces.map((ws) => {
                  const isActive = ws.id === activeId;
                  const isOpen = openIds.includes(ws.id);
                  return (
                    <button
                      key={ws.id}
                      type="button"
                      role="menuitem"
                      className={`w-full flex items-start gap-2 px-3 py-1.5 text-left hover:bg-primary/10 ${
                        isActive ? 'bg-black/[0.03] dark:bg-white/[0.04]' : ''
                      }`}
                      onClick={() => {
                        setMenuOpen(false);
                        onSelect(ws.id);
                      }}
                    >
                      {isOpen ? (
                        <FolderOpen size={13} className="text-amber-500 mt-0.5 shrink-0" />
                      ) : (
                        <Folder size={13} className="text-amber-500 mt-0.5 shrink-0" />
                      )}
                      <div className="min-w-0 flex-1">
                        <div className="font-medium text-textMain truncate">
                          {workspaceDisplayName(ws)}
                        </div>
                        <div className="text-[10px] text-textMuted truncate">{ws.rootPath}</div>
                      </div>
                      {isActive ? (
                        <Check size={14} className="text-emerald-500 shrink-0 mt-0.5" />
                      ) : (
                        <span className="w-3.5 h-3.5 rounded-full border border-border/80 shrink-0 mt-1" />
                      )}
                    </button>
                  );
                })}
              </div>
            ) : null}
            <button
              type="button"
              role="menuitem"
              className="w-full flex items-center gap-2 px-3 py-2 text-left hover:bg-primary/10"
              onClick={() => void handleOpenFolder()}
            >
              <Folder size={13} className="text-sky-500" />
              打开现有文件夹
            </button>
            <button
              type="button"
              role="menuitem"
              className="w-full flex items-center gap-2 px-3 py-2 text-left hover:bg-primary/10"
              onClick={() => {
                setMenuOpen(false);
                onCreateNew();
              }}
            >
              <Plus size={13} className="text-emerald-500" />
              创建新工作区
            </button>
          </div>,
          document.body,
        )
      : null;

  return (
    <div className="flex items-stretch gap-0.5 min-w-0 flex-1 h-full overflow-visible">
      <div className="os-ws-tab-scroll relative flex items-end gap-0.5 min-w-0 flex-1 overflow-x-auto scrollbar-thin">
        {openList.map((ws) => {
          const active = ws.id === activeId;
          return (
            <div
              key={ws.id}
              className={`group relative flex items-center gap-1.5 max-w-[180px] px-2.5 h-8 text-[12px] leading-none cursor-pointer shrink-0 ${
                active
                  ? 'os-ws-tab-active text-textMain z-[2]'
                  : 'os-ws-tab-idle text-textMuted hover:text-textMain/80'
              }`}
              onClick={() => onSelect(ws.id)}
              title={ws.rootPath}
            >
              <Folder
                size={13}
                className={`shrink-0 ${active ? 'text-amber-500' : 'text-textMuted/70'}`}
              />
              <span className="os-ws-tab-label truncate font-medium">
                {workspaceDisplayName(ws)}
              </span>
              <button
                type="button"
                className={`p-0.5 rounded hover:bg-primary/15 shrink-0 ${
                  active ? 'opacity-0 group-hover:opacity-100' : 'opacity-0 group-hover:opacity-70'
                }`}
                title="关闭工作区"
                onClick={(e) => {
                  e.stopPropagation();
                  onRequestClose(ws.id);
                }}
              >
                <X size={12} />
              </button>
            </div>
          );
        })}
      </div>
      <div className="relative shrink-0 self-end flex h-8 items-center">
        <button
          ref={plusBtnRef}
          type="button"
          onClick={() => setMenuOpen((v) => !v)}
          className={`p-1.5 rounded-md text-textMuted hover:bg-primary/10 ${
            menuOpen ? 'bg-black/[0.06] dark:bg-white/10' : ''
          }`}
          title="打开或创建工作区"
          aria-haspopup="menu"
          aria-expanded={menuOpen}
        >
          <Plus size={14} />
        </button>
        {menu}
      </div>
    </div>
  );
};
