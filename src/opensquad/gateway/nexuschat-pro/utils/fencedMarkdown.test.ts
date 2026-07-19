/**
 * Unit tests for fenced Markdown → highlighted code HTML.
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

  it('emits mermaid placeholders instead of highlighted code', () => {
    const html = renderFencedMarkdown(
      '流程：\n\n```mermaid\nflowchart TD\n  A[User] --> B[Agent]\n```\n',
    );
    expect(html).toContain('ai-mermaid');
    expect(html).toContain('data-src=');
    expect(html).not.toContain('ai-code-wrap');
    expect(decodeURIComponent(/data-src="([^"]+)"/.exec(html)?.[1] || '')).toContain('flowchart TD');
  });
});
