// @vitest-environment jsdom
/**
 * Unit tests for fenced Markdown → highlighted code HTML.
 *
 * MUST run on jsdom, not happy-dom (2026-09-13). happy-dom 20.14.3 breaks
 * DOMPurify in two independent ways, and the failure mode is *silent*:
 *
 *  1. It defines `nodeName` as a getter on the base `Node.prototype` that just
 *     returns `''` (real value lives on the subclass prototypes). DOMPurify
 *     deliberately caches that getter from `Node.prototype` to resist DOM
 *     clobbering, so it reads `tagName === ''` for every element, treats every
 *     element as disallowed and strips it.
 *  2. Its `NodeIterator.nextNode()` aborts the walk as soon as the current node
 *     is removed (verified with a raw iterator: `DIV,a` vs `DIV,a,b`). So the
 *     walk dies at the first element and the rest of the payload is returned
 *     *unsanitized*.
 *
 * Net effect under happy-dom: the first rendered element vanishes and
 * `<iframe>` / `onerror` / `javascript:` survive — which looks exactly like a
 * broken sanitizer. It is not: the same assertions pass against the real
 * renderer in Chromium and under jsdom. Keep this file on jsdom.
 */
import { describe, expect, it } from 'vitest';
import { closeOpenCodeFences, renderFencedMarkdown } from './fencedMarkdown';

describe('closeOpenCodeFences', () => {
  it('leaves balanced fences alone', () => {
    const src = 'before\n```html\n<div></div>\n```\nafter';
    expect(closeOpenCodeFences(src)).toBe(src);
  });

  it('closes an open fence for streaming', () => {
    const src = '```python\nprint(1)';
    expect(closeOpenCodeFences(src)).toBe('```python\nprint(1)\n```');
  });
});

describe('renderFencedMarkdown', () => {
  it('renders ```html as a code block with language label', () => {
    const html = renderFencedMarkdown('见示例：\n\n```html\n<!DOCTYPE html>\n<html></html>\n```\n');
    expect(html).toContain('ai-code-wrap');
    expect(html).toContain('ai-code-lang');
    expect(html).toMatch(/html/i);
    expect(html).toContain('ai-code-block');
    expect(html).not.toContain('<!DOCTYPE html>'); // escaped / highlighted, not raw DOM
  });

  it('renders ```python as a highlighted code block', () => {
    const html = renderFencedMarkdown('```python\ndef hello():\n    return 1\n```');
    expect(html).toContain('ai-code-wrap');
    expect(html).toContain('language-python');
    expect(html).toContain('hljs');
  });

  it('emits mermaid placeholders with a source preview until hydrate', () => {
    const html = renderFencedMarkdown(
      '流程：\n\n```mermaid\nflowchart TD\n  A[User] --> B[Agent]\n```\n',
    );
    expect(html).toContain('ai-mermaid');
    expect(html).toContain('data-src=');
    expect(html).toContain('ai-mermaid-fallback');
    expect(html).toContain('flowchart TD');
    expect(html).not.toContain('ai-code-wrap');
    expect(decodeURIComponent(/data-src="([^"]+)"/.exec(html)?.[1] || '')).toContain('flowchart TD');
  });

  it('wraps GFM tables in the scroll box the table CSS targets', () => {
    const html = renderFencedMarkdown(
      '| 代码 | 名称 | 依据原句 |\n| --- | --- | --- |\n| 688131 | 皓元医药 | 年报命中 AI 制药 4 词 |\n',
    );
    expect(html).toMatch(/<div class="ai-table-wrap"><table[^>]*>/);
    expect(html).toContain('</table></div>');
    expect(html).toContain('<th>代码</th>');
    expect(html).toContain('688131');
  });

  it('leaves a <table> written inside a fenced block alone', () => {
    const html = renderFencedMarkdown('```html\n<table><tr><td>x</td></tr></table>\n```');
    expect(html).toContain('ai-code-wrap');
    expect(html).not.toContain('ai-table-wrap');
  });
});

/**
 * Model replies and tool results reach `dangerouslySetInnerHTML` verbatim, and
 * marked passes raw HTML through — so this output must be sanitized.
 */
describe('renderFencedMarkdown sanitization', () => {
  it('drops inline event handlers but keeps the element', () => {
    const html = renderFencedMarkdown('hi <img src="x" onerror="alert(1)"> there');
    expect(html).not.toContain('onerror');
    expect(html).toContain('<img');
    expect(html).toContain('there');
  });

  it('drops javascript: URLs', () => {
    const html = renderFencedMarkdown('<a href="javascript:alert(1)">x</a>');
    expect(html).not.toContain('javascript:');
  });

  it('removes script and iframe elements', () => {
    const html = renderFencedMarkdown(
      '<script>alert(1)</script><iframe src="https://evil.example"></iframe>ok',
    );
    expect(html).not.toContain('<script');
    expect(html).not.toContain('<iframe');
    expect(html).toContain('ok');
  });

  it('preserves our own renderer markup after sanitizing', () => {
    const html = renderFencedMarkdown('```python\nprint(1)\n```');
    expect(html).toContain('ai-code-wrap');
    expect(html).toContain('hljs');
  });
});
