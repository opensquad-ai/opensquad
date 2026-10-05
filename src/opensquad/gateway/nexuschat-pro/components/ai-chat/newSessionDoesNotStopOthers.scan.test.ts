/**
 * Starting a session in one pane must not stop a turn running in another.
 *
 * From the field log: starting a session in the deploy_test workspace sent
 *
 *   15:39:38 [Adapter] Stop task for session 20261005_073753_8kub by user 217223
 *   15:39:38 [Dispatcher] popped sid=…8kub content='__STOP__' busy=True stop=True
 *   15:39:38 [GatewayAdapter] Event turn_cancelled: {'sid': '…8kub', …}
 *
 * — the session of the *other* workspace, stopped because it happened to be the focused one when the
 * new session was created. The stop was correctly scoped to a session; the mistake was deciding to
 * send it at all. The parallel scheduler defers a busy session and runs it when a slot frees, so
 * nothing has to be stopped for a new session to start.
 */
import fs from 'fs';
import path from 'path';
import { describe, expect, it } from 'vitest';

const PAGE = fs.readFileSync(path.resolve(__dirname, '../AIChatPage.tsx'), 'utf8');

const anchor = PAGE.indexOf('must not stop a turn somewhere else');
const newSessionPath = PAGE.slice(anchor, anchor + 900);

describe('starting a new session', () => {
  it('does not stop another session', () => {
    expect(newSessionPath.length).toBeGreaterThan(0);
    expect(newSessionPath).not.toContain('stopTask');
    expect(newSessionPath).not.toContain('userStoppedBySidRef');
  });

  it('keeps the bookkeeping that follows', () => {
    expect(newSessionPath).toContain('clearSessionRunState(previousSid)');
  });

  it('says why, so the stop is not helpfully put back', () => {
    expect(newSessionPath).toContain('must not stop a turn somewhere else');
    expect(newSessionPath).toContain('parallel turn scheduler defers a busy session');
  });
});
