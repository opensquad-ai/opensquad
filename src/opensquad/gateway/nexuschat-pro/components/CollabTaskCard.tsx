/**
 * CollabTaskCard — the clickable collaboration card shown in group chat and in
 * the 1:1 DM window.
 *
 * The backend announces collaboration events as a TEXT message carrying a
 * `[[COLLAB_TASK]]{json}[[/COLLAB_TASK]]` marker (same convention as the
 * approval cards). This renders the marker as a card: task title, participants
 * with their invite state, and a button that opens the task window.
 */
import React from 'react';
import { useTranslation } from 'react-i18next';
import { ArrowUpRight, CheckCircle2, CircleDot, XCircle } from 'lucide-react';
import type { CollabTaskCardPayload, CollabTaskParticipant } from '../services/api';

export const COLLAB_TASK_START = '[[COLLAB_TASK]]';
export const COLLAB_TASK_END = '[[/COLLAB_TASK]]';

const MARKER_RE = /\[\[COLLAB_TASK\]\]\s*(\{[\s\S]*?\})\s*\[\[\/COLLAB_TASK\]\]/;

/** Parse a collaboration-task card out of message content, or null. */
export function parseCollabTask(content?: string | null): CollabTaskCardPayload | null {
  if (!content || !content.includes(COLLAB_TASK_START)) return null;
  const m = MARKER_RE.exec(content);
  if (!m) return null;
  try {
    const data = JSON.parse(m[1]);
    if (!data || typeof data !== 'object' || !data.id || !data.collab_id) return null;
    return data as CollabTaskCardPayload;
  } catch {
    return null;
  }
}

/** Drop only this card's marker, keeping the readable text of the message. */
export function stripCollabTaskMarker(content?: string | null): string {
  const text = content || '';
  if (!text.includes(COLLAB_TASK_START)) return text;
  return text.replace(MARKER_RE, '').trim();
}

/** Open the single-task window — App listens for this event. */
export function openCollabTaskWindow(collabId: string): void {
  if (!collabId) return;
  window.dispatchEvent(new CustomEvent('openCollabTask', { detail: { collabId } }));
}

const KIND_CLASS: Record<string, string> = {
  invite: 'bg-primary/10 text-primary',
  assign: 'bg-sky-500/10 text-sky-600',
  progress: 'bg-amber-500/10 text-amber-600',
  discussion: 'bg-violet-500/10 text-violet-600',
  done: 'bg-emerald-500/10 text-emerald-600',
};

const ParticipantState: React.FC<{ state: CollabTaskParticipant['state'] }> = ({ state }) => {
  const { t } = useTranslation();
  if (state === 'accepted') {
    return (
      <span className="inline-flex items-center gap-1 text-emerald-600" title={t('collabTask.state.accepted')}>
        <CheckCircle2 size={11} />
        {t('collabTask.state.accepted')}
      </span>
    );
  }
  if (state === 'declined') {
    return (
      <span className="inline-flex items-center gap-1 text-rose-500" title={t('collabTask.state.declined')}>
        <XCircle size={11} />
        {t('collabTask.state.declined')}
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 text-textMuted" title={t('collabTask.state.invited')}>
      <CircleDot size={11} />
      {t('collabTask.state.invited')}
    </span>
  );
};

export interface CollabTaskCardProps {
  payload: CollabTaskCardPayload;
  /** Open the single-task window (keyed by collab_id). */
  onOpen: (collabId: string) => void;
  /** Accept/decline an invite (only meaningful for kind="invite"). */
  onRespond?: (action: 'accept' | 'decline') => void | Promise<void>;
  disabled?: boolean;
}

export const CollabTaskCard: React.FC<CollabTaskCardProps> = ({ payload, onOpen, onRespond, disabled }) => {
  const { t } = useTranslation();
  const kind = payload.kind || 'discussion';
  const participants = payload.participants || [];
  const kindLabel = t(`collabTask.kind.${kind}`, { defaultValue: kind });

  return (
    <div className="rounded-xl border border-border bg-panel px-3.5 py-3 shadow-sm max-w-full">
      <div className="flex items-center gap-2">
        <span className={`shrink-0 rounded-md px-1.5 py-0.5 text-[10px] font-semibold ${KIND_CLASS[kind] || KIND_CLASS.discussion}`}>
          {kindLabel}
        </span>
        <span className="min-w-0 flex-1 truncate text-[13px] font-bold text-textMain">{payload.title}</span>
        {payload.card ? (
          <span className="shrink-0 rounded-md bg-bgLight px-1.5 py-0.5 text-[10px] text-textMuted">{payload.card}</span>
        ) : null}
      </div>

      {payload.summary ? (
        <div className="mt-2 whitespace-pre-wrap break-words text-[12px] leading-relaxed text-textMuted">
          {payload.summary}
        </div>
      ) : null}

      {participants.length ? (
        <div className="mt-2" data-testid="collab-task-participants">
          <div className="text-[11px] text-textMuted">{t('collabTask.participants')}</div>
          <div className="mt-1 flex flex-wrap gap-1.5">
            {participants.map((p) => (
              <span
                key={p.agent_id}
                className="inline-flex items-center gap-1 rounded-lg border border-border bg-bgLight px-2 py-0.5 text-[11px] text-textMain"
              >
                <span className="max-w-[120px] truncate">{p.name || p.agent_id}</span>
                <ParticipantState state={p.state} />
              </span>
            ))}
          </div>
        </div>
      ) : null}

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={() => onOpen(payload.collab_id)}
          className="inline-flex items-center gap-1 rounded-lg bg-primary px-2.5 py-1 text-[12px] text-white hover:opacity-90"
        >
          {t('collabTask.open')}
          <ArrowUpRight size={12} />
        </button>
        {kind === 'invite' && onRespond ? (
          <button
            type="button"
            disabled={disabled}
            onClick={() => void onRespond('accept')}
            className="rounded-lg border border-border px-2.5 py-1 text-[12px] text-textMain hover:bg-primary/10 disabled:opacity-50"
          >
            {t('collabTask.respond')}
          </button>
        ) : null}
        <span className="ml-auto shrink-0 font-mono text-[10px] text-textMuted">{payload.collab_id}</span>
      </div>
    </div>
  );
};

export default CollabTaskCard;
