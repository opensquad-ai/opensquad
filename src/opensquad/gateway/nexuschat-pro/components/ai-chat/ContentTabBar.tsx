/**
 * ContentTabBar — L2 session + file tabs + split / close-all actions.
 * Many tabs shrink equally (browser-style) instead of horizontal scrolling.
 * Tabs support smooth drag-and-drop reordering (does not trigger file-upload overlay).
 */
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Clock,
  Columns2,
  FileCode2,
  FileText,
  MoreHorizontal,
  MessageSquare,
  Plus,
  Rows2,
  X,
} from 'lucide-react';
import type { ContentTab } from '../../utils/workspaceStore';
import { contentTabKey } from '../../utils/workspaceStore';

export const OPENSQUAD_TAB_MIME = 'application/x-opensquad-tab';

export type ContentTabLabel = {
  tab: ContentTab;
  title: string;
  dirty?: boolean;
};

interface ContentTabBarProps {
  tabs: ContentTabLabel[];
  activeKey: string | null;
  onSelect: (tab: ContentTab) => void;
  onClose: (tab: ContentTab) => void;
  onNewSession: () => void;
  /** Reorder: move `from` tab to the position of `to`. */
  onReorder?: (from: ContentTab, to: ContentTab) => void;
  /** Left-right split */
  onSplitRow?: () => void;
  /** Up-down split */
  onSplitCol?: () => void;
  canSplit?: boolean;
  onCloseAll?: () => void;
  onClosePane?: () => void;
  canClosePane?: boolean;
}

function TabIcon({ kind }: { kind: ContentTab['kind'] }) {
  if (kind === 'session') return <MessageSquare size={12} className="text-sky-500 shrink-0" />;
  if (kind === 'scheduled-tasks') return <Clock size={12} className="text-violet-500 shrink-0" />;
  return <FileCode2 size={12} className="text-amber-500 shrink-0" />;
}

function moveKey(keys: string[], fromKey: string, toKey: string): string[] {
  if (fromKey === toKey) return keys;
  const fromIdx = keys.indexOf(fromKey);
  const toIdx = keys.indexOf(toKey);
  if (fromIdx < 0 || toIdx < 0) return keys;
  const next = [...keys];
  const [moved] = next.splice(fromIdx, 1);
  next.splice(toIdx, 0, moved);
  return next;
}

