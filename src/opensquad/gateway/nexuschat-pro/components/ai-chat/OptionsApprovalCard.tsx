/**
 * OptionsApprovalCard — N-way choice card for agent-proposed options.
 *
 * Supports single-select (radio) or multi-select (checkbox) when
 * `allow_multiple` is true. Amber highlight for pending selection;
 * 忽略 / 提交 footer. Optional free-form "输入自己的答案".
 */
import React, { useState } from 'react';
import { Check, Minus } from 'lucide-react';

export interface ProposedOption {
  id: string;
  title: string;
  description?: string;
}

export interface OptionsProposal {
  id: string;
  prompt: string;
  options: ProposedOption[];
  allow_custom?: boolean;
  allow_multiple?: boolean;
  status: 'pending' | 'chosen' | 'ignored' | 'custom';
  chosen_option_id?: string;
  chosen_option_ids?: string[];
  custom_answer?: string;
}

interface OptionsApprovalCardProps {
  proposal: OptionsProposal;
  onSubmit: (id: string, chosenOptionIds: string[]) => void;
  onCustom: (id: string, customAnswer: string) => void;
  onIgnore: (id: string) => void;
}

/** Rebuild pending propose_options cards from session events (survive refresh). */
export function hydrateOptionsProposalsFromEvents(events: any[] | undefined | null): OptionsProposal[] {
  const byId = new Map<string, OptionsProposal>();
  for (const evt of events || []) {
    if (!evt || typeof evt !== 'object') continue;
    const payload =
      evt.type === 'info'
        ? (evt.data && typeof evt.data === 'object' ? evt.data : evt.content)
        : evt.data && typeof evt.data === 'object' && evt.data.event
          ? evt.data
          : evt;
    if (!payload || typeof payload !== 'object') continue;
    const name = String((payload as any).event || '');
    if (name === 'propose_options') {
      const id = String((payload as any).id || '');
      const rawOpts = (payload as any).options || [];
      const options = Array.isArray(rawOpts)
        ? rawOpts
            .map((o: any) => ({
              id: String((o && o.id) || ''),
              title: String((o && (o.title || o.name || o.label)) || ''),
              description: String((o && (o.description || o.summary)) || '') || undefined,
            }))
            .filter((o: { id: string; title: string }) => o.id && o.title)
        : [];
      if (!id || options.length < 2) continue;
      byId.set(id, {
        id,
        prompt: String((payload as any).prompt || '请选择一个选项：'),
        options,
        allow_custom: (payload as any).allow_custom !== false,
        allow_multiple: !!(payload as any).allow_multiple,
        status: 'pending',
      });
    } else if (name === 'propose_options_resolved') {
      const id = String((payload as any).id || '');
      if (!id || !byId.has(id)) continue;
      const statusRaw = String((payload as any).status || 'chosen');
      const status: OptionsProposal['status'] =
        statusRaw === 'ignored' ? 'ignored' : statusRaw === 'custom' ? 'custom' : 'chosen';
      const chosenOptionId = String((payload as any).chosen_option_id || '');
      const rawIds = (payload as any).chosen_option_ids;
      const chosenOptionIds = Array.isArray(rawIds)
        ? rawIds.map((x: any) => String(x)).filter(Boolean)
        : chosenOptionId
          ? [chosenOptionId]
          : [];
      const prev = byId.get(id)!;
      byId.set(id, {
        ...prev,
        status,
        chosen_option_id: chosenOptionIds[0] || chosenOptionId,
        chosen_option_ids: chosenOptionIds,
        custom_answer: String((payload as any).custom_answer || ''),
      });
    }
  }
  return [...byId.values()].filter((p) => p.status === 'pending');
}

