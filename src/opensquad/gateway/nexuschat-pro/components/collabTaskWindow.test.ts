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

import { DiscussionBubble, ItemBlock, MarkdownText, ProgressBar } from './CollabTaskWindow';

function boardItem(over: Record<string, unknown> = {}) {
  return {
    id: 'a1',
    collab_id: 'T1',
    agent_id: 'pm',
    item_type: 'task',
    title: '贪吃蛇游戏实现',
    content: '## 主任务\n**负责人**: pm',
    status: 'pending',
    progress: 0,
    visibility: 'public',
    created_at: '2026-10-02T10:00:00Z',
    ...over,
  } as never;
}

function discussionItem(over: Record<string, unknown> = {}) {
  return {
    id: 'i1',
    collab_id: 'T1',
    agent_id: 'ss',
    item_type: 'discussion',
    title: 'User',
    content: '这里能看到吗',
    status: 'info',
    progress: 0,
    visibility: 'public',
    created_at: '2026-10-02T10:00:00Z',
    ...over,
  } as never;
}

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

  it('draws a message as the group chat does — own on the right, others on the left', () => {
    const mine = renderToStaticMarkup(React.createElement(DiscussionBubble, { item: discussionItem(), self: true }));
    expect(mine).toContain('bg-chatBubbleSelf');
    expect(mine).toContain('flex-row-reverse');
    expect(mine).toContain('data-self="1"');

    const theirs = renderToStaticMarkup(
      React.createElement(DiscussionBubble, { item: discussionItem({ agent_id: 'agent305' }), self: false }),
    );
    expect(theirs).toContain('bg-chatBubbleOther');
    expect(theirs).toContain('data-self="0"');
    expect(theirs).not.toContain('bg-chatBubbleSelf');
  });

  it('keeps a picture inside the bubble it was sent with', () => {
    const html = renderToStaticMarkup(
      React.createElement(DiscussionBubble, {
        item: discussionItem({
          extra: { attachments: [{ url: '/uploads/pic.png', type: 'image', name: 'pic.png' }] },
        }),
        self: false,
      }),
    );

    expect(html).toContain('collab-task-bubble-attachment');
    expect(html).toContain('/uploads/pic.png');
    expect(html).toContain('<img');
  });

  it('a board entry is a card: long text clamps, the reader expands it', () => {
    const long = renderToStaticMarkup(
      React.createElement(ItemBlock, { item: boardItem({ content: 'x'.repeat(400) }) }),
    );
    expect(long).toContain('max-h-24');
    expect(long).toContain('collab-item-expand');

    const short = renderToStaticMarkup(React.createElement(ItemBlock, { item: boardItem() }));
    expect(short).not.toContain('max-h-24');
    expect(short).not.toContain('collab-item-expand');
  });

  it('an entry reports its progress as a bar, not a number', () => {
    const html = renderToStaticMarkup(React.createElement(ItemBlock, { item: boardItem({ progress: 40 }) }));
    expect(html).toContain('width:40%');
  });

  it('assignment and progress lay out as a board, with the blocking gate flagged', () => {
    const src = fs.readFileSync(path.resolve(__dirname, 'CollabTaskWindow.tsx'), 'utf8');
    expect(src).toContain('grid gap-1.5 sm:grid-cols-2');
    expect(src).toContain('data-testid="collab-task-blocking-gate"');
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
