import { useEffect, useRef } from 'react';
import { hydrateMermaidIn } from '../utils/mermaidHydrate';

/**
 * Attach to a markdown container that may contain `.ai-mermaid` placeholders.
 * Re-runs when *html* (or any dep) changes.
 */
export function useMermaidHydration(html: string, enabled = true) {
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!enabled || !html) return;
    const el = ref.current;
    if (!el) return;
    let cancelled = false;
    const run = () => {
      if (cancelled) return;
      void hydrateMermaidIn(el);
    };
    // Defer one frame so dangerouslySetInnerHTML has committed
    const t = window.setTimeout(run, 0);
    return () => {
      cancelled = true;
      window.clearTimeout(t);
    };
  }, [html, enabled]);

  return ref;
}
