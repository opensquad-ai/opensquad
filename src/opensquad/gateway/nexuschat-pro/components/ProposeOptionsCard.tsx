/**
 * ProposeOptionsCard — N-way single-choice card posted in group chat.
 * Marker: [[PROPOSE_OPTIONS]]{json}[[/PROPOSE_OPTIONS]]
 *
 * Distinct from CollabStepApprovalCard (which is Approve/Reject). This card
 * lets the user pick one of several agent-proposed options, or type a custom
 * answer, or ignore.
 */
import React, { useState } from 'react';
import { Check, Minus, HelpCircle } from 'lucide-react';

export interface ProposedOptionPayload {
  id: string;
  title: string;
  description?: string;
}

export interface ProposeOptionsPayload {
  v?: number;
  id: string;
  prompt: string;
  options: ProposedOptionPayload[];
  allow_custom?: boolean;
  status: 'pending' | 'chosen' | 'ignored' | 'custom' | string;
  chosen_option_id?: string;
  custom_answer?: string;
  resolve_note?: string;
  group_id?: string;
  message_id?: string;
}

const MARKER_RE = /\[\[PROPOSE_OPTIONS\]\]\s*(\{[\s\S]*?\})\s*\[\[\/PROPOSE_OPTIONS\]\]/;

export function parseProposeOptions(content: string): ProposeOptionsPayload | null {
  if (!content || !content.includes('[[PROPOSE_OPTIONS]]')) {
    return null;
  }
  const m = content.match(MARKER_RE);
  if (!m) return null;
  try {
    const data = JSON.parse(m[1]);
    if (!data || typeof data !== 'object' || !data.id || !Array.isArray(data.options)) return null;
    return data as ProposeOptionsPayload;
  } catch {
    return null;
  }
}

export function stripProposeOptionsMarker(content: string): string {
  if (!content) return content;
  return content
    .replace(MARKER_RE, '')
    .replace(/^❓\s*选择一个选项：.*$/gm, '')
    .replace(/^\d+\.\s.*$/gm, '')
    .replace(/^请在下方卡片中选择.*$/gm, '')
    .trim();
}

interface ProposeOptionsCardProps {
  payload: ProposeOptionsPayload;
  groupId: string;
  messageId: string;
  onResolve: (action: 'choose' | 'custom' | 'ignore', value: string) => Promise<void>;
  disabled?: boolean;
}

