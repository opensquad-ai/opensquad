/**
 * Regression locks: per-round token usage (消耗 badge) data path.
 *
 * Backend emits a `turn_usage` session event right after a round's final
 * assistant message. Locked here:
 * - `stampTurnUsage` attaches the usage payload to the round's final
 *   assistant bubble, never crossing the user-message round boundary.
 * - `buildTimelineFromSession` pulls turn_usage OUT of the workflow event
 *   stream (no phantom workflow blocks after refresh) and stamps usage.
 * - Source locks: MessageBubble renders the badge + time popovers through
 *   HoverTooltip (click-toggle), and useAgentWebSocket patches the live
 *   timeline on `turn_usage` for the visible session only.
 */
import { describe, expect, it } from 'vitest';
import { buildTimelineFromSession, stampTurnUsage, type TimelineEntry } from './aiChatTimeline';

const msg = (role: 'user' | 'assistant', content: string, timestamp: string): any => ({
  role,
  content,
  type: 'text',
  timestamp,
});

const usageEvt = (timestamp: string, data: Record<string, number>): any => ({
  type: 'turn_usage',
  data,
  timestamp,
});

const usageData = {
  input_tokens: 8_192,
  output_tokens: 4_096,
  total_tokens: 12_288,
  elapsed_ms: 574_000,
  started_ms: 1_000,
  ended_ms: 575_000,
};

const assistantEntries = (timeline: TimelineEntry[]) =>
  timeline.filter((e) => e.kind === 'message' && (e.data as any).role === 'assistant') as Array<
    Extract<TimelineEntry, { kind: 'message' }> & { data: { usage?: Record<string, number> } }
  >;

describe('stampTurnUsage', () => {
  it('stamps the usage onto the nearest preceding assistant message', () => {
    const timeline: TimelineEntry[] = [
      { kind: 'message', data: msg('user', 'hi', '2026-09-13T10:00:00Z'), _uid: 'u1' },
      { kind: 'workflow', data: { events: [], completed: true }, _uid: 'w1' } as any,
      { kind: 'message', data: msg('assistant', 'done', '2026-09-13T10:09:00Z'), _uid: 'a1' },
    ];
    const stamped = stampTurnUsage(timeline, [usageEvt('2026-09-13T10:09:05Z', usageData)]);
    const assistants = assistantEntries(stamped);
    expect(assistants).toHaveLength(1);
    expect(assistants[0].data.usage?.total_tokens).toBe(12_288);
    expect(assistants[0].data.usage?.input_tokens).toBe(8_192);
    // Original array must not be mutated.
    expect(assistantEntries(timeline)[0].data.usage).toBeUndefined();
  });

  it('leaves the timeline untouched when no assistant bubble exists', () => {
    const timeline: TimelineEntry[] = [
      { kind: 'message', data: msg('user', 'next question', '2026-09-13T10:10:00Z'), _uid: 'u2' },
    ];
    const stamped = stampTurnUsage(timeline, [usageEvt('2026-09-13T10:11:00Z', usageData)]);
    expect(stamped).toEqual(timeline);
    expect((stamped[0].data as any).usage).toBeUndefined();
  });

  it('attributes two rounds to two different assistant bubbles', () => {
    const timeline: TimelineEntry[] = [
      { kind: 'message', data: msg('user', 'q1', '2026-09-13T10:00:00Z'), _uid: 'u1' },
      { kind: 'message', data: msg('assistant', 'r1', '2026-09-13T10:01:00Z'), _uid: 'a1' },
      { kind: 'message', data: msg('user', 'q2', '2026-09-13T10:02:00Z'), _uid: 'u2' },
      { kind: 'message', data: msg('assistant', 'r2', '2026-09-13T10:03:00Z'), _uid: 'a2' },
    ];
    const first = { ...usageData, total_tokens: 111 };
    const second = { ...usageData, total_tokens: 222 };
    const stamped = stampTurnUsage(timeline, [
      usageEvt('2026-09-13T10:01:30Z', first),
      usageEvt('2026-09-13T10:03:30Z', second),
    ]);
    const assistants = assistantEntries(stamped);
    expect(assistants[0].data.usage?.total_tokens).toBe(111);
    expect(assistants[1].data.usage?.total_tokens).toBe(222);
  });

  it('skips assistant bubbles newer than the usage event', () => {
    // Out-of-order disk array: usage lands BEFORE the bubble it belongs to is
    // pushed. The stamper must not attach usage to a bubble from the future.
    const timeline: TimelineEntry[] = [
      { kind: 'message', data: msg('user', 'q', '2026-09-13T10:00:00Z'), _uid: 'u1' },
      { kind: 'message', data: msg('assistant', 'later bubble', '2026-09-13T10:20:00Z'), _uid: 'a1' },
    ];
    const stamped = stampTurnUsage(timeline, [usageEvt('2026-09-13T10:05:00Z', usageData)]);
    expect(assistantEntries(stamped)[0].data.usage).toBeUndefined();
  });

  it('derives missing totals and elapsed from components', () => {
    const timeline: TimelineEntry[] = [
      { kind: 'message', data: msg('assistant', 'r', '2026-09-13T10:00:00Z'), _uid: 'a1' },
    ];
    const stamped = stampTurnUsage(timeline, [
      usageEvt('2026-09-13T10:01:00Z', { input_tokens: 100, output_tokens: 50, started_ms: 0, ended_ms: 30_000 }),
    ]);
    const usage = assistantEntries(stamped)[0].data.usage!;
    expect(usage.total_tokens).toBe(150);
    expect(usage.elapsed_ms).toBe(30_000);
  });
});

