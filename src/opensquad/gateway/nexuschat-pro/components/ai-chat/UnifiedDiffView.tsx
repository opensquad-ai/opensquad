/**
 * UnifiedDiffView — red/green line diff for session file changes.
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
    <div className="flex-1 min-h-0 overflow-auto bg-[#0d1117] font-mono text-[11px] leading-5">
      <div className="sticky top-0 z-[1] flex items-center gap-2 px-2 py-1 border-b border-white/5 bg-[#0d1117]/80 backdrop-blur-sm text-[10px]">
        <span className="text-emerald-400 tabular-nums">+{additions}</span>
        <span className="text-rose-400/90 tabular-nums">-{deletions}</span>
      </div>
      <div className="min-w-full inline-block">
        {lines.map((line, i) => {
          if (line.type === 'collapse') {
            const open = expandedCollapses.has(i);
            return (
              <button
                key={i}
                type="button"
                className="w-full text-left px-2 py-0.5 text-[10px] text-gray-500 hover:text-gray-300 hover:bg-white/[0.03]"
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
          const bg =
            line.type === 'insert'
              ? 'bg-emerald-500/15'
              : line.type === 'delete'
                ? 'bg-rose-500/15'
                : '';
          const numColor =
            line.type === 'insert'
              ? 'text-emerald-500/70'
              : line.type === 'delete'
                ? 'text-rose-400/70'
                : 'text-gray-600';
          return (
            <div key={i} className={`flex items-start ${bg}`}>
              <span className={`select-none w-10 shrink-0 text-right pr-1 tabular-nums text-[10px] ${numColor}`}>
                {line.old_lineno ?? ''}
              </span>
              <span className={`select-none w-10 shrink-0 text-right pr-2 tabular-nums text-[10px] border-r border-gray-800 ${numColor}`}>
                {line.new_lineno ?? ''}
              </span>
              <span
                className="flex-1 min-w-0 whitespace-pre-wrap break-words pl-2 text-gray-200"
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
