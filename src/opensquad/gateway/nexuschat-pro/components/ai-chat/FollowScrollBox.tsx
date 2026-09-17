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
};

export const FollowScrollBox: React.FC<FollowScrollBoxProps> = ({
  children,
  className,
  style,
  contentKey,
  follow = true,
  as = 'div',
  onStickChange,
}) => {
  const ref = useRef<HTMLDivElement | HTMLPreElement | null>(null);
  const stickRef = useRef(true);
  // Latest-ref: keeps `publish` stable so the effects below do not re-run when
  // a parent re-renders (it does, on every streamed chunk).
  const notifyRef = useRef(onStickChange);
  notifyRef.current = onStickChange;

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
    // Second pass after paint — streaming fonts/wrap can grow height one frame late.
    const id = requestAnimationFrame(() => {
      if (!stickRef.current || !ref.current) return;
      ref.current.scrollTop = ref.current.scrollHeight;
    });
    return () => cancelAnimationFrame(id);
    // Intentionally omit `children`: parent re-renders (elapsed tick, live
    // stream) recreate element identity and would re-scroll every frame,
    // wiping text selection. contentKey already tracks content growth.
  }, [contentKey, follow]);

  const onScroll = () => {
    const el = ref.current;
    if (!el) return;
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
