import React, { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { AlertCircle } from 'lucide-react';
import { pluginAPI } from '../../services/api';
import { getPluginViewAdapter, PluginViewAdapter } from './registry';
import { OpenSquadLoader } from '../OpenSquadLoader';

interface PluginViewContainerProps {
  viewKey: string;
  onBack: () => void;
}

/**
 * PluginViewContainer
 *
 * Two-effect lifecycle to correctly sequence async loading and DOM mounting:
 *
 *   Effect 1 (depends on viewKey):
 *     Async-loads the PluginViewAdapter and stores it in state.
 *     Shows a spinner while loading, error UI on failure.
 *
 *   Effect 2 (depends on adapter):
 *     Runs AFTER React commits the <div ref> to the DOM (loading = false).
 *     Calls adapter.mount(container, props).
 *     Cleanup calls adapter.unmount(container).
 *
 * This order is critical: the container <div> is only rendered once loading
 * is false, so Effect 1 cannot call mount() directly (containerRef would be
 * null at that point).
 */
export const PluginViewContainer: React.FC<PluginViewContainerProps> = ({
  viewKey,
  onBack,
}) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const { t, i18n } = useTranslation();
  // Keep latest onBack in a ref — changes don't re-trigger mount/unmount
  const onBackRef = useRef(onBack);
  useEffect(() => { onBackRef.current = onBack; }, [onBack]);

  const [adapter, setAdapter] = useState<PluginViewAdapter | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // --- Effect 1: load the adapter ---
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    setAdapter(null);

    getPluginViewAdapter(viewKey)
      .then(a => {
        if (cancelled) return;
        if (!a) {
          setError(`No adapter registered for view key: "${viewKey}"`);
        } else {
          setAdapter(a);
        }
        setLoading(false);
      })
      .catch((e: any) => {
        if (cancelled) return;
        const msg: string = e?.message ?? String(e);
        setError(msg);
        setLoading(false);
        const pluginName = viewKey.split(':')[0];
        pluginAPI
          .reportPluginViewError(pluginName, viewKey, msg, e?.stack ?? '')
          .catch((re: any) =>
            console.warn('[PluginViewContainer] Failed to report error:', re)
          );
      });

    return () => { cancelled = true; };
  }, [viewKey]);

  // --- Effect 2: mount when adapter is ready (container div is now in DOM) ---
  // 依赖 i18n.language：用户切换语言时重新 mount 插件，让插件用新语言渲染。
  // 远程 ESM 插件无法热替换字典，remount 是最简单可靠的方式。
  useEffect(() => {
    if (!adapter || !containerRef.current) return;
    const el = containerRef.current;
    const locale = (i18n.language === 'en' ? 'en' : 'zh') as 'zh' | 'en';
    try {
      adapter.mount(el, {
        onBack: () => onBackRef.current(),
        locale,
        t: (key: string, options?: Record<string, unknown>) =>
          String(t(key, options as any)),
      });
    } catch (e: any) {
      console.error('[PluginViewContainer] mount() threw:', e);
    }
    return () => {
      try { adapter.unmount(el); } catch (_) {}
    };
  }, [adapter, i18n.language, t]);

  if (loading) {
    return (
      <div className="flex flex-1 items-center justify-center h-full">
        <OpenSquadLoader size={36} />
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex-1 h-full bg-bgLight flex flex-col items-center justify-center p-8 gap-4">
        <AlertCircle className="text-red-400" size={48} />
        <p className="text-textMain font-medium">{t('pluginManager.viewLoadFailed')}</p>
        <p className="text-textMuted text-sm text-center max-w-md">
          {t('pluginManager.viewErrorMsg', { viewKey })}
        </p>
        <pre className="bg-panel text-red-400 text-xs p-4 rounded-lg max-w-full overflow-auto max-h-48">
          {error}
        </pre>
        <button
          onClick={onBack}
          className="mt-2 px-4 py-2 bg-primary text-white rounded-lg hover:opacity-90 transition-opacity"
        >
          {t('pluginManager.backToList')}
        </button>
      </div>
    );
  }

  // Container div — Effect 2 mounts the plugin view into this element
  return <div ref={containerRef} className="flex-1 h-full overflow-auto" />;
};