describe('buildTimelineFromSession × turn_usage', () => {
  it('stamps usage after refresh and creates no workflow block from it', () => {
    const timeline = buildTimelineFromSession(
      [
        msg('user', '帮我写个脚本', '2026-09-13T10:00:00Z'),
        msg('assistant', '写好了', '2026-09-13T10:09:00Z'),
      ],
      [
        usageEvt('2026-09-13T10:09:05Z', usageData),
      ],
    );
    const assistants = assistantEntries(timeline);
    expect(assistants).toHaveLength(1);
    expect(assistants[0].data.usage?.total_tokens).toBe(12_288);
    expect(timeline.some((e) => e.kind === 'workflow')).toBe(false);
  });

  it('keeps working when usage events are absent (old sessions)', () => {
    const timeline = buildTimelineFromSession(
      [msg('user', 'q', '2026-09-13T10:00:00Z'), msg('assistant', 'a', '2026-09-13T10:01:00Z')],
      [],
    );
    expect(assistantEntries(timeline)[0].data.usage).toBeUndefined();
  });
});

// ---- Source locks (the live path + UI wiring) ----

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const read = (p: string): string => readFileSync(resolve(__dirname, p), 'utf-8');

describe('source locks: UI + live wiring', () => {
  it('MessageBubble renders the usage badge via click-toggle HoverTooltip (assistant only)', () => {
    const src = read('../components/ai-chat/MessageBubble.tsx');
    expect(src).toContain('MessageUsage');
    expect(src).toContain('usage?: MessageUsage');
    // Badge is assistant-only and rides HoverTooltip with click toggle.
    const badgeBlock = src.slice(src.indexOf('usageBadge ='), src.indexOf('timeWithPopover ='));
    expect(badgeBlock).toContain('!isUser && message.usage');
    expect(badgeBlock).toContain('toggleOnClick');
    // i18n keys + the format helpers, asserted as ATOMIC substrings: prettier
    // may wrap `t('key')` / argument lists across lines, so a wrapped-call
    // substring would break on reformat for no real reason.
    expect(badgeBlock).toContain('aiChat.usage.input');
    expect(badgeBlock).toContain('aiChat.usage.output');
    expect(badgeBlock).toContain('formatTokenExact(message.usage.input_tokens)');
    expect(badgeBlock).toContain('formatTokenExact(message.usage.output_tokens)');
    expect(badgeBlock).toContain('msg-usage-badge');
    // Arrow direction is load-bearing and was inverted once in production:
    // ↓ marks the prompt tokens flowing INTO the model, ↑ the completion coming
    // back OUT. Compare on a whitespace-stripped copy so prettier wrapping the
    // `t('…')` call across lines cannot silently make the assertion vacuous.
    const compactBadge = badgeBlock.replace(/\s+/g, '');
    // The `,?` matters: prettier wraps the argument list, so the source really
    // is `t('aiChat.usage.output',)` — a plain substring check misses it.
    expect(compactBadge).toMatch(/↓\$\{t\('aiChat\.usage\.input',?\)\}/);
    expect(compactBadge).toMatch(/↑\$\{t\('aiChat\.usage\.output',?\)\}/);
    expect(compactBadge).not.toMatch(/↑\$\{t\('aiChat\.usage\.input'/);
    expect(compactBadge).not.toMatch(/↓\$\{t\('aiChat\.usage\.output'/);
    // Badge pill text: elapsed · compact total (reference UI "9m 34s · 3.0M").
    expect(badgeBlock).toContain('formatDuration(message.usage.elapsed_ms)');
    expect(badgeBlock).toContain('formatTokenCount(message.usage.total_tokens)');
    // Time popover shows the full zh-CN timestamp.
    const timeAt = src.indexOf('timeWithPopover =');
    expect(timeAt).toBeGreaterThan(-1);
    const timeBlock = src.slice(timeAt, src.indexOf('usageBadge', timeAt));
    expect(timeBlock).toContain('formatFullTimestamp(message.timestamp, i18n.language)');
    expect(timeBlock).toContain('toggleOnClick');
    // The classic action row renders both.
    const actionRow = src.slice(src.indexOf('const actionRow'), src.indexOf('if (isUser) {', 800));
    expect(actionRow).toContain('{usageBadge}');
    expect(actionRow).toContain('{timeWithPopover}');
  });

  it('HoverTooltip supports toggleOnClick', () => {
    const src = read('../components/HoverTooltip.tsx');
    expect(src).toContain('toggleOnClick');
    expect(src).toContain('setShow((prev) => !prev)');
  });

  it('HoverTooltip offers the CSS anchor strategy (always directly above)', () => {
    const src = read('../components/HoverTooltip.tsx');
    // The anchor branch must be pure CSS: a `relative` wrapper + a bubble
    // pinned with `bottom-full`. The measured `fixed` strategy lands off
    // target inside transformed ancestors, which is what put the footer
    // tooltip BELOW the trigger instead of above it.
    expect(src).toContain("strategy?: HoverTooltipStrategy");
    expect(src).toContain('absolute bottom-full left-0');
    expect(src).toContain('className="relative inline-flex items-center"');
    // The anchor branch must be REACHABLE (guarded by `if (anchored) {`, not
    // disabled) and must never wait for measured coordinates.
    expect(src).toContain('if (anchored) {');
    expect(src).toContain('if (show && !anchored) updatePos()');
  });

  it('footer tooltips are anchored, and the footer time has an explicit size', () => {
    const src = read('../components/ai-chat/MessageBubble.tsx');
    // Both footer popovers must opt into the anchor strategy.
    const badgeBlock = src.slice(src.indexOf('usageBadge ='), src.indexOf('timeWithPopover ='));
    expect(badgeBlock).toContain('strategy="anchor"');
    const timeBlock = src.slice(src.indexOf('timeWithPopover ='), src.indexOf('const mediaAndBody'));
    expect(timeBlock).toContain('strategy="anchor"');
    // The time must not inherit its font size: the classic action row sits
    // next to a text-[15px] body wrapper, so an unstyled span rendered the
    // footer clock far larger than the reference UI.
    expect(timeBlock).toContain('text-[11px]');
    // Badge is the reference pill ("9m 34s · 3.0M").
    expect(badgeBlock).toContain('rounded-full');
  });

  it('useAgentWebSocket patches the visible session timeline on turn_usage', () => {
    const src = read('../hooks/useAgentWebSocket.ts');
    const at = src.indexOf("onWs('turn_usage'");
    expect(at).toBeGreaterThan(-1);
    const block = src.slice(at, src.indexOf('unsubTurnCancelled', at));
    // sid guard: other panes must not leak into the focused timeline.
    expect(block).toContain('currentSessionIdRef.current');
    // Only assistant bubbles receive the badge (mirror of the disk stamper).
    expect(block).toContain("m.role !== 'assistant'");
    expect(block).toContain('usage');
  });

  it('buildTimelineFromSession strips turn_usage from the workflow stream', () => {
    const src = read('./aiChatTimeline.ts');
    expect(src).toContain("e?.type === 'turn_usage'");
    expect(src).toContain('stampTurnUsage(timeline, turnUsageEvents)');
    const sortedAt = src.indexOf('const sortedEvents = workflowEvents');
    expect(sortedAt).toBeGreaterThan(-1);
  });
});
