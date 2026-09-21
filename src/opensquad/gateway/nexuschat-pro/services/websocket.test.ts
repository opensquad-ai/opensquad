/**
 * Regression tests for wsService.connect() re-entrancy guard.
 *
 * Root cause (2026-09-21, duplicated group bubbles on PC): React StrictMode
 * double-invokes the App init effect in dev. The old guard only checked
 * readyState === OPEN, so a second connect() during the first socket's
 * CONNECTING handshake orphaned that socket — it stayed open and kept
 * delivering. The server then held N live connections for the user and every
 * broadcast was delivered N times into the same tab (rendered as N identical
 * bubbles; the rAF flush only deduped across batches, not within one).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const sockets: FakeSocket[] = [];

class FakeSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;
  readyState = 0; // CONNECTING
  onopen: (() => void) | null = null;
  onclose: ((e: { code: number; reason: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  onmessage: ((e: { data: string }) => void) | null = null;
  sent: string[] = [];
  constructor() {
    sockets.push(this);
  }
  send(data: string) {
    this.sent.push(data);
  }
  close() {
    this.readyState = 3;
  }
}

let getAuthTokenResult: string | null = 'tok';

vi.mock('./api', () => ({
  getAuthToken: () => getAuthTokenResult,
  WS_BASE_URL: 'ws://localhost.test',
}));

import { wsService } from './websocket';

describe('WebSocketService.connect re-entrancy guard', () => {
  let originalWS: unknown;

  beforeEach(() => {
    sockets.length = 0;
    wsService.disconnect(); // reset singleton state between tests
    originalWS = (globalThis as any).WebSocket;
    (globalThis as any).WebSocket = FakeSocket;
  });

  afterEach(() => {
    (globalThis as any).WebSocket = originalWS;
    vi.restoreAllMocks();
    getAuthTokenResult = 'tok';
  });

  it('does not create a second socket while the first is still CONNECTING', () => {
    const svc = wsService;
    svc.connect();
    svc.connect(); // StrictMode double-invoke: first socket still in handshake
    expect(sockets).toHaveLength(1);
  });

  it('does not create a second socket when already OPEN', () => {
    const svc = wsService;
    svc.connect();
    sockets[0].readyState = 1; // OPEN
    svc.connect();
    expect(sockets).toHaveLength(1);
  });

  it('skips connect without a token', () => {
    getAuthTokenResult = null;
    const svc = wsService;
    svc.connect();
    expect(sockets).toHaveLength(0);
  });

  it('allows reconnect after the previous socket closed', () => {
    const svc = wsService;
    svc.connect();
    sockets[0].readyState = 3; // CLOSED
    svc.connect();
    expect(sockets).toHaveLength(2);
  });
});
