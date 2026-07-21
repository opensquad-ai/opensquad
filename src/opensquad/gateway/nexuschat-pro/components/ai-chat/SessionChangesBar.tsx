/**
 * SessionChangesBar — cumulative +/− stats above the composer.
 * Click opens the files panel "changed" tab;
 * Commit & Push sends that intent to the agent.
 */
import React from 'react';
import { ChevronDown } from 'lucide-react';

export type SessionChangesSummary = {
  additions: number;
  deletions: number;
  count: number;
};

/** Canonical user message text for Commit & Push (matched by system prompt). */
export const COMMIT_PUSH_MESSAGE = 'Commit & Push';

interface SessionChangesBarProps {
  summary: SessionChangesSummary | null;
  busy?: boolean;
  onOpenChanges: () => void;
  onCommitPush: () => void;
}

export const SessionChangesBar: React.FC<SessionChangesBarProps> = ({
  summary,
  busy,
  onOpenChanges,
  onCommitPush,
}) => {
  if (!summary || (summary.count === 0 && summary.additions === 0 && summary.deletions === 0)) {
    return null;
  }

  const add = summary.additions || 0;
  const del = summary.deletions || 0;

  return (
    <div className="flex items-center gap-2 flex-wrap px-0.5">
      <button
        type="button"
        onClick={onOpenChanges}
        disabled={busy}
        className="inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-[12px] font-medium
          bg-black/[0.04] dark:bg-white/[0.08] border border-border/60
          text-textMain hover:bg-primary/15 transition-colors
          disabled:opacity-50"
        title="查看变动文件"
      >
        <span className="text-textMuted">Changes</span>
        <span className="text-emerald-500 tabular-nums">+{add.toLocaleString()}</span>
        <span className="text-rose-400/90 tabular-nums">-{del.toLocaleString()}</span>
      </button>
      <button
        type="button"
        onClick={onCommitPush}
        disabled={busy}
        className="inline-flex items-center gap-1 rounded-full px-3 py-1 text-[12px] font-medium
          bg-black/[0.04] dark:bg-white/[0.08] border border-border/60
          text-textMain hover:bg-primary/15 transition-colors
          disabled:opacity-50"
        title="发送 Commit & Push：让 Agent 提交并推送当前改动"
      >
        Commit & Push
        <ChevronDown size={12} className="text-textMuted opacity-70" />
      </button>
    </div>
  );
};

export default SessionChangesBar;
