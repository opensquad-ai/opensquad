import { useCallback, useEffect, useRef, useState, type TouchEvent } from 'react';

const SWIPE_AXIS_LOCK = 14;
const SWIPE_TRIGGER = 72;
const SWIPE_MOUNT_GUARD_MS = 400;

export interface UseMobileChatSwipeOptions {
  groupId: string;
  /** Right swipe past threshold → leave chat (e.g. onBack). */
  onSwipeRight: () => void;
  /** Left swipe past threshold → open group settings. */
  onSwipeLeft: () => void;
  /** Called once when the gesture locks to horizontal (cancel pull-to-load). */
  onHorizontalLock?: () => void;
}

export interface UseMobileChatSwipeResult {
  swipeOffset: number;
  swipeTransition: boolean;
  handleNavSwipeStart: (e: TouchEvent) => void;
  handleNavSwipeMove: (e: TouchEvent) => void;
  handleNavSwipeEnd: () => void;
  resetNavSwipe: () => void;
  /** True while the active gesture is locked to horizontal. */
  isNavSwipeHorizontal: () => boolean;
  springBackNavSwipe: () => void;
}

const isMobileViewport = () =>
  typeof window !== 'undefined' && window.innerWidth < 768;

/**
 * Full-screen horizontal swipe on mobile group chat:
 * - right → leave chat (group list)
 * - left → open group settings (RightPanel)
 */
export function useMobileChatSwipe({
  groupId,
  onSwipeRight,
  onSwipeLeft,
  onHorizontalLock,
}: UseMobileChatSwipeOptions): UseMobileChatSwipeResult {
  const navSwipeRef = useRef<{
    startX: number;
    startY: number;
    axis: 'undecided' | 'h' | 'v';
  } | null>(null);
  const swipeReadyAtRef = useRef(0);
  const swipeOffsetRef = useRef(0);
  const onSwipeRightRef = useRef(onSwipeRight);
  const onSwipeLeftRef = useRef(onSwipeLeft);
  const onHorizontalLockRef = useRef(onHorizontalLock);
  const [swipeOffset, setSwipeOffset] = useState(0);
  const [swipeTransition, setSwipeTransition] = useState(false);

  useEffect(() => {
    onSwipeRightRef.current = onSwipeRight;
    onSwipeLeftRef.current = onSwipeLeft;
    onHorizontalLockRef.current = onHorizontalLock;
  }, [onSwipeRight, onSwipeLeft, onHorizontalLock]);

  const resetNavSwipe = useCallback(() => {
    navSwipeRef.current = null;
    swipeOffsetRef.current = 0;
    setSwipeOffset(0);
    setSwipeTransition(false);
  }, []);

  const springBackNavSwipe = useCallback(() => {
    setSwipeTransition(true);
    setSwipeOffset(0);
    window.setTimeout(() => resetNavSwipe(), 160);
  }, [resetNavSwipe]);

  // Ignore swipe briefly after entering a group (tap that opened chat can ghost onto this view)
  useEffect(() => {
    swipeReadyAtRef.current = Date.now() + SWIPE_MOUNT_GUARD_MS;
    navSwipeRef.current = null;
    swipeOffsetRef.current = 0;
    setSwipeOffset(0);
    setSwipeTransition(false);
  }, [groupId]);

  const isNavSwipeHorizontal = useCallback(
    () => navSwipeRef.current?.axis === 'h',
    [],
  );

  const handleNavSwipeStart = useCallback((e: TouchEvent) => {
    if (!isMobileViewport() || e.touches.length !== 1) return;
    if (Date.now() < swipeReadyAtRef.current) return;
    const target = e.target as HTMLElement | null;
    if (target?.closest('input, textarea, [contenteditable="true"], video, audio')) return;

    navSwipeRef.current = {
      startX: e.touches[0].clientX,
      startY: e.touches[0].clientY,
      axis: 'undecided',
    };
    setSwipeTransition(false);
  }, []);

  const handleNavSwipeMove = useCallback((e: TouchEvent) => {
    const swipe = navSwipeRef.current;
    if (!swipe || e.touches.length !== 1) return;

    const dx = e.touches[0].clientX - swipe.startX;
    const dy = e.touches[0].clientY - swipe.startY;

    if (swipe.axis === 'undecided') {
      if (Math.abs(dx) < SWIPE_AXIS_LOCK && Math.abs(dy) < SWIPE_AXIS_LOCK) return;
      swipe.axis = Math.abs(dx) > Math.abs(dy) * 1.15 ? 'h' : 'v';
      if (swipe.axis === 'h') {
        onHorizontalLockRef.current?.();
      }
    }

    if (swipe.axis !== 'h') return;

    // Right swipe can travel far; left swipe only peeks (settings is an overlay).
    const visual = dx >= 0
      ? Math.min(window.innerWidth * 0.85, dx)
      : Math.max(-56, dx);
    swipeOffsetRef.current = dx;
    setSwipeOffset(visual);
  }, []);

  const handleNavSwipeEnd = useCallback(() => {
    const swipe = navSwipeRef.current;
    if (!swipe || swipe.axis !== 'h') {
      resetNavSwipe();
      return;
    }

    const dx = swipeOffsetRef.current;

    if (dx >= SWIPE_TRIGGER) {
      onSwipeRightRef.current();
      resetNavSwipe();
      return;
    }

    if (dx <= -SWIPE_TRIGGER) {
      setSwipeTransition(true);
      setSwipeOffset(0);
      onSwipeLeftRef.current();
      window.setTimeout(() => resetNavSwipe(), 160);
      return;
    }

    setSwipeTransition(true);
    setSwipeOffset(0);
    window.setTimeout(() => resetNavSwipe(), 160);
  }, [resetNavSwipe]);

  return {
    swipeOffset,
    swipeTransition,
    handleNavSwipeStart,
    handleNavSwipeMove,
    handleNavSwipeEnd,
    resetNavSwipe,
    isNavSwipeHorizontal,
    springBackNavSwipe,
  };
}
