/**
 * WindowCard — the generic, agent-sent card.
 *
 * A `[[WINDOW_CARD]]{json}[[/WINDOW_CARD]]` message renders as a compact card in
 * group chat or in a DM; clicking it opens a window whose content is whatever
 * the sender put in `view` (sections / table / flow / metrics / raw). Nothing
 * here is business-specific — a new use case is a new payload, not new code.
 */
import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ArrowUpRight, Check, Copy, ExternalLink, Maximize2 } from 'lucide-react';
import { openCollabTaskWindow } from './CollabTaskCard';
import { windowCardAPI } from '../services/api';

export const WINDOW_CARD_START = '[[WINDOW_CARD]]';
export const WINDOW_CARD_END = '[[/WINDOW_CARD]]';

const MARKER_RE = /\[\[WINDOW_CARD\]\]\s*(\{[\s\S]*?\})\s*\[\[\/WINDOW_CARD\]\]/;

export interface WindowCardBlock {
  title?: string;
  text?: string;
  items?: string[];
  status?: string;
}

export interface WindowCardFormField {
  id: string;
  label: string;
  type: 'text' | 'textarea' | 'select' | 'radio' | 'checkbox';
  placeholder?: string;
  required?: boolean;
  options?: { id: string; label: string }[];
}

export interface WindowCardForm {
  submit_label?: string;
  cancel_label?: string;
  fields: WindowCardFormField[];
}

export interface WindowCardView {
  kind: 'sections' | 'table' | 'flow' | 'metrics' | 'raw';
  blocks?: WindowCardBlock[];
  steps?: { title?: string; detail?: string; status?: string }[];
  items?: { label?: string; value?: string }[];
  columns?: string[];
  rows?: string[][];
  /** Interactive fields the user fills in; submitting posts the answer back. */
  form?: WindowCardForm | null;
  text?: string;
}

export interface WindowCardAction {
  id: string;
  label: string;
  intent: 'open_url' | 'copy' | 'open_collab_task' | 'respond' | 'confirm' | 'decline' | 'none';
  url?: string;
  copy?: string;
  collab_id?: string;
}

export interface WindowCardPayload {
  v?: number;
  id: string;
  kind?: string;
  title: string;
  summary?: string;
  icon?: string;
  sender?: { agent_id?: string; agent_name?: string };
  target?: { group_id?: string; recipient_name?: string };
  source?: string;
  view: WindowCardView;
  actions?: WindowCardAction[];
  state?: string;
  /** The user's answer, recorded in the card after they submit / press a button. */
  response?: {
    action_id?: string;
    values?: Record<string, any>;
    by?: string;
    at?: string;
  } | null;
}

/** Parse a window card out of message content, or null. */
export function parseWindowCard(content?: string | null): WindowCardPayload | null {
  if (!content || !content.includes(WINDOW_CARD_START)) return null;
  const m = MARKER_RE.exec(content);
  if (!m) return null;
  try {
    const data = JSON.parse(m[1]);
    if (!data || typeof data !== 'object' || !data.id || !data.title || !data.view) return null;
    return data as WindowCardPayload;
  } catch {
    return null;
  }
}

/** Drop only this card's marker, keeping the readable text. */
export function stripWindowCardMarker(content?: string | null): string {
  const text = content || '';
  if (!text.includes(WINDOW_CARD_START)) return text;
  return text.replace(MARKER_RE, '').trim();
}

/** Open the window for a card (App listens for this event). */
export function openWindowCard(payload: WindowCardPayload, messageId?: string): void {
  if (!payload?.id) return;
  window.dispatchEvent(new CustomEvent('openWindowCard', { detail: { payload, messageId } }));
}

/** Post an answer (form submit / confirm / decline) back to the gateway. */
export function answerWindowCard(
  payload: WindowCardPayload,
  actionId: string,
  values: Record<string, any>,
  messageId?: string,
): Promise<unknown> {
  return windowCardAPI.respond(payload.id, {
    messageId: messageId || '',
    actionId: actionId || 'submit',
    values: values || {},
  });
}

