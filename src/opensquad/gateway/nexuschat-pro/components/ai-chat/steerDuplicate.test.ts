/**
 * A steer (插话 / a chat message that arrived mid-turn) must be ONE row, on both paths.
 *
 * Field report: opening an agent web session sometimes showed the same 群消息 card several times
 * and the conversation grew on every adopt — a page refresh fixed it. Cause: the live row came from
 * `makeUserSteerEvent` (which carried the source message id) while the rebuilt one came from
 * `convertSessionEventsToWorkflow` (which dropped it and minted a fresh `_uid`), so `adoptSession`'s
 * `[...entries, ...liveWfs]` could not tell they were the same steer and rendered both. A refresh
 * has no live rows, which is why it looked fine.
 *
 * These tests pin the identity itself — if both paths agree on it, the merge has something to dedupe
 * on; if one of them mints a fresh id again, this fails immediately.
 */
import { describe, expect, it } from 'vitest';
import fs from 'fs';
import path from 'path';

import { convertSessionEventsToWorkflow, makeUserSteerEvent } from '../../utils/aiChatTimeline';

const PAGE = fs.readFileSync(path.resolve(__dirname, '../AIChatPage.tsx'), 'utf8');

describe('steer identity', () => {
  it('is the same on the live path and the rebuilt path', () => {
    const live = makeUserSteerEvent({ text: 'hi', message_id: 'msg-1' });
    const rebuilt = convertSessionEventsToWorkflow([
      { type: 'user_steer', data: { text: 'hi', message_id: 'msg-1' }, timestamp: 1 },
    ]);

    expect(rebuilt).toHaveLength(1);
    expect(live._uid).toBe('steer:msg-1');
    expect(rebuilt[0]._uid).toBe('steer:msg-1');
    expect(rebuilt[0]._uid).toBe(live._uid);
  });

  it('keeps the source message id on the rebuilt row', () => {
    const [row] = convertSessionEventsToWorkflow([
      { type: 'user_steer', data: { text: 'hi', source: 'group', message_id: 'msg-9' }, timestamp: 1 },
    ]);
    expect((row.content as { message_id?: string }).message_id).toBe('msg-9');
    expect((row.content as { source?: string }).source).toBe('group');
  });

  it('still works without an id (manual steer)', () => {
    const live = makeUserSteerEvent('just words');
    const [row] = convertSessionEventsToWorkflow([
      { type: 'user_steer', data: 'just words', timestamp: 1 },
    ]);
    expect(live._uid).toBeTruthy();
    expect(row._uid).toBeTruthy();
    expect(live._uid).not.toBe('steer:');
  });

  it('reads the id from either shape the event may use', () => {
    const [nested] = convertSessionEventsToWorkflow([
      { type: 'user_steer', data: { text: 'x', content: { message_id: 'msg-7' } }, timestamp: 1 },
    ]);
    expect(nested._uid).toBe('steer:msg-7');
  });

  it('dedupes the live fold against the rebuilt rows when adopting a session', () => {
    // The merge site is the only place the two paths meet; it has to drop a live steer
    // whose id the rebuilt timeline already carries.
    const start = PAGE.indexOf('const adoptSession = (');
    const block = PAGE.slice(start, start + 4000);
    expect(block).toContain('[...entries, ...dedupedLive]');
    expect(block).toContain('seenSteers');
    expect(
      /user_steer/.test(block) && /message_id/.test(block),
      'the adopt merge must compare user_steer message ids before concatenating',
    ).toBe(true);
  });
});
