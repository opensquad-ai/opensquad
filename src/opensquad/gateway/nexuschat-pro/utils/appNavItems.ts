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

/** Views that open as large SoftOverlay modals (not fullscreen page takeovers). */
export const APP_MODAL_VIEWS = new Set<string>([
  ...SETTINGS_APP_NAV_ITEMS.map((i) => i.view),
  'market',
  'collab-board',
]);

export function isAppModalView(view: string): boolean {
  if (APP_MODAL_VIEWS.has(view)) return true;
  // Dynamic plugin views use "pluginName:viewName"
  return view.includes(':');
}

export function navigateAppView(view: string): void {
  window.dispatchEvent(new CustomEvent('switchView', { detail: view }));
}
