/**
 * 方形紫色点阵 — Agent Web 工作指示器。
 * 10 个点按 5×2 网格（两层）排列；一个最亮点 + 周围渐变暗的光晕，
 * 亮点沿顺时针路径匀速逐点推进（外层 10 点闭环），每格经 CSS 过渡
 * 平滑交叉淡化，不跳跃、不忽前忽后。亮度恒定、无呼吸节奏、无随机。
 * 品牌紫 #8257CC。使用 PulseDotsOrbit 单独显示点阵，或 PulseDotsStatus 显示点阵 + 文案。
 */
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { formatElapsedAtLeastOneSecond } from '../../utils/formatElapsed';

export type PulseDotsKind = 'preparing' | 'thinking' | 'working';

const LABELS: Record<PulseDotsKind, string> = {
  preparing: '准备中',
  thinking: '深度思考中',
  working: '工作中',
};

/** 亮点每格推进节奏（ms）：匀速逐点顺时针，经 CSS 过渡平滑接力。 */
const HIGHLIGHT_STEP_MS = 240;

/** 10 点两层网格（5 列 × 2 行，容器 0-100% 坐标）。 */
const GRID_POINTS: DotPos[] = (() => {
  const pts: DotPos[] = [];
  for (let row = 0; row < 2; row++) {
    for (let col = 0; col < 5; col++) {
      pts.push({
        x: 50 + (col - 2) * 20,
        y: 50 + (row - 0.5) * 30,
        baseOpacity: 0.2,
        scale: 1,
      });
    }
  }
  return pts;
})();

/** 光晕宽度（路径格数）：σ≈1.1 格——亮点全亮、相邻点次亮、周围渐变暗。 */
const HIGHLIGHT_SIGMA_GRID = 1.1;

/** 顺时针闭环路径（row-major 索引）：两层 10 点即整个外围——
 *  顶行左→右 (0..4) → 右列下 (9) → 底行右→左 (8..5) → 回到 0。
 *  10 点全部在矩形外围上，构成完整闭合环，无内部点、无折返。 */
const SWEEP_PATH = [0, 1, 2, 3, 4, 9, 8, 7, 6, 5];

/** 网格索引 → 路径位置（沿 SWEEP_PATH 的序号），用于环向距离计算。 */
const PATH_INDEX: number[] = (() => {
  const m = new Array<number>(GRID_POINTS.length).fill(-1);
  SWEEP_PATH.forEach((p, i) => {
    m[p] = i;
  });
  return m;
})();

type DotPos = {
  x: number;
  y: number;
  /** 暗态基础亮度。 */
  baseOpacity: number;
  scale: number;
};

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
  const dots = useMemo(() => GRID_POINTS, []);
  const cellRefs = useRef<(HTMLSpanElement | null)[]>([]);
  const cell = Math.max(1.5, size * (2.5 / 18));

  useEffect(() => {
    const mq = window.matchMedia?.('(prefers-reduced-motion: reduce)');
    if (mq?.matches) return; // 静态：保持暗态即可

    // 亮点从路径固定起点起步（不随机），匀速逐点顺时针推进
    let idx = 0;

    const apply = () => {
      GRID_POINTS.forEach((d, i) => {
        const el = cellRefs.current[i];
        if (!el) return;
        // 该点在路径上的位置（环向最短距离，格数）
        const cur = PATH_INDEX[i];
        let ring = Math.abs(cur - idx);
        ring = Math.min(ring, SWEEP_PATH.length - ring);
        // 光晕：距亮点越近越亮、周围渐变暗
        const g = Math.exp(-(ring * ring) / (2 * HIGHLIGHT_SIGMA_GRID ** 2));
        el.style.opacity = String(d.baseOpacity + (1 - d.baseOpacity) * g);
        el.style.transform = `translate(-50%, -50%) scale(${(1 + g * 0.55).toFixed(3)})`;
      });
    };

    apply();
    const id = window.setInterval(() => {
      idx = (idx + 1) % SWEEP_PATH.length;
      apply();
    }, HIGHLIGHT_STEP_MS);

    return () => window.clearInterval(id);
  }, [dots]);

  return (
    <span
      className={`os-pulse-orbit ${className}`}
      style={{ width: size, height: size }}
      aria-hidden
    >
      {dots.map((d, i) => (
        <span
          key={i}
          ref={(el) => { cellRefs.current[i] = el; }}
          className="os-pulse-cell"
          style={{
            left: `${d.x}%`,
            top: `${d.y}%`,
            width: cell * d.scale,
            height: cell * d.scale,
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
