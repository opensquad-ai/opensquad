/**
 * UnifiedDiffView — Cursor-style inline diff (context / green inserts / red deletes).
 */
import React, { useMemo, useState } from 'react';
import { getLangForFile, highlightLine } from '../../utils/codeHighlight';

export type DiffLine = {
  type: 'context' | 'insert' | 'delete' | 'collapse';
  old_lineno?: number | null;
  new_lineno?: number | null;
  text: string;
  count?: number;
};

interface UnifiedDiffViewProps {
  fileName: string;
  lines: DiffLine[];
  additions?: number;
  deletions?: number;
  oversized?: boolean;
}

export const UnifiedDiffView: React.FC<UnifiedDiffViewProps> = ({
  fileName,
  lines,
  additions = 0,
  deletions = 0,
  oversized,
}) => {
  const lang = useMemo(() => getLangForFile(fileName), [fileName]);
  const [expandedCollapses, setExpandedCollapses] = useState<Set<number>>(new Set());

  if (oversized) {
    return (
      <div className="px-3 py-4 text-[11px] text-textMuted">
        文件过大，无法展示完整 diff。请在编辑器中手动核对。
      </div>
    );
  }

  if (!lines.length) {
    return (
      <div className="px-3 py-4 text-[11px] text-textMuted/60">无差异</div>
    );
  }

  return (
    <div className="flex-1 min-h-0 overflow-auto bg-[#1e1e1e] font-mono text-[11px] leading-[18px]">
      <div className="sticky top-0 z-[1] flex items-center gap-2 px-2 py-1 border-b border-white/10 bg-[#1e1e1e]/95 backdrop-blur-sm text-[10px]">
        <span className="text-emerald-400 tabular-nums font-medium">+{additions}</span>
        <span className="text-rose-400 tabular-nums font-medium">-{deletions}</span>
      </div>
      <div className="min-w-full inline-block">
        {lines.map((line, i) => {
          if (line.type === 'collapse') {
            const open = expandedCollapses.has(i);
            return (
              <button
                key={i}
                type="button"
                className="w-full text-left px-2 py-0.5 text-[10px] text-gray-500 hover:text-gray-300 bg-[#2a2a2a]/80 hover:bg-[#333] border-y border-black/40"
                onClick={() => {
                  setExpandedCollapses((prev) => {
                    const next = new Set(prev);
                    if (next.has(i)) next.delete(i);
                    else next.add(i);
                    return next;
                  });
                }}
              >
                {open ? '▾ ' : '▸ '}
                {line.text || `${line.count || 0} unmodified lines`}
              </button>
            );
          }

          const isIns = line.type === 'insert';
          const isDel = line.type === 'delete';
          const rowBg = isIns
            ? 'bg-[#12261e]'
            : isDel
              ? 'bg-[#2a1215]'
              : 'bg-transparent';
          const gutterBg = isIns
            ? 'bg-[#0e3a28] text-emerald-400/90'
            : isDel
              ? 'bg-[#4a151c] text-rose-300/90'
              : 'text-gray-600';
          const mark = isIns ? '+' : isDel ? '-' : ' ';
          const markColor = isIns
            ? 'text-emerald-400'
            : isDel
              ? 'text-rose-400'
              : 'text-transparent';

          return (
            <div key={i} className={`flex items-stretch ${rowBg}`}>
              <span
                className={`select-none w-9 shrink-0 text-right pr-1.5 tabular-nums text-[10px] leading-[18px] ${gutterBg}`}
              >
                {line.old_lineno ?? ''}
              </span>
              <span
                className={`select-none w-9 shrink-0 text-right pr-1.5 tabular-nums text-[10px] leading-[18px] border-r border-white/5 ${gutterBg}`}
              >
                {line.new_lineno ?? ''}
              </span>
              <span
                className={`select-none w-4 shrink-0 text-center font-semibold leading-[18px] ${markColor}`}
              >
                {mark}
              </span>
              <span
                className={`flex-1 min-w-0 whitespace-pre-wrap break-words pr-2 leading-[18px] ${
                  isIns ? 'text-emerald-100/95' : isDel ? 'text-rose-100/90' : 'text-gray-300'
                }`}
                dangerouslySetInnerHTML={{ __html: highlightLine(line.text || '', lang) }}
              />
            </div>
          );
        })}
      </div>
    </div>
  );
};

export default UnifiedDiffView;