/** Run an action button: the useful intents work with no server round trip. */
export function runWindowCardAction(
  payload: WindowCardPayload,
  action: WindowCardAction,
  messageId?: string,
): void {
  if (action.intent === 'open_url' && action.url) {
    window.open(action.url, '_blank', 'noopener,noreferrer');
    return;
  }
  if (action.intent === 'copy') {
    const text = action.copy || payload.title;
    void navigator.clipboard?.writeText(text).catch(() => undefined);
    return;
  }
  if (action.intent === 'open_collab_task' && action.collab_id) {
    openCollabTaskWindow(action.collab_id);
    return;
  }
  if (action.intent === 'respond' || action.intent === 'confirm' || action.intent === 'decline') {
    // Answering from the card itself, with no extra input.
    void answerWindowCard(payload, action.id || action.intent, {}, messageId).catch(() => undefined);
    return;
  }
  window.dispatchEvent(
    new CustomEvent('windowCardAction', { detail: { cardId: payload.id, actionId: action.id } }),
  );
}

export interface WindowCardProps {
  payload: WindowCardPayload;
  /** Backend id of the card message (needed to post an answer back). */
  messageId?: string;
  /** Override the default open (App event). */
  onOpen?: (payload: WindowCardPayload) => void;
}

export const WindowCard: React.FC<WindowCardProps> = ({ payload, messageId, onOpen }) => {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);
  const actions = payload.actions || [];
  const sender = payload.sender?.agent_name || payload.sender?.agent_id || '';
  const answered = !!payload.state && payload.state !== 'open';

  return (
    <div
      className="rounded-xl border border-border bg-panel px-3.5 py-3 shadow-sm max-w-full"
      data-testid="window-card"
    >
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            {payload.kind ? (
              <span className="shrink-0 rounded-md bg-primary/10 px-1.5 py-0.5 text-[10px] font-semibold text-primary">
                {payload.kind}
              </span>
            ) : null}
            <span className="min-w-0 truncate text-[13px] font-bold text-textMain">{payload.title}</span>
          </div>
          {payload.summary ? (
            <div className="mt-1 whitespace-pre-wrap break-words text-[12px] leading-relaxed text-textMuted">
              {payload.summary}
            </div>
          ) : null}
          {sender ? <div className="mt-1 text-[10px] text-textMuted">@{sender}</div> : null}
        </div>
        <button
          type="button"
          onClick={() => (onOpen ? onOpen(payload) : openWindowCard(payload, messageId))}
          className="shrink-0 rounded-lg p-1 text-textMuted hover:bg-primary/10 hover:text-textMain"
          title={t('windowCard.open')}
          aria-label={t('windowCard.open')}
          data-testid="window-card-open"
        >
          <Maximize2 size={13} />
        </button>
      </div>

      <div className="mt-2.5 flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={() => (onOpen ? onOpen(payload) : openWindowCard(payload, messageId))}
          className="inline-flex items-center gap-1 rounded-lg bg-primary px-2.5 py-1 text-[12px] text-white hover:opacity-90"
        >
          {t('windowCard.open')}
          <ArrowUpRight size={12} />
        </button>
        {actions.map((a) => (
          <button
            key={a.id}
            type="button"
            disabled={answered && (a.intent === 'respond' || a.intent === 'confirm' || a.intent === 'decline')}
            onClick={() => runWindowCardAction(payload, a, messageId)}
            className="inline-flex items-center gap-1 rounded-lg border border-border px-2.5 py-1 text-[12px] text-textMain hover:bg-primary/10 disabled:opacity-50"
          >
            {a.intent === 'open_url' ? <ExternalLink size={11} /> : null}
            {a.label}
          </button>
        ))}
        {answered ? (
          <span className="shrink-0 rounded-md bg-emerald-500/10 px-1.5 py-0.5 text-[10px] font-semibold text-emerald-600">
            {t('windowCard.answered')}
          </span>
        ) : null}
        <button
          type="button"
          onClick={() => {
            void navigator.clipboard?.writeText(payload.title).catch(() => undefined);
            setCopied(true);
            window.setTimeout(() => setCopied(false), 1200);
          }}
          className="ml-auto shrink-0 rounded-lg p-1 text-textMuted hover:text-textMain"
          title={t('windowCard.copy')}
          aria-label={t('windowCard.copy')}
        >
          {copied ? <Check size={12} /> : <Copy size={12} />}
        </button>
      </div>
    </div>
  );
};

export default WindowCard;
