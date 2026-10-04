/**
 * When a message is too long to show in full, and what to show instead.
 *
 * A wall of pasted text pushes every other message off the screen. Folding is only safe on plain
 * prose: the bodies that carry structure — collaboration cards, approval cards, fenced code, tables
 * — are rendered from markup, and cutting those at an arbitrary character cuts the markup with it.
 */

export const LONG_TEXT_LIMIT = 200;

const STRUCTURED_MARKERS = [
  '[[COLLAB_TASK]]',
  '[[/COLLAB_TASK]]',
  '[[COLLAB_APPROVAL]]',
  '[[/COLLAB_APPROVAL]]',
  '```',
];

/** True when this text is long enough to fold, and is not content whose structure matters. */
export function shouldFold(text: string | null | undefined, limit: number = LONG_TEXT_LIMIT): boolean {
  const value = String(text ?? '');
  if (value.length <= limit) return false;
  if (STRUCTURED_MARKERS.some((marker) => value.includes(marker))) return false;
  // A table keeps its meaning only in full.
  if (/^\s*\|/m.test(value)) return false;
  return true;
}

/** The part that stays visible while folded. */
export function foldedText(text: string | null | undefined, limit: number = LONG_TEXT_LIMIT): string {
  const value = String(text ?? '');
  return value.length <= limit ? value : value.slice(0, limit);
}
