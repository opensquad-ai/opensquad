/**
 * A user turn the user never typed: an embedded form's submission, a reminder
 * the agent injected, or group/DM traffic merged into the conversation.
 *
 * Rendered instead of the wire text (see `parseMachineUserMessage`), in two
 * sizes:
 *   - `bubble` — top-level user row (idle send).
 *   - `inline` — inside the running turn's tool fold (`user_steer` event), where
 *     it must stay one compact line so the fold's virtualization is unaffected.
 */
import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Bell, CheckCircle2, MessageSquare, MessagesSquare } from 'lucide-react';

import {
  machineNoticeDetail,
  machineNoticeHasDetail,
  machineNoticeLabelKey,
  machineNoticeSummary,
  type MachineNotice,
} from '../../utils/machineUserMessage';

const ICON: Record<string, React.ComponentType<{ size?: number; className?: string }>> = {
  submitted: CheckCircle2,
  reminder: Bell,
  group: MessagesSquare,
  dm: MessageSquare,
};

/** Tone per notice kind: submitted = done, reminder = the agent nudging itself,
 *  group/DM = someone else's words. */
const TONE: Record<string, string> = {
  submitted: 'border-emerald-500/30 bg-emerald-500/[0.07] text-emerald-700',
  reminder: 'border-amber-500/30 bg-amber-500/[0.07] text-amber-700',
  group: 'border-border/70 bg-bgLight/70 text-primary',
  dm: 'border-border/70 bg-bgLight/70 text-primary',
};

const FALLBACK: Record<string, string> = {
  submitted: '已提交',
  reminder: '提醒',
  group: '群消息',
  dm: '私聊消息',
};

interface MachineUserNoticeProps {
  notice: MachineNotice;
  variant?: 'bubble' | 'inline';
  className?: string;
}

export const MachineUserNotice: React.FC<MachineUserNoticeProps> = ({
  notice,
  variant = 'bubble',
  className = '',
}) => {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const labelKey = machineNoticeLabelKey(notice);
  const label = t(`aiChat.machineNotice.${labelKey}`, { defaultValue: FALLBACK[labelKey] });
  const summary = machineNoticeSummary(notice);
  const detail = machineNoticeDetail(notice);
  // The toggle only earns its place when expanding shows more than the summary.
  const hasDetail = machineNoticeHasDetail(notice) && detail.trim() !== summary.trim();
  const Icon = ICON[labelKey] || Bell;
  const inline = variant === 'inline';

  const toggle = hasDetail ? (
    <button
      type="button"
      data-machine-notice-toggle="1"
      onClick={(e) => {
        e.stopPropagation();
        setOpen((o) => !o);
      }}
      className={`flex-shrink-0 rounded px-1 text-[11px] text-textMuted hover:bg-primary/10 hover:text-textMain transition-colors ${
        inline ? 'opacity-70 hover:opacity-100' : ''
      }`}
      title={open ? t('aiChat.machineNotice.hide', { defaultValue: '收起' }) : t('aiChat.machineNotice.details', { defaultValue: '详情' })}
    >
      {open
        ? t('aiChat.machineNotice.hide', { defaultValue: '收起' })
        : t('aiChat.machineNotice.details', { defaultValue: '详情' })}
    </button>
  ) : null;

  const body = open && hasDetail ? (
    <pre
      data-machine-notice-body="1"
      className={`whitespace-pre-wrap break-words rounded-lg border border-border/50 bg-bgMain/50 px-2 py-1.5 font-mono text-[11px] leading-relaxed text-textMain/85 ${
        inline ? 'max-h-40 overflow-y-auto' : 'max-h-60 overflow-y-auto'
      }`}
    >
      {detail}
    </pre>
  ) : null;

  if (inline) {
    return (
      <div data-machine-notice={labelKey} className={`w-full flex items-start gap-1.5 ${className}`}>
        <Icon size={12} className="mt-[3px] flex-shrink-0 text-primary/70" />
        <span className="flex-shrink-0 text-[11px] text-primary/70">{label}</span>
        <span className="min-w-0 flex-1 truncate text-textMain/85">{summary}</span>
        {toggle}
        {open && <div className="w-full">{body}</div>}
      </div>
    );
  }

  return (
    <div
      data-machine-notice={labelKey}
      className={`flex flex-col gap-1.5 rounded-xl border px-3 py-2 ${TONE[labelKey] || TONE.group} ${className}`}
    >
      <div className="flex items-center gap-2 min-w-0">
        <Icon size={14} className="flex-shrink-0" />
        <span className="flex-shrink-0 text-[12px] font-medium">{label}</span>
        {summary && (
          <span className="min-w-0 flex-1 truncate text-[12px] text-textMain/80" title={summary}>
            {summary}
          </span>
        )}
        {toggle}
      </div>
      {body}
    </div>
  );
};
