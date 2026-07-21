/**
 * Circular constellation of pulsing dots + status label + elapsed timer.
 * Classic Agent Web "working / thinking" chrome (Manus-like).
 */
import React, { useEffect, useMemo, useState } from 'react';
import { formatElapsedAtLeastOneSecond } from '../../utils/formatElapsed';

export type PulseDotsKind = 'preparing' | 'thinking' | 'working';

const LABELS: Record<PulseDotsKind, string> = {
  preparing: '准备中',
  thinking: '深度思考中',
  working: '工作中',
};

/** Concentric rings → many round dots packed into a circle */
const RING_LAYOUT: { r: number; n: number }[] = [
  { r: 0, n: 1 },
  { r: 0.34, n: 6 },
  { r: 0.62, n: 10 },
  { r: 0.9, n: 14 },
];

type DotPos = { x: number; y: number; delay: number };

function buildCircularDots(): DotPos[] {
  const pts: DotPos[] = [];
  for (const ring of RING_LAYOUT) {
    for (let i = 0; i < ring.n; i++) {
      const t = ring.n === 1 ? 0 : i / ring.n;
      const a = t * Math.PI * 2 - Math.PI / 2;
      const x = 50 + Math.cos(a) * ring.r * 50;
      const y = 50 + Math.sin(a) * ring.r * 50;
      // Angular sweep + slight radial offset → soft rotating breathe
      const delay = t * 0.95 + ring.r * 0.22;
      pts.push({ x, y, delay });
    }
  }
  return pts;
}

const CIRCULAR_DOTS = buildCircularDots();

export interface PulseDotsStatusProps {
  kind?: PulseDotsKind;
  /** Wall-clock start of the current turn; omit to hide the timer */
  startedMs?: number;
  /** Optional step / depth counter (↓N) */
  stepCount?: number;
  className?: string;
  /** @deprecated Ignored — always circular many-dot constellation */
  variant?: string;
}

export const PulseDotsStatus: React.FC<PulseDotsStatusProps> = ({
  kind = 'preparing',
  startedMs,
  stepCount,
  className = '',
}) => {
  const [elapsedMs, setElapsedMs] = useState(0);
  const dots = useMemo(() => CIRCULAR_DOTS, []);

  useEffect(() => {
    if (startedMs == null) {
      setElapsedMs(0);
      return;
    }
    const tick = () => setElapsedMs(Math.max(0, Date.now() - startedMs));
    tick();
    const id = window.setInterval(tick, 200);
    return () => window.clearInterval(id);
  }, [startedMs]);

  const label = LABELS[kind];
  const timeLabel =
    startedMs != null ? formatElapsedAtLeastOneSecond(elapsedMs) : null;

  return (
    <div
      className={`os-pulse-status inline-flex items-center gap-2 select-none ${className}`}
      role="status"
      aria-live="polite"
      aria-label={[label, timeLabel, stepCount != null ? `${stepCount}` : null]
        .filter(Boolean)
        .join(' · ')}
    >
      <span className="os-pulse-orbit" aria-hidden>
        {dots.map((d, i) => (
          <span
            key={i}
            className="os-pulse-cell"
            style={{
              left: `${d.x}%`,
              top: `${d.y}%`,
              animationDelay: `${d.delay}s`,
            }}
          />
        ))}
      </span>
      <span className="text-[13px] leading-none text-textMuted/80 tracking-tight">
        {label}
        {timeLabel != null ? (
          <>
            <span className="mx-1 opacity-50">·</span>
            <span className="tabular-nums">{timeLabel}</span>
          </>
        ) : null}
        {typeof stepCount === 'number' && stepCount > 0 ? (
          <>
            <span className="mx-1 opacity-50">·</span>
            <span className="tabular-nums">↓{stepCount}</span>
          </>
        ) : null}
      </span>
    </div>
  );
};

export function pulseKindFromFlags(opts: {
  thinking?: boolean;
  working?: boolean;
}): PulseDotsKind {
  if (opts.thinking) return 'thinking';
  if (opts.working) return 'working';
  return 'preparing';
}
