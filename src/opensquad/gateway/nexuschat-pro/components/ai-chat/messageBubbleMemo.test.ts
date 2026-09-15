// @vitest-environment jsdom
//
// Regression lock for the AI-chat 消耗 badge.
//
// The badge data (`message.usage`) is patched ONTO AN ALREADY-MOUNTED row — the
// live `turn_usage` WS frame does `{ ...m, usage }` inside setTimeline, and a
// session-history re-hydrate rebuilds the same entry with a new object identity
// but identical text. The React.memo comparator is the only gate between that
// new object and the DOM, so it MUST compare `usage`; otherwise React sees
// "nothing rendered changed", skips the render, and the badge never appears —
// which is exactly the bug this locks shut.

import { describe, expect, it } from 'vitest';

import {
  areMessageBubblePropsEqual,
  usageMemoKey,
  type ChatMessage,
  type MessageBubbleProps,
} from './MessageBubble';

const USAGE = { input_tokens: 58315, output_tokens: 46, total_tokens: 58361, elapsed_ms: 10569 };

const mk = (over: Partial<ChatMessage> = {}): ChatMessage => ({
  role: 'assistant',
  content: 'hello',
  timestamp: '2026-09-14T05:55:09Z',
  ...over,
});

const props = (over: Partial<ChatMessage> = {}): MessageBubbleProps => ({ message: mk(over) });

describe('MessageBubble memo comparator', () => {
  it('treats two structurally identical prop sets as equal', () => {
    expect(areMessageBubblePropsEqual(props(), props())).toBe(true);
  });

  it('usageMemoKey: undefined differs from stamped, equal numbers collapse', () => {
    expect(usageMemoKey(undefined)).toBe('');
    expect(usageMemoKey(USAGE)).not.toBe('');
    expect(usageMemoKey(USAGE)).toBe(usageMemoKey({ ...USAGE }));
    expect(usageMemoKey(USAGE)).not.toBe(usageMemoKey({ ...USAGE, elapsed_ms: 1 }));
  });

  it('breaks equality when usage is stamped in place (the regression)', () => {
    expect(areMessageBubblePropsEqual(props(), props({ usage: USAGE }))).toBe(false);
    expect(areMessageBubblePropsEqual(props({ usage: USAGE }), props())).toBe(false);
  });

  it('breaks equality when usage numbers change', () => {
    expect(
      areMessageBubblePropsEqual(props({ usage: USAGE }), props({ usage: { ...USAGE, total_tokens: 1 } })),
    ).toBe(false);
  });

  it('breaks equality when the timestamp changes (full-date footer popover)', () => {
    expect(areMessageBubblePropsEqual(props(), props({ timestamp: '2026-09-14T05:55:10Z' }))).toBe(false);
  });

  it('still breaks equality for content / media / end_task', () => {
    expect(areMessageBubblePropsEqual(props(), props({ content: 'other' }))).toBe(false);
    expect(areMessageBubblePropsEqual(props(), props({ output_images: ['a.png'] }))).toBe(false);
    expect(areMessageBubblePropsEqual(props(), props({ end_task: true }))).toBe(false);
  });

  it('ignores onWithdraw identity (inline lambda is normal)', () => {
    const a: MessageBubbleProps = { message: mk(), onWithdraw: () => {} };
    const b: MessageBubbleProps = { message: mk(), onWithdraw: () => {} };
    expect(areMessageBubblePropsEqual(a, b)).toBe(true);
  });
});
