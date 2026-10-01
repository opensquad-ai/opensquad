/**
 * 版面切换：Work / Code 两段 + 一旁的「聊天」开关。
 *
 * 「聊天」不是第三个 Work/Code 面版 —— 它换掉整个 Web 版面（左侧变通讯录，
 * 右侧是聊天窗口），所以它是一个独立开关，而不是同一个 tablist 里的第三段。
 * 点回去即恢复原来的 Work/Code 会话列表版面。
 */
import React from 'react';
import { useTranslation } from 'react-i18next';
import { BookOpen, Code2, MessageCircle } from 'lucide-react';

export type UiMode = 'classic' | 'solo';

interface UiModeSwitchProps {
  uiMode: UiMode;
  onUiModeChange?: (mode: UiMode) => void;
  /** 聊天（通讯录）版面是否开启。 */
  chatUi: boolean;
  onChatUiChange?: (on: boolean) => void;
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
];

export const UiModeSwitch: React.FC<UiModeSwitchProps> = ({
  uiMode,
  onUiModeChange,
  chatUi,
  onChatUiChange,
}) => {
  const { t } = useTranslation();
  return (
    <div className="flex min-w-0 flex-1 items-center gap-1.5">
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
            aria-selected={!chatUi && uiMode === m.id}
            onClick={() => onUiModeChange?.(m.id)}
            title={t(m.hintKey)}
            className={`flex min-w-0 flex-1 items-center justify-center gap-1 rounded-[9px] px-1.5 py-[5px] text-[11px] font-medium transition-all duration-150 ${
              !chatUi && uiMode === m.id
                ? 'bg-white text-textMain shadow-[0_1px_2px_rgba(0,0,0,0.08)] dark:bg-panel dark:shadow-[0_1px_2px_rgba(0,0,0,0.35)]'
                : 'text-textMuted hover:text-textMain'
            }`}
          >
            {!chatUi && uiMode === m.id ? m.icon : null}
            <span className="truncate">{t(m.labelKey)}</span>
          </button>
        ))}
      </div>
      <button
        type="button"
        aria-pressed={chatUi}
        onClick={() => onChatUiChange?.(!chatUi)}
        title={t('aiChat.uiModeChatHint')}
        className={`flex shrink-0 items-center justify-center gap-1 rounded-xl px-2 py-[6px] text-[11px] font-medium transition-colors ${
          chatUi
            ? 'bg-primary/15 text-primary'
            : 'bg-black/[0.055] text-textMuted hover:text-textMain dark:bg-white/[0.08]'
        }`}
      >
        {chatUi ? <MessageCircle size={13} strokeWidth={1.75} className="shrink-0" /> : null}
        <span>{t('aiChat.uiModeChat')}</span>
      </button>
    </div>
  );
};

export default UiModeSwitch;