export const OptionsApprovalCard: React.FC<OptionsApprovalCardProps> = ({
  proposal,
  onSubmit,
  onCustom,
  onIgnore,
}) => {
  const pending = proposal.status === 'pending';
  const multi = !!proposal.allow_multiple;
  const initialIds =
    proposal.chosen_option_ids?.length
      ? proposal.chosen_option_ids
      : proposal.chosen_option_id
        ? [proposal.chosen_option_id]
        : multi
          ? []
          : proposal.options[0]?.id
            ? [proposal.options[0].id]
            : [];
  const [selectedIds, setSelectedIds] = useState<string[]>(initialIds);
  const [customText, setCustomText] = useState('');
  const [customMode, setCustomMode] = useState(false);

  const allowCustom = proposal.allow_custom !== false;
  const resolved = !pending;
  const resolvedIds =
    proposal.chosen_option_ids?.length
      ? proposal.chosen_option_ids
      : proposal.chosen_option_id
        ? [proposal.chosen_option_id]
        : [];
  const resolvedCustom = proposal.custom_answer;

  const toggleId = (id: string) => {
    if (resolved) return;
    setCustomMode(false);
    if (multi) {
      setSelectedIds((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));
    } else {
      setSelectedIds([id]);
    }
  };

  const handleSubmit = () => {
    if (customMode) {
      const text = customText.trim();
      if (!text) return;
      onCustom(proposal.id, text);
      return;
    }
    if (!selectedIds.length) return;
    onSubmit(proposal.id, selectedIds);
  };

  const markClass = multi ? 'rounded-sm' : 'rounded-full';
  const hint = pending
    ? multi
      ? '可多选，然后提交'
      : '选择一个答案'
    : '已处理';

  return (
    <div className="my-3 mx-1 rounded-xl border border-border bg-panel px-3.5 py-3 shadow-sm">
      <div className="flex items-start justify-between gap-2 mb-1">
        <div className="text-[12px] font-semibold text-textMain leading-snug">
          {proposal.prompt || (multi ? '请选择一个或多个选项：' : '请选择一个选项：')}
        </div>
        <span className="text-[10px] text-textMuted shrink-0 mt-0.5">
          {proposal.options.length} 个选项{multi ? ' · 多选' : ''}
        </span>
      </div>
      <div className="text-[11px] text-textMuted mb-3">{hint}</div>

      <div className="flex flex-col gap-2">
        {proposal.options.map((opt) => {
          const isSelected = pending && !customMode && selectedIds.includes(opt.id);
          const isResolvedChoice = resolved && resolvedIds.includes(opt.id);
          return (
            <button
              key={opt.id}
              type="button"
              role={multi ? 'checkbox' : 'option'}
              aria-checked={isSelected || isResolvedChoice}
              disabled={resolved}
              onClick={() => toggleId(opt.id)}
              className={`w-full text-left rounded-lg border px-3 py-2.5 transition-colors ${
                isResolvedChoice
                  ? 'border-primary bg-primary/10'
                  : isSelected
                    ? 'border-amber-400 bg-amber-500/10'
                    : 'border-border bg-transparent hover:bg-black/[0.03] dark:hover:bg-white/[0.04]'
              } ${resolved ? 'cursor-default opacity-80' : 'cursor-pointer'}`}
            >
              <div className="flex items-start gap-2.5">
                <span
                  className={`mt-0.5 shrink-0 w-4 h-4 border-2 flex items-center justify-center transition-colors ${markClass} ${
                    isResolvedChoice
                      ? 'border-primary'
                      : isSelected
                        ? 'border-amber-400'
                        : 'border-textMuted/50'
                  }`}
                >
                  {(isSelected || isResolvedChoice) &&
                    (multi ? (
                      <Check
                        size={10}
                        className={isResolvedChoice ? 'text-primary' : 'text-amber-500'}
                        strokeWidth={3}
                      />
                    ) : (
                      <span
                        className={`w-2 h-2 rounded-full ${
                          isResolvedChoice ? 'bg-primary' : 'bg-amber-400'
                        }`}
                      />
                    ))}
                </span>
                <div className="flex-1 min-w-0">
                  <div className="text-[13px] font-medium text-textMain leading-snug">{opt.title}</div>
                  {opt.description && (
                    <div className="text-[11px] text-textMuted mt-0.5 leading-relaxed">
                      {opt.description}
                    </div>
                  )}
                </div>
              </div>
            </button>
          );
        })}

        {allowCustom && (
          <div
            className={`rounded-lg border px-3 py-2.5 transition-colors ${
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
              disabled={resolved}
              onClick={() => {
                if (resolved) return;
                setCustomMode(true);
                setSelectedIds([]);
              }}
              className="w-full text-left flex items-start gap-2.5"
            >
              <span
                className={`mt-0.5 shrink-0 w-4 h-4 rounded-full border-2 flex items-center justify-center ${
                  resolved && resolvedCustom ? 'border-primary' : customMode ? 'border-amber-400' : 'border-textMuted/50'
                }`}
              >
                {(customMode || (resolved && resolvedCustom)) && (
                  <span className={`w-2 h-2 rounded-full ${resolved && resolvedCustom ? 'bg-primary' : 'bg-amber-400'}`} />
                )}
              </span>
              <div className="flex-1 min-w-0">
                <div className="text-[13px] font-medium text-textMain leading-snug">输入自己的答案</div>
              </div>
            </button>
            {(customMode || (resolved && resolvedCustom)) && (
              <input
                type="text"
                value={pending ? customText : resolvedCustom || ''}
                disabled={resolved}
                onChange={(e) => setCustomText(e.target.value)}
                placeholder="输入你的答案..."
                className="mt-2 w-full text-[12px] rounded-md border border-border bg-bgLight/60 dark:bg-black/20 px-2 py-1.5 text-textMain outline-none focus:border-amber-400 disabled:opacity-70"
              />
            )}
          </div>
        )}
      </div>

      <div className="flex items-center justify-between mt-3">
        {pending ? (
          <>
            <button
              type="button"
              onClick={() => onIgnore(proposal.id)}
              className="text-[12px] text-textMuted hover:text-textMain transition-colors border-0 bg-transparent cursor-pointer px-1 py-1"
            >
              忽略
            </button>
            <button
              type="button"
              onClick={handleSubmit}
              disabled={(customMode && !customText.trim()) || (!customMode && selectedIds.length === 0)}
              className="inline-flex items-center gap-1 px-3.5 py-1.5 rounded-lg text-[12px] font-medium bg-black text-white hover:opacity-90 border-0 cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed dark:bg-white dark:text-black"
            >
              提交
            </button>
          </>
        ) : (
          <div className="flex items-center gap-1.5 text-[11px] text-textMuted w-full justify-end">
            {proposal.status === 'ignored' ? (
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
                {resolvedIds.length > 1 ? `（${resolvedIds.length}）` : ''}
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
};
