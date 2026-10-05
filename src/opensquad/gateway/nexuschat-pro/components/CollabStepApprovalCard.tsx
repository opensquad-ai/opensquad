/**
 * GroupApprovalCard — Approve/Reject cards posted in group chat.
 * Markers: [[GROUP_APPROVAL]]{json}[[/GROUP_APPROVAL]]
 *           [[COLLAB_APPROVAL]]{json}[[/COLLAB_APPROVAL]] (legacy)
 */
import React, { useState } from 'react';
import { Check, X, ClipboardCheck, RefreshCw, Hand } from 'lucide-react';

import { collabBoardAPI } from '../services/api';

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

/** Escape raw control characters inside JSON string literals (see the Python twin). */
function repairJsonText(text: string): string {
  let out = '';
  let inStr = false;
  let esc = false;
  for (const ch of text) {
    if (inStr) {
      if (esc) esc = false;
      else if (ch === '\\') esc = true;
      else if (ch === '"') inStr = false;
      else if (ch.charCodeAt(0) < 0x20) {
        out += ch === '\n' ? '\\n' : ch === '\r' ? '\\r' : ch === '\t' ? '\\t' : `\\u${ch.charCodeAt(0).toString(16).padStart(4, '0')}`;
        continue;
      }
    } else if (ch === '"') {
      inStr = true;
    }
    out += ch;
  }
  return out;
}

/**
 * The marker's payload plus the span it occupies.
 *
 * Tolerant on purpose, because a hand-built card (the model typing the marker rather than calling
 * the tool) is what actually arrives in the wild — it can be missing its closing tag, contain the
 * tag's text inside a value, or carry a real newline inside a JSON string. Any of those used to
 * mean "no card", and the chat then painted the marker verbatim: the reported big bubble. The
 * object is located first and the closing tag looked for after it; the JSON is repaired before
 * parsing; the span is returned so callers can drop or rewrite the whole marker.
 */
export function readApprovalMarker(
  content: string,
): { payload: GroupApprovalPayload; start: number; end: number } | null {
  if (!content) return null;
  const startMatch = MARKER_START_RE.exec(content);
  if (!startMatch) return null;
  const openAt = content.indexOf('{', startMatch.index + startMatch[0].length);
  if (openAt < 0) return null;
  const closeAt = balancedJsonEnd(content, openAt);
  if (closeAt < 0) return null;
  try {
    const data = JSON.parse(repairJsonText(content.slice(openAt, closeAt)));
    if (!data || typeof data !== 'object' || !data.id) return null;
    const after = content.slice(closeAt);
    const endMatch = MARKER_END_RE.exec(after);
    return {
      payload: data as GroupApprovalPayload,
      start: startMatch.index,
      end: endMatch ? closeAt + endMatch.index + endMatch[0].length : closeAt,
    };
  } catch {
    return null;
  }
}

/** True when the content carries an approval marker, parseable or not. */
export function hasApprovalMarker(content?: string | null): boolean {
  return !!content && MARKER_START_RE.test(content);
}

/**
 * What to show when a marker is there but its JSON still cannot be read.
 *
 * The last resort must never be the marker itself: the readable headline the encoder appends
 * (`📋 协作批准请求：…`, or a verdict line) is enough to say what happened.
 */
export function approvalFallbackLine(content: string): string {
  const line = String(content || '')
    .split('\n')
    .map((s) => s.trim())
    .find((s) => /^(📋|✅|❌|🔄|✋)/.test(s));
  return line || '📋 批准请求（内容无法解析）';
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
  const [supplementOpen, setSupplementOpen] = useState(false);
  const [supplement, setSupplement] = useState('');
  const [supplementSent, setSupplementSent] = useState(false);
  const pending = (payload.status || 'pending') === 'pending';
  const { label, Icon } = kindMeta(payload.kind);
  const kind = (payload.kind || '').toLowerCase();

  const subtitle =
    kind === 'mode_switch'
      ? `模式：${payload.from_mode || '?'} → ${payload.to_mode || '?'}`
      : kind === 'collab_step'
        ? `环节：${payload.step || payload.title}`
        : null;

  /**
   * 补充：ask for a change without deciding.
   *
   * 确定 and 拒绝 are both answers, and neither fits "not this, here is what I need instead" — which
   * is why the only way to say it was to type into the group chat, where it was exposed to everyone
   * and the gate sat untouched. The note is posted to the collaboration's task window instead: the
   * agent reads it there and is woken by it, and the gate stays pending so the user can still
   * approve once they are satisfied. Only the collaboration gates offer it — a mode switch has
   * nothing to supplement.
   */
  const canSupplement = kind === 'collab_step' && !!payload.collab_id;

  const sendSupplement = async () => {
    const text = supplement.trim();
    if (!text || !canSupplement || busy || disabled) return;
    setBusy(true);
    setError(null);
    try {
      await collabBoardAPI.postTaskMessage(String(payload.collab_id), text);
      setSupplement('');
      setSupplementOpen(false);
      setSupplementSent(true);
    } catch (e: any) {
      setError(e?.message || String(e) || '补充发送失败');
    } finally {
      setBusy(false);
    }
  };

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
        <>
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
            {canSupplement ? (
              <button
                type="button"
                disabled={busy || disabled}
                onClick={() => setSupplementOpen((open) => !open)}
                data-testid="approval-supplement-toggle"
                title="不通过也可以先补充要求"
                className="ml-auto text-[11px] text-textMuted underline decoration-dotted hover:text-textMain border-0 bg-transparent cursor-pointer disabled:opacity-50"
              >
                补充
              </button>
            ) : null}
          </div>
          {canSupplement && supplementOpen ? (
            <div className="mt-2" data-testid="approval-supplement">
              <textarea
                value={supplement}
                onChange={(e) => setSupplement(e.target.value)}
                rows={3}
                autoFocus
                placeholder="需要补充什么？这条会发到协作任务窗口，卡片的审批保持待办"
                className="w-full rounded-lg border border-border bg-bgLight px-2 py-1.5 text-[12px] text-textMain outline-none focus:border-primary/40 resize-y"
              />
              <div className="mt-1 flex items-center gap-2">
                <button
                  type="button"
                  disabled={busy || disabled || !supplement.trim()}
                  onClick={() => void sendSupplement()}
                  className="rounded-lg bg-primary px-2.5 py-1 text-[11px] text-white hover:opacity-90 border-0 cursor-pointer disabled:opacity-50"
                >
                  发送补充
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setSupplementOpen(false);
                    setSupplement('');
                  }}
                  className="rounded-lg border border-border px-2.5 py-1 text-[11px] text-textMuted hover:text-textMain cursor-pointer bg-transparent"
                >
                  取消
                </button>
              </div>
            </div>
          ) : null}
          {supplementSent ? (
            <div className="mt-1 text-[11px] text-textMuted" data-testid="approval-supplement-sent">
              已补充，等待 agent 回应
            </div>
          ) : null}
        </>
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
