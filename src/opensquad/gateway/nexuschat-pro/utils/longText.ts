/**
 * When a message is too long to show in full, and what to show instead.
 *
 * A wall of pasted text pushes every other message off the screen. Length is the only test: a table
 * or a code block folds like anything else, because the folded view prints the text as it was
 * written and expanding brings the rendering back — refusing to fold them just meant the walls
 * stayed walls.
 *
 * The protocol markers are the one exception. A collaboration or approval card is not prose to read
 * at all: the UI parses those messages into cards, so folding one would only fold something nobody
 * was ever shown.
 */

export const LONG_TEXT_LIMIT = 200;

const STRUCTURED_MARKERS = [
  '[[COLLAB_TASK]]',
  '[[/COLLAB_TASK]]',
  '[[COLLAB_APPROVAL]]',
  '[[/COLLAB_APPROVAL]]',
];

/** True when this text is long enough to fold and is not a card the UI parses. */
export function shouldFold(text: string | null | undefined, limit: number = LONG_TEXT_LIMIT): boolean {
  const value = String(text ?? '');
  if (value.length <= limit) return false;
  if (STRUCTURED_MARKERS.some((marker) => value.includes(marker))) return false;
  return true;
}

/** The part that stays visible while folded. */
export function foldedText(text: string | null | undefined, limit: number = LONG_TEXT_LIMIT): string {
  const value = String(text ?? '');
  return value.length <= limit ? value : value.slice(0, limit);
}
