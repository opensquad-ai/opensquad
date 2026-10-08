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

import {
  AssignmentGroup,
  DiscussionBubble,
  ItemBlock,
  MarkdownText,
  StepRow,
  gateDisplayFor,
} from './CollabTaskWindow';

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

/**
 * The source of one gate's page: from `tab === '<gate>'` to the next `{tab === `.
 *
 * Paginating by gate is the point of that layout, so a gate's content must not leak onto
 * another gate's page. Comparing page bodies (not the whole file) is what proves it.
 */
function gatePage(src: string, gate: string): string {
  const marker = `tab === '${gate}'`;
  const start = src.indexOf(marker);
  expect(start, `no page for ${gate}`).toBeGreaterThanOrEqual(0);
  const next = src.indexOf('{tab === ', start + marker.length);
  return src.slice(start, next === -1 ? undefined : next);
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

  it('no progress bar and no latest-tool line is left in the window', () => {
    const src = fs.readFileSync(path.resolve(__dirname, 'CollabTaskWindow.tsx'), 'utf8');

    // A bar said "100%" for a task whose steps are drawn right underneath it, and every
    // step is already a row — so the bar is gone, not restyled.
    expect(src).not.toContain('ProgressBar');
    expect(src).not.toContain('width: ${pct}%');
    // the tool stream's "最近工具: im__send_message" told the reader nothing about the work
    expect(src).not.toContain('latestTool');
    expect(src).not.toContain('latest_tool_name');
  });

  it('draws a task step as the board does: icon, title, chevron, status', () => {
    const done = renderToStaticMarkup(
      React.createElement(StepRow, {
        step: { title: '编写转换器 HTML', detail: '', status: 'done' },
      }),
    );
    expect(done).toContain('collab-task-step');
    expect(done).toContain('data-status="done"');
    expect(done).toContain('编写转换器 HTML');

    const withDetail = renderToStaticMarkup(
      React.createElement(StepRow, {
        step: { title: 'Playwright 自测', detail: '6 条验收标准', status: 'doing' },
      }),
    );
    expect(withDetail).toContain('data-status="doing"');
    expect(withDetail).toContain('-rotate-90'); // folded until asked for
  });

  it('groups assignments by worker, each task with its steps and its plan folded away', () => {
    const group = renderToStaticMarkup(
      React.createElement(AssignmentGroup, {
        agentId: 'agent305',
        tasks: [
          boardItem({
            id: 'a1',
            agent_id: 'agent305',
            title: '进制转换器',
            extra: {
              structured: true,
              subtasks: [
                { title: '编写 HTML', status: 'done' },
                { title: 'Playwright 自测', status: 'doing' },
              ],
            },
          }),
        ],
      }),
    );

    expect(group).toContain('data-testid="collab-task-group"');
    expect(group).toContain('data-agent="agent305"');
    expect(group).toContain('@agent305');
    expect(group).toContain('进制转换器');
    // each subtask is one row — not a pasted checklist plus a second list of the same items
    expect(group.match(/collab-task-step/g)?.length).toBe(2);
    // and the plan text stays reachable, folded
    expect(group).toContain('collab-task-plan-toggle');
    expect(group).toContain('data-status="done"');
  });

  it('assignment and the blocking gate lay out as the board does', () => {
    const src = fs.readFileSync(path.resolve(__dirname, 'CollabTaskWindow.tsx'), 'utf8');
    expect(src).toContain('data-testid="collab-task-assignments"');
    expect(src).toContain('groupByWorker(tasks)');
    expect(src).toContain('data-testid="collab-task-blocking-gate"');
  });

  it('has no progress area any more', () => {
    // The board's `status` items were an auto-synced "latest tool call" feed; the
    // collaboration mechanism no longer needs it, so neither the section nor the
    // component that drew it may come back.
    const src = fs.readFileSync(path.resolve(__dirname, 'CollabTaskWindow.tsx'), 'utf8');
    expect(src).not.toContain('collabTask.progress');
    expect(src).not.toContain('collab-task-progress-groups');
    expect(src).not.toContain('ProgressGroup');
    expect(src).not.toContain('items.status');
  });

  it('draws the four gates as tabs, with only the picked page underneath', () => {
    const src = fs.readFileSync(path.resolve(__dirname, 'CollabTaskWindow.tsx'), 'utf8');
    expect(src).toContain('role="tablist"');
    expect(src).toContain('data-testid="collab-task-gates"');
    expect(src).toContain('data-testid="collab-gate-tab"');
    expect(src).toContain('data-gate={tabDef.id}');

    // Each gate owns a page carrying its own content — and nothing else's, which is what
    // "翻页" means here. Reading 确定需求 must not drag 方案 / 任务分配 onto the screen.
    const requirementPage = gatePage(src, '确定需求');
    expect(requirementPage).toContain('collabTask.requirement');
    expect(requirementPage).toContain("gateApprovalBlock('确定需求')");
    expect(requirementPage).not.toContain('collabTask.plan');

    const planPage = gatePage(src, '讨论方案');
    expect(planPage).toContain('collabTask.plan');
    expect(planPage).not.toContain('collabTask.requirement');

    const assignPage = gatePage(src, '任务分配');
    expect(assignPage).toContain('collabTask.assign');
    expect(assignPage).toContain('collab-task-assignments');

    // 任务验收 = that gate's verdict + the products of the collaboration
    const acceptPage = gatePage(src, '任务验收');
    expect(acceptPage).toContain("gateApprovalBlock('任务验收')");
    for (const product of ['collabTask.projectDir', 'collabTask.attachments', 'collabTask.files']) {
      expect(acceptPage, product).toContain(product);
    }
    expect(acceptPage).toContain('collab-open-project-dir');
    expect(acceptPage).toContain('collab-open-file');
    expect(acceptPage).not.toContain('collabTask.requirement');
  });

  it('keeps the task thread in a window of its own, with its own scrollbar', () => {
    const src = fs.readFileSync(path.resolve(__dirname, 'CollabTaskWindow.tsx'), 'utf8');
    // opened from the bottom bar, drawn in a modal above the task window
    expect(src).toContain('data-testid="collab-open-thread"');
    expect(src).toContain('<SoftOverlay');
    expect(src).toContain('data-testid="collab-thread-modal"');
    expect(src).toContain('data-testid="collab-thread-scroll"');
    // the scroller itself owns the scrollbar (className comes before the testid)
    expect(src).toMatch(/className="[^"]*overflow-y-auto[^"]*"[^>]*data-testid="collab-thread-scroll"/);

    // the thread and the composer that writes into it live inside that modal
    const modal = src.slice(src.indexOf('collab-thread-modal'));
    expect(modal).toContain('data-testid="collab-task-thread"');
    expect(modal).toContain('<CollabTaskComposer');
    // ...and nowhere else: the body is the four gate pages now
    expect(src.match(/data-testid="collab-task-thread"/g)?.length).toBe(1);
    expect(src.match(/<CollabTaskComposer/g)?.length).toBe(1);
  });

  it('opens the project directory and each file in the OS file manager', () => {
    const src = fs.readFileSync(path.resolve(__dirname, 'CollabTaskWindow.tsx'), 'utf8');
    // through the launcher that owns the agent — a browser cannot launch Explorer
    expect(src).toContain('adminAPI.revealProjectPath(ownerAgent, path, root)');
    expect(src).toMatch(/summary\?\.task\?\.created_by/);
    // the project directory itself...
    expect(src).toContain('data-testid="collab-open-project-dir"');
    // ...and each entry of the files list
    expect(src).toContain('data-testid="collab-open-file"');
    expect(src).toContain('reveal(`file:${f}`, f, projectDir)');
    // a failed reveal reports itself instead of failing silently
    expect(src).toContain('data-testid="collab-reveal-error"');
  });

  it('an assigned task does not report its gate as not started', () => {
    // The field report: three assignments on the board, and the 任务分配 gate said 未开始
    // because nobody had posted an approval card for that step.
    expect(gateDisplayFor('任务分配', { counts: { 任务分配: 3 } })).toEqual({ key: 'gateDoing', count: 3 });

    // a gate the user actually decided keeps the verdict
    expect(gateDisplayFor('任务分配', { approval: { status: 'approved' } as never, counts: {} }).key).toBe(
      'gateApproved',
    );
    expect(gateDisplayFor('任务分配', { approval: { status: 'pending' } as never, counts: {} }).key).toBe(
      'gatePending',
    );
    // and an empty board still reads as not started
    expect(gateDisplayFor('讨论方案', { counts: {} }).key).toBe('gateNone');
  });

  it('the not-started notice only shows while the board is empty', () => {
    const src = fs.readFileSync(path.resolve(__dirname, 'CollabTaskWindow.tsx'), 'utf8');
    expect(src).toContain('const boardStarted =');
    expect(src).toContain('data-testid="collab-task-blocking-gate"');
  });

  it('the window and its item rows actually use them', () => {
    const src = fs.readFileSync(path.resolve(__dirname, 'CollabTaskWindow.tsx'), 'utf8');
    const item = src.slice(src.indexOf('const ItemBlock'), src.indexOf('const Section'));

    expect(item).toContain('<MarkdownText text={item.content}');
    // the step rows come from the same parser the board uses, so the two surfaces agree
    expect(src).toContain('parseTaskSteps(task, t)');
    // approval and discussion text go through the same renderer (item rows,
    // approvals, discussion, step detail)
    expect(src.match(/<MarkdownText /g)?.length ?? 0).toBeGreaterThanOrEqual(3);
  });

  it('shows the task project directory, and says so when the PM has not filled it in', () => {
    const src = fs.readFileSync(path.resolve(__dirname, 'CollabTaskWindow.tsx'), 'utf8');

    // filled: the path, with a copy button
    expect(src).toContain("t('collabTask.projectDir')");
    expect(src).toContain('data-testid="collab-project-dir"');
    expect(src).toContain('copyProjectDir');
    // empty: the window tells the reader who fills it, not just "暂无内容"
    expect(src).toContain('data-testid="collab-project-dir-missing"');
    expect(src).toContain("t('collabTask.projectDirMissing')");

    // …and it reads the field the summary exposes
    const api = fs.readFileSync(path.resolve(__dirname, '..', 'services', 'api.ts'), 'utf8');
    expect(api).toMatch(/project_dir\?: string;/);
  });
});
