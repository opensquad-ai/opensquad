// @vitest-environment jsdom
/**
 * Context-usage panel: prompt-cache hit rate + input / output split.
 *
 * The panel used to show a single "本轮用量" total, which hides the number that
 * actually explains the bill: how much of the prompt was served from the
 * provider's cache. The split is now
 *
 *     input = 命中缓存 (cache_read) + 未命中缓存 (input − cache_read)
 *     hit rate = hit / (hit + miss)
 *
 * `cache_read_tokens` is a subset of the prompt tokens on every backend, and
 * `cache_miss_tokens` is computed server-side (`_provider_base.cache_miss_tokens`)
 * so the UI never has to re-derive a provider-specific rule — the fallback here
 * only keeps payloads from an older gateway rendering.
 *
 * This drives the real component through the real entry point: the popover is
 * behind a click, so `renderToStaticMarkup` cannot reach it.
 */
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { SoloContextFooter, type SoloTokenStats } from './SoloContextFooter';
import i18n from '../../i18n';

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}

const h = React.createElement;

let container: HTMLDivElement;
let root: Root;

/** 42% Full, ~96.0K / 228.0K — the numbers from the reported screenshot. */
const BASE: SoloTokenStats = {
  used: 96_000,
  max: 228_000,
  breakdown: {
    system: 16_000,
    tool_defs: 15_900,
    thought: 5_000,
    tool: 8_900,
    user: 17_900,
    response: 1_000,
    overhead: 946,
  },
};

function renderFooter(tokenStats: SoloTokenStats) {
  act(() => {
    root.render(
      h(SoloContextFooter, {
        cwd: null,
        tokenStats,
        onViewReport: () => {},
        onCompressContext: () => {},
      }),
    );
  });
  return container.textContent ?? '';
}

/** The token-ring button is the only control titled `aiChat.contextUsage`. */
function clickTokenRing() {
  const toggle = Array.from(container.querySelectorAll('button')).find(
    (b) => b.getAttribute('title') === i18n.t('aiChat.contextUsage'),
  );
  expect(toggle, 'the token-ring toggle must exist (canOpenTokenPanel)').toBeTruthy();
  act(() => {
    (toggle as HTMLButtonElement).click();
  });
  return container.textContent ?? '';
}

