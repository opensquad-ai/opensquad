/**
 * @mention rendering: mark mentions in the markdown source, then swap them for
 * the clickable span the chat CSS styles.
 *
 * Group chat used to run `@(\w+)` by hand — which misses every real name that is
 * not ASCII-word-shaped (`@调查员`, `@coder-001`, `@子代2号`) — and the window
 * showed mentions as plain text. Both surfaces go through here now, so a mention
 * is highlighted wherever the message is rendered.
 */

/** Classes on the mention span: the chat CSS and the click handler key off it. */
export const MENTION_CLASS = 'mention-link text-primary font-bold cursor-pointer hover:underline';

/**
 * Unicode letters/digits plus the separators names actually use. The lookbehind
 * keeps e-mail addresses (`x@y.com`) and already-decorated mentions (`>@name<`)
 * from being matched again.
 */
export const MENTION_RE = /(?<![\p{L}\p{N}_.\->])@([\p{L}\p{N}_.\-]+)/gu;

const escapeHtml = (text: string): string =>
  text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');

/**
 * Decorate mentions in the markdown source.
 *
 * The span is emitted into the source (marked passes inline HTML through) so
 * every surface that renders markdown — group chat, the 1:1 window, tool output
 * — highlights mentions without each of them post-processing the HTML.
 */
export function markMentions(source: string): string {
  if (!source || !source.includes('@')) return source;
  return source.replace(MENTION_RE, (_match, name: string) => mentionSpan(String(name)));
}

/** Turn mentions in already-rendered HTML into the styled, clickable span. */
export function renderMentionSpans(html: string): string {
  if (!html || !html.includes('@')) return html;
  return html.replace(MENTION_RE, (_match, name: string) => mentionSpan(String(name)));
}

/** The span itself — shared so both passes produce identical markup. */
export function mentionSpan(name: string): string {
  const safe = escapeHtml(name);
  return `<span class="${MENTION_CLASS}" data-username="${safe}">@${safe}</span>`;
}
