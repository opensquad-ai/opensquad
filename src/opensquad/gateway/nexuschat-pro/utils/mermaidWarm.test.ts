/**
 * The mermaid chunk is lazy (~276 KB). It used to be fetched only when the *first*
 * diagram was rendered, i.e. after the answer had finished streaming — that wait is what
 * read as "输出完了图还不出来" (2026-10-10). Warming is what moves it off that path, so
 * these pin that it happens once and that it can never break the message.
 */
import { describe, expect, it, vi } from 'vitest';

const importCount = vi.hoisted(() => ({ n: 0 }));

vi.mock('mermaid', () => {
  importCount.n += 1;
  return { default: { initialize: vi.fn(), render: vi.fn() } };
});

import { warmMermaid } from './mermaidHydrate';

describe('warmMermaid', () => {
  it('loads the chunk once, however often it is called', async () => {
    await warmMermaid();
    await warmMermaid();
    await warmMermaid();
    expect(importCount.n).toBe(1);
  });
});

describe('warmMermaid when the chunk cannot load', () => {
  it('never rejects — warming must not break the message that asked for it', async () => {
    vi.resetModules();
    vi.doMock('mermaid', () => {
      throw new Error('offline');
    });
    const fresh = await import('./mermaidHydrate');
    await expect(fresh.warmMermaid()).resolves.toBeUndefined();
  });
});
