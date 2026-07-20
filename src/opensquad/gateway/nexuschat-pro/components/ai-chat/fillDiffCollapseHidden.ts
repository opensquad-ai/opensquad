/**
 * Reconstruct omitted equal lines for collapse markers that lack ``hidden``.
 * Uses current file text + neighboring new_lineno anchors (equal-span folds).
 */
export type CollapseFillLine = {
  type: string;
  old_lineno?: number | null;
  new_lineno?: number | null;
  text?: string;
  count?: number;
  hidden?: CollapseFillLine[];
};

export function fillDiffCollapseHidden<T extends CollapseFillLine>(
  lines: T[],
  sourceContent: string | null | undefined,
): T[] {
  if (!sourceContent || !lines.length) return lines;
  const needsFill = lines.some(
    (l) => l.type === 'collapse' && !(Array.isArray(l.hidden) && l.hidden.length > 0),
  );
  if (!needsFill) return lines;

  const fileLines = sourceContent.split(/\r?\n/);
  return lines.map((line, i) => {
    if (line.type !== 'collapse') return line;
    if (Array.isArray(line.hidden) && line.hidden.length > 0) return line;

    let prevNew: number | null = null;
    for (let j = i - 1; j >= 0; j--) {
      const t = lines[j];
      if (t.type === 'collapse') continue;
      if (t.new_lineno != null) {
        prevNew = t.new_lineno;
        break;
      }
    }
    let nextNew: number | null = null;
    for (let j = i + 1; j < lines.length; j++) {
      const t = lines[j];
      if (t.type === 'collapse') continue;
      if (t.new_lineno != null) {
        nextNew = t.new_lineno;
        break;
      }
    }

    const start = prevNew ?? 0;
    const end = nextNew ?? fileLines.length + 1;
    const hidden: CollapseFillLine[] = [];
    for (let n = start + 1; n < end; n++) {
      hidden.push({
        type: 'context',
        old_lineno: n,
        new_lineno: n,
        text: fileLines[n - 1] ?? '',
      });
    }
    if (!hidden.length) return line;
    return {
      ...line,
      count: hidden.length,
      text: `${hidden.length} unmodified lines`,
      hidden,
    };
  });
}

/** Expand collapse markers into context rows (All Files full preview). */
export function flattenDiffCollapses<T extends CollapseFillLine>(lines: T[]): T[] {
  const out: T[] = [];
  for (const line of lines) {
    if (line.type !== 'collapse') {
      out.push(line);
      continue;
    }
    const hidden = line.hidden;
    if (!hidden?.length) continue;
    for (const h of hidden) {
      out.push({
        ...(h as T),
        type: 'context',
        text: h.text ?? '',
      });
    }
  }
  return out;
}
