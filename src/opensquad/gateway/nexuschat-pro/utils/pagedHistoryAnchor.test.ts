/**
 * Invariant lock: scrolling up must never re-render a message that is already
 * on screen.
 *
 * The reported bug — "会话内容往上翻会把自己发送的消息重复出现" — came from
 * `historyOffsetRef`: it counts loaded messages FROM THE TAIL, and the server
 * slices with the same convention (`end_idx = total - offset`). A live session
 * grows at the tail on every turn, and nothing advanced the offset for those
 * appends, so each appended message silently re-aimed the window backwards and
 * the next page overlapped bubbles that were already painted.
 *
 * The fix has two independent guards, both locked here:
 *   1. the page is anchored on a message identity (`before_id`) instead of a
 *      tail-relative count, so overlap is structurally impossible;
 *   2. the prepend seam drops anything the timeline already renders, so even a
 *      stale/absent anchor cannot produce a visible duplicate.
 */
import { describe, expect, it } from 'vitest';
import {
  buildTimelineFromSession,
  demoteIntermediateAssistantMessages,
  dropEntriesAlreadyPresent,
  mergeAdjacentWorkflowEntries,
  sessionMessageIdentity,
  type TimelineEntry,
} from './aiChatTimeline';

type Rec = Record<string, any>;

const PAGE = 80;

/** Server double mirroring `AgentSessionReader.get_session_history_paged`. */
function serverPage(
  all: Rec[],
  opts: { offset?: number; limit?: number; beforeId?: string | null },
) {
  const limit = opts.limit ?? 50;
  const total = all.length;
  let endIdx = total - (opts.offset ?? 0);
  if (opts.beforeId) {
    const idx = all.findIndex((m) => sessionMessageIdentity(m) === opts.beforeId);
    if (idx >= 0) endIdx = idx;
  }
  const startIdx = Math.max(0, endIdx - limit);
  const messages = endIdx <= 0 ? [] : all.slice(startIdx, endIdx);
  return { messages, events: [] as Rec[], has_more: startIdx > 0 };
}

/** Client double mirroring `AIChatPage.loadMoreHistory`. */
function loadMoreHistory(
  prev: TimelineEntry[],
  all: Rec[],
  state: { offset: number; anchor: string | null },
) {
  const resp = serverPage(all, { offset: state.offset, limit: 50, beforeId: state.anchor });
  const older = buildTimelineFromSession(resp.messages, resp.events);
  state.anchor = sessionMessageIdentity(resp.messages[0]) || state.anchor;
  state.offset += resp.messages.length;
  const next = mergeAdjacentWorkflowEntries(
    demoteIntermediateAssistantMessages([
      ...dropEntriesAlreadyPresent(older, prev),
      ...prev,
    ]),
  );
  return { next, hasMore: resp.has_more, pageSize: resp.messages.length };
}

function buildSession(turns: number): Rec[] {
  const all: Rec[] = [];
  for (let i = 0; i < turns; i += 1) {
    all.push({
      role: 'user',
      content: `用户消息 ${i}`,
      message_id: `u-${i}`,
      timestamp: `2026-09-20T10:${String(i % 60).padStart(2, '0')}:00Z`,
    });
    all.push({
      role: 'assistant',
      content: `回答 ${i}`,
      message_id: `a-${i}`,
      timestamp: `2026-09-20T10:${String(i % 60).padStart(2, '0')}:01Z`,
    });
  }
  return all;
}

function identitiesRendered(entries: TimelineEntry[]): string[] {
  return entries
    .filter((e) => e.kind === 'message')
    .map((e) => sessionMessageIdentity(e.data as Rec));
}

function duplicates(ids: string[]): string[] {
  const seen = new Set<string>();
  const dup = new Set<string>();
  for (const id of ids) {
    if (!id) continue;
    if (seen.has(id)) dup.add(id);
    seen.add(id);
  }
  return [...dup];
}

