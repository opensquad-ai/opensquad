/**
 * Regression lock: the「上下文详情」panel must follow the session whose footer
 * button opened it — never the focused live session.
 *
 * Failure this guards: in workspace-tab mode, switching session tabs only
 * updates pane/tab state; `currentSessionId` and the solo timeline are
 * deliberately left alone because re-pointing them remounts the live chatSlot
 * and re-triggers the global loading overlay (see handleContentTabSelect).
 * After an app restart `currentSessionId` stays pinned on the boot (latest)
 * session, so the panel showed that one session's context no matter which tab
 * was open — reported from a screenshot after a restart.
 */
import { describe, it, expect } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';

const SRC = fs.readFileSync(
  path.join(process.cwd(), 'components/AIChatPage.tsx'),
  'utf8',
);

/** The `<ContextViewer …/>` render site, sliced so assertions cannot be
 *  satisfied by some unrelated component's props elsewhere in the file. */
function viewerBlock(): string {
  const start = SRC.indexOf('<ContextViewer');
  expect(start, 'ContextViewer render site not found in AIChatPage').toBeGreaterThanOrEqual(0);
  const end = SRC.indexOf('/>', start);
  expect(end, 'ContextViewer JSX not closed').toBeGreaterThan(start);
  return SRC.slice(start, end);
}

describe('ContextViewer follows the session it was opened from', () => {
  it('panel sessionId is contextViewerSessionId; currentSessionId is fallback only', () => {
    const block = viewerBlock();
    expect(block).toMatch(/sessionId=\{contextViewerSessionId \?\? currentSessionId\}/);
    // The pre-fix wiring — the whole bug — must not come back.
    expect(block).not.toMatch(/sessionId=\{currentSessionId\}/);
  });

  it('entries and tokenStats resolve for that same viewer session', () => {
    const block = viewerBlock();
    expect(block).toMatch(
      /tokenStats=\{resolveTokenStatsForSession\(contextViewerSessionId \?\? currentSessionId\)\}/,
    );
    expect(block).toMatch(/entries=\{contextViewerEntries\}/);
    // contextEntries is derived from the solo timeline only; passing it here
    // reintroduces the bug for every non-focused session.
    expect(block).not.toMatch(/entries=\{contextEntries\}/);
  });

  it('the composer footer reports its own session, not the focused one', () => {
    // onViewReport lives inside renderComposer(sessionId) — the closure param
    // is the only session identity the footer button has.
    expect(SRC).toMatch(/setContextViewerSessionId\(sessionId\)/);
    expect(SRC).not.toMatch(/setContextViewerSessionId\(currentSessionId\)/);
    // Closing must clear the override so a legacy open path falls back to the
    // focused session instead of resurrecting a stale one.
    expect(SRC).toMatch(/setContextViewerSessionId\(null\)/);
  });

  it('non-current viewer sessions resolve from their own bucket or cache', () => {
    const start = SRC.indexOf('const contextViewerEntries');
    expect(start, 'contextViewerEntries memo not found').toBeGreaterThanOrEqual(0);
    const scope = SRC.slice(start, SRC.indexOf('}, [', start));
    // Same resolution order tokenStats uses (live bucket → disk cache), so the
    //「上下文构成」bars and the「原始消息」list always describe one session.
    expect(scope).toContain('pickSessionLiveTimeline(liveTimelinesBySession, sid)');
    expect(scope).toContain('getCachedSessionTimeline(agentId, sid)');
    expect(scope).toContain('if (!sid || sid === currentSessionId) return contextEntries');
  });

  it('solo timeline and per-session buckets share one flattening path', () => {
    expect(SRC).toContain(
      'flattenTimelineToContextEntries(timeline, currentSessionId)',
    );
    // Exactly one hand-written copy of the mapping may exist — a second one
    // is how the two entry shapes drift apart.
    const copies = SRC.match(/kind: entry\.data\.role as 'user' \| 'assistant'/g);
    expect(copies?.length ?? 0).toBe(1);
  });
});
