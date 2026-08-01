/**
 * Bottom account strip for session / chat side rails (Manus-style footer).
 * Left: avatar + name → profile. Right: optional action icons + settings.
 */
import React from 'react';
import { Settings } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { getAvatarUrl, getLocalAvatarFallback } from '../utils/image';

export type AccountUser = {
  id: string;
  name: string;
  avatar?: string | null;
} | null;

interface AccountRailFooterProps {
  currentUser: AccountUser;
  onOpenProfile: () => void;
  onOpenSettings: () => void;
  /** Extra icon buttons rendered before the settings gear. */
  actions?: React.ReactNode;
  /** Same-row extras between profile and action icons (e.g. agent nav shortcuts). */
  shortcuts?: React.ReactNode;
}

export const AccountRailFooter: React.FC<AccountRailFooterProps> = ({
  currentUser,
  onOpenProfile,
  onOpenSettings,
  actions,
  shortcuts,
}) => {
  const { t } = useTranslation();
  const name = currentUser?.name?.trim() || t('profile.displayName');
  const uid = currentUser?.id || 'guest';

  return (
    <div className="shrink-0 border-t border-border/60 p-2">
      <div className="flex items-center gap-1.5 rounded-xl bg-black/[0.04] px-2 py-1.5 dark:bg-white/[0.06]">
        <button
          type="button"
          onClick={onOpenProfile}
          className="flex min-w-0 flex-1 items-center gap-2 rounded-lg px-1 py-0.5 text-left hover:bg-primary/10"
          title={t('profile.editProfile')}
        >
          {currentUser ? (
            <img
              src={getAvatarUrl(currentUser.avatar ?? undefined, uid, name)}
              alt=""
              className="h-7 w-7 shrink-0 rounded-full object-cover bg-border"
              loading="lazy"
              onError={(e) => {
                const img = e.currentTarget;
                if (img.dataset.fallbackApplied) return;
                img.dataset.fallbackApplied = '1';
                img.src = getLocalAvatarFallback(uid, name);
              }}
            />
          ) : (
            <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-primary/15 text-[11px] font-semibold text-primary">
              ?
            </span>
          )}
          <span className="min-w-0 truncate text-[12px] font-medium text-textMain">{name}</span>
        </button>
        {shortcuts}
        <div className="flex shrink-0 items-center gap-0.5">
          {actions}
          <button
            type="button"
            onClick={onOpenSettings}
            className="rounded-lg p-1.5 text-textMuted hover:bg-primary/10 hover:text-textMain"
            title={t('nav.settings')}
            aria-label={t('nav.settings')}
          >
            <Settings size={16} strokeWidth={1.75} />
          </button>
        </div>
      </div>
    </div>
  );
};
