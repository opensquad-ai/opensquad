/**
 * Adds a copy button to every markdown table in a rendered body.
 *
 * Why this is built here instead of emitted by the renderer
 * --------------------------------------------------------
 * `renderFencedMarkdown` output is sanitized by `utils/safeHtml.ts`, whose
 * allowlist deliberately excludes `button` and `svg` (and every `on*`) because
 * that allowlist is the XSS boundary for agent-authored text. Widening it so
 * markdown could carry a UI button would put the boundary in the wrong place —
 * so the button is created here, from trusted constants, onto the DOM node the
 * sanitized html was injected into. The sanitized string is left untouched.
 *
 * What lands in the clipboard is TSV (tab separated, header row first): the one
 * plain-text shape that pastes into a spreadsheet as cells *and* reads as a
 * table in a chat box.
 */
import { useEffect, type RefObject } from 'react';
import i18n from 'i18next';
import { writeClipboard } from '../utils/clipboard';

/**
 * Serialize a rendered table to TSV. Cell text is whitespace-collapsed — a
 * cell containing a newline (a wrapped 依据原句) would otherwise split the row
 * into two lines and shift every following column.
 */
export function tableToTsv(table: HTMLTableElement): string {
  return Array.from(table.rows)
    .map((row) =>
      Array.from(row.cells)
        .map((cell) => (cell.textContent || '').replace(/\s+/g, ' ').trim())
        .join('\t'),
    )
    .join('\n');
}

export const COPY_TABLE_CLASS = 'ai-table-copy';
export const COPY_READY_CLASS = 'is-copy-ready';

/** Decorate every not-yet-decorated table under `root`. Returns how many. */
export function decorateMarkdownTables(root: HTMLElement): number {
  const wraps = Array.from(root.querySelectorAll<HTMLElement>('.ai-table-wrap'));
  if (wraps.length === 0) return 0;

  let added = 0;
  for (const wrap of wraps) {
    if (wrap.classList.contains(COPY_READY_CLASS)) continue;
    const table = wrap.querySelector('table');
    if (!table) continue;

    // The button must stay put when the table is wider than the column, and an
    // absolutely positioned child of a horizontally scrolling box scrolls away
    // with the content — so the scroller moves in one level and the wrapper
    // becomes the (unclipped) positioned parent. Unclipped because the button
    // sits ABOVE the table's top border, in the band the wrapper's own margin
    // reserves; the scroller, not the wrapper, clips the table's corners.
    if (!wrap.querySelector(':scope > .ai-table-scroll')) {
      const scroller = document.createElement('div');
      scroller.className = 'ai-table-scroll';
      table.replaceWith(scroller);
      scroller.appendChild(table);
    }

    const label = i18n.t('aiChat.copyTable', { defaultValue: '复制表格' });
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = COPY_TABLE_CLASS;
    btn.title = label;
    btn.setAttribute('aria-label', label);
    btn.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      void (async () => {
        const ok = await writeClipboard(tableToTsv(table));
        if (!ok) return;
        const copied = i18n.t('aiChat.tableCopied', { defaultValue: '已复制表格' });
        btn.classList.add('is-copied');
        btn.title = copied;
        btn.setAttribute('aria-label', copied);
        window.setTimeout(() => {
          btn.classList.remove('is-copied');
          btn.title = label;
          btn.setAttribute('aria-label', label);
        }, 1200);
      })();
    });

    wrap.classList.add(COPY_READY_CLASS);
    wrap.appendChild(btn);
    added += 1;
  }
  return added;
}

/**
 * Attach to the same container the markdown html is injected into, with that
 * html as the dependency: React replaces the container's children on every
 * re-render, so the decoration is re-applied rather than tracked.
 */
export function useTableCopyButtons(
  ref: RefObject<HTMLElement | null>,
  html: string,
): void {
  useEffect(() => {
    const root = ref.current;
    if (!root || !html) return;
    // One frame, so dangerouslySetInnerHTML has committed (same contract as
    // useMermaidHydration, which decorates the same nodes).
    const t = window.setTimeout(() => decorateMarkdownTables(root), 0);
    return () => window.clearTimeout(t);
  }, [ref, html]);
}
