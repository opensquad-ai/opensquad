/**
 * When the backend is gone, the activity row must stop — not keep counting.
 *
 * An unfinished fold is *live by definition* in the activity row
 * (`stillLive = … || !block.completed`) and its elapsed is
 * `Date.now() - started_ms`, so a fold the backend never settled ticks forever:
 * killing the service mid-turn left a day-long "执行中 · 23h 48m 48s" on screen.
 *
 * The old disconnect path only sealed the sessions the client *believed* were
 * busy (`busySessionsRef`), and a stale/empty `busy_sessions` snapshot therefore
 * left the fold open for good. Sealing every live fold is what actually stops
 * the clock, and it has to wait out a grace window first — a blip reconnects in
 * a second and must not end a fold the agent is still working on.
 */
import fs from 'fs';
import path from 'path';
import { describe, expect, it } from 'vitest';

const read = (rel: string) => fs.readFileSync(path.resolve(__dirname, rel), 'utf8');

const HOOK = read('../hooks/useAgentWebSocket.ts');
const PAGE = read('../components/AIChatPage.tsx');

describe('backend-loss watchdog (useAgentWebSocket)', () => {
  it('waits out a grace window instead of sealing on the first close', () => {
    expect(HOOK).toContain('const BACKEND_LOST_GRACE_MS = 10_000;');

    const at = HOOK.indexOf("} else if (status === 'disconnected') {");
    expect(at).toBeGreaterThan(0);
    const branch = HOOK.slice(at, HOOK.indexOf("} else if (status === 'connecting')", at));
    expect(branch).toContain('armBackendLost();');
    // Sealing here would end a fold the agent is still working on during a blip,
    // and it only ever covered the client's idea of "busy".
    expect(branch).not.toContain('sealIncompleteWorkflows');
    expect(branch).not.toContain('busySessionsRef');
  });

  it('cancels the watchdog when the connection returns, and on cleanup', () => {
    const connectedAt = HOOK.indexOf("if (status === 'connected') {");
    expect(HOOK.slice(connectedAt, connectedAt + 200)).toContain('cancelBackendLost();');
    expect(HOOK).toContain('cancelBackendLost();\n      unsubAuthExpired();');
  });

  it('only fires when still disconnected, and through the ctx callback', () => {
    expect(HOOK).toContain('if (aiWsService.isConnected) return;');
    expect(HOOK).toContain('ctxRef.current.onBackendLost?.()');
  });
});

describe('handleBackendLost (AIChatPage)', () => {
  it('is wired into the WS ctx', () => {
    expect(PAGE).toContain('onBackendLost: handleBackendLost,');
  });

  it('seals every live fold, not just the client-declared busy sessions', () => {
    const at = PAGE.indexOf('const handleBackendLost = useCallback(');
    expect(at).toBeGreaterThan(0);
    const body = PAGE.slice(at, at + 2200);
    expect(body).toContain('Object.entries(buckets)');
    expect(body).toContain('sealIncompleteWorkflows(entries, sealOpts)');
    expect(body).toContain('setLiveTimelinesBySession(next)');
    // The focused pane's mirror must freeze too, or the visible row keeps ticking.
    expect(body).toContain('setTimelineState((prev) =>');
  });

  it('drops the per-session busy markers so nothing pulses', () => {
    const at = PAGE.indexOf('const handleBackendLost = useCallback(');
    const body = PAGE.slice(at, at + 2200);
    expect(body).toContain('busySessionsRef.current = [];');
    expect(body).toContain('isStreamingBySessionRef.current = {};');
    expect(body).toContain('turnStartedMsRef.current = undefined;');
  });
});