describe('scroll-up paging never repeats a rendered message', () => {
  it('a tail-relative offset drifts once the live turn appends (the bug)', () => {
    const all = buildSession(60); // 120 messages
    // First paint: the newest 80.
    const first = serverPage(all, { offset: 0, limit: PAGE });
    const painted = buildTimelineFromSession(first.messages, first.events);
    const offsetAfterPaint = first.messages.length; // historyOffsetRef.current

    // Live turn appends 4 messages (2 exchanges) — offset is NOT advanced.
    all.push(
      { role: 'user', content: '新问题 1', message_id: 'u-live-1', timestamp: '2026-09-20T11:00:00Z' },
      { role: 'assistant', content: '新回答 1', message_id: 'a-live-1', timestamp: '2026-09-20T11:00:01Z' },
      { role: 'user', content: '新问题 2', message_id: 'u-live-2', timestamp: '2026-09-20T11:01:00Z' },
      { role: 'assistant', content: '新回答 2', message_id: 'a-live-2', timestamp: '2026-09-20T11:01:01Z' },
    );
    const onScreen = [...painted]; // the pane now shows the painted window
    const renderedBefore = new Set(identitiesRendered(onScreen));

    const drifted = serverPage(all, { offset: offsetAfterPaint, limit: 50 });
    const overlap = drifted.messages
      .map(sessionMessageIdentity)
      .filter((id) => renderedBefore.has(id));
    expect(overlap.length).toBeGreaterThan(0);

    // And with no seam guard, the drifted page really does double-render.
    const older = buildTimelineFromSession(drifted.messages, drifted.events);
    const naive = demoteIntermediateAssistantMessages([...older, ...onScreen]);
    expect(duplicates(identitiesRendered(naive)).length).toBeGreaterThan(0);
  });

  it('an id-anchored page cannot overlap, however much was appended', () => {
    const all = buildSession(60);
    const first = serverPage(all, { offset: 0, limit: PAGE });
    let prev = buildTimelineFromSession(first.messages, first.events);
    const state = {
      offset: first.messages.length,
      anchor: sessionMessageIdentity(first.messages[0]),
    };

    for (let round = 0; round < 4; round += 1) {
      // Appends land between pages — the drift trigger. They grow the server
      // tail AND appear on screen immediately as live bubbles.
      const live: Rec[] = [];
      for (let k = 0; k < 3; k += 1) {
        const m = {
          role: 'user',
          content: `追加 ${round}-${k}`,
          message_id: `live-${round}-${k}`,
          timestamp: `2026-09-20T12:${String(round).padStart(2, '0')}:0${k}Z`,
        };
        all.push(m);
        live.push(m);
      }
      prev = [...prev, ...buildTimelineFromSession(live, [])];

      const { next } = loadMoreHistory(prev, all, state);
      // The page must contribute NEW identities only — none of the bubbles
      // already on screen (including the just-sent ones) may come back.
      expect(duplicates(identitiesRendered(next))).toEqual([]);
      expect(next.length).toBeGreaterThanOrEqual(prev.length);
      prev = next;
    }

    expect(duplicates(identitiesRendered(prev))).toEqual([]);
    // Every user turn of the original session is on screen exactly once.
    const ids = identitiesRendered(prev);
    for (let i = 0; i < 60; i += 1) {
      expect(ids.filter((id) => id === `u-${i}`)).toHaveLength(1);
    }
  });

  it('a stale anchor still cannot double-render, thanks to the seam guard', () => {
    // Belt-and-braces: an old cache (or a rotated file) can leave us with no
    // anchor at all, so the request falls back to the drifting offset. The
    // prepend guard must still keep the pane duplicate-free.
    const all = buildSession(60);
    const first = serverPage(all, { offset: 0, limit: PAGE });
    let prev = buildTimelineFromSession(first.messages, first.events);
    const state = { offset: first.messages.length, anchor: null as string | null };

    const live: Rec[] = [];
    for (let k = 0; k < 3; k += 1) {
      const m = {
        role: 'user',
        content: `新发送 ${k}`,
        message_id: `live-${k}`,
        timestamp: `2026-09-20T14:00:0${k}Z`,
      };
      all.push(m);
      live.push(m);
    }
    prev = [...prev, ...buildTimelineFromSession(live, [])];

    // The un-anchored window really does overlap what is on screen …
    const onScreen = new Set(identitiesRendered(prev));
    const drifted = serverPage(all, { offset: state.offset, limit: 50, beforeId: null });
    expect(drifted.messages.map(sessionMessageIdentity).filter((id) => onScreen.has(id)))
      .not.toHaveLength(0);

    // … yet the pane stays duplicate-free.
    const { next } = loadMoreHistory(prev, all, state);
    expect(duplicates(identitiesRendered(next))).toEqual([]);
  });

  it('paging to the very top renders every message exactly once', () => {
    const all = buildSession(90); // 180 messages
    const first = serverPage(all, { offset: 0, limit: PAGE });
    let prev = buildTimelineFromSession(first.messages, first.events);
    const state = {
      offset: first.messages.length,
      anchor: sessionMessageIdentity(first.messages[0]),
    };

    let hasMore = first.has_more;
    let guard = 0;
    while (hasMore && guard < 40) {
      // Grow the tail between every page — worst case for a drifting offset.
      const m = {
        role: 'user',
        content: `尾部 ${guard}`,
        message_id: `tail-${guard}`,
        timestamp: `2026-09-20T13:${String(guard).padStart(2, '0')}:00Z`,
      };
      all.push(m);
      prev = [...prev, ...buildTimelineFromSession([m], [])];

      const out = loadMoreHistory(prev, all, state);
      prev = out.next;
      hasMore = out.hasMore;
      guard += 1;
    }

    const ids = identitiesRendered(prev);
    expect(duplicates(ids)).toEqual([]);
    // Nothing was skipped either — the whole session is on screen.
    expect(new Set(ids).size).toBe(all.length);
  });
});

