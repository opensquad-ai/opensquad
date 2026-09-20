/**
 * Scroll container that sticks to the bottom while content streams in.
 * If the user scrolls up to read, auto-follow pauses until they return near bottom.
 */
import React, { useEffect, useLayoutEffect, useRef } from 'react';

const NEAR_BOTTOM_PX = 48;

type FollowScrollBoxProps = {
  children: React.ReactNode;
  className?: string;
  style?: React.CSSProperties;
  /** Changing this (e.g. content length) triggers a stick-to-bottom check. */
  contentKey: string | number;
  /** When true, re-arm stick-to-bottom (e.g. while thought is still streaming). */
  follow?: boolean;
  as?: 'div' | 'pre';
  /**
   * Reports every *transition* of "am I pinned to the bottom" — never called
   * repeatedly for the same value. Consumers use it to drop a scroll-edge
   * treatment while the reader is up in the text (see `os-thought-tail`).
   */
  onStickChange?: (stuck: boolean) => void;
  /**
   * Reports every *transition* of "content overflows the box" (scrollbar
   * present). Consumers use it to upgrade edge treatments that only make
   * sense once the body actually scrolls (see `os-thought-drift`).
   */
  onOverflowChange?: (overflowing: boolean) => void;
};

export const FollowScrollBox: React.FC<FollowScrollBoxProps> = ({
  children,
  className,
  style,
  contentKey,
  follow = true,
  as = 'div',
  onStickChange,
  onOverflowChange,
}) => {
  const ref = useRef<HTMLDivElement | HTMLPreElement | null>(null);
  const stickRef = useRef(true);
  // Last observed scrollHeight — lets onScroll tell "content streamed in"
  // apart from "the reader scrolled", so a temporarily large bottom gap
  // during fast streaming never flips the stick state (the tail class would
  // flap on every chunk — the visible "jitter" while a thought streams).
  const lastScrollHeightRef = useRef(0);
  // Latest-ref: keeps `publish` stable so the effects below do not re-run when
  // a parent re-renders (it does, on every streamed chunk).
  const notifyRef = useRef(onStickChange);
  notifyRef.current = onStickChange;
  // Overflow reporter: latest-ref + edge-only, same contract as stick state.
  const overflowNotifyRef = useRef(onOverflowChange);
  overflowNotifyRef.current = onOverflowChange;
  const overflowRef = useRef(false);
  const checkOverflow = () => {
    const el = ref.current;
    if (!el) return;
    const next = el.scrollHeight > el.clientHeight + 1;
    if (overflowRef.current === next) return;
    overflowRef.current = next;
    overflowNotifyRef.current?.(next);
  };

  /** Single writer for stick state — edges only, so callers can setState. */
  const publish = (next: boolean) => {
    if (stickRef.current === next) return;
    stickRef.current = next;
    notifyRef.current?.(next);
  };

  useEffect(() => {
    if (follow) publish(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [follow]);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (!stickRef.current) return;
    el.scrollTop = el.scrollHeight;
    lastScrollHeightRef.current = el.scrollHeight;
    checkOverflow();
    // Second pass after paint — streaming fonts/wrap can grow height one frame late.
    const id = requestAnimationFrame(() => {
      if (!stickRef.current || !ref.current) return;
      ref.current.scrollTop = ref.current.scrollHeight;
      lastScrollHeightRef.current = ref.current.scrollHeight;
      checkOverflow();
    });
    return () => cancelAnimationFrame(id);
    // Intentionally omit `children`: parent re-renders (elapsed tick, live
    // stream) recreate element identity and would re-scroll every frame,
    // wiping text selection. contentKey already tracks content growth.
  }, [contentKey, follow]);

  const onScroll = () => {
    const el = ref.current;
    if (!el) return;
    // scrollHeight changed since the last event → content streamed in, and
    // the pin effect re-arms right after. A large bottom gap at that moment
    // is a race, not a user scroll — keep the stick state instead of flapping.
    // The very first observation is a baseline, not a growth signal.
    const prev = lastScrollHeightRef.current;
    lastScrollHeightRef.current = el.scrollHeight;
    checkOverflow();
    if (prev > 0 && prev !== el.scrollHeight && stickRef.current) return;
    publish(el.scrollHeight - el.scrollTop - el.clientHeight < NEAR_BOTTOM_PX);
  };

  if (as === 'pre') {
    return (
      <pre ref={ref as React.RefObject<HTMLPreElement>} className={className} style={style} onScroll={onScroll}>
        {children}
      </pre>
    );
  }

  return (
    <div ref={ref as React.RefObject<HTMLDivElement>} className={className} style={style} onScroll={onScroll}>
      {children}
    </div>
  );
};
