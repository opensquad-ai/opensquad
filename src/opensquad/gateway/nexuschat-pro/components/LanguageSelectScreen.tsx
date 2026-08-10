import React, { useState } from 'react';
import { Globe, ChevronRight } from 'lucide-react';
import { OpenSquadLoader } from './OpenSquadLoader';

interface LanguageSelectScreenProps {
  onSelect: (lang: 'zh' | 'en') => void | Promise<void>;
}

/**
 * First-launch language picker.
 *
 * Shown only on initial visit (when localStorage has no `opensquad_lang`).
 * After the user picks a language, App.tsx queries /auth/registration-status
 * to decide whether to show the registration form (no web account yet) or
 * the login form.
 */
export const LanguageSelectScreen: React.FC<LanguageSelectScreenProps> = ({ onSelect }) => {
  const [picking, setPicking] = useState<'zh' | 'en' | null>(null);

  const handlePick = async (lang: 'zh' | 'en') => {
    if (picking) return;
    setPicking(lang);
    try {
      await onSelect(lang);
    } catch (e) {
      // The caller surfaces its own error UI. Re-enable picking so the user
      // can retry if the next step fails for any reason.
      setPicking(null);
      throw e;
    }
  };

  return (
    <div className="h-full w-full bg-bgLight flex items-center justify-center font-sans overflow-hidden relative">
      <div className="bg-panel p-6 sm:p-10 rounded-2xl shadow-xl w-full max-w-md mx-4 border border-border animate-in fade-in slide-in-from-bottom-4 duration-500">
        <div className="flex flex-col items-center mb-6">
          <img
            src="/logo.svg"
            alt="OpenSquad"
            className="w-14 h-14 mb-4 drop-shadow-lg"
          />
          <div className="flex items-center gap-2 text-textMuted text-xs uppercase tracking-widest font-semibold mb-2">
            <Globe size={14} />
            <span>OpenSquad</span>
          </div>
        </div>

        <div className="space-y-3">
          <button
            type="button"
            onClick={() => handlePick('zh')}
            disabled={picking !== null}
            data-testid="lang-pick-zh"
            className="w-full flex items-center justify-between gap-3 p-4 bg-bgLight border border-border rounded-xl hover:border-primary hover:bg-primary/5 transition-all group disabled:opacity-60 disabled:cursor-not-allowed"
          >
            <div className="flex items-center gap-3">
              <span className="text-2xl" aria-hidden>中</span>
              <div className="text-left">
                <div className="text-base font-semibold text-textMain">中文（简体）</div>
                <div className="text-xs text-textMuted">Simplified Chinese</div>
              </div>
            </div>
            {picking === 'zh' ? (
              <OpenSquadLoader size={20} label="正在选择语言" />
            ) : (
              <ChevronRight className="text-textMuted group-hover:text-primary transition-colors" size={20} />
            )}
          </button>

          <button
            type="button"
            onClick={() => handlePick('en')}
            disabled={picking !== null}
            data-testid="lang-pick-en"
            className="w-full flex items-center justify-between gap-3 p-4 bg-bgLight border border-border rounded-xl hover:border-primary hover:bg-primary/5 transition-all group disabled:opacity-60 disabled:cursor-not-allowed"
          >
            <div className="flex items-center gap-3">
              <span className="text-2xl font-bold" aria-hidden>EN</span>
              <div className="text-left">
                <div className="text-base font-semibold text-textMain">English</div>
                <div className="text-xs text-textMuted">英语</div>
              </div>
            </div>
            {picking === 'en' ? (
              <OpenSquadLoader size={20} label="正在选择语言" />
            ) : (
              <ChevronRight className="text-textMuted group-hover:text-primary transition-colors" size={20} />
            )}
          </button>
        </div>
      </div>
    </div>
  );
};