function openPanel(tokenStats: SoloTokenStats) {
  renderFooter(tokenStats);
  return clickTokenRing();
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  void i18n.changeLanguage('zh');
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe('cache split', () => {
  it('is behind the token-ring click, not always on screen', () => {
    const closed = renderFooter({ ...BASE, session: { input_tokens: 10_000, cache_read_tokens: 8_810 } });
    expect(closed).not.toContain('缓存命中率');
  });

  it('splits input into cached / uncached and reports the hit rate', () => {
    const text = openPanel({
      ...BASE,
      session: {
        total_tokens: 12_500,
        total_requests: 16,
        input_tokens: 10_000,
        output_tokens: 2_500,
        cache_read_tokens: 8_810,
      },
    });

    // Label and value are adjacent nodes, so each pair is contiguous in
    // textContent. Assert the *pair*: a bare `toContain('2.5K')` is satisfied
    // by the unrelated `12.5K` session total — a vacuous guard.
    expect(text).toContain('缓存命中率88.1%'); // 8810 / (8810 + 1190)
    expect(text).toContain('输入 · 命中缓存8.8K'); // 8 810 cached
    expect(text).toContain('输入 · 未命中缓存1.2K'); // 1 190 uncached
    expect(text).toContain('输入合计10.0K'); // hit + miss — the rows reconcile
    expect(text).toContain('输出2.5K'); // 2 500 output — not the 12.5K total
  });

  it('derives the miss half when an older payload has no cache_miss_tokens', () => {
    const text = openPanel({
      ...BASE,
      session: { input_tokens: 1_000, output_tokens: 100, cache_read_tokens: 250 },
    });
    expect(text).toContain('缓存命中率25.0%');
    expect(text).toContain('输入 · 未命中缓存750'); // input − hit, no server-provided miss
  });

  it('takes the server-computed miss count when present', () => {
    const text = openPanel({
      ...BASE,
      session: {
        input_tokens: 1_000,
        output_tokens: 100,
        cache_read_tokens: 250,
        cache_miss_tokens: 750,
      },
    });
    expect(text).toContain('缓存命中率25.0%');
    expect(text).toContain('输入 · 未命中缓存750');
  });

  it('reads a fully cached prompt as 100%, never above', () => {
    const text = openPanel({
      ...BASE,
      session: { input_tokens: 4_000, output_tokens: 10, cache_read_tokens: 4_000 },
    });
    expect(text).toContain('缓存命中率100.0%');
    expect(text).toContain('输入 · 未命中缓存0');
  });

  it('clamps a stale hit counter that exceeds the input count', () => {
    // Can happen for one turn after a provider-side eviction: the two counters
    // come from different responses. A negative miss would read as >100%.
    const text = openPanel({
      ...BASE,
      session: { input_tokens: 100, output_tokens: 10, cache_read_tokens: 500 },
    });
    // Label and value are adjacent nodes, so the pair is contiguous in textContent.
    expect(text).toContain('缓存命中率100.0%');
    expect(text).toContain('输入 · 未命中缓存0');
    // The total stays hit + miss, so the three input rows still reconcile.
    expect(text).toContain('输入合计500');
  });

  it('stays hidden while the session has no token accounting yet', () => {
    const text = openPanel({ ...BASE, session: { total_tokens: 0, total_requests: 0 } });
    expect(text).toContain('本轮用量'); // the pre-existing row is untouched
    expect(text).not.toContain('缓存命中率');
  });

  it('stays hidden when no session block was sent at all', () => {
    const text = openPanel({ ...BASE });
    expect(text).not.toContain('缓存命中率');
  });
});

/**
 * Control + treatment pair for the reported bug: a session showing
 * `0 命中缓存 / 1.6M 未命中` because the endpoint never sent a usage object.
 * Identical counters mean two very different things depending on provenance.
 */
const ZERO_HIT_SESSION = {
  input_tokens: 1_600_000,
  output_tokens: 6_600,
  cache_read_tokens: 0,
  cache_miss_tokens: 1_600_000,
};

describe('estimated usage', () => {
  it('prints a real 0% when the provider did report usage (control)', () => {
    // Positive control: the same counters are honest when they came from the
    // API. Without this, the treatment test below could pass on a typo.
    const text = openPanel({ ...BASE, session: { ...ZERO_HIT_SESSION, usage_estimated: false } });
    expect(text).toContain('缓存命中率0.0%');
    expect(text).toContain('输入 · 未命中缓存1.6M');
  });

  it('reports "unavailable" instead of a fabricated 0% for estimated turns', () => {
    const text = openPanel({
      ...BASE,
      session: { ...ZERO_HIT_SESSION, usage_estimated: true },
    });

    expect(text).toContain('缓存命中率—');
    expect(text).not.toContain('0.0%');
    // The split itself is a lie in this state, so neither row is rendered.
    expect(text).not.toContain('未命中缓存');
    expect(text).not.toContain('命中缓存');
    expect(text).toContain('该模型未返回用量数据');
    // The totals are still useful — they are just marked as approximations.
    expect(text).toContain('输入合计~1.6M');
    expect(text).toContain('输出~6.6K');
  });

  it('keeps the real split when a later turn starts reporting usage', () => {
    // `usage_estimated` is only set when a turn lacked usage, so a session that
    // recovers must go straight back to the split — no sticky "unavailable".
    const text = openPanel({
      ...BASE,
      session: { input_tokens: 1_600_000, output_tokens: 6_600, cache_read_tokens: 1_600_000 },
    });
    expect(text).toContain('缓存命中率100.0%');
    expect(text).toContain('输入 · 命中缓存1.6M');
  });
});
