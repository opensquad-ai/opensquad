/**
 * Collapse — the ONE fold primitive for the whole app.
 *
 * Every collapsible surface (tool call detail, thinking, plan, task process,
 * shell job, file diff, sidebar groups, context rows …) must go through this
 * so that open/close is animated exactly the same way everywhere and the
 * transition can never silently regress back to "conditional unmount".
 *
 * Why a component instead of copy-pasted classes:
 *
 * 1. **Unmounting cannot animate.** `{open ? children : null}` removes the node,
 *    so there is no "from" box to interpolate — the classic instant jump. The
 *    body must stay MOUNTED; height animates via an animatable
 *    `grid-template-rows: 1fr -> 0fr` (`.os-collapse` / `.is-closed`), which
 *    needs no JS measurement and reflows correctly with dynamic content.
 * 2. **The inner wrapper needs `overflow: hidden`** or the `0fr` row still
 *    paints its children (`.os-collapse-body`).
 * 3. **The closed body must leave the tab order and the a11y tree** — handled
 *    with `inert` (React 19 passes it through as a boolean attribute), so
 *    hidden controls are not reachable by Tab / screen readers.
 *
 * `useFold` additionally solves the one case where "stay mounted" is too
 * expensive: bodies that render Markdown / JSON / diffs (tool results are the
 * single heaviest string in the app). Those mount lazily on first expand — yet
 * still animate, because the body is mounted ONE FRAME BEFORE it opens. After
 * that first expansion it is a plain CSS transition like everything else.
 */
import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { ChevronRight } from 'lucide-react';

export const cx = (...parts: Array<string | false | null | undefined>): string =>
  parts.filter(Boolean).join(' ');

/**
 * Timestamp of the most recent fold open/close (0 = none since load). Follow
 * effects (ChatTimeline / WorkflowContainer stick-to-bottom) consult
 * {@link isFoldAnimating} so a USER-initiated expand does not get yanked to
 * the container bottom just because the column grew — that auto-follow is for
 * appended stream content, not for layout the user just changed themselves.
 */
let lastFoldToggleAt = 0;

export function isFoldAnimating(): boolean {
  return performance.now() - lastFoldToggleAt < 350;
}

export interface FoldState {
  /** Whether the fold is visually open. */
  open: boolean;
  /** Whether the body has ever been opened — gate expensive children on this. */
  mounted: boolean;
  /** Toggle, animating the very first expansion too. Returns the new state so
   *  callers can mirror it (e.g. persist, notify) without re-deriving it. */
  toggle: () => boolean;
  /** Programmatic set (external state, auto-open…) — animates. */
  setOpen: (next: boolean) => void;
  /**
   * Programmatic set with NO animation. Use this for state restored on mount
   * (localStorage / `defaultOpen`), where an animated expand would look like a
   * flash right after the first paint.
   */
  setOpenNow: (next: boolean) => void;
}

/**
 * Open/close state for a `<Collapse>`.
 *
 * @param initialOpen start expanded (e.g. `defaultOpen`, persisted state).
 */
export function useFold(initialOpen = false): FoldState {
  const [open, setRawOpen] = useState(initialOpen);
  const [everOpened, setEverOpened] = useState(initialOpen);
  // `arming` = the body was just mounted while still closed; the next frame
  // flips it open so the grid track has a distance to animate across.
  const [arming, setArming] = useState(false);
  const mounted = everOpened || open;

  useEffect(() => {
    if (!arming) return;
    const raf = window.requestAnimationFrame(() => {
      setArming(false);
      setRawOpen(true);
    });
    return () => window.cancelAnimationFrame(raf);
  }, [arming]);

  const setOpen = useCallback(
    (next: boolean) => {
      if (!next) {
        setRawOpen(false);
        return;
      }
      if (mounted) {
        setRawOpen(true);
        return;
      }
      // First expansion of a lazily-mounted body: mount now, open next frame.
      setEverOpened(true);
      setArming(true);
    },
    [mounted],
  );

  const toggle = useCallback((): boolean => {
    if (arming) return true; // already arming open — a second click must not cancel
    const next = !open;
    setOpen(next);
    return next;
  }, [arming, open, setOpen]);

  const setOpenNow = useCallback((next: boolean) => {
    setArming(false);
    if (next) setEverOpened(true);
    setRawOpen(next);
  }, []);

  return { open, mounted, toggle, setOpen, setOpenNow };
}