export const ProposeOptionsCard: React.FC<ProposeOptionsCardProps> = ({
  payload,
  onResolve,
  disabled,
}) => {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string>(payload.options[0]?.id || '');
  const [customMode, setCustomMode] = useState(false);
  const [customText, setCustomText] = useState('');

  const pending = (payload.status || 'pending') === 'pending';
  const allowCustom = payload.allow_custom !== false;
  const resolved = !pending;
  const chosenId = payload.chosen_option_id;
  const resolvedCustom = payload.custom_answer;

  const handle = async (action: 'choose' | 'custom' | 'ignore', value: string) => {
    if (!pending || busy || disabled) return;
    setBusy(true);
    setError(null);
    try {
      await onResolve(action, value);
    } catch (e: any) {
      setError(e?.message || String(e) || '操作失败');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="my-1 rounded-xl border border-border bg-panel px-3.5 py-3 shadow-sm min-w-[240px] max-w-[380px]">
      <div className="flex items-center gap-1.5 text-[12px] font-semibold text-textMain mb-1">
        <HelpCircle size={14} className="text-primary shrink-0" />
        <span>{payload.prompt || '请选择一个选项'}</span>
      </div>
      <div className="text-[11px] text-textMuted mb-2">{pending ? '选择一个答案' : '已处理'}</div>

      <div className="flex flex-col gap-2 mb-3">
        {payload.options.map((opt) => {
          const isSelected = pending && !customMode && opt.id === selectedId;
          const isResolvedChoice = resolved && chosenId === opt.id;
          return (
            <button
              key={opt.id}
              type="button"
              disabled={resolved || busy || disabled}
              onClick={() => {
                setCustomMode(false);
                setSelectedId(opt.id);
              }}
              className={`w-full text-left rounded-lg border px-2.5 py-2 transition-colors ${
                isResolvedChoice
                  ? 'border-primary bg-primary/10'
                  : isSelected
                    ? 'border-amber-400 bg-amber-500/10'
                    : 'border-border bg-transparent hover:bg-black/[0.03] dark:hover:bg-white/[0.04]'
              } ${resolved ? 'cursor-default opacity-80' : 'cursor-pointer'}`}
            >
              <div className="flex items-start gap-2">
                <span
                  className={`mt-0.5 shrink-0 w-3.5 h-3.5 rounded-full border-2 flex items-center justify-center ${
                    isResolvedChoice ? 'border-primary' : isSelected ? 'border-amber-400' : 'border-textMuted/50'
                  }`}
                >
                  {(isSelected || isResolvedChoice) && (
                    <span className={`w-1.5 h-1.5 rounded-full ${isResolvedChoice ? 'bg-primary' : 'bg-amber-400'}`} />
                  )}
                </span>
                <div className="flex-1 min-w-0">
                  <div className="text-[12px] font-medium text-textMain leading-snug">{opt.title}</div>
                  {opt.description && (
                    <div className="text-[10px] text-textMuted mt-0.5 leading-relaxed">{opt.description}</div>
                  )}
                </div>
              </div>
            </button>
          );
        })}

        {allowCustom && (
          <div
            className={`rounded-lg border px-2.5 py-2 transition-colors ${
              resolved
                ? resolvedCustom
                  ? 'border-primary bg-primary/10'
                  : 'border-border opacity-80'
                : customMode
                  ? 'border-amber-400 bg-amber-500/10'
                  : 'border-border bg-transparent hover:bg-black/[0.03] dark:hover:bg-white/[0.04]'
            }`}
          >
            <button
              type="button"
              disabled={resolved || busy || disabled}
              onClick={() => {
                setCustomMode(true);
                setSelectedId('');
              }}
              className="w-full text-left flex items-start gap-2"
            >
              <span
                className={`mt-0.5 shrink-0 w-3.5 h-3.5 rounded-full border-2 flex items-center justify-center ${
                  resolved && resolvedCustom ? 'border-primary' : customMode ? 'border-amber-400' : 'border-textMuted/50'
                }`}
              >
                {(customMode || (resolved && resolvedCustom)) && (
                  <span className={`w-1.5 h-1.5 rounded-full ${resolved && resolvedCustom ? 'bg-primary' : 'bg-amber-400'}`} />
                )}
              </span>
              <div className="flex-1 min-w-0">
                <div className="text-[12px] font-medium text-textMain leading-snug">输入自己的答案</div>
              </div>
            </button>
            {(customMode || (resolved && resolvedCustom)) && (
              <input
                type="text"
                value={pending ? customText : resolvedCustom || ''}
                disabled={resolved}
                onChange={(e) => setCustomText(e.target.value)}
                placeholder="输入你的答案..."
                className="mt-1.5 w-full text-[11px] rounded-md border border-border bg-bgLight/60 dark:bg-black/20 px-2 py-1 text-textMain outline-none focus:border-amber-400 disabled:opacity-70"
              />
            )}
          </div>
        )}
      </div>

      {pending ? (
        <div className="flex items-center justify-between">
          <button
            type="button"
            disabled={busy || disabled}
            onClick={() => handle('ignore', '')}
            className="text-[11px] text-textMuted hover:text-textMain transition-colors border-0 bg-transparent cursor-pointer px-1 disabled:opacity-50"
          >
            忽略
          </button>
          <button
            type="button"
            disabled={busy || disabled || (customMode && !customText.trim())}
            onClick={() => {
              if (customMode) {
                const text = customText.trim();
                if (text) handle('custom', text);
              } else if (selectedId) {
                handle('choose', selectedId);
              }
            }}
            className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg text-[12px] font-medium bg-black text-white hover:opacity-90 border-0 cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed dark:bg-white dark:text-black"
          >
            提交
          </button>
        </div>
      ) : (
        <div className="flex items-center gap-1.5 text-[11px] text-textMuted">
          {payload.status === 'ignored' ? (
            <>
              <Minus size={12} /> 已忽略
            </>
          ) : resolvedCustom ? (
            <>
              <Check size={12} /> 自定义：{resolvedCustom}
            </>
          ) : (
            <>
              <Check size={12} /> 已选择
            </>
          )}
          {payload.resolve_note ? ` — ${payload.resolve_note}` : ''}
        </div>
      )}
      {error ? <div className="mt-2 text-[11px] text-red-500">{error}</div> : null}
    </div>
  );
};
