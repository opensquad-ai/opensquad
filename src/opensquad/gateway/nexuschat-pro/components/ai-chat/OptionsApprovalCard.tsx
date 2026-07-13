/**
 * OptionsApprovalCard — N-way single-choice card for agent-proposed options.
 *
 * The agent calls `propose_options` when it has several viable plans and wants
 * the user to pick one. This card renders an inline radio list (title +
 * description), highlights the selected option with an amber border, and
 * offers 忽略 / 提交 actions. An optional "输入自己的答案" free-form input
 * is shown when `allow_custom` is true.
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
  status: 'pending' | 'chosen' | 'ignored' | 'custom';
  chosen_option_id?: string;
  custom_answer?: string;
}

interface OptionsApprovalCardProps {
  proposal: OptionsProposal;
  onSubmit: (id: string, chosenOptionId: string) => void;
  onCustom: (id: string, customAnswer: string) => void;
  onIgnore: (id: string) => void;
}

export const OptionsApprovalCard: React.FC<OptionsApprovalCardProps> = ({
  proposal,
  onSubmit,
  onCustom,
  onIgnore,
}) => {
  const pending = proposal.status === 'pending';
  const [selectedId, setSelectedId] = useState<string>(
    proposal.chosen_option_id || proposal.options[0]?.id || '',
  );
  const [customText, setCustomText] = useState('');
  const [customMode, setCustomMode] = useState(false);

  const allowCustom = proposal.allow_custom !== false;
  const resolved = !pending;
  const chosenId = proposal.chosen_option_id;
  const resolvedCustom = proposal.custom_answer;

  const handleSubmit = () => {
    if (customMode) {
      const text = customText.trim();
      if (!text) return;
      onCustom(proposal.id, text);
      return;
    }
    if (!selectedId) return;
    onSubmit(proposal.id, selectedId);
  };

  return (
    <div className="my-3 mx-1 rounded-xl border border-border bg-panel px-3.5 py-3 shadow-sm">
      {/* Header */}
      <div className="flex items-start justify-between gap-2 mb-1">
        <div className="text-[12px] font-semibold text-textMain leading-snug">
          {proposal.prompt || '请选择一个选项：'}
        </div>
        <span className="text-[10px] text-textMuted shrink-0 mt-0.5">
          {proposal.options.length} 个选项
        </span>
      </div>
      <div className="text-[11px] text-textMuted mb-3">{pending ? '选择一个答案' : '已处理'}</div>

      {/* Options list */}
      <div className="flex flex-col gap-2">
        {proposal.options.map((opt) => {
          const isSelected = pending && !customMode && opt.id === selectedId;
          const isResolvedChoice = resolved && chosenId === opt.id;
          return (
            <button
              key={opt.id}
              type="button"
              role="option"
              aria-selected={isSelected || isResolvedChoice}
              disabled={resolved}
              onClick={() => {
                if (resolved) return;
                setCustomMode(false);
                setSelectedId(opt.id);
              }}
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
                  className={`mt-0.5 shrink-0 w-4 h-4 rounded-full border-2 flex items-center justify-center transition-colors ${
                    isResolvedChoice
                      ? 'border-primary'
                      : isSelected
                        ? 'border-amber-400'
                        : 'border-textMuted/50'
                  }`}
                >
                  {(isSelected || isResolvedChoice) && (
                    <span
                      className={`w-2 h-2 rounded-full ${
                        isResolvedChoice ? 'bg-primary' : 'bg-amber-400'
                      }`}
                    />
                  )}
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

        {/* Custom answer option */}
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
                setSelectedId('');
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

      {/* Footer actions */}
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
              disabled={customMode && !customText.trim()}
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
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
};
