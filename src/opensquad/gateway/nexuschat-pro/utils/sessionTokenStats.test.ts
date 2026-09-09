import { describe, expect, it } from 'vitest';
import type { TimelineEntry } from './aiChatTimeline';
import {
  estimateConversationTokens,
  mergeSessionTokenStats,
  unwrapTokenStatsPayload,
} from './sessionTokenStats';

function msg(content: string, role: 'user' | 'assistant' = 'user'): TimelineEntry {
  return { kind: 'message', data: { role, content }, _uid: content };
}

describe('unwrapTokenStatsPayload', () => {
  it('reads flat content', () => {
    expect(unwrapTokenStatsPayload({ content: { used: 10, max: 100 } })).toEqual({
      used: 10,
      max: 100,
    });
  });

  it('unwraps EventBus { sid, data }', () => {
    expect(
      unwrapTokenStatsPayload({
        content: { sid: 'a', agent_id: 'x', data: { used: 12, max: 128 } },
      }),
    ).toEqual({ used: 12, max: 128 });
  });
});

describe('estimateConversationTokens', () => {
  it('is zero for empty timeline', () => {
    expect(estimateConversationTokens([])).toBe(0);
    expect(estimateConversationTokens(null)).toBe(0);
  });

  it('counts message chars / 4', () => {
    expect(estimateConversationTokens([msg('abcd')])).toBe(1);
    expect(estimateConversationTokens([msg('abcdefgh')])).toBe(2);
  });
});

describe('mergeSessionTokenStats', () => {
  it('lifts used when WS is only tools/system and timeline has chat', () => {
    const ws = {
      used: 12800,
      max: 128000,
      breakdown: { system: 2800, tool_defs: 10000, user: 0, thought: 0, tool: 0, response: 0 },
    };
    const long = 'x'.repeat(40000); // ~10k tokens
    const merged = mergeSessionTokenStats(ws, [msg(long)]);
    expect(merged?.used).toBe(12800 + 10000);
    expect(merged?.max).toBe(128000);
  });

  it('keeps WS used when it already includes the conversation', () => {
    const ws = {
      used: 50000,
      max: 128000,
      breakdown: { system: 2800, tool_defs: 10000, user: 20000, thought: 0, tool: 0, response: 17200 },
    };
    const merged = mergeSessionTokenStats(ws, [msg('hi')]);
    expect(merged).toBe(ws);
  });

  it('makes a short vs long session differ under the same WS snapshot', () => {
    const ws = { used: 12800, max: 128000, breakdown: { system: 2800, tool_defs: 10000 } };
    const short = mergeSessionTokenStats(ws, [msg('hi')]);
    const long = mergeSessionTokenStats(ws, [msg('y'.repeat(80000))]);
    expect(short?.used).toBeLessThan(long?.used || 0);
  });
});
