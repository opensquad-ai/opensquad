/**
 * Sidebar paging + timeline reveal — structural fences.
 *
 * `sessionListWindow.test.ts` covers the pure helpers. This file covers the
 * part unit tests cannot reach: that the sidebar actually *calls* them, and
 * that the two failure modes that caused the reported bug cannot come back.
 *
 * Why fences rather than a render test: the sidebar needs the api layer, i18n,
 * localStorage and the WS event bus; a jsdom mount would spend most of its
 * budget on mocks and still would not catch the backend half of the loop.
 *
 *   R1  the sidebar imports the window helpers;
 *   R2  its page-0 read is window-sized — the bare `(agentId, 0, PAGE_SIZE)`
 *       form is the truncation that re-armed the sentinel (the bug);
 *   R3  paging state is a ref, not a numeric-offset `useState` (`offset` in
 *       loadMoreSessions' dep array also changed its identity every page);
 *   R4  the refresh merges instead of overwriting, and load-more advances the
 *       cursor by raw server rows;
 *   R5  the backend reports `has_more` from an over-fetch, not `len >= limit`;
 *   R6  the row entrance animation is gated on `.os-revealing` — an animation
 *       on bare `.timeline-row` would replay on every virtual remount, i.e. a
 *       list that flickers while scrolling.
 *
 * Verified by mutation (each makes this file fail):
 *   MF1 SessionSidebar back to `getSessionList(agentId, 0, SESSION_LIST_PAGE_SIZE)` → R2
 *   MF2 `const [offset, setOffset] = useState(0)` restored               → R3
 *   MF3 refresh writes `list` directly instead of merging                → R4
 *   MF4 `_main.py` back to `len(sessions) >= limit`                      → R5
 *   MF5 `.os-revealing > .timeline-row` → `.timeline-row`                → R6
 */
import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';

const ROOT = path.resolve(__dirname, '..');
const read = (rel: string) => fs.readFileSync(path.join(ROOT, rel), 'utf8');
/** .../src — the Python tree lives next to the frontend at ../../../ */
const PY_ROOT = path.resolve(ROOT, '../../..');
const py = (rel: string) => fs.readFileSync(path.join(PY_ROOT, 'opensquad', rel), 'utf8');

/** Comments describe the rules below and must not trip them. */
const tsCode = (src: string) =>
  src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
