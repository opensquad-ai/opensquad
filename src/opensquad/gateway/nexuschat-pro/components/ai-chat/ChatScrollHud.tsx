import React, { useCallback, useEffect, useLayoutEffect, useRef, useState, type RefObject } from 'react';
import { ChevronDown, ChevronUp } from 'lucide-react';

/**
 * Scroll-edge chrome that listens on the chat scroller itself.
 * State lives here so dragging the main thumb does not re-render AIChatPage
 * (which would rebuild the whole markdown timeline every frame).
 */
export function ChatScrollHud({
  scrollRef,
  onNearTop,
  nearTopEnabled = false,
  onUnpin,
}: {
  scrollRef: RefObject<HTMLElement | null>;
  onNearTop?: () => void;
  nearTopEnabled?: boolean;
  /** Keep the timeline unpin ref in sync without going through React. */
  onUnpin?: (awayFromBottom: boolean) => void;
}) {
  const [showTop, setShowTop] = useState(false);
  const [showBottom, setShowBottom] = useState(false);
  const [active, setActive] = useState(false);
  const hideTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const idleTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const raf = useRef(0);

  useLayoutEffect(() => {
    let el = scrollRef.current;
    let cancelled = false;
    let waitRaf = 0;

    const attach = (node: HTMLElement) => {
      const measure = (markActive: boolean) => {
        const { scrollTop, scrollHeight, clientHeight } = node;
        const dist = scrollHeight - scrollTop - clientHeight;
        setShowTop((v) => (v === scrollTop > 200 ? v : scrollTop > 200));
        setShowBottom((v) => (v === dist > 200 ? v : dist > 200));
        onUnpin?.(dist > 100);
        if (!markActive) return;
        setActive((v) => (v ? v : true));
        if (hideTimer.current) clearTimeout(hideTimer.current);
        hideTimer.current = setTimeout(() => setActive(false), 1500);
      };

      const onScroll = () => {
        if (raf.current) return;
        raf.current = requestAnimationFrame(() => {
          raf.current = 0;
          measure(true);
        });
        if (idleTimer.current) clearTimeout(idleTimer.current);
        idleTimer.current = setTimeout(() => {
          if (!nearTopEnabled || !onNearTop) return;
          if (node.scrollTop < 100) onNearTop();
        }, 180);
      };

      measure(false);
      node.addEventListener('scroll', onScroll, { passive: true });
      return () => {
        node.removeEventListener('scroll', onScroll);
        if (raf.current) cancelAnimationFrame(raf.current);
        if (hideTimer.current) clearTimeout(hideTimer.current);
        if (idleTimer.current) clearTimeout(idleTimer.current);
      };
    };

    let detach: (() => void) | undefined;
    if (el) {
      detach = attach(el);
    } else {
      waitRaf = requestAnimationFrame(() => {
        if (cancelled) return;
        el = scrollRef.current;
        if (el) detach = attach(el);
      });
    }

    return () => {
      cancelled = true;
      if (waitRaf) cancelAnimationFrame(waitRaf);
      detach?.();
    };
  }, [scrollRef, onNearTop, nearTopEnabled, onUnpin]);

  const scrollToTop = useCallback(() => {
    scrollRef.current?.scrollTo({ top: 0, behavior: 'smooth' });
  }, [scrollRef]);

  const scrollToBottom = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' });
    onUnpin?.(false);
  }, [scrollRef, onUnpin]);

  if (!showTop && !showBottom) return null;

  return (
    <div
      className="pointer-events-none absolute right-1 bottom-4 z-20 transition-opacity duration-300"
      style={{ opacity: active ? 1 : 0, pointerEvents: active ? undefined : 'none' }}
    >
      <div className="pointer-events-auto flex flex-col gap-2">
        {showTop && (
          <button
            type="button"
            onClick={scrollToTop}
            className="w-8 h-8 bg-panel border border-border/70 rounded-full shadow-md flex items-center justify-center text-textMuted hover:text-primary hover:bg-primary/10 transition-colors"
            title="滚动到顶部"
          >
            <ChevronUp size={18} />
          </button>
        )}
        {showBottom && (
          <button
            type="button"
            onClick={scrollToBottom}
            className="w-8 h-8 bg-panel border border-border/70 rounded-full shadow-md flex items-center justify-center text-textMuted hover:text-primary hover:bg-primary/10 transition-colors"
            title="滚动到底部"
          >
            <ChevronDown size={18} />
          </button>
        )}
      </div>
    </div>
  );
}

/** Classic layout: centered jump-to-bottom chip above the composer. */
export function ChatScrollComposerHint({
  scrollRef,
  columnClass,
  onUnpin,
}: {
  scrollRef: RefObject<HTMLElement | null>;
  columnClass: string;
  onUnpin?: (awayFromBottom: boolean) => void;
}) {
  const [show, setShow] = useState(false);
  const raf = useRef(0);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const sync = () => {
      const dist = el.scrollHeight - el.scrollTop - el.clientHeight;
      setShow((v) => (v === dist > 200 ? v : dist > 200));
    };
    const onScroll = () => {
      if (raf.current) return;
      raf.current = requestAnimationFrame(() => {
        raf.current = 0;
        sync();
      });
    };
    sync();
    el.addEventListener('scroll', onScroll, { passive: true });
    return () => {
      el.removeEventListener('scroll', onScroll);
      if (raf.current) cancelAnimationFrame(raf.current);
    };
  }, [scrollRef]);

  if (!show) return null;

  return (
    <div className="relative flex-shrink-0 z-20 pointer-events-none h-0">
      <div className={`${columnClass} relative`}>
        <button
          type="button"
          onClick={() => {
            const el = scrollRef.current;
            if (!el) return;
            el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' });
            onUnpin?.(false);
          }}
          className="pointer-events-auto absolute left-1/2 -translate-x-1/2 -top-10 w-8 h-8 rounded-full bg-panel border border-border/70 shadow-[0_2px_10px_rgba(0,0,0,0.08)] flex items-center justify-center text-textMuted hover:text-primary hover:bg-primary/10 transition-opacity duration-300 cursor-pointer"
          title="滚动到底部"
        >
          <ChevronDown size={18} className="text-gray-500" />
        </button>
      </div>
    </div>
  );
}
