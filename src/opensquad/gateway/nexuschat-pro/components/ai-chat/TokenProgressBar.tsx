/**
 * TokenProgressBar - displays token usage progress.
 *
 * Shows used/max tokens with a progress bar, colored by usage level:
 *   - Green: < 50%
 *   - Amber: 50-80%
 *   - Red: > 80%
 *
 * Optionally shows session usage breakdown.
 */
import React from 'react';

interface SessionStats {
  total_input_tokens: number;
  total_output_tokens: number;
  total_tokens: number;
  total_requests: number;
}

interface TokenProgressBarProps {
  used: number;
  max: number;
  breakdown?: {
    system?: number;
    user: number;
    thought: number;
    tool: number;
    tool_defs?: number;
    response: number;
  };
  /** 本会话统计（后端新会话时 reset，直接显示无需 delta 计算） */
  session?: SessionStats | null;
  compact?: boolean;
}

function formatTokenCount(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

export const TokenProgressBar: React.FC<TokenProgressBarProps> = ({
  used,
  max,
  breakdown,
  session,
  compact = false,
}) => {
  if (!max || max <= 0) return null;

  const pct = Math.min((used / max) * 100, 100);
  const barColor =
    pct > 80 ? 'bg-red-500' :
    pct > 50 ? 'bg-amber-500' :
    'bg-emerald-500';

  if (compact) {
    return (
      <div className="flex items-center gap-2 text-[10px] text-textMuted">
        <div className="w-16 h-1.5 bg-bgLight rounded-full overflow-hidden">
          <div className={`h-full rounded-full ${barColor} transition-all`} style={{ width: `${pct}%` }} />
        </div>
        <span>{formatTokenCount(used)}/{formatTokenCount(max)}</span>
      </div>
    );
  }

  return (
    <div className="px-3 py-1.5 mt-1 border-t border-border/50">
      {/* Main progress */}
      <div className="flex items-center gap-2 mb-1">
        <span className="text-[10px] text-textMuted">Tokens</span>
        <div className="flex-1 h-1.5 bg-bgLight rounded-full overflow-hidden">
          <div className={`h-full rounded-full ${barColor} transition-all`} style={{ width: `${pct}%` }} />
        </div>
        <span className="text-[10px] text-textMuted font-mono">
          {formatTokenCount(used)}/{formatTokenCount(max)}
        </span>
      </div>

      {/* Breakdown — Total/Requests stay on the Other line (no extra row) */}
      <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-[9px] text-textMuted">
        <span>Sys: {formatTokenCount(breakdown?.system ?? 0)}</span>
        <span>User: {formatTokenCount(breakdown?.user ?? 0)}</span>
        <span>Thought: {formatTokenCount(breakdown?.thought ?? 0)}</span>
        <span>Tool: {formatTokenCount(breakdown?.tool ?? 0)}</span>
        <span>ToolDefs: {formatTokenCount(breakdown?.tool_defs ?? 0)}</span>
        <span>Reply: {formatTokenCount(breakdown?.response ?? 0)}</span>
        <span>Other: {formatTokenCount(breakdown?.overhead ?? 0)}</span>
        {session && (
          <>
            <span>Total: {formatTokenCount(session.total_tokens ?? 0)}</span>
            <span>Requests: {session.total_requests ?? 0}</span>
          </>
        )}
      </div>
    </div>
  );
};
