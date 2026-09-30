/**
 * Work / Code / 聊天 三段切换。
 *
 * Extracted so the chat-mode rail can offer the same switch without mounting
 * the Work/Code session sidebar (which owns a different body entirely).
 */
import React from 'react';
import { useTranslation } from 'react-i18next';
import { BookOpen, Code2, MessageCircle } from 'lucide-react';

export type UiMode = 'classic' | 'solo' | 'chat';

interface UiModeSwitchProps {
  uiMode: UiMode;
  onUiModeChange?: (mode: UiMode) => void;
}

const MODES: Array<{ id: UiMode; icon: React.ReactNode; labelKey: string; hintKey: string }> = [
  {
    id: 'classic',
    icon: <BookOpen size={13} strokeWidth={1.75} className="shrink-0 opacity-80" />,
    labelKey: 'aiChat.uiModeClassic',
    hintKey: 'aiChat.uiModeClassicHint',
  },
  {
    id: 'solo',
    icon: <Code2 size={13} strokeWidth={1.75} className="shrink-0 opacity-80" />,
    labelKey: 'aiChat.uiModeSolo',
    hintKey: 'aiChat.uiModeSoloHint',
  },
  {
    id: 'chat',
    icon: <MessageCircle size={13} strokeWidth={1.75} className="shrink-0 opacity-80" />,
    labelKey: 'aiChat.uiModeChat',
    hintKey: 'aiChat.uiModeChatHint',
  },
];

export const UiModeSwitch: React.FC<UiModeSwitchProps> = ({ uiMode, onUiModeChange }) => {
  const { t } = useTranslation();
  return (
    <div
      className="flex min-w-0 flex-1 items-center rounded-xl bg-black/[0.055] p-[3px] dark:bg-white/[0.08]"
      role="tablist"
      aria-label={t('aiChat.uiModeLabel')}
    >
      {MODES.map((m) => (
        <button
          key={m.id}
          type="button"
          role="tab"
          aria-selected={uiMode === m.id}
          onClick={() => onUiModeChange?.(m.id)}
          title={t(m.hintKey)}
          className={`flex min-w-0 flex-1 items-center justify-center gap-1 rounded-[9px] px-1.5 py-[5px] text-[11px] font-medium transition-all duration-150 ${
            uiMode === m.id
              ? 'bg-white text-textMain shadow-[0_1px_2px_rgba(0,0,0,0.08)] dark:bg-panel dark:shadow-[0_1px_2px_rgba(0,0,0,0.35)]'
              : 'text-textMuted hover:text-textMain'
          }`}
        >
          {uiMode === m.id ? m.icon : null}
          <span className="truncate">{t(m.labelKey)}</span>
        </button>
      ))}
    </div>
  );
};

export default UiModeSwitch;
