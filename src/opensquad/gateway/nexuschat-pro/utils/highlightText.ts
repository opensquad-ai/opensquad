/**
 * Search-term highlighter for message previews.
 *
 * Extracted from `RightPanel` (2026-09-13) for two reasons:
 *  1. It builds HTML from raw message content and the result is injected via
 *     `dangerouslySetInnerHTML`. Inline in a component it was untestable, so
 *     nothing noticed it never escaped `text` — only the search *query* was
 *     regex-escaped. A message containing `<img onerror=…>` was executable.
 *  2. Escaping `text` is not optional; keeping the function in `utils/` next to
 *     `escapeHtml` makes that obvious and lets `highlightText.test.ts` pin it.
 */
import { escapeHtml } from './safeHtml';

/** Wrapper emitted around each match. Kept in one place for the tests. */
export const HIGHLIGHT_TAG_OPEN = '<mark class="bg-yellow-200 text-yellow-800 px-0.5 rounded">';

/**
 * Escape `text`, then wrap every case-insensitive occurrence of `query` in a
 * `<mark>`.
 *
 * Both sides are escaped *before* matching, so a query containing `&`, `<` or a
 * regex metacharacter still matches literally inside the escaped text.
 */
export function highlightText(text: string, query: string): string {
  const safe = escapeHtml(text ?? '');
  const q = (query ?? '').trim();
  if (!q) return safe;
  const needle = escapeHtml(q);
  const regex = new RegExp(`(${needle.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`, 'gi');
  return safe.replace(regex, `${HIGHLIGHT_TAG_OPEN}$1</mark>`);
}
