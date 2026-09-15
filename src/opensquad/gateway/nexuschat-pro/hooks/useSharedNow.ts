import { useEffect, useState } from 'react';

/**
 * One shared clock for live elapsed-time labels.
 * Multiple running workflows subscribe to the same interval instead of
 * each mounting its own 100ms timer.
 */
const listeners = new Set<(now: number) => void>();
let intervalId: ReturnType<typeof setInterval> | null = null;
const TICK_MS = 400;

function startTicker() {
  if (intervalId != null) return;
  intervalId = setInterval(() => {
    const now = Date.now();
    listeners.forEach((fn) => fn(now));
  }, TICK_MS);
}

function stopTickerIfIdle() {
  if (listeners.size > 0 || intervalId == null) return;
  clearInterval(intervalId);
  intervalId = null;
}

export function useSharedNow(enabled: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!enabled) return;
    const fn = (t: number) => setNow(t);
    listeners.add(fn);
    startTicker();
    setNow(Date.now());
    return () => {
      listeners.delete(fn);
      stopTickerIfIdle();
    };
  }, [enabled]);
  return now;
}
