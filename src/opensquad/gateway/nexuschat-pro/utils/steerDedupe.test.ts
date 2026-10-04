/**
 * One steer, one row — however many times it is delivered.
 *
 * Reported from a real session: the same 私聊消息 row appeared again and again in the agent's tool
 * stream after switching back to the tab, and a page refresh collapsed them to one. A consumed
 * steer is broadcast to every client watching the agent and a reconnect replays what was missed,
 * while each delivery appended a fresh event with a new uid — nothing looked at the message id the
 * frame carries. A refresh rebuilds the row from disk, once, which is why it looked correct again.
 */
import fs from 'fs';
import path from 'path';
import { describe, expect, it } from 'vitest';

import {
  appendUserSteerToTimeline,
  makeUserSteerEvent,
  timelineHasSteer,
  type TimelineEntry,
} from './aiChatTimeline';

const openFold = (): TimelineEntry[] =>
  [{ kind: 'workflow', data: { completed: false, events: [] } } as unknown as TimelineEntry];

const events = (timeline: TimelineEntry[]) =>
  (timeline[0] as any).data.events as any[];

const steer = (message_id: string, text = '发个消息试试') => ({
  text,
  source: 'dm',
  sender_name: 'ss·gjxwzp',
  message_id,
});

describe('a re-delivered steer', () => {
  it('is appended once, and the second delivery is a no-op', () => {
    const first = appendUserSteerToTimeline(openFold(), steer('dm-1'));
    expect(first).not.toBeNull();
    expect(events(first!)).toHaveLength(1);

    const again = appendUserSteerToTimeline(first!, steer('dm-1'));

    expect(again).not.toBeNull();
    expect(events(again!)).toHaveLength(1);
    expect(events(again!)[0].content.message_id).toBe('dm-1');
  });

  it('survives a replay that arrives after the fold has sealed', () => {
    const sealed = [
      { kind: 'workflow', data: { completed: true, events: [makeUserSteerEvent(steer('dm-2'))] } },
    ] as unknown as TimelineEntry[];

    const again = appendUserSteerToTimeline(sealed, steer('dm-2'));

    expect(again).toBe(sealed);
    expect(events(again!)).toHaveLength(1);
  });

  it('does not swallow a genuinely different steer', () => {
    const first = appendUserSteerToTimeline(openFold(), steer('dm-3', 'one'));
    const second = appendUserSteerToTimeline(first!, steer('dm-4', 'two'));

    expect(events(second!)).toHaveLength(2);
  });

  it('keeps two steers with the same text but different ids', () => {
    const first = appendUserSteerToTimeline(openFold(), steer('dm-5', 'same words'));
    const second = appendUserSteerToTimeline(first!, steer('dm-6', 'same words'));

    expect(events(second!)).toHaveLength(2);
  });
});

describe('timelineHasSteer', () => {
  it('finds a steer in an open fold, in a sealed one, and on a fallback bubble', () => {
    const open = [{ kind: 'workflow', data: { completed: false, events: [makeUserSteerEvent(steer('a'))] } }];
    const sealed = [{ kind: 'workflow', data: { completed: true, events: [makeUserSteerEvent(steer('b'))] } }];
    const bubble = [{ kind: 'message', data: { message_id: 'c' } }];

    expect(timelineHasSteer(open as unknown as TimelineEntry[], 'a')).toBe(true);
    expect(timelineHasSteer(sealed as unknown as TimelineEntry[], 'b')).toBe(true);
    expect(timelineHasSteer(bubble as unknown as TimelineEntry[], 'c')).toBe(true);
  });

  it('says no for an unknown or empty id', () => {
    const timeline = [{ kind: 'workflow', data: { completed: false, events: [makeUserSteerEvent(steer('d'))] } }];

    expect(timelineHasSteer(timeline as unknown as TimelineEntry[], 'not-there')).toBe(false);
    expect(timelineHasSteer(timeline as unknown as TimelineEntry[], '')).toBe(false);
  });
});

describe('the frame the window listens to', () => {
  it('carries the message id through to the appended event', () => {
    const src = fs.readFileSync(path.resolve(__dirname, '../components/AIChatPage.tsx'), 'utf8');

    expect(src).toContain('message_id: messageId,');
    expect(src).toContain('if (messageId) {');
  });
});