export const ContentTabBar: React.FC<ContentTabBarProps> = ({
  tabs,
  activeKey,
  onSelect,
  onClose,
  onNewSession,
  onReorder,
  onSplitRow,
  onSplitCol,
  canSplit = true,
  onCloseAll,
  onClosePane,
  canClosePane = false,
}) => {
  const { t } = useTranslation();
  const [menuOpen, setMenuOpen] = useState(false);
  const [dragKey, setDragKey] = useState<string | null>(null);
  const [overKey, setOverKey] = useState<string | null>(null);
  /** Live preview order while dragging (smooth, not jump-on-drop only). */
  const [previewKeys, setPreviewKeys] = useState<string[] | null>(null);
  const dragKeyRef = useRef<string | null>(null);
  const didDragRef = useRef(false);
  const menuRef = useRef<HTMLDivElement>(null);
  const rowRef = useRef<HTMLDivElement>(null);
  const tabElsRef = useRef<Map<string, HTMLDivElement>>(new Map());
  const prevRectsRef = useRef<Map<string, DOMRect>>(new Map());

  const propKeys = useMemo(() => tabs.map((t) => contentTabKey(t.tab)), [tabs]);
  const byKey = useMemo(() => {
    const m = new Map<string, ContentTabLabel>();
    for (const item of tabs) m.set(contentTabKey(item.tab), item);
    return m;
  }, [tabs]);

  const displayKeys = previewKeys || propKeys;

  // FLIP: animate tabs sliding into their new slots while dragging.
  useEffect(() => {
    const row = rowRef.current;
    if (!row) return;
    const nextRects = new Map<string, DOMRect>();
    for (const key of displayKeys) {
      const el = tabElsRef.current.get(key);
      if (el) nextRects.set(key, el.getBoundingClientRect());
    }
    const prev = prevRectsRef.current;
    if (prev.size > 0 && dragKeyRef.current) {
      for (const [key, nextRect] of nextRects) {
        const prevRect = prev.get(key);
        const el = tabElsRef.current.get(key);
        if (!prevRect || !el || key === dragKeyRef.current) continue;
        const dx = prevRect.left - nextRect.left;
        if (Math.abs(dx) < 1) continue;
        el.style.transition = 'none';
        el.style.transform = `translateX(${dx}px)`;
        // Force reflow then ease back to 0
        void el.offsetWidth;
        el.style.transition = 'transform 200ms cubic-bezier(0.2, 0.8, 0.2, 1)';
        el.style.transform = 'translateX(0)';
      }
    }
    prevRectsRef.current = nextRects;
  }, [displayKeys.join('|')]);

  useEffect(() => {
    // External order changed while not dragging — drop preview.
    if (!dragKeyRef.current) setPreviewKeys(null);
  }, [propKeys.join('|')]);

  useEffect(() => {
    if (!menuOpen) return;
    const onDown = (e: MouseEvent) => {
      if (menuRef.current?.contains(e.target as Node)) return;
      setMenuOpen(false);
    };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [menuOpen]);

  const clearDrag = () => {
    dragKeyRef.current = null;
    setDragKey(null);
    setOverKey(null);
    setPreviewKeys(null);
    for (const el of tabElsRef.current.values()) {
      el.style.transition = '';
      el.style.transform = '';
    }
  };

  /** Keep tab DnD from bubbling into the page-level file-upload overlay. */
  const stopPageDrag = (e: React.DragEvent) => {
    e.stopPropagation();
  };

  return (
    <div
      className="flex items-center gap-0.5 min-w-0 flex-1 h-9 px-1"
      onDragEnter={stopPageDrag}
      onDragOver={stopPageDrag}
      onDrop={stopPageDrag}
    >
      <div ref={rowRef} className="flex items-center gap-0.5 min-w-0 flex-1 overflow-hidden">
        {displayKeys.map((key) => {
          const item = byKey.get(key);
          if (!item) return null;
          const { tab, title, dirty } = item;
          const active = key === activeKey;
          const isDragging = dragKey === key;
          const isOver = overKey === key && dragKey !== null && dragKey !== key;
          return (
            <div
              key={key}
              ref={(el) => {
                if (el) tabElsRef.current.set(key, el);
                else tabElsRef.current.delete(key);
              }}
              draggable={!!onReorder}
              onDragStart={(e) => {
                stopPageDrag(e);
                if (!onReorder) return;
                if ((e.target as HTMLElement).closest('button')) {
                  e.preventDefault();
                  return;
                }
                didDragRef.current = false;
                dragKeyRef.current = key;
                setDragKey(key);
                setPreviewKeys([...propKeys]);
                e.dataTransfer.effectAllowed = 'move';
                // Custom MIME first so page upload handlers can ignore this drag.
                try {
                  e.dataTransfer.setData(OPENSQUAD_TAB_MIME, key);
                } catch {
                  /* ignore */
                }
                e.dataTransfer.setData('text/plain', key);
                // Empty drag image feels smoother than a huge OS ghost for tabs
                try {
                  const ghost = document.createElement('div');
                  ghost.style.cssText =
                    'position:fixed;top:-1000px;left:-1000px;padding:4px 10px;border-radius:6px;' +
                    'background:rgba(0,0,0,0.08);font-size:11px;pointer-events:none;';
                  ghost.textContent = title;
                  document.body.appendChild(ghost);
                  e.dataTransfer.setDragImage(ghost, 16, 12);
                  window.setTimeout(() => ghost.remove(), 0);
                } catch {
                  /* ignore */
                }
              }}
              onDragEnd={(e) => {
                stopPageDrag(e);
                clearDrag();
                window.setTimeout(() => {
                  didDragRef.current = false;
                }, 0);
              }}
              onDragEnter={(e) => {
                stopPageDrag(e);
                if (!onReorder || !dragKeyRef.current) return;
                e.preventDefault();
              }}
              onDragOver={(e) => {
                stopPageDrag(e);
                if (!onReorder || !dragKeyRef.current) return;
                e.preventDefault();
                e.dataTransfer.dropEffect = 'move';
                const from = dragKeyRef.current;
                if (from === key) return;
                setOverKey(key);
                setPreviewKeys((prev) => {
                  const base = prev || propKeys;
                  const next = moveKey(base, from, key);
                  return next.join('|') === base.join('|') ? prev : next;
                });
              }}
              onDragLeave={(e) => {
                stopPageDrag(e);
                if (overKey === key) setOverKey(null);
              }}
              onDrop={(e) => {
                stopPageDrag(e);
                e.preventDefault();
                const fromKey =
                  dragKeyRef.current ||
                  e.dataTransfer.getData(OPENSQUAD_TAB_MIME) ||
                  e.dataTransfer.getData('text/plain');
                const ordered = previewKeys || propKeys;
                clearDrag();
                if (!onReorder || !fromKey) return;
                const fromTab = byKey.get(fromKey)?.tab;
                if (!fromTab) return;
                // Find a single move that reproduces the live preview order.
                let toKey = key;
                for (const cand of ordered) {
                  if (cand === fromKey) continue;
                  if (moveKey(propKeys, fromKey, cand).join('|') === ordered.join('|')) {
                    toKey = cand;
                    break;
                  }
                }
                if (fromKey === toKey) return;
                const toTab = byKey.get(toKey)?.tab;
                if (!toTab) return;
                didDragRef.current = true;
                onReorder(fromTab, toTab);
              }}
              className={`group flex min-w-0 flex-1 items-center gap-1 max-w-[180px] px-1.5 sm:px-2 h-7 rounded-lg text-[11px] select-none will-change-transform cursor-default ${
                active
                  ? 'bg-black/[0.08] dark:bg-white/15 text-textMain'
                  : 'text-textMuted hover:bg-primary/10'
              } ${isDragging ? 'opacity-45 scale-[0.97] shadow-sm z-10' : 'opacity-100 scale-100'} ${
                isOver ? 'ring-1 ring-primary/40 bg-primary/10' : ''
              }`}
              style={{
                transition: isDragging
                  ? 'opacity 150ms ease, transform 150ms ease, box-shadow 150ms ease'
                  : 'opacity 150ms ease, box-shadow 150ms ease, background-color 150ms ease',
              }}
              onClick={() => {
                if (didDragRef.current) return;
                onSelect(tab);
              }}
              title={title}
            >
              <TabIcon kind={tab.kind} />
              {tab.kind === 'file' && title.toLowerCase().endsWith('.md') ? (
                <FileText size={11} className="text-blue-500 shrink-0" />
              ) : null}
              <span className="min-w-0 flex-1 truncate pointer-events-none">{title}</span>
              {dirty ? <span className="w-1.5 h-1.5 rounded-full bg-amber-500 shrink-0" /> : null}
              <button
                type="button"
                draggable={false}
                className="opacity-0 group-hover:opacity-100 p-0.5 rounded hover:bg-primary/15 transition-opacity shrink-0"
                title={t('common.close')}
                onClick={(e) => {
                  e.stopPropagation();
                  onClose(tab);
                }}
                onMouseDown={(e) => e.stopPropagation()}
              >
                <X size={11} />
              </button>
            </div>
          );
        })}
      </div>
      <button
        type="button"
        onClick={onNewSession}
        className="p-1 rounded-md text-textMuted hover:bg-primary/10 shrink-0"
        title={t('aiChat.newChat')}
      >
        <Plus size={14} />
      </button>

      <div className="flex items-center gap-0.5 shrink-0 pl-1 border-l border-border/50">
        <button
          type="button"
          disabled={!canSplit || !onSplitRow}
          onMouseDown={(e) => e.stopPropagation()}
          onClick={(e) => {
            e.preventDefault();
            e.stopPropagation();
            onSplitRow?.();
          }}
          className="p-1 rounded-md text-textMuted hover:bg-primary/10 disabled:opacity-30"
          title={t('aiChat.splitLeftRight')}
        >
          <Columns2 size={14} />
        </button>
        <button
          type="button"
          disabled={!canSplit || !onSplitCol}
          onMouseDown={(e) => e.stopPropagation()}
          onClick={(e) => {
            e.preventDefault();
            e.stopPropagation();
            onSplitCol?.();
          }}
          className="p-1 rounded-md text-textMuted hover:bg-primary/10 disabled:opacity-30"
          title={t('aiChat.splitTopBottom')}
        >
          <Rows2 size={14} />
        </button>
        <div className="relative" ref={menuRef}>
          <button
            type="button"
            onMouseDown={(e) => e.stopPropagation()}
            onClick={(e) => {
              e.stopPropagation();
              setMenuOpen((v) => !v);
            }}
            className={`p-1 rounded-md text-textMuted hover:bg-primary/10 ${
              menuOpen ? 'bg-black/[0.06] dark:bg-white/10' : ''
            }`}
            title={t('aiChat.more')}
          >
            <MoreHorizontal size={14} />
          </button>
          {menuOpen ? (
            <div className="absolute right-0 top-full mt-1 z-[90] min-w-[140px] py-1 rounded-lg bg-bgLight border border-border shadow-xl text-[12px]">
              <button
                type="button"
                className="w-full px-3 py-1.5 text-left hover:bg-primary/10"
                onClick={() => {
                  setMenuOpen(false);
                  onCloseAll?.();
                }}
              >
                {t('aiChat.closeAllTabs')}
              </button>
              {canClosePane ? (
                <button
                  type="button"
                  className="w-full px-3 py-1.5 text-left hover:bg-primary/10"
                  onClick={() => {
                    setMenuOpen(false);
                    onClosePane?.();
                  }}
                >
                  {t('aiChat.closePane')}
                </button>
              ) : null}
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
};
