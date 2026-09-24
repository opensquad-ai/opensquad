/**
 * Action menu for a text selection: 复制文本 / 添加到会话（会话文本）or 添加到上下文（文件）.
 *
 * Quotable surfaces mark themselves with `data-quote-source`, so the menu never
 * has to know which component it is looking at:
 *
 *   `chat` — the scrolling timeline (`.os-chat-scroll`, set by `ChatTimeline`);
 *   `file` — the workspace file viewer's text (`WorkspaceFileEditor`), which
 *            also carries `data-quote-path` so the quote can name its source.
 *
 * It opens on a **right click** over selected text, at the pointer — a drag that
 * merely marks a passage to read must not cover it with a menu. Chat and the
 * workspace file viewer both work this way; the file viewer's own 复制/剪切/粘贴
 * menu gives way to this one *for a selection*, because 复制文本 is offered here
 * and the keyboard shortcuts keep cutting/pasting available. With nothing
 * selected — a plain right click, or a selection that started outside a
 * quotable surface — the browser's menu is untouched.
 *
 * Mounted once per page: the target pane is read off the DOM (`[data-pane-id]`,
 * set by `WorkspacePaneShell`) instead of being threaded through the pane-shell
 * handlers, so every host (live chat slot, SessionChatPane, file tabs) works
 * without any of them knowing this exists.
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Check, Copy, MessageSquarePlus } from 'lucide-react';
import { writeClipboard } from '../../utils/clipboard';

export type SelectionQuoteMenuProps = {
  /**
   * Attach the selected passage to a pane's composer. `paneId` is null when the
   * selection sits outside a pane shell (no composer to attach to); `label`
   * names the source when it has one (e.g. `bin/opensquad.js:12-25`).
   */
  onAddQuote: (paneId: string | null, text: string, label?: string) => void;
};

type QuoteKind = 'chat' | 'file';

type MenuState = {
  x: number;
  y: number;
  text: string;
  label?: string;
  kind: QuoteKind;
  paneId: string | null;
};

const MENU_W = 176;
const MENU_H = 84;
/** Keeps the menu off the cursor, so opening it cannot hover an item by accident. */
const MENU_GAP = 4;

/** A container whose text may be quoted (see the component doc above). */
const SOURCE_SELECTOR = '[data-quote-source]';

type Hit = {
  kind: QuoteKind;
  text: string;
  label?: string;
  paneId: string | null;
};

/** 1-based, inclusive line numbers touched by `[start, end)` in `value`. */
export function selectionLines(value: string, start: number, end: number): [number, number] {
  const from = value.slice(0, Math.max(0, start)).split('\n').length;
  const to = value.slice(0, Math.max(0, end)).split('\n').length;
  return [from, to];
}

const sourceKind = (el: Element): QuoteKind =>
  el.getAttribute('data-quote-source') === 'file' ? 'file' : 'chat';

const sourcePaneId = (el: Element): string | null =>
  (el.closest('[data-pane-id]') as HTMLElement | null)?.dataset.paneId ?? null;

/** `path` (or `path:12` / `path:12-25`) when the surface names its file. */
function sourceLabel(el: Element, lines?: [number, number]): string | undefined {
  const path = (el as HTMLElement).dataset.quotePath?.trim();
  if (!path) return undefined;
  if (!lines) return path;
  const [from, to] = lines;
  return `${path}:${from}${to > from ? `-${to}` : ''}`;
}

/**
 * The selected text on a quotable surface, or null when there is nothing to act
 * on. The single place a selection is judged, so "is there something to act on"
 * and "should the native menu step aside" can never disagree.
 */
function readSelection(target: Element | null): Hit | null {
  if (!target) return null;

  // The source editor paints its text in a highlight layer and keeps the real
  // selection in a transparent <textarea> — `window.getSelection()` is empty
  // there, so the textarea owns the range.
  const area = target.closest('textarea');
  const areaSource = area?.closest(SOURCE_SELECTOR);
  if (area && areaSource) {
    const { selectionStart, selectionEnd, value } = area as HTMLTextAreaElement;
    if (selectionStart == null || selectionEnd == null || selectionStart === selectionEnd) return null;
    const text = value.slice(selectionStart, selectionEnd).trim();
    if (!text) return null;
    return {
      kind: sourceKind(areaSource),
      text,
      label: sourceLabel(areaSource, selectionLines(value, selectionStart, selectionEnd)),
      paneId: sourcePaneId(areaSource),
    };
  }

  const source = target.closest(SOURCE_SELECTOR);
  if (!source) return null;
  const sel = window.getSelection();
  if (!sel || sel.isCollapsed || sel.rangeCount === 0) return null;
  const text = sel.toString().trim();
  if (!text) return null;
  const range = sel.getRangeAt(0);
  // A selection that started outside the surface has a common ancestor above
  // it — quoting half a message plus half a sidebar is never wanted.
  if (!source.contains(range.commonAncestorContainer)) return null;
  return {
    kind: sourceKind(source),
    text,
    label: sourceLabel(source),
    paneId: sourcePaneId(source),
  };
}