const cssCode = (src: string) => src.replace(/\/\*[\s\S]*?\*\//g, '');
const pyCode = (src: string) => src.replace(/^\s*#.*$/gm, '');
/** Collapse whitespace so a re-wrapped call still matches. */
const flat = (src: string) => src.replace(/\s+/g, ' ');

const SIDEBAR = tsCode(read('components/ai-chat/SessionSidebar.tsx'));
const TIMELINE = tsCode(read('components/ai-chat/ChatTimeline.tsx'));
const CSS = cssCode(read('index.css'));
const SIDEBAR_TS = flat(SIDEBAR);

const PY_ROUTE = pyCode(py('gateway/backend/app/ai_web/routes/_main.py'));
const PY_LAUNCHER = pyCode(py('launcher/management_api/_sessions.py'));

describe('R1 — the sidebar is wired to the paging window helpers', () => {
  it('imports them from utils/sessionListWindow', () => {
    expect(SIDEBAR).toMatch(/from '\.\.\/\.\.\/utils\/sessionListWindow'/);
    for (const name of [
      'advanceLoadedRows',
      'appendSessionPage',
      'isNearListEnd',
      'mergeRefreshedPrefix',
      'refreshLimit',
      'SESSION_LIST_PAGE_SIZE',
    ]) {
      expect(SIDEBAR, `${name} must be imported`).toContain(name);
    }
  });

  it('has exactly one definition of the page size, in the helper module', () => {
    // A local re-declaration would silently drift from the server's cap math.
    expect(SIDEBAR).not.toMatch(/SESSION_LIST_PAGE_SIZE\s*=/);
    const helper = read('utils/sessionListWindow.ts');
    expect(helper).toMatch(/export const SESSION_LIST_PAGE_SIZE = \d+/);
    expect(helper).toMatch(/export const SESSION_LIST_MAX_LIMIT = 500/);
  });
});

describe('R2 — the refresh re-reads a window, not page 1', () => {
  it('asks for page 0 with a window-sized limit', () => {
    expect(SIDEBAR_TS).toContain('getSessionList(agentId, 0, refreshLimit(');
  });

  it('never issues a bare page-sized page-0 read', () => {
    // The exact shape that truncated a scrolled list back to one page.
    expect(SIDEBAR_TS).not.toMatch(/getSessionList\(agentId, 0, SESSION_LIST_PAGE_SIZE\)/);
  });
});

describe('R3 — paging state is a ref, not a dep-array-churning state', () => {
  it('keeps no numeric offset state', () => {
    expect(SIDEBAR).not.toMatch(/setOffset\(/);
    expect(SIDEBAR).toMatch(/loadedRowsRef = useRef\(0\)/);
  });

  it('guards overlapping fetches', () => {
    expect(SIDEBAR).toMatch(/fetchingRef = useRef\(false\)/);
    // loadMoreSessions must bail on an in-flight page.
    expect(SIDEBAR_TS).toMatch(/if \(!agentId \|\| fetchingRef\.current \|\| !hasMore\) return;/);
  });
});

describe('R4 — refresh merges, load-more advances by raw server rows', () => {
  it('merges the refreshed prefix instead of overwriting', () => {
    expect(SIDEBAR_TS).toContain('mergeRefreshedPrefix(prev, list)');
    // The old write was `setSessions((prev) => sessionsListEqual(prev, list) ? prev : list)`.
    expect(SIDEBAR_TS).not.toMatch(/sessionsListEqual\(prev, list\) \? prev : list\b/);
  });

  it('advances the cursor by the raw response length', () => {
    expect(SIDEBAR_TS).toContain('advanceLoadedRows(loadedRowsRef.current, raw.length)');
    // `raw` is the unfiltered page; `more`/`list` are post-filter and under-count.
    expect(SIDEBAR_TS).not.toMatch(/advanceLoadedRows\(loadedRowsRef\.current, (more|list)\.length\)/);
  });

  it('never resumes from a filtered length', () => {
    expect(SIDEBAR_TS).not.toMatch(/getSessionList\(agentId, (more|list)\.length/);
  });

  it('resets the window when the agent changes', () => {
    expect(SIDEBAR_TS).toMatch(/\}, \[agentId\]\);/);
    expect(SIDEBAR_TS).toContain('loadedRowsRef.current = 0');
  });
});

describe('R5 — `has_more` reports whether another page really exists', () => {
  it('gateway route over-fetches one row as the sentinel', () => {
    expect(flat(PY_ROUTE)).toContain('async_get_session_list(limit=limit + 1, offset=offset)');
    expect(flat(PY_ROUTE)).toContain('has_more = len(page) > limit');
    expect(flat(PY_ROUTE)).not.toContain('len(sessions) >= limit');
  });

  it('launcher session list does the same', () => {
    expect(flat(PY_LAUNCHER)).toContain('get_session_list(limit=limit + 1, offset=offset)');
    expect(flat(PY_LAUNCHER)).toContain('has_more = len(page) > limit');
    expect(flat(PY_LAUNCHER)).not.toContain('len(sessions) >= limit');
  });

  it('keeps the unlimited read path working (no limit → no has_more claim)', () => {
    expect(flat(PY_LAUNCHER)).toContain('get_session_list(limit=None, offset=offset)');
  });
});

describe('R6 — the row entrance animation is gated, so remounts cannot replay it', () => {
  it('scopes the keyframes to direct rows of a revealing column', () => {
    expect(CSS).toContain('.os-revealing > .timeline-row');
    expect(CSS).toContain('@keyframes os-row-reveal');
  });

  const ANIMATED = (() => {
    const blocks = [...CSS.matchAll(/([^{}]+)\{([^{}]*)\}/g)];
    return blocks
      .filter(([, , body]) => /animation[^;]*os-row-reveal/.test(body))
      .map(([, selector]) => selector.trim());
  })();

  it('has no rule animating rows outside the reveal gate', () => {
    // Every animating selector must depend on `.os-revealing`.
    expect(ANIMATED.length).toBeGreaterThan(0);
    for (const selector of ANIMATED) {
      expect(selector, `ungated reveal: ${selector}`).toContain('.os-revealing');
    }
  });

  it('respects prefers-reduced-motion', () => {
    const reduced = [...CSS.matchAll(/@media \(prefers-reduced-motion: reduce\)\s*\{([\s\S]*?)\n\}/g)]
      .map(([, body]) => body)
      .join('\n');
    expect(reduced).toContain('.os-revealing > .timeline-row');
    expect(reduced).toMatch(/animation:\s*none/);
  });

  it('opens the gate once per revealKey and clears it on a timer', () => {
    expect(TIMELINE).toContain('revealKey');
    // Content-gated: an empty pane must not spend the one-shot window.
    expect(TIMELINE).toMatch(/revealKey && entries\.length > 0 \? revealKey : null/);
    expect(TIMELINE).toMatch(/setTimeout\(\(\) => setRevealing\(false\), REVEAL_TOTAL_MS\)/);
    // The column class must be driven by the gate, not hard-coded.
    expect(TIMELINE).toMatch(/revealing \? 'os-revealing' : ''/);
  });

  it('stagger is capped and starts from the rendered window, not a global index', () => {
    expect(TIMELINE).toMatch(/REVEAL_MAX_STAGGERED_ROWS/);
    expect(TIMELINE).toMatch(/Math\.min\(ordinal, REVEAL_MAX_STAGGERED_ROWS\)/);
    expect(TIMELINE).toContain('--reveal-delay');
  });

  it('both timeline call sites opt in', () => {
    expect(read('components/ai-chat/SessionChatPane.tsx')).toContain('revealKey={sessionId}');
    expect(read('components/AIChatPage.tsx')).toContain('revealKey={currentSessionId}');
  });
});
