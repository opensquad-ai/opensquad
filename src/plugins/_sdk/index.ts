/**
 * @opensquad/plugin-sdk
 *
 * Canonical type definitions for OpenSquad plugin UI adapters.
 *
 * Every plugin UI must export two functions:
 *
 *   export function mount(container: HTMLElement, props: PluginViewProps): void
 *   export function unmount(container: HTMLElement): void
 *
 * The framework (PluginViewContainer.tsx) calls:
 *   adapter.mount(el, { onBack: () => onBackRef.current() })
 *   adapter.unmount(el)
 *
 * Implementation requirements:
 *   1. Accept `props` typed as PluginViewProps (not `any`)
 *   2. Pass `props.onBack` into the root component
 *   3. Show a back button in the UI header that calls `onBack`
 *   4. Use WeakMap<HTMLElement, Root> (NOT module-level singletons) for root management
 */

/**
 * Props passed by the framework to every plugin view component.
 *
 * i18n 字段（locale/t）为可选，便于插件按需接入中英双语。
 * 框架在用户切换导航栏语言时会重新 mount 插件，保证文案即时刷新。
 */
export interface PluginViewProps {
  /** Call this to navigate back to the Plugin Manager. */
  onBack: () => void;
  /** 当前语言代码，例如 'zh' | 'en'。插件可据此做条件渲染。 */
  locale?: 'zh' | 'en';
  /** i18n 翻译函数，key 使用主应用 locales 命名空间（如 'tokenAnalytics.title'）。 */
  t?: (key: string, options?: Record<string, unknown>) => string;
}

/**
 * The adapter contract that every plugin UI module must satisfy.
 * Both `mount` and `unmount` must be named exports from the module.
 */
export interface PluginViewAdapter {
  mount(container: HTMLElement, props: PluginViewProps): void;
  unmount(container: HTMLElement): void;
}
