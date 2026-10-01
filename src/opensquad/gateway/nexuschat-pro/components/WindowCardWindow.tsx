/**
 * WindowCardWindow — the window a [[WINDOW_CARD]] opens.
 *
 * Pure data-driven: it renders whatever `view` the agent sent, so a new kind of
 * card needs no new component. Sections / table / flow / metrics / raw are
 * supported, and an unknown kind degrades to the raw payload instead of
 * rendering nothing.
 */
import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { CheckCircle2, ChevronDown, ChevronRight, Circle, Code2, Loader2, X, XCircle } from 'lucide-react';
import type { WindowCardPayload } from './WindowCard';
import { runWindowCardAction } from './WindowCard';

export interface WindowCardWindowProps {
  payload: WindowCardPayload;
  onClose: () => void;
}

const StepIcon: React.FC<{ status?: string }> = ({ status }) => {
  if (status === 'done') return <CheckCircle2 size={13} className="shrink-0 text-emerald-600" />;
  if (status === 'doing') return <Loader2 size={13} className="shrink-0 text-amber-500" />;
  if (status === 'blocked') return <XCircle size={13} className="shrink-0 text-rose-500" />;
  return <Circle size={13} className="shrink-0 text-textMuted" />;
};

const Sections: React.FC<{ payload: WindowCardPayload }> = ({ payload }) => {
  const blocks = payload.view.blocks || [];
  const { t } = useTranslation();
  if (!blocks.length) return <div className="text-[12px] text-textMuted">{t('windowCard.empty')}</div>;
  return (
    <div className="space-y-2">
      {blocks.map((b, i) => (
        <div key={`${b.title || 'block'}-${i}`} className="rounded-lg border border-border bg-bgLight px-2.5 py-2">
          {b.title ? <div className="text-[12px] font-semibold text-textMain">{b.title}</div> : null}
          {b.text ? (
            <div className="mt-0.5 whitespace-pre-wrap break-words text-[12px] leading-relaxed text-textMuted">
              {b.text}
            </div>
          ) : null}
          {b.items?.length ? (
            <ul className="mt-1 list-disc pl-4 text-[12px] text-textMuted">
              {b.items.map((it, j) => (
                <li key={j} className="break-words">
                  {it}
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      ))}
    </div>
  );
};

const Table: React.FC<{ payload: WindowCardPayload }> = ({ payload }) => {
  const { t } = useTranslation();
  const { columns = [], rows = [] } = payload.view;
  if (!columns.length && !rows.length) return <div className="text-[12px] text-textMuted">{t('windowCard.empty')}</div>;
  return (
    <div className="overflow-x-auto rounded-lg border border-border">
      <table className="w-full border-collapse text-[12px]">
        {columns.length ? (
          <thead>
            <tr className="bg-bgLight">
              {columns.map((c, i) => (
                <th key={i} className="border-b border-border px-2.5 py-1.5 text-left font-semibold text-textMain">
                  {c}
                </th>
              ))}
            </tr>
          </thead>
        ) : null}
        <tbody>
          {rows.map((row, i) => (
            <tr key={i} className="even:bg-bgLight/40">
              {row.map((cell, j) => (
                <td key={j} className="border-b border-border px-2.5 py-1.5 align-top text-textMain">
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
};

const Flow: React.FC<{ payload: WindowCardPayload }> = ({ payload }) => {
  const { t } = useTranslation();
  const steps = payload.view.steps || [];
  if (!steps.length) return <div className="text-[12px] text-textMuted">{t('windowCard.empty')}</div>;
  return (
    <div className="space-y-1.5">
      {steps.map((s, i) => (
        <div key={i} className="flex items-start gap-2 rounded-lg border border-border bg-bgLight px-2.5 py-2">
          <span className="mt-0.5">
            <StepIcon status={s.status} />
          </span>
          <div className="min-w-0 flex-1">
            <div className="text-[12px] font-semibold text-textMain">{s.title}</div>
            {s.detail ? <div className="mt-0.5 break-words text-[12px] text-textMuted">{s.detail}</div> : null}
          </div>
          {s.status ? <span className="shrink-0 text-[10px] text-textMuted">{s.status}</span> : null}
        </div>
      ))}
    </div>
  );
};

const Metrics: React.FC<{ payload: WindowCardPayload }> = ({ payload }) => {
  const { t } = useTranslation();
  const items = payload.view.items || [];
  if (!items.length) return <div className="text-[12px] text-textMuted">{t('windowCard.empty')}</div>;
  return (
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
      {items.map((it, i) => (
        <div key={i} className="rounded-lg border border-border bg-bgLight px-2.5 py-2">
          <div className="text-[11px] text-textMuted">{it.label}</div>
          <div className="mt-0.5 truncate text-[14px] font-bold text-textMain">{it.value}</div>
        </div>
      ))}
    </div>
  );
};

const Raw: React.FC<{ payload: WindowCardPayload }> = ({ payload }) => (
  <div className="whitespace-pre-wrap break-words rounded-lg border border-border bg-bgLight px-2.5 py-2 text-[12px] leading-relaxed text-textMain">
    {payload.view.text || ''}
  </div>
);

export const WindowCardWindow: React.FC<WindowCardWindowProps> = ({ payload, onClose }) => {
  const { t } = useTranslation();
  const [showRaw, setShowRaw] = useState(false);
  const kind = payload.view?.kind || 'raw';

  return (
    <div className="h-full min-h-0 flex flex-col" data-testid="window-card-window">
      <div className="shrink-0 border-b border-border px-4 py-3 flex items-center gap-2">
        <span className="min-w-0 flex-1 truncate text-sm font-bold text-textMain">{payload.title}</span>
        {payload.sender?.agent_name || payload.sender?.agent_id ? (
          <span className="shrink-0 text-[11px] text-textMuted">
            @{payload.sender?.agent_name || payload.sender?.agent_id}
          </span>
        ) : null}
        <button
          type="button"
          onClick={() => setShowRaw((v) => !v)}
          className="shrink-0 rounded-lg p-1 text-textMuted hover:bg-primary/10 hover:text-textMain"
          title={t('windowCard.raw')}
          aria-label={t('windowCard.raw')}
          data-testid="window-card-raw-toggle"
        >
          <Code2 size={13} />
        </button>
        <button
          type="button"
          onClick={onClose}
          className="shrink-0 rounded-lg p-1 text-textMuted hover:bg-primary/10 hover:text-textMain"
          aria-label={t('common.close')}
        >
          <X size={14} />
        </button>
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto px-4 py-3">
        {payload.summary ? (
          <div className="mb-3 whitespace-pre-wrap break-words text-[12px] leading-relaxed text-textMuted">
            {payload.summary}
          </div>
        ) : null}

        {kind === 'table' ? <Table payload={payload} /> : null}
        {kind === 'flow' ? <Flow payload={payload} /> : null}
        {kind === 'metrics' ? <Metrics payload={payload} /> : null}
        {kind === 'sections' ? <Sections payload={payload} /> : null}
        {kind === 'raw' ? <Raw payload={payload} /> : null}
        {!['table', 'flow', 'metrics', 'sections', 'raw'].includes(kind) ? <Raw payload={payload} /> : null}

        {(payload.actions || []).length ? (
          <div className="mt-3 flex flex-wrap gap-2">
            {(payload.actions || []).map((a) => (
              <button
                key={a.id}
                type="button"
                onClick={() => runWindowCardAction(payload, a)}
                className="rounded-lg border border-border px-2.5 py-1 text-[12px] text-textMain hover:bg-primary/10"
              >
                {a.label}
              </button>
            ))}
          </div>
        ) : null}

        {payload.source ? (
          <div className="mt-3 text-[10px] text-textMuted">{t('windowCard.source')}: {payload.source}</div>
        ) : null}

        {showRaw ? (
          <pre
            data-testid="window-card-raw"
            className="mt-3 max-h-64 overflow-auto rounded-lg border border-border bg-bgLight p-2 text-[11px] text-textMuted"
          >
            {JSON.stringify(payload, null, 2)}
          </pre>
        ) : null}
      </div>
    </div>
  );
};

export const WindowCardCollapseHint: React.FC<{ label: string }> = ({ label }) => (
  <span className="inline-flex items-center gap-1 text-[11px] text-textMuted">
    <ChevronRight size={11} />
    {label}
    <ChevronDown size={11} />
  </span>
);

export default WindowCardWindow;