export const SelectionQuoteMenu: React.FC<SelectionQuoteMenuProps> = ({ onAddQuote }) => {
  const { t } = useTranslation();
  const [menu, setMenu] = useState<MenuState | null>(null);
  const [copied, setCopied] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const show = (x: number, y: number, hit: Hit) => {
      setCopied(false);
      setMenu({ x, y, text: hit.text, label: hit.label, kind: hit.kind, paneId: hit.paneId });
    };

    // Right click is the only trigger. A left-button selection used to open this
    // on its own (drag, double click, whatever produced it), which covered the
    // passage the reader was merely marking — acting on a selection is now the
    // gesture that has to say so. Nothing is suppressed unless a quotable
    // selection is really there, so a plain right click (and one outside a
    // quotable surface) still gets the browser's own menu.
    const onContextMenu = (e: MouseEvent) => {
      const hit = readSelection(e.target as Element | null);
      if (!hit) return;
      e.preventDefault();
      show(e.clientX + MENU_GAP, e.clientY + MENU_GAP, hit);
    };

    document.addEventListener('contextmenu', onContextMenu);
    return () => {
      document.removeEventListener('contextmenu', onContextMenu);
    };
  }, []);

  useEffect(() => {
    if (!menu) return;
    const close = () => setMenu(null);
    const onPointerDown = (e: MouseEvent) => {
      if (!menuRef.current?.contains(e.target as Node)) close();
    };
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') close();
    };
    // CAPTURE, not bubble: the composer stops mousedown propagation at its root
    // (`e.stopPropagation()` for pane focus), which also stops the native event
    // before a document-level bubble listener sees it — clicking into the
    // composer would leave this menu floating over the conversation.
    document.addEventListener('mousedown', onPointerDown, true);
    document.addEventListener('keydown', onKeyDown);
    // Any scroll/resize moves the text out from under a fixed menu.
    window.addEventListener('scroll', close, true);
    window.addEventListener('resize', close);
    return () => {
      document.removeEventListener('mousedown', onPointerDown, true);
      document.removeEventListener('keydown', onKeyDown);
      window.removeEventListener('scroll', close, true);
      window.removeEventListener('resize', close);
    };
  }, [menu]);

  const handleCopy = useCallback(async () => {
    if (!menu) return;
    const ok = await writeClipboard(menu.text);
    if (!ok) {
      setMenu(null);
      return;
    }
    setCopied(true);
    window.setTimeout(() => setMenu(null), 550);
  }, [menu]);

  const handleAdd = useCallback(() => {
    if (!menu) return;
    onAddQuote(menu.paneId, menu.text, menu.label);
    setMenu(null);
  }, [menu, onAddQuote]);

  if (!menu) return null;

  // The handlers already offset from the pointer / selection; this only keeps
  // the menu inside the viewport.
  const left = Math.max(8, Math.min(menu.x, window.innerWidth - MENU_W - 8));
  const top = Math.max(8, Math.min(menu.y, window.innerHeight - MENU_H - 8));
  const addLabel =
    menu.kind === 'file'
      ? t('aiChat.addToContext', { defaultValue: '添加到上下文' })
      : t('aiChat.addToChat', { defaultValue: '添加到会话' });

  return (
    <div
      ref={menuRef}
      role="menu"
      className="fixed z-[60] w-44 bg-panel rounded-lg shadow-xl border border-border py-1 animate-in fade-in zoom-in-95 duration-100"
      style={{ top, left }}
      onContextMenu={(e) => e.preventDefault()}
    >
      <button
        type="button"
        role="menuitem"
        onClick={handleCopy}
        className="w-full text-left px-4 py-2 text-sm text-textMain hover:bg-bgLight flex items-center gap-2 cursor-pointer"
      >
        {copied ? <Check size={14} className="text-green-500" /> : <Copy size={14} />}
        <span>{copied ? t('aiChat.selectionCopied', { defaultValue: '已复制' }) : t('aiChat.copyText', { defaultValue: '复制文本' })}</span>
      </button>
      <button
        type="button"
        role="menuitem"
        disabled={!menu.paneId}
        onClick={handleAdd}
        className="w-full text-left px-4 py-2 text-sm text-textMain hover:bg-bgLight flex items-center gap-2 cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
      >
        <MessageSquarePlus size={14} />
        <span>{addLabel}</span>
      </button>
    </div>
  );
};
