import React, { useLayoutEffect, useRef, useState, useCallback, useEffect } from 'react';

export type HoverTooltipPlacement = 'top' | 'bottom' | 'auto';

/**
 * How the bubble is positioned.
 *
 * - `fixed`  (default): viewport-fixed bubble with JS-computed coordinates.
 *   Escapes any ancestor with `overflow: hidden/auto` (that is why the model
 *   download cards use it). Caveat: an ancestor that establishes a containing
 *   block via `transform` / `filter` / `will-change` / `contain` re-anchors
 *   `position: fixed` to itself while the coordinates came from
 *   `getBoundingClientRect()` (viewport space) — the bubble then lands far away
 *   from the trigger.
 * - `anchor`: plain CSS — `relative` wrapper + `absolute bottom-full`. The
 *   bubble is ALWAYS directly above the trigger regardless of zoom, ancestor
 *   transforms, scroll or DPR quirks. May clip against a scroll container's
 *   edge, so it suits inline footers (timestamps, usage badges) rather than
 *   absolute-positioned cards.
 */
export type HoverTooltipStrategy = 'fixed' | 'anchor';

/** Bubble typography. `mono` for paths/identifiers, `plain` for prose/numbers. */
export type HoverTooltipVariant = 'mono' | 'plain';

interface HoverTooltipProps {
  /** Text to display in the tooltip. May include newlines. */
  text: string;
  /** Trigger element. Must be able to forwardRef. */
  children: React.ReactNode;
  /** Where to place the tooltip relative to the trigger. */
  placement?: HoverTooltipPlacement;
  /** Positioning strategy. Default: `fixed`. */
  strategy?: HoverTooltipStrategy;
  /** Bubble typography preset. Default: `mono`. */
  variant?: HoverTooltipVariant;
  /** Max-width of the tooltip body (CSS length). Default: 22rem. */
  maxWidth?: string;
  /** Extra classes to add to the tooltip bubble. */
  className?: string;
  /** Delay in ms before the tooltip appears. Default: 200. */
  delayMs?: number;
  /** Also toggle visibility on click (touch users / "click to peek"). */
  toggleOnClick?: boolean;
}

/**
 * HoverTooltip
 *
 * A small hover tooltip. Two positioning strategies (see
 * {@link HoverTooltipStrategy}): the default `fixed` one escapes
 * `overflow: hidden` parents by rendering with `position: fixed` and
 * computing coordinates from the trigger's bounding rect; the `anchor` one is
 * pure CSS and guarantees "directly above the trigger".
 *
 * The trigger itself stays a single line (whitespace-nowrap) so it never gets
 * squeezed by the surrounding grid.
 */
