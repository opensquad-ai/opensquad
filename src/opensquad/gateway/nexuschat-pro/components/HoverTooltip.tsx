import React, { useLayoutEffect, useRef, useState, useCallback, useEffect } from 'react';

export type HoverTooltipPlacement = 'top' | 'bottom' | 'auto';

interface HoverTooltipProps {
  /** Text to display in the tooltip. May include newlines. */
  text: string;
  /** Trigger element. Must be able to forwardRef. */
  children: React.ReactNode;
  /** Where to place the tooltip relative to the trigger. */
  placement?: HoverTooltipPlacement;
  /** Max-width of the tooltip body (CSS length). Default: 22rem. */
  maxWidth?: string;
  /** Extra classes to add to the tooltip bubble. */
  className?: string;
  /** Delay in ms before the tooltip appears. Default: 200. */
  delayMs?: number;
}

/**
 * HoverTooltip
 *
 * A small hover tooltip that escapes any parent that has `overflow: hidden`
 * or `overflow-y: auto` by rendering the bubble with `position: fixed` and
 * computing its own coordinates from the trigger's bounding rect.
 *
 * Used by the model-download cards to show the on-disk model path on
 * hover without polluting the card layout. The trigger itself stays a
 * single line (whitespace-nowrap) so it never gets squeezed by the
 * surrounding grid.
 */
export const HoverTooltip: React.FC<HoverTooltipProps> = ({
  text,
  children,
  placement = 'auto',
  maxWidth = '22rem',
  className = '',
  delayMs = 180,
}) => {
  const wrapRef = useRef<HTMLSpanElement | null>(null);
  const bubbleRef = useRef<HTMLSpanElement | null>(null);
  const [pos, setPos] = useState<{ top: number; left: number; place: 'top' | 'bottom' } | null>(null);
  const [show, setShow] = useState(false);
  const timer = useRef<number | null>(null);

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
    if (!show) return;
    const handler = () => updatePos();
    window.addEventListener('scroll', handler, true);
    window.addEventListener('resize', handler);
    return () => {
      window.removeEventListener('scroll', handler, true);
      window.removeEventListener('resize', handler);
    };
  }, [show, updatePos]);

  // Use layout effect so the bubble is in the DOM with size before we
  // measure for positioning.
  useLayoutEffect(() => {
    if (show) updatePos();
  }, [show, text, updatePos]);

  const onEnter = () => {
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setShow(true), delayMs);
  };
  const onLeave = () => {
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = null;
    setShow(false);
  };

  return (
    <span
      ref={wrapRef}
      className="inline-flex items-center"
      onMouseEnter={onEnter}
      onMouseLeave={onLeave}
      onFocus={onEnter}
      onBlur={onLeave}
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
          className={`px-2 py-1 rounded-md bg-bgDark/95 text-textMain text-[11px] font-mono leading-snug break-all shadow-lg ring-1 ring-border ${className}`}
        >
          {text}
        </span>
      ) : null}
    </span>
  );
};

export default HoverTooltip;
