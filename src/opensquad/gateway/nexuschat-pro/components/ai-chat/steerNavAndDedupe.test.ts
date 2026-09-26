// @vitest-environment jsdom
/**
 * Interjections: one row inside the fold, one rail marker, one notice.
 *
 * Three separate reports, one shared cause — the same interjection reached the
 * screen by more than one route:
 *
 *   1. after a refresh the queue restored a consumed steer and drew it as an
 *      ordinary outgoing bubble *and* the fold drew its row;
 *   2. an embedded form posting the same payload again mid-turn queued another
 *      copy, so the "已提交" notice stacked up (the 1s double-fire window only
 *      catches a double-click);
 *   3. once (1) is fixed the rail lost the marker entirely, because it only
 *      walked user *messages*.
 *
 * Mutations verified (applied, run, reverted):
 *   M1 hydrate/save the queue without the `steered` filter     -> R4 fails
 *   M2 drop the `alreadyQueued` guard in submitEmbedForm       -> R4 fails
 *   M3 acceptFormSubmit ignores the payload key                -> R1 fails
 *   M4 the rail builder skips workflow entries                 -> R2 fails
 *   M5 jumpToNavNode never requests the reveal                 -> R3 fails
 */

import { readFileSync } from 'fs';
import path from 'path';
import { describe, expect, it, vi } from 'vitest';
import {
  buildUserNavNodesFromTimeline,
  steerNavAnchorDomId,
  steerNavNodeId,
  steerUidFromNavNodeId,
  jumpToNavNode,
} from './SoloUserNavRail';
import { requestSteerReveal, pendingSteerRevealId } from '../../utils/steerReveal';
import { acceptFormSubmit } from './HtmlEmbedBlock';

const STEER_TEXT = '检查好了没';

function timelineWithSteer() {
  return [
    { kind: 'message', _uid: 'u1', data: { role: 'user', content: '检查下当前目录项目' } },
    {
      kind: 'workflow',
      _uid: 'w1',
      data: {
        completed: true,
        events: [
          { type: 'tool_call', _uid: 't1', content: {} },
          { type: 'user_steer', _uid: 's1', content: { text: STEER_TEXT } },
          { type: 'tool_call', _uid: 't2', content: {} },
        ],
      },
    },
    { kind: 'message', _uid: 'u2', data: { role: 'user', content: '继续' } },
  ];
}

describe('acceptFormSubmit', () => {
  it('R1 — drops a double-fire, and the same payload re-posted mid-turn', () => {
    const now = 1_000_000;
    // Burst double-click.
    expect(acceptFormSubmit(null, now - 200, '{"a":1}', now)).toBe(false);
    // Same payload, well past the burst window but inside the identical window.
    expect(acceptFormSubmit({ key: '{"a":1}', at: now - 5_000 }, now - 20_000, '{"a":1}', now)).toBe(false);
    // A genuinely different payload right after one is a new submission.
    expect(acceptFormSubmit({ key: '{"a":1}', at: now - 5_000 }, now - 5_000, '{"b":2}', now)).toBe(true);
    // The same payload much later is allowed again (the user fixed a field).
    expect(acceptFormSubmit({ key: '{"a":1}', at: now - 30_000 }, now - 30_000, '{"a":1}', now)).toBe(true);
  });
});

describe('user nav rail', () => {
  it('R2 — a steer inside a fold still gets a marker, in reading order', () => {
    const nodes = buildUserNavNodesFromTimeline(timelineWithSteer());
    expect(nodes.map((n) => n.id)).toEqual(['u1', steerNavNodeId('s1'), 'u2']);
    expect(nodes.map((n) => n.kind)).toEqual(['message', 'steer', 'message']);
    expect(nodes[1].preview).toBe(STEER_TEXT);
    expect(steerUidFromNavNodeId(nodes[1].id)).toBe('s1');
    expect(steerUidFromNavNodeId(nodes[0].id)).toBeNull();
  });

  it('R2b — a blank steer adds no marker', () => {
    const tl = [
      {
        kind: 'workflow',
        _uid: 'w1',
        data: { events: [{ type: 'user_steer', _uid: 's1', content: { text: '   ' } }] },
      },
    ];
    expect(buildUserNavNodesFromTimeline(tl)).toEqual([]);
  });

  it('R3 — jumping to a steer asks its fold to open, then scrolls to the row', async () => {
    const uid = 's1';
    const anchor = document.createElement('div');
    anchor.id = steerNavAnchorDomId(uid);
    document.body.appendChild(anchor);

    const container = document.createElement('div');
    container.scrollTo = vi.fn();
    container.getBoundingClientRect = () => ({ top: 0 }) as DOMRect;
    anchor.getBoundingClientRect = () => ({ top: 120 }) as DOMRect;

    jumpToNavNode(container, steerNavNodeId(uid));
    expect(pendingSteerRevealId()).toBe(uid);
    await new Promise((r) => requestAnimationFrame(() => r(null)));
    await new Promise((r) => requestAnimationFrame(() => r(null)));
    expect(container.scrollTo).toHaveBeenCalled();
    anchor.remove();
  });

  it('R3b — a plain message jump does not request any reveal', () => {
    const before = pendingSteerRevealId();
    const container = document.createElement('div');
    container.scrollTo = vi.fn();
    jumpToNavNode(container, 'no-such-message');
    expect(pendingSteerRevealId()).toBe(before);
  });
});

describe('queue guards (source fences)', () => {
  const aiChat = readFileSync(
    path.resolve(__dirname, '../AIChatPage.tsx'),
    'utf-8',
  );

  it('R4 — a consumed steer is never restored or persisted as a queue item', () => {
    const steeredFilters = aiChat.match(/&&\s*!m\.steered/g) ?? [];
    expect(steeredFilters.length).toBeGreaterThanOrEqual(2); // stored key + legacy migration
    expect(aiChat).toMatch(/pendingMessages\.filter\(\(m\) => !m\.steered\)/);
  });

  it('R4b — an identical submission already queued is not queued twice', () => {
    expect(aiChat).toMatch(/const alreadyQueued = pendingMessagesRef\.current\.some\(/);
    expect(aiChat).toMatch(/m\.text === text/);
    expect(aiChat).toMatch(/!\s*alreadyQueued &&/);
  });
});
