import { useEffect, useRef } from 'react';
import { hydrateMermaidIn, warmMermaid } from '../utils/mermaidHydrate';

/**
 * Attach to a markdown container that may contain `.ai-mermaid` placeholders.
 * Re-runs when *html* (or any dep) changes.
 *
 * Warming is deliberately not gated on `enabled`: the lazy mermaid chunk is fetched as
 * soon as a placeholder appears — i.e. while the answer is still streaming — so that the
 * render the caller unlocks at the end of the stream starts immediately instead of first
 * waiting for a ~276 KB chunk. See `utils/mermaidHydrate.loadMermaid`.
 */
export function useMermaidHydration(html: string, enabled = true) {
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!html) return;
    if (html.includes('ai-mermaid')) void warmMermaid();
    if (!enabled) return;
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
