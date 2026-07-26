import { useEffect, useState } from 'react';

/**
 * Subscribe to a CSS media query. Returns false during SSR / before mount
 * when `defaultValue` is omitted (avoids hydration flashes favoring mobile).
 */
export function useMatchMedia(query: string, defaultValue = false): boolean {
  const [matches, setMatches] = useState(() => {
    if (typeof window === 'undefined' || !window.matchMedia) return defaultValue;
    try {
      return window.matchMedia(query).matches;
    } catch {
      return defaultValue;
    }
  });

  useEffect(() => {
    if (typeof window === 'undefined' || !window.matchMedia) return;
    let mql: MediaQueryList;
    try {
      mql = window.matchMedia(query);
    } catch {
      return;
    }
    const onChange = () => setMatches(mql.matches);
    onChange();
    if (typeof mql.addEventListener === 'function') {
      mql.addEventListener('change', onChange);
      return () => mql.removeEventListener('change', onChange);
    }
    mql.addListener(onChange);
    return () => mql.removeListener(onChange);
  }, [query]);

  return matches;
}

/** Tailwind `md` and below — phone / small tablet portrait. */
export function useIsMobileViewport(): boolean {
  return useMatchMedia('(max-width: 767px)');
}

/**
 * Mobile Agent Web layout — side rails must overlay, not sit in-flow
 * (session + files panels otherwise crush the chat column to ~0 width).
 * Matches Tailwind `md` (desktop three-column layout stays unchanged).
 */
export function useIsCompactAgentWeb(): boolean {
  return useMatchMedia('(max-width: 767px)');
}