describe('dropEntriesAlreadyPresent', () => {
  const msg = (over: Rec = {}): TimelineEntry => ({
    kind: 'message',
    _uid: over.message_id || 'x',
    data: { role: 'user', content: 'hi', timestamp: 't', ...over } as any,
  });

  it('drops identity duplicates regardless of position', () => {
    const existing = [msg({ message_id: 'a' }), msg({ message_id: 'b' })];
    const older = [msg({ message_id: 'b' }), msg({ message_id: 'c' })];
    expect(dropEntriesAlreadyPresent(older, existing).map((e) => e._uid)).toEqual(['c']);
  });

  it('falls back to role+content+timestamp when no identity exists', () => {
    const existing = [msg({ content: '无 id', timestamp: '2026-01-01T00:00:00Z' })];
    const older = [
      msg({ content: '无 id', timestamp: '2026-01-01T00:00:00Z' }),
      msg({ content: '无 id', timestamp: '2026-01-02T00:00:00Z' }),
    ];
    const out = dropEntriesAlreadyPresent(older, existing);
    expect(out).toHaveLength(1);
    expect((out[0].data as Rec).timestamp).toBe('2026-01-02T00:00:00Z');
  });

  it('never collapses id-less repeats without a timestamp to disambiguate', () => {
    const existing = [msg({ content: 'same', timestamp: '' })];
    const older = [msg({ content: 'same', timestamp: '' })];
    expect(dropEntriesAlreadyPresent(older, existing)).toHaveLength(1);
  });

  it('keeps the first copy and preserves relative order', () => {
    const existing: TimelineEntry[] = [];
    const older = [msg({ message_id: 'a' }), msg({ message_id: 'a' }), msg({ message_id: 'b' })];
    expect(dropEntriesAlreadyPresent(older, existing).map((e) => e._uid)).toEqual(['a', 'b']);
  });
});

describe('sessionMessageIdentity', () => {
  it('mirrors buildTimelineFromSession precedence', () => {
    expect(sessionMessageIdentity({ message_id: 'm', client_id: 'c', id: 'i' })).toBe('m');
    expect(sessionMessageIdentity({ client_id: 'c', id: 'i' })).toBe('c');
    expect(sessionMessageIdentity({ id: 'i' })).toBe('i');
    expect(sessionMessageIdentity({ extra: { message_id: 'em' } })).toBe('em');
    expect(sessionMessageIdentity({})).toBe('');
    expect(sessionMessageIdentity(null)).toBe('');
  });

  it('stamps the same identity onto the built entry', () => {
    const entries = buildTimelineFromSession(
      [{ role: 'user', content: 'x', client_id: 'cid-1', timestamp: 't' }],
      [],
    );
    expect(entries).toHaveLength(1);
    expect((entries[0].data as Rec).message_id).toBe('cid-1');
    expect(entries[0]._uid).toBe('cid-1');
  });
});
