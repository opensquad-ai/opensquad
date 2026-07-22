/**
 * Circular constellation of pulsing dots — Agent Web working indicator.
 * Use PulseDotsOrbit alone beside titles, or PulseDotsStatus for orbit + label.
 */
import React, { useEffect, useMemo, useState } from 'react';
import { formatElapsedAtLeastOneSecond } from '../../utils/formatElapsed';

export type PulseDotsKind = 'preparing' | 'thinking' | 'working';

const LABELS: Record<PulseDotsKind, string> = {
  preparing: '准备中',
  thinking: '深度思考中',
  working: '工作中',
};

/** Concentric rings → circular cluster (no angular / rotating sweep). */
const RING_LAYOUT: { r: number; n: number }[] = [
  { r: 0, n: 1 },
  { r: 0.36, n: 6 },
  { r: 0.66, n: 10 },
  { r: 0.92, n: 14 },
];

type DotPos = {
  x: number;
  y: number;
  /** Steady radial fade (center brighter); animation only modulates opacity. */
  baseOpacity: number;
  scale: number;
  /** Radial breathe delay only — never angular, so it does not look like spin. */
  delay: number;
};

function buildCircularDots(): DotPos[] {
  const pts: DotPos[] = [];
  for (const ring of RING_LAYOUT) {
    for (let i = 0; i < ring.n; i++) {
      const t = ring.n === 1 ? 0 : i / ring.n;
      const a = t * Math.PI * 2 - Math.PI / 2;
      const x = 50 + Math.cos(a) * ring.r * 50;
      const y = 50 + Math.sin(a) * ring.r * 50;
      // Soft spatial fade: center solid, rim ghosted (matches reference stills).
      const radial = 1 - ring.r;
      const jitter = ((i * 17 + Math.round(ring.r * 40)) % 7) / 7; // fixed, not time-based
      const baseOpacity = Math.min(1, 0.18 + radial * 0.72 + jitter * 0.12);
      const scale = 0.72 + radial * 0.4;
      // Only radial phase — whole ring fades together, no rotating highlight.
      const delay = ring.r * 0.5;
      pts.push({ x, y, baseOpacity, scale, delay });
    }
  }
  return pts;
}

const CIRCULAR_DOTS = buildCircularDots();

export interface PulseDotsOrbitProps {
  /** Outer box size in px (default 18). */
  size?: number;
  className?: string;
}

/** Dot-matrix orbit only — for session list / beside “Working for”. */
export const PulseDotsOrbit: React.FC<PulseDotsOrbitProps> = ({
  size = 18,
  className = '',
}) => {
  const dots = useMemo(() => CIRCULAR_DOTS, []);
  const cell = Math.max(1.5, size * (2.5 / 18));
  return (
    <span
      className={`os-pulse-orbit ${className}`}
      style={{ width: size, height: size }}
      aria-hidden
    >
      {dots.map((d, i) => (
        <span
          key={i}
          className="os-pulse-cell"
          style={{
            left: `${d.x}%`,
            top: `${d.y}%`,
            width: cell * d.scale,
            height: cell * d.scale,
            ['--pulse-base' as string]: String(d.baseOpacity),
            animationDelay: `${d.delay}s`,
          }}
        />
      ))}
    </span>
  );
};

export interface PulseDotsStatusProps {
  kind?: PulseDotsKind;
  /** Wall-clock start of the current turn; omit to hide the timer */
  startedMs?: number;
  /** Optional step / depth counter (↓N) */
  stepCount?: number;
  className?: string;
  /** Orbit diameter in px */
  orbitSize?: number;
  /** @deprecated Ignored — always circular many-dot constellation */
  variant?: string;
}

export const PulseDotsStatus: React.FC<PulseDotsStatusProps> = ({
  kind = 'preparing',
  startedMs,
  stepCount,
  className = '',
  orbitSize = 18,
}) => {
  const [elapsedMs, setElapsedMs] = useState(0);

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
      <PulseDotsOrbit size={orbitSize} />
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
