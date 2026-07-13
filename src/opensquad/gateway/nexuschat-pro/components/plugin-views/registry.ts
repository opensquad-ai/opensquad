/**
 * Plugin View Registry
 *
 * Maps contributed view keys ("pluginName:viewName") to lazily-loaded
 * framework-agnostic adapters ({ mount, unmount }).
 *
 * This design allows plugin views to be written in any frontend framework
 * (React, Vue, Svelte, vanilla JS, etc.) without the core app needing to know
 * anything about the plugin's internal technology.
 *
 * ===========================================================
 * To add a new plugin view:
 *   1. Create a directory: components/plugin-views/{plugin-name}/
 *   2. Implement your view and export `mount` + `unmount` from that file
 *   3. Add ONE entry to PLUGIN_VIEW_LOADERS below
 *   4. That's it — no other files need to change
 *
 * Adapter shape:
 *   mount(container: HTMLElement, props: PluginViewProps): void
 *   unmount(container: HTMLElement): void
 *
 * Framework examples:
 *
 *   React:
 *     import { createRoot } from 'react-dom/client';
 *     let root: ReturnType<typeof createRoot>;
 *     export const mount = (el, props) => {
 *       root = createRoot(el);
 *       root.render(<MyView {...props} />);
 *     };
 *     export const unmount = (_el) => root?.unmount();
 *
 *   Vue:
 *     import { createApp } from 'vue';
 *     let app: ReturnType<typeof createApp>;
 *     export const mount = (el, props) => {
 *       app = createApp(MyView, props);
 *       app.mount(el);
 *     };
 *     export const unmount = (_el) => app?.unmount();
 * ===========================================================
 */

export type PluginViewProps = {
  onBack: () => void;
  /** 当前语言代码，插件可据此做条件渲染（可选，向后兼容） */
  locale?: 'zh' | 'en';
  /** i18n 翻译函数（可选，向后兼容） */
  t?: (key: string, options?: Record<string, unknown>) => string;
};

export type PluginViewAdapter = {
  mount(container: HTMLElement, props: PluginViewProps): void;
  unmount(container: HTMLElement): void;
};

/**
 * Map of view key → dynamic import function.
 * Each function must resolve to a module exporting `mount` and `unmount`.
 */
const PLUGIN_VIEW_LOADERS: Record<string, () => Promise<PluginViewAdapter>> = {
  "vcs_remote:audit": async () => {
    const { VCSAuditTimeline } = await import("../VCSAuditTimeline");
    const { createRoot } = await import("react-dom/client");
    const React = await import("react");
    let root: any = null;
    return {
      mount: (el, props) => {
        root = createRoot(el);
        root.render(React.createElement(VCSAuditTimeline, props));
      },
      unmount: () => {
        if (root) {
          root.unmount();
          root = null;
        }
      }
    };
  },
  "long_memory:panel": async () => {
    const { mount, unmount } = await import("./long-memory/LongMemoryPanel");
    return { mount, unmount };
  },
  "self_learn:panel": async () => {
    const { mount, unmount } = await import("./self-learn/SelfLearnPanel");
    return { mount, unmount };
  },
};

// Adapter cache — avoids re-importing on every mount
const _adapterCache: Record<string, PluginViewAdapter> = {};

/**
 * Build a GenericPluginView adapter for plugins that have no custom ui/index.js.
 * Renders the built-in collapsible JSON dashboard backed by the plugin's data endpoint.
 */
async function buildGenericAdapter(pluginName: string, viewTitle: string): Promise<PluginViewAdapter> {
  const { GenericPluginView } = await import("./GenericPluginView");
  const { createRoot } = await import("react-dom/client");
  const React = await import("react");
  const roots = new WeakMap<HTMLElement, any>();
  return {
    mount(el, props) {
      const root = createRoot(el);
      roots.set(el, root);
      root.render(
        React.createElement(GenericPluginView, {
          pluginName,
          viewTitle,
          onBack: props.onBack,
        })
      );
    },
    unmount(el) {
      const root = roots.get(el);
      if (root) {
        root.unmount();
        roots.delete(el);
      }
    },
  };
}

/**
 * Asynchronously loads and returns the PluginViewAdapter for the given view
 * key, or null if no adapter is registered.
 * The result is cached so the dynamic import runs at most once per key.
 *
 * Load order:
 *   1. Hardcoded PLUGIN_VIEW_LOADERS (built-in plugin views)
 *   2. Remote ESM from /api/plugins/static/{pluginName}/ui/index.js
 *   3. GenericPluginView fallback (for plugins with no custom UI but with
 *      a data endpoint — they get a free JSON dashboard automatically)
 */
export async function getPluginViewAdapter(
  viewKey: string
): Promise<PluginViewAdapter | null> {
  const loader = PLUGIN_VIEW_LOADERS[viewKey];

  // 1. Check internal hardcoded loaders first
  if (loader) {
    if (!_adapterCache[viewKey]) {
      _adapterCache[viewKey] = await loader();
    }
    return _adapterCache[viewKey];
  }

  // 2. Fallback to Dynamic Remote Loading (Atomic Plugins)
  // Format: "pluginName:viewName" -> load from /api/plugins/static/pluginName/ui/index.js
  if (viewKey.includes(':')) {
    const [pluginName, viewName] = viewKey.split(':');

    if (!_adapterCache[viewKey]) {
      const entryUrl = `/api/plugins/static/${pluginName}/ui/index.js`;
      try {
        console.log(`[PluginRegistry] Attempting to dynamic import remote plugin: ${viewKey} from ${entryUrl}`);
        // Use native ESM import for remote JS
        // @ts-ignore
        const module = await import(/* @vite-ignore */ entryUrl);
        if (module && typeof module.mount === 'function') {
          _adapterCache[viewKey] = module as PluginViewAdapter;
        } else {
          console.warn(`[PluginRegistry] Remote module at ${entryUrl} does not export mount() — falling back to GenericPluginView`);
          _adapterCache[viewKey] = await buildGenericAdapter(pluginName, viewName);
        }
      } catch (err) {
        console.warn(`[PluginRegistry] Failed to load remote plugin view ${viewKey} — falling back to GenericPluginView:`, err);
        _adapterCache[viewKey] = await buildGenericAdapter(pluginName, viewName);
      }
    }
    return _adapterCache[viewKey];
  }

  return null;
}

/**
 * Synchronous check — true if a custom adapter is registered for viewKey.
 * Note: For dynamic plugins, we can't know synchronously if the file exists,
 * so we assume true if it follows the "plugin:view" format and let the async
 * loader handle the rest.
 */
export function hasPluginViewAdapter(viewKey: string): boolean {
  return viewKey in PLUGIN_VIEW_LOADERS || viewKey.includes(':');
}
