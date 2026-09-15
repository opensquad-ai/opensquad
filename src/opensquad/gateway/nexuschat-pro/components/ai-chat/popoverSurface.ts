/** Opaque floating-menu fill. Avoid `bg-bgLight` — theme CSS vars can drop the paint. */
export const POPOVER_SURFACE_CLASS = 'os-popover-surface !bg-white dark:!bg-zinc-900';

import { useEffect, useState } from 'react';

/**
 * Keep a popover mounted for `ms` after `open` flips false so the
 * `.os-pop-menu-out` exit animation can play before unmount. Pair with:
 * `className={open ? 'os-pop-menu' : 'os-pop-menu-out'}`.
 */
export function usePopMenuMounted(open: boolean, ms = 130): boolean {
  const [mounted, setMounted] = useState(open);
  useEffect(() => {
    if (open) {
      setMounted(true);
      return;
    }
    const t = window.setTimeout(() => setMounted(false), ms);
    return () => window.clearTimeout(t);
  }, [open, ms]);
  return mounted;
}
