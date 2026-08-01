/**
 * Load plugin nav shortcuts that users enabled for the old sidebar —
 * now shown under Settings → 应用.
 */
import { useEffect, useState } from 'react';
import { pluginAPI } from '../services/api';
import { hasPluginViewAdapter } from '../components/plugin-views/registry';

export type PluginNavItem = {
  name: string;
  label: string;
  view: string;
  iconType?: 'lucide' | 'image' | 'initial';
  icon?: string;
  iconUrl?: string;
};

function getPluginNavEnabled(pluginName: string): boolean {
  try {
    return localStorage.getItem(`plugin_nav_enabled_${pluginName}`) === 'true';
  } catch {
    return false;
  }
}

export function usePluginNavItems(): PluginNavItem[] {
  const [items, setItems] = useState<PluginNavItem[]>([]);

  useEffect(() => {
    const load = async () => {
      try {
        const { plugins } = await pluginAPI.getPlugins();
        const navItems = plugins
          .filter((p) => getPluginNavEnabled(p.name))
          .flatMap((p): PluginNavItem[] => {
            const nav = p.contributes?.navigation;
            if (nav) {
              if (!hasPluginViewAdapter(nav.view)) return [];
              return [
                {
                  name: p.name,
                  label: nav.label || p.display_name || p.name,
                  view: nav.view,
                  iconType: nav.iconType || ('initial' as const),
                  icon: nav.icon || '',
                  iconUrl: nav.iconUrl,
                },
              ];
            }
            const views = p.contributes?.views;
            if (!views || views.length === 0) return [];
            const firstView = views[0];
            return [
              {
                name: p.name,
                label: p.display_name || p.name,
                view: `${p.name}:${firstView.name}`,
                iconType: 'initial' as const,
              },
            ];
          });
        setItems(navItems);
      } catch (err) {
        console.error('[usePluginNavItems] Failed to load plugin navigation:', err);
      }
    };
    void load();
    const onChange = () => void load();
    window.addEventListener('plugin-nav-changed', onChange);
    return () => window.removeEventListener('plugin-nav-changed', onChange);
  }, []);

  return items;
}
