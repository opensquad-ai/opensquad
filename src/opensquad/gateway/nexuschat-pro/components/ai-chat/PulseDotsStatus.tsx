/**
 * 方形紫色点阵 — Agent Web 工作指示器。
 * 4 个点排成一行；亮点从左到右用纯 CSS 推进。
 * 不用 JS 改 opacity，避免工具行插入时把标题动画打断。
 * 品牌紫 #8257CC。
 */
import React, { useEffect, useState } from 'react';
import { formatElapsedAtLeastOneSecond } from '../../utils/formatElapsed';

export type PulseDotsKind = 'preparing' | 'thinking' | 'working';

const LABELS: Record<PulseDotsKind, string> = {
  preparing: '准备中',
  thinking: '深度思考中',
  working: '工作中',
};

/** 4 点单行网格（1 行 × 4 列，容器 0-100% 坐标）。 */
const GRID_POINTS: DotPos[] = (() => {
  const pts: DotPos[] = [];
  for (let col = 0; col < 4; col++) {
    pts.push({
      x: 50 + (col - 1.5) * 22,
      y: 50,
      scale: 1,
    });
  }
  return pts;
})();

/** 从左到右循环推进。 */
const SWEEP_PATH = [0, 1, 2, 3];

const PATH_INDEX: number[] = (() => {
  const m = new Array<number>(GRID_POINTS.length).fill(0);
  SWEEP_PATH.forEach((p, i) => {
    m[p] = i;
  });
  return m;
})();

type DotPos = {
  x: number;
  y: number;
  scale: number;
};

export interface PulseDotsOrbitProps {
  /** Outer box size in px (default 18). */
  size?: number;
  className?: string;
}

/** Dot-matrix orbit only — for session list / beside “Working for”. */
export const PulseDotsOrbit: React.FC<PulseDotsOrbitProps> = React.memo(({
  size = 18,
  className = '',
}) => {
  const cell = Math.max(1.5, size * (2.5 / 18));

  return (
    <span
      className={`os-pulse-orbit ${className}`}
      style={{ width: size, height: size }}
      aria-hidden
    >
      {GRID_POINTS.map((d, i) => (
        <span
          key={i}
          className="os-pulse-cell"
          style={{
            left: `${d.x}%`,
            top: `${d.y}%`,
            width: cell * d.scale,
            height: cell * d.scale,
            ['--os-pulse-i' as string]: PATH_INDEX[i],
          }}
        />
      ))}
    </span>
  );
});

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
