/**
 * Cursor-style indent guides: dashed verticals only at real indent columns
 * on the left of the code, not a full-width graph-paper grid.
 */
import React, { useMemo } from 'react';

const MAX_LEVELS = 16;
const GRADIENT =
  'repeating-linear-gradient(to bottom, var(--file-indent-guide) 0 2px, transparent 2px 7px)';

function gcd(a: number, b: number): number {
  let x = Math.abs(a);
  let y = Math.abs(b);
  while (y) {
    const t = y;
    y = x % y;
    x = t;
  }
  return x;
}

function leadingColumns(line: string): number {
  let n = 0;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (ch === ' ') n += 1;
    else if (ch === '\t') n += 4;
    else break;
  }
  return n;
}

/** Indent unit and how many guide columns the file actually uses. */
export function measureIndentGuides(text: string): { indentSize: number; levels: number } {
  let maxCols = 0;
  let g = 0;
  for (const line of text.split('\n')) {
    if (!line.trim()) continue;
    const n = leadingColumns(line);
    if (n <= 0) continue;
    maxCols = Math.max(maxCols, n);
    g = g === 0 ? n : gcd(g, n);
  }
  if (maxCols <= 0) return { indentSize: 2, levels: 0 };
  let indentSize = g || 2;
  if (indentSize === 1) indentSize = 2;
  if (indentSize > 4) indentSize = indentSize % 4 === 0 ? 4 : 2;
  return {
    indentSize,
    levels: Math.min(MAX_LEVELS, Math.floor(maxCols / indentSize)),
  };
}

export const FileIndentGuides: React.FC<{ padLeft?: string; text?: string }> = ({
  padLeft = '0.5rem',
  text = '',
}) => {
  const { indentSize, levels } = useMemo(() => measureIndentGuides(text), [text]);
  const style = useMemo(() => {
    if (levels <= 0) return null;
    return {
      backgroundImage: Array.from({ length: levels }, () => GRADIENT).join(', '),
      backgroundSize: Array.from({ length: levels }, () => '1px 7px').join(', '),
      backgroundRepeat: Array.from({ length: levels }, () => 'repeat-y').join(', '),
      backgroundPosition: Array.from(
        { length: levels },
        (_, i) => `calc(${padLeft} + ${(i + 1) * indentSize}ch) 0`,
      ).join(', '),
    };
  }, [indentSize, levels, padLeft]);

  if (!style) return null;
  return (
    <div
      aria-hidden
      className="file-indent-guides pointer-events-none absolute inset-0 z-0"
      style={style}
    />
  );
};