export interface CollapseProps {
  open: boolean;
  children?: React.ReactNode;
  /** Extra classes for the grid wrapper (e.g. a divider border). */
  className?: string;
  /** Forwarded for aria-controls wiring. */
  id?: string;
}

/**
 * Animated height fold. Children stay mounted; render them conditionally on
 * `useFold().mounted` when they are expensive.
 */
export const Collapse: React.FC<CollapseProps> = ({ open, children, className, id }) => {
  // Stamp the toggle time only when `open` actually CHANGES (not on mount),
  // so the initial session bottom-pin still runs right after first paint.
  const prevOpenRef = useRef(open);
  useLayoutEffect(() => {
    if (prevOpenRef.current !== open) {
      prevOpenRef.current = open;
      lastFoldToggleAt = performance.now();
    }
  }, [open]);
  return (
    <div
      id={id}
      className={cx('os-collapse', !open && 'is-closed', className)}
      inert={!open}
      aria-hidden={!open || undefined}
    >
      <div className="os-collapse-body">{children}</div>
    </div>
  );
};

/**
 * Fully-controlled fold: the caller owns the boolean (e.g. `expandedIds.has(id)`
 * in a multi-open accordion) and gets lazy mounting + an animated first
 * expansion for free.
 *
 * Returns the state to feed `<Collapse>`:
 *   `open`    — true only once the body has been mounted AND a frame has passed,
 *               so the very first expansion has somewhere to animate from;
 *   `mounted` — whether the body should be rendered at all.
 */
export function useControlledFold(desired: boolean): { open: boolean; mounted: boolean } {
  const [everOpened, setEverOpened] = useState(desired);
  // `settled` flips true one frame after the body first appears. While false the
  // wrapper is mounted but still closed — that one frame is the animation.
  const [settled, setSettled] = useState(desired);

  if (desired && !everOpened) {
    // Render-phase latch (bounded: true only for the first expansion).
    setEverOpened(true);
    setSettled(false);
  }

  useEffect(() => {
    if (!desired || settled) return;
    const raf = window.requestAnimationFrame(() => setSettled(true));
    return () => window.cancelAnimationFrame(raf);
  }, [desired, settled]);

  return { open: desired && settled, mounted: everOpened };
}

export interface ControlledFoldProps {
  open: boolean;
  children?: React.ReactNode;
  className?: string;
}

/** `<Collapse>` driven by an externally-owned boolean, with lazy mounting. */
export const ControlledFold: React.FC<ControlledFoldProps> = ({ open, children, className }) => {
  const fold = useControlledFold(open);
  return (
    <Collapse open={fold.open} className={className}>
      {fold.mounted ? children : null}
    </Collapse>
  );
};

export interface FoldChevronProps {
  open: boolean;
  size?: number;
  className?: string;
}

/**
 * The fold indicator. Always the SAME icon, rotated — swapping
 * `<ChevronRight/>` for `<ChevronDown/>` changes the DOM node identity, which
 * can never be animated.
 */
export const FoldChevron: React.FC<FoldChevronProps> = ({ open, size = 12, className }) => (
  <ChevronRight
    size={size}
    aria-hidden="true"
    className={cx('os-fold-chevron text-textMuted flex-shrink-0', open && 'is-open', className)}
  />
);

/** Convenience: `<button>` header that toggles a fold with correct a11y. */
export function foldAriaProps(open: boolean, bodyId?: string) {
  return {
    'aria-expanded': open,
    ...(bodyId ? { 'aria-controls': bodyId } : {}),
  } as const;
}

/** Stable id helper for aria-controls targets. */
export function useFoldId(prefix: string): string {
  const ref = useRef<string | null>(null);
  if (ref.current === null) {
    ref.current = `${prefix}-${Math.random().toString(36).slice(2, 9)}`;
  }
  return ref.current;
}
