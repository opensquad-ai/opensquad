/**
 * App navigation entries shown inside System Config (settings) left rail.
 * Business switches (chat / agent manager / collab) stay outside settings.
 */
import type { LucideIcon } from 'lucide-react';
import {
  BookOpen,
  Cpu,
  Package,
  Puzzle,
  Radio,
  ScrollText,
  Server,
  UserCircle,
} from 'lucide-react';

export type AppNavView =
  | 'plugins'
  | 'mods'
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
  /** Plugin `name` (plugin.json) this entry belongs to: it is shown only while
   *  that plugin is installed and enabled. */
  requiresPlugin?: string;
};

/** Static entries migrated from the former global Sidebar (settings-only). */
export const SETTINGS_APP_NAV_ITEMS: AppNavItem[] = [
  { view: 'plugins', i18nKey: 'nav.plugins', icon: Puzzle },
  // Mods *is* the mods_host plugin's surface: with the plugin off there is no
  // host behind the page, so the entry follows the flag that turns it off.
  { view: 'mods', i18nKey: 'nav.mods', icon: Package, requiresPlugin: 'mods_host' },
  { view: 'services', i18nKey: 'nav.services', icon: Radio },
  { view: 'mcp', i18nKey: 'nav.mcp', icon: Server },
  { view: 'skills', i18nKey: 'nav.skills', icon: BookOpen },
  { view: 'roles', i18nKey: 'nav.roles', icon: UserCircle },
  { view: 'models', i18nKey: 'nav.models', icon: Cpu },
  { view: 'logs', i18nKey: 'nav.logs', icon: ScrollText },
];

/**
 * Drop rail entries whose gating plugin is known to be gone or disabled.
 *
 * `pluginEnabled === null` means "not known yet" — the launcher has not
 * answered, or answered with an error — and keeps every entry: a transient
 * failure must not make a working page unreachable. Once the plugin list is
 * known, an entry is kept only when its plugin is there *and* enabled, so a
 * plugin that was disabled (or uninstalled) takes its entry with it.
 */
export function visibleAppNavItems(
  items: AppNavItem[],
  pluginEnabled: Record<string, boolean> | null,
): AppNavItem[] {
  if (!pluginEnabled) return items;
  return items.filter((item) => !item.requiresPlugin || pluginEnabled[item.requiresPlugin] === true);
}

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
