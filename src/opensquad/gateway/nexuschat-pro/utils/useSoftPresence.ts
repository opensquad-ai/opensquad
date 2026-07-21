import { useEffect, useState } from 'react';

/**
 * Keep a node mounted through exit so width can ease to 0 (center grows in sync).
 * `visible` drives width/open class; `mounted` stays until the width transition ends.
 */
export function useSoftPresence(open: boolean, ms = 240): { mounted: boolean; visible: boolean } {
  const [mounted, setMounted] = useState(open);
  const [visible, setVisible] = useState(open);

  useEffect(() => {
    if (typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      setMounted(open);
      setVisible(open);
      return;
    }
    if (open) {
      setMounted(true);
      // Paint width:0 first, then open — so expand eases instead of popping
      const id = window.requestAnimationFrame(() => {
        window.requestAnimationFrame(() => setVisible(true));
      });
      return () => window.cancelAnimationFrame(id);
    }
    setVisible(false);
    const t = window.setTimeout(() => setMounted(false), ms);
    return () => window.clearTimeout(t);
  }, [open, ms]);

  return { mounted, visible };
}

/** Must be ≥ CSS width transition (--duration-panel) */
export const SOFT_PRESENCE_MS = 240;
