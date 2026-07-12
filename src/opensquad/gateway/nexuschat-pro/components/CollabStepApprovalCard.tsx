/**
 * GroupApprovalCard — Approve/Reject cards posted in group chat.
 * Markers: [[GROUP_APPROVAL]]{json}[[/GROUP_APPROVAL]]
 *           [[COLLAB_APPROVAL]]{json}[[/COLLAB_APPROVAL]] (legacy)
 */
import React, { useState } from 'react';
import { Check, X, ClipboardCheck, RefreshCw, Hand } from 'lucide-react';

export interface GroupApprovalPayload {
  v?: number;
  id: string;
  kind?: 'collab_step' | 'mode_switch' | 'generic' | string;
  collab_id?: string;
  step?: string;
  title: string;
  summary?: string;
  status: 'pending' | 'approved' | 'rejected' | string;
  agent_id?: string;
  agent_name?: string;
  pm_agent_id?: string;
  pm_agent_name?: string;
  group_id?: string;
  from_mode?: string;
  to_mode?: string;
  resolve_note?: string;
}

const MARKER_RE =
  /\[\[(?:GROUP_APPROVAL|COLLAB_APPROVAL)\]\]\s*(\{[\s\S]*?\})\s*\[\[\/(?:GROUP_APPROVAL|COLLAB_APPROVAL)\]\]/;

export function parseCollabApproval(content: string): GroupApprovalPayload | null {
  if (
    !content ||
    (!content.includes('[[COLLAB_APPROVAL]]') && !content.includes('[[GROUP_APPROVAL]]'))
  ) {
    return null;
  }
  const m = content.match(MARKER_RE);
  if (!m) return null;
  try {
    const data = JSON.parse(m[1]);
    if (!data || typeof data !== 'object' || !data.id) return null;
    return data as GroupApprovalPayload;
  } catch {
    return null;
  }
}

/** @deprecated use parseCollabApproval (parses both markers) */
export const parseGroupApproval = parseCollabApproval;

export function stripCollabApprovalMarker(content: string): string {
  if (!content) return content;
  return content
    .replace(MARKER_RE, '')
    .replace(/^📋\s*协作批准请求：.*$/gm, '')
    .replace(/^🔄\s*模式切换申请：.*$/gm, '')
    .replace(/^✋\s*批准请求：.*$/gm, '')
    .replace(/^环节：.*$/gm, '')
    .replace(/^模式：.*$/gm, '')
    .replace(/^请在下方卡片中点击.*$/gm, '')
    .trim();
}

interface CollabStepApprovalCardProps {
  payload: GroupApprovalPayload;
  groupId: string;
  messageId: string;
  onResolve: (action: 'approve' | 'reject') => Promise<void>;
  disabled?: boolean;
}

function kindMeta(kind: string | undefined) {
  const k = (kind || '').toLowerCase();
  if (k === 'mode_switch') {
    return { label: '模式切换申请', Icon: RefreshCw };
  }
  if (k === 'collab_step') {
    return { label: '协作环节批准', Icon: ClipboardCheck };
  }
  return { label: '批准请求', Icon: Hand };
}

export const CollabStepApprovalCard: React.FC<CollabStepApprovalCardProps> = ({
  payload,
  onResolve,
  disabled,
}) => {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pending = (payload.status || 'pending') === 'pending';
  const { label, Icon } = kindMeta(payload.kind);
  const kind = (payload.kind || '').toLowerCase();

  const subtitle =
    kind === 'mode_switch'
      ? `模式：${payload.from_mode || '?'} → ${payload.to_mode || '?'}`
      : kind === 'collab_step'
        ? `环节：${payload.step || payload.title}`
        : null;

  const handle = async (action: 'approve' | 'reject') => {
    if (!pending || busy || disabled) return;
    setBusy(true);
    setError(null);
    try {
      await onResolve(action);
    } catch (e: any) {
      setError(e?.message || String(e) || '操作失败');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="my-1 rounded-xl border border-border bg-panel px-3.5 py-3 shadow-sm min-w-[220px] max-w-[360px]">
      <div className="flex items-center gap-1.5 text-[12px] font-semibold text-textMain mb-1">
        <Icon size={14} className="text-primary shrink-0" />
        <span>{label}</span>
      </div>
      <div className="text-[13px] font-medium text-textMain mb-0.5">
        {payload.title || payload.step || '批准请求'}
      </div>
      {subtitle ? <div className="text-[11px] text-textMuted mb-2">{subtitle}</div> : <div className="mb-2" />}
      {payload.summary ? (
        <div className="text-[12px] text-textMuted mb-3 leading-relaxed whitespace-pre-wrap break-words max-h-40 overflow-y-auto">
          {payload.summary}
        </div>
      ) : (
        <div className="mb-3" />
      )}
      {pending ? (
        <div className="flex items-center gap-2">
          <button
            type="button"
            disabled={busy || disabled}
            onClick={() => handle('approve')}
            className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg text-[12px] font-medium bg-primary text-white hover:opacity-90 border-0 cursor-pointer disabled:opacity-50"
          >
            <Check size={13} />
            确定
          </button>
          <button
            type="button"
            disabled={busy || disabled}
            onClick={() => handle('reject')}
            className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg text-[12px] font-medium text-textMuted hover:bg-black/[0.05] dark:hover:bg-white/[0.06] border border-border cursor-pointer bg-transparent disabled:opacity-50"
          >
            <X size={13} />
            拒绝
          </button>
        </div>
      ) : (
        <div className="text-[11px] text-textMuted">
          {payload.status === 'approved' ? '已确定 ✓' : '已拒绝 ✗'}
          {payload.resolve_note ? ` — ${payload.resolve_note}` : ''}
        </div>
      )}
      {error ? <div className="mt-2 text-[11px] text-red-500">{error}</div> : null}
    </div>
  );
};

/** Alias */
export const GroupApprovalCard = CollabStepApprovalCard;
