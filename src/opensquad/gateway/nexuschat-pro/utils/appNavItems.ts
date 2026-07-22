/**
 * App navigation entries shown inside System Config (settings) left rail.
 * Business switches (chat / agent manager / collab) stay outside settings.
 */
import type { LucideIcon } from 'lucide-react';
import {
  BookOpen,
  Cpu,
  Puzzle,
  Radio,
  ScrollText,
  Server,
  UserCircle,
} from 'lucide-react';

export type AppNavView =
  | 'plugins'
  | 'services'
  | 'mcp'
  | 'skills'
  | 'roles'
  | 'models'
  | 'logs';

export type AppNavItem = {
  view: AppNavView;
  i18nKey: string;
  icon: LucideIcon;
};

/** Static entries migrated from the former global Sidebar (settings-only). */
export const SETTINGS_APP_NAV_ITEMS: AppNavItem[] = [
  { view: 'plugins', i18nKey: 'nav.plugins', icon: Puzzle },
  { view: 'services', i18nKey: 'nav.services', icon: Radio },
  { view: 'mcp', i18nKey: 'nav.mcp', icon: Server },
  { view: 'skills', i18nKey: 'nav.skills', icon: BookOpen },
  { view: 'roles', i18nKey: 'nav.roles', icon: UserCircle },
  { view: 'models', i18nKey: 'nav.models', icon: Cpu },
  { view: 'logs', i18nKey: 'nav.logs', icon: ScrollText },
];

/** App panels embedded inside the settings shell (not separate overlays). */
export function isSettingsAppView(view: string): boolean {
  if (view === 'collab-board' || view === 'chat' || view === 'admin' || view === 'ai-chat') {
    return false;
  }
  if (SETTINGS_APP_NAV_ITEMS.some((i) => i.view === view)) return true;
  if (view === 'market') return true;
  // Dynamic plugin views use "pluginName:viewName"
  return view.includes(':');
}

/** @deprecated use isSettingsAppView */
export function isAppModalView(view: string): boolean {
  return isSettingsAppView(view);
}

export function navigateAppView(view: string): void {
  window.dispatchEvent(new CustomEvent('switchView', { detail: view }));
}
