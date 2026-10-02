/**
 * The task window's own rendering: board text goes through the shared Markdown
 * renderer (agents write it), and progress is drawn, not just printed as a number.
 *
 * The renderer itself needs a DOM (DOMPurify), and this suite runs in a node
 * environment, so it is mocked here: what is under test is that the window routes
 * text through it and injects the result with the markdown class.
 */
import fs from 'node:fs';
import path from 'node:path';

import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';

vi.mock('../utils/fencedMarkdown', () => ({
  AI_MARKDOWN_CLASS: 'ai-markdown',
  renderFencedMarkdown: (text: string) => `<h2>${text}</h2>`,
}));

import { MarkdownText, ProgressBar } from './CollabTaskWindow';

describe('collab task window', () => {
  it('renders board text through the markdown renderer, with the chat styling hook', () => {
    const html = renderToStaticMarkup(React.createElement(MarkdownText, { text: '主任务: 贪吃蛇游戏' }));

    expect(html).toContain('<h2>主任务: 贪吃蛇游戏</h2>');
    expect(html).toContain('ai-markdown');
  });

  it('renders nothing for an empty body', () => {
    expect(renderToStaticMarkup(React.createElement(MarkdownText, { text: '' }))).toBe('');
  });

  it('draws progress as a bar and clamps what the bar can show', () => {
    const html = renderToStaticMarkup(React.createElement(ProgressBar, { percent: 42 }));
    expect(html).toContain('width:42%');

    const over = renderToStaticMarkup(React.createElement(ProgressBar, { percent: 140 }));
    expect(over).toContain('width:100%');

    const missing = renderToStaticMarkup(React.createElement(ProgressBar, {}));
    expect(missing).toContain('width:0%');
  });

  it('the window and its item rows actually use them', () => {
    const src = fs.readFileSync(path.resolve(__dirname, 'CollabTaskWindow.tsx'), 'utf8');
    const item = src.slice(src.indexOf('const ItemBlock'), src.indexOf('const Section'));

    expect(item).toContain('<MarkdownText text={item.content}');
    expect(item).toContain('<ProgressBar percent={pct}');
    // the task's own progress is shown next to its status in the header
    expect(src).toContain('testId="collab-task-progress"');
    // approval and discussion text go through the same renderer (item rows,
    // approvals, discussion)
    expect(src.match(/<MarkdownText /g)?.length ?? 0).toBeGreaterThanOrEqual(3);
  });
});
