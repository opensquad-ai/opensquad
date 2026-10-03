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

const MARKER_START_RE = /\[\[(?:GROUP_APPROVAL|COLLAB_APPROVAL)\]\]/;
const MARKER_END_RE = /\[\[\/(?:GROUP_APPROVAL|COLLAB_APPROVAL)\]\]/;

/** Index just past the `}` closing the object at `openAt` (nested objects / braces in strings). */
function balancedJsonEnd(text: string, openAt: number): number {
  let depth = 0;
  let inStr = false;
  let esc = false;
  for (let i = openAt; i < text.length; i += 1) {
    const ch = text[i];
    if (inStr) {
      if (esc) esc = false;
      else if (ch === '\\') esc = true;
      else if (ch === '"') inStr = false;
      continue;
    }
    if (ch === '"') inStr = true;
    else if (ch === '{') depth += 1;
    else if (ch === '}') {
      depth -= 1;
      if (depth === 0) return i + 1;
    }
  }
  return -1;
}

/**
 * The marker's payload plus the span it occupies, tolerating a missing closing tag.
 *
 * The closing tag used to be required, so a card whose end marker was lost (a truncated send, a
 * hand-built message, an older client) parsed as *no card at all* — and the chat then painted the
 * raw marker JSON inside an ordinary bubble. That is the "big bubble of text appeared after I
 * clicked 确定" report. The span is returned so callers can drop or rewrite the whole marker.
 */
export function readApprovalMarker(
  content: string,
): { payload: GroupApprovalPayload; start: number; end: number } | null {
  if (!content) return null;
  const startMatch = MARKER_START_RE.exec(content);
  if (!startMatch) return null;
  const bodyStart = startMatch.index + startMatch[0].length;
  const rest = content.slice(bodyStart);
  const endMatch = MARKER_END_RE.exec(rest);
  const bodyEnd = endMatch ? bodyStart + endMatch.index : content.length;
  const body = content.slice(bodyStart, bodyEnd);
  const openAt = body.indexOf('{');
  if (openAt < 0) return null;
  const closeAt = balancedJsonEnd(body, openAt);
  if (closeAt < 0) return null;
  try {
    const data = JSON.parse(body.slice(openAt, closeAt));
    if (!data || typeof data !== 'object' || !data.id) return null;
    return {
      payload: data as GroupApprovalPayload,
      start: startMatch.index,
      end: endMatch ? bodyStart + endMatch.index + endMatch[0].length : bodyStart + closeAt,
    };
  } catch {
    return null;
  }
}

export function parseCollabApproval(content: string): GroupApprovalPayload | null {
  return readApprovalMarker(content)?.payload ?? null;
}

/**
 * The single quiet line an approval leaves behind once it is answered.
 *
 * A decided card is not history worth keeping — the answer is on the board — so the chat shows
 * this instead of the card, in small grey text and without a bubble, which is exactly what the
 * group chat gets from the backend's SYSTEM message.
 */
export function approvalQuietLine(payload: GroupApprovalPayload): string {
  const what = payload.title || payload.step || '批准请求';
  if (payload.status === 'approved') return `✅ 协作环节已批准：${what}`;
  if (payload.status === 'rejected') return `❌ 协作环节已拒绝：${what}`;
  return `📋 批准请求：${what}`;
}

/** The marker's opposite: the same thing with no marker at all. */
export const KIND_APPROVAL_MARKERS = [MARKER_START_RE, MARKER_END_RE];

/** @deprecated use parseCollabApproval (parses both markers) */
export const parseGroupApproval = parseCollabApproval;

export function stripCollabApprovalMarker(content: string): string {
  if (!content) return content;
  const found = readApprovalMarker(content);
  const withoutMarker = found ? content.slice(0, found.start) + content.slice(found.end) : content;
  return withoutMarker
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

/**
 * True when the message is an approval card the user has already answered.
 *
 * A decided card is not history worth keeping — the answer is on the board (the gate
 * chip, the task window) — and leaving it in the group chat kept a full card and a
 * "已确定 ✓" line per gate. Answered cards leave the chat, like an answered
 * propose-options card does.
 */
export function isResolvedApprovalMessage(content: string): boolean {
  const payload = parseCollabApproval(content || '');
  if (!payload) return false;
  return String(payload.status || 'pending') !== 'pending';
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

  // Answered: the card is done and gone (the caller also filters these out of the list,
  // this is the guarantee when a resolved one reaches the renderer some other way).
  if (!pending) return null;

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
