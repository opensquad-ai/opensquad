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
 */
export interface PluginViewProps {
  /** Call this to navigate back to the Plugin Manager. */
  onBack: () => void;
}

/**
 * The adapter contract that every plugin UI module must satisfy.
 * Both `mount` and `unmount` must be named exports from the module.
 */
export interface PluginViewAdapter {
  mount(container: HTMLElement, props: PluginViewProps): void;
  unmount(container: HTMLElement): void;
}
