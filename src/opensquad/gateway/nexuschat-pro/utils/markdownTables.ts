/**
 * Wrap rendered markdown tables in `.ai-table-wrap`.
 *
 * `renderFencedMarkdown` (the agent-web renderer) wraps tables as it renders, and
 * the chat CSS keys its table styling — and the copy button — off
 * `.ai-markdown .ai-table-wrap`. Group chat parses with plain `marked`, so it has
 * to wrap afterwards to get the same treatment.
 */
export function wrapMarkdownTables(html: string): string {
  if (!html || !html.includes('<table')) return html;
  if (html.includes('class="ai-table-wrap"')) return html; // already wrapped
  return html
    .replace(/<table/g, '<div class="ai-table-wrap"><table')
    .replace(/<\/table>/g, '</table></div>');
}
