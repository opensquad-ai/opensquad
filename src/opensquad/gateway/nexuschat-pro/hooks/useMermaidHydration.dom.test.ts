// @vitest-environment jsdom
/**
 * Warming is not gated on `enabled`: the chunk is fetched while the answer is still
 * streaming (placeholders exist mid-stream), and rendering starts the moment the caller
 * unlocks it. `React.createElement` rather than JSX: vitest's include glob is `**\/*.test.ts`.
 */
import { act } from 'react';
import React from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const mocks = vi.hoisted(() => ({ warm: vi.fn(), hydrate: vi.fn() }));

vi.mock('../utils/mermaidHydrate', () => ({
  warmMermaid: mocks.warm,
  hydrateMermaidIn: mocks.hydrate,
}));

import { useMermaidHydration } from './useMermaidHydration';

const MERMAID_HTML =
  '<div class="ai-mermaid" data-src="flowchart%20LR%0A%20%20A%20--%3E%20B">' +
  '<pre class="ai-mermaid-fallback"><code>flowchart LR</code></pre></div>';

let container: HTMLDivElement;
let root: Root;

function Host({ html, enabled }: { html: string; enabled: boolean }) {
  const ref = useMermaidHydration(html, enabled);
  return React.createElement('div', { ref });
}

const render = (html: string, enabled: boolean) =>
  act(() => {
    root.render(React.createElement(Host, { html, enabled }));
  });

beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.clearAllMocks();
});

describe('useMermaidHydration', () => {
  it('warms the chunk while the answer still streams, and does not render yet', () => {
    render(MERMAID_HTML, false);
    expect(mocks.warm).toHaveBeenCalled();
    expect(mocks.hydrate).not.toHaveBeenCalled();
  });

  it('renders as soon as the caller unlocks it', async () => {
    render(MERMAID_HTML, false);
    render(MERMAID_HTML, true);
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 5)); // the hook defers one frame
    });
    expect(mocks.hydrate).toHaveBeenCalledTimes(1);
  });

  it('leaves mermaid alone for ordinary markdown', () => {
    render('<p>hello</p>', true);
    expect(mocks.warm).not.toHaveBeenCalled();
    expect(mocks.hydrate).not.toHaveBeenCalled();
  });
});
