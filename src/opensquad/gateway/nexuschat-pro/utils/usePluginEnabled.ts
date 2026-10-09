/**
 * Live enabled-state of every installed plugin, keyed by plugin `name`.
 *
 * The settings rail gates built-in entries on a plugin (see `AppNavItem.requiresPlugin`).
 * The Plugin Manager is the only place that flag changes, and the entry has to move
 * with it — the user turns a plugin off and its page leaves the rail in the same
 * breath. Both the gate and the toggle therefore read the same endpoint, and the
 * manager announces its writes so a mounted rail re-derives without a reload.
 */
import { useEffect, useState } from 'react';
import { pluginAPI } from '../services/api';

/** Fired after the plugin list changed (enable/disable/uninstall). */
export const PLUGINS_CHANGED_EVENT = 'opensquad:plugins-changed';

export function notifyPluginsChanged(): void {
  window.dispatchEvent(new Event(PLUGINS_CHANGED_EVENT));
}

/**
 * `null` until the first successful read: unknown is not "off". A failed read
 * leaves the last known map (or `null`) in place rather than reporting every
 * plugin as disabled, which would silently hide entries the user still has.
 */
export function usePluginEnabled(): Record<string, boolean> | null {
  const [enabled, setEnabled] = useState<Record<string, boolean> | null>(null);

  useEffect(() => {
    let mounted = true;
    const load = async () => {
      try {
        const { plugins } = await pluginAPI.getPlugins();
        if (!mounted) return;
        const map: Record<string, boolean> = {};
        for (const p of plugins || []) map[p.name] = !!p.enabled;
        setEnabled(map);
      } catch {
        /* keep whatever we already knew */
      }
    };
    void load();
    const onChange = () => void load();
    window.addEventListener(PLUGINS_CHANGED_EVENT, onChange);
    return () => {
      mounted = false;
      window.removeEventListener(PLUGINS_CHANGED_EVENT, onChange);
    };
  }, []);

  return enabled;
}
