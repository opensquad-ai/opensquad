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
  // Last observed scrollHeight — the very first observation is only a baseline
  // (not a growth signal); afterwards it lets onScroll tell "content streamed
  // in" apart from "the reader scrolled" when the two are otherwise ambiguous.
  const lastScrollHeightRef = useRef(0);
  // 最后一次"程序贴底"写入的 scrollTop。scroll 事件无法区分来源：程序贴底
  // 写入与用户滚动都会触发事件。回声事件（scrollTop === lastSetTop）必须忽略，
  // 否则流式期间会被误判为用户离开 → 贴底/离开来回翻转（抖动）；反过来，
  // 用户在流式期间的真实滚动必须被尊重，否则贴底会与用户拖动的滑块"拉扯"。
  const lastSetTopRef = useRef(-1);
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

  /** 单一贴底写入点：写 scrollTop 并登记 lastSetTop，供 onScroll 识别回声。 */
  const pinToBottom = (el: HTMLDivElement | HTMLPreElement) => {
    el.scrollTop = el.scrollHeight;
    lastSetTopRef.current = el.scrollTop;
    lastScrollHeightRef.current = el.scrollHeight;
    checkOverflow();
  };

  useEffect(() => {
    if (follow) publish(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [follow]);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (!stickRef.current) {
      // 用户离开过底部。但流式增长会把"贴近底部"的视口越推越远：若此刻
      // 已在贴底容差内（刚拖回底部、或松手后滑块只差一点），重新武装跟随，
      // 否则滑块会在后续输出中越漂越远（"拖到底了又自动跑上去"）。
      if (el.scrollHeight - el.scrollTop - el.clientHeight >= NEAR_BOTTOM_PX) return;
      publish(true);
    }
    pinToBottom(el);
    // Second pass after paint — streaming fonts/wrap can grow height one frame late.
    const id = requestAnimationFrame(() => {
      if (!stickRef.current || !ref.current) return;
      pinToBottom(ref.current);
    });
    return () => cancelAnimationFrame(id);
    // Intentionally omit `children`: parent re-renders (elapsed tick, live
    // stream) recreate element identity and would re-scroll every frame,
    // wiping text selection. contentKey already tracks content growth.
  }, [contentKey, follow]);

  // 内容尺寸观察：contentKey 只覆盖文本长度，代码围栏闭合后的重排、字体
  // 加载引起的换行变化同样会改变 scrollHeight —— 这些也要补一次贴底。
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(() => {
      if (!stickRef.current || !ref.current) return;
      pinToBottom(ref.current);
    });
    ro.observe(el.firstElementChild ?? el);
    return () => ro.disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const onScroll = () => {
    const el = ref.current;
    if (!el) return;
    const prev = lastScrollHeightRef.current;
    lastScrollHeightRef.current = el.scrollHeight;
    checkOverflow();
    const grew = prev > 0 && prev !== el.scrollHeight;
    // 内容增长 + scrollTop 没变 → 程序贴底写入的回声，不是用户滚动，忽略
    // （快速流式时误翻转会让尾迹雾化闪烁）。scrollTop 变了才是用户：
    // 拖回底部（gap 小）→ 重新跟随；往上翻（gap 大）→ 进入阅读模式。
    if (grew && el.scrollTop === lastSetTopRef.current) return;
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