export const HoverTooltip: React.FC<HoverTooltipProps> = ({
  text,
  children,
  placement = 'auto',
  strategy = 'fixed',
  variant = 'mono',
  maxWidth = '22rem',
  className = '',
  delayMs = 180,
  toggleOnClick = false,
}) => {
  const wrapRef = useRef<HTMLSpanElement | null>(null);
  const bubbleRef = useRef<HTMLSpanElement | null>(null);
  const [pos, setPos] = useState<{ top: number; left: number; place: 'top' | 'bottom' } | null>(null);
  const [show, setShow] = useState(false);
  const timer = useRef<number | null>(null);
  const anchored = strategy === 'anchor';

  const updatePos = useCallback(() => {
    const trigger = wrapRef.current;
    if (!trigger) return;
    const rect = trigger.getBoundingClientRect();
    const bubble = bubbleRef.current;
    const bH = bubble?.offsetHeight ?? 0;
    const bW = bubble?.offsetWidth ?? 240;
    const margin = 6; // gap between trigger and bubble

    // Prefer above; fall back to below if there isn't room.
    const wantTop = placement === 'top' || placement === 'auto';
    const wantBottom = placement === 'bottom' || placement === 'auto';
    let place: 'top' | 'bottom' = 'top';
    if (wantTop && !wantBottom) place = 'top';
    else if (wantBottom && !wantTop) place = 'bottom';
    else {
      // auto: prefer top, but if too close to viewport top, switch
      place = rect.top - bH - margin > 8 ? 'top' : 'bottom';
    }

    let top: number;
    if (place === 'top') {
      top = rect.top - bH - margin;
    } else {
      top = rect.bottom + margin;
    }

    // Clamp horizontally so the bubble stays on-screen. Anchor on the
    // left edge of the trigger; shift left if it would overflow right.
    let left = rect.left;
    const maxLeft = window.innerWidth - bW - 4;
    if (left > maxLeft) left = maxLeft;
    if (left < 4) left = 4;

    setPos({ top, left, place });
  }, [placement]);

  // Recompute on scroll / resize while visible so the bubble tracks
  // the trigger if the user moves the page.
  useEffect(() => {
    if (!show || anchored) return;
    const handler = () => updatePos();
    window.addEventListener('scroll', handler, true);
    window.addEventListener('resize', handler);
    return () => {
      window.removeEventListener('scroll', handler, true);
      window.removeEventListener('resize', handler);
    };
  }, [show, anchored, updatePos]);

  // Use layout effect so the bubble is in the DOM with size before we
  // measure for positioning.
  useLayoutEffect(() => {
    if (show && !anchored) updatePos();
  }, [show, anchored, text, updatePos]);

  const onEnter = () => {
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setShow(true), delayMs);
  };
  const onLeave = () => {
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = null;
    setShow(false);
  };
  // Click toggles instantly (skips the hover delay); a following mouseleave
  // still closes, so the two input modes never fight.
  const onClick = toggleOnClick
    ? () => {
        if (timer.current) window.clearTimeout(timer.current);
        timer.current = null;
        setShow((prev) => !prev);
      }
    : undefined;

  // Typography presets. Kept disjoint (no utility appears in two presets) so a
  // caller's `className` can never collide with a same-property utility — in
  // Tailwind the winner is decided by stylesheet order, which is not the class
  // attribute order.
  //
  // Surface: an OPAQUE theme colour. The bubble floats over message text, so a
  // translucent background lets the content bleed through and the label turns
  // unreadable — `bg-panel` is the raised-surface token the rest of the app uses
  // for popovers (see components/Tooltip.tsx), and `border-border` gives it an
  // edge in the presets where panel and page background coincide.
  const bubbleCls =
    (variant === 'plain'
      ? 'px-2.5 py-1.5 rounded-lg text-[12px] font-medium'
      : 'px-2 py-1 rounded-md text-[11px] font-mono break-all') +
    ' bg-panel text-textMain leading-snug shadow-lg border border-border';

  if (anchored) {
    return (
      <span
        ref={wrapRef}
        className="relative inline-flex items-center"
        onMouseEnter={onEnter}
        onMouseLeave={onLeave}
        onFocus={onEnter}
        onBlur={onLeave}
        onClick={onClick}
      >
        {children}
        {show ? (
          <span
            ref={bubbleRef}
            role="tooltip"
            style={{ maxWidth }}
            className={`absolute bottom-full left-0 mb-1.5 z-50 pointer-events-none ${bubbleCls} ${
              className || 'whitespace-nowrap'
            }`}
          >
            {text}
          </span>
        ) : null}
      </span>
    );
  }

  return (
    <span
      ref={wrapRef}
      className="inline-flex items-center"
      onMouseEnter={onEnter}
      onMouseLeave={onLeave}
      onFocus={onEnter}
      onBlur={onLeave}
      onClick={onClick}
    >
      {children}
      {show && pos ? (
        <span
          ref={bubbleRef}
          role="tooltip"
          style={{
            position: 'fixed',
            top: `${pos.top}px`,
            left: `${pos.left}px`,
            maxWidth,
            zIndex: 9999,
          }}
          className={`${bubbleCls} ${className}`}
        >
          {text}
        </span>
      ) : null}
    </span>
  );
};

export default HoverTooltip;
