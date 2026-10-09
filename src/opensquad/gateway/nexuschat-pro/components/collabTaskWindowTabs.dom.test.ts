// @vitest-environment jsdom
/**
 * 协作窗口的信息架构 —— 真渲染、真点击。
 *
 * 四个门闸现在是 tab，点哪个只渲染哪一页（"像翻页一样"）；这一页放它自己的内容与该闸
 * 的审批卡；任务验收页 = 审批卡 + 产出物（项目目录 / 附件 / 相关文件）。讨论搬进独立的
 * 模态窗。
 *
 * 同目录的 `collabTaskWindow.test.ts` 读源码文本（structure）；这一份把窗口真的挂到 DOM
 * 上，回答它答不了的问题：**点下去页面真的换了吗**、讨论在没打开时是否真的不在正文里。
 *
 * `React.createElement` 而非 JSX：vitest 的 include 是 `**\/*.test.ts`。
 */
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import i18n from '../i18n';
import { CollabTaskWindow } from './CollabTaskWindow';

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}

const h = React.createElement;

// 正文里的 Markdown 走共享渲染器（needs a DOM）；内容照旧，断言只看文本。
vi.mock('../utils/fencedMarkdown', () => ({
  AI_MARKDOWN_CLASS: 'ai-markdown',
  renderFencedMarkdown: (text: string) => `<p>${text}</p>`,
}));

const { taskSummary } = vi.hoisted(() => ({ taskSummary: vi.fn() }));
vi.mock('../services/api', () => ({
  SERVER_BASE_URL: '',
  adminAPI: { revealProjectPath: vi.fn(async () => ({ ok: true })) },
  collabBoardAPI: { taskSummary, postTaskMessage: vi.fn(async () => ({})) },
  messageAPI: { resolveCollabApproval: vi.fn(async () => ({})) },
  uploadAPI: { upload: vi.fn(async () => ({})) },
}));

const summary = {
  collab_id: 'AB12CD',
  task: {
    task_id: 'AB12CD',
    task_name: '发布流程',
    created_by: 'pm',
    status: 'active',
    extra: { group_id: 'g-1' },
  },
  title: '发布流程',
  status: 'active',
  progress: 0,
  board_rev: 1,
  card: 'software_dev_team',
  project_dir: '/work/pub',
  skills: ['collab_software_dev_team'],
  files: ['src/app.py'],
  attachments: [],
  participants: [{ agent_id: 'pm', name: 'PM', state: 'accepted' }],
  items: {
    requirement: [{ id: 'r1', item_type: 'requirement', title: '需求甲', content: '需求正文', status: '已确认', agent_id: 'pm' }],
    plan: [{ id: 'p1', item_type: 'plan', title: '方案乙', content: '方案正文', status: 'doing', agent_id: 'pm' }],
    task: [{ id: 't1', item_type: 'task', item_key: 'k1', title: '任务丙', content: '- [ ] 步骤一', status: 'pending', agent_id: 'coder' }],
    approval: [
      {
        id: 'a1',
        item_type: 'approval',
        item_key: 'appr_1',
        title: '任务分配',
        content: '请批准分配',
        status: 'pending',
        agent_id: 'pm',
        extra: { approval: { step: '任务分配' }, message_id: 'm1' },
      },
    ],
    discussion: [
      { id: 'd1', item_type: 'discussion', title: 'User', content: '讨论内容丁', status: 'info', agent_id: 'pm', created_at: '2026-10-02T10:00:00Z' },
    ],
  },
};

let container: HTMLDivElement;
let root: Root;

beforeEach(async () => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  taskSummary.mockReset();
  taskSummary.mockResolvedValue(summary);
  // jsdom 不带 matchMedia，而 SoftOverlay 的 useSoftPresence 会读它。
  (window as unknown as { matchMedia: unknown }).matchMedia = () => ({
    matches: false,
    media: '',
    onchange: null,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
    dispatchEvent: () => false,
  });
  await act(async () => {
    await i18n.changeLanguage('zh');
  });
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

async function render() {
  await act(async () => {
    root.render(h(CollabTaskWindow, { collabId: 'AB12CD', onClose: vi.fn(), viewerName: 'ss' }));
    await new Promise((r) => setTimeout(r, 0));
  });
}

const click = (el: Element) =>
  act(async () => {
    el.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });

const tab = (gate: string) =>
  container.querySelector<HTMLElement>(`[data-testid="collab-gate-tab"][data-gate="${gate}"]`);
const text = () => container.textContent || '';

describe('the four gates are pages, and clicking one turns to it', () => {
  it('opens on the gate the task is on, and that page alone', async () => {
    await render();

    expect(tab('确定需求'), '门闸 tab 没渲染').toBeTruthy();
    expect(tab('确定需求')!.getAttribute('aria-selected')).toBe('true');
    // 它自己那页在……
    expect(text()).toContain('需求正文');
    // ……别页的内容不在（否则就不是"翻页"了）
    expect(text()).not.toContain('方案正文');
    expect(text()).not.toContain('任务丙');
  });

  it('turns to 讨论方案 and shows only that page', async () => {
    await render();
    await click(tab('讨论方案')!);

    expect(text()).toContain('方案正文');
    expect(text()).not.toContain('需求正文');
  });

  it('任务分配 carries its assignments and its own verdict card', async () => {
    await render();
    await click(tab('任务分配')!);

    expect(text()).toContain('任务丙');
    expect(text()).toContain('请批准分配');
    expect(container.querySelector('[data-testid="collab-task-assignments"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="collab-gate-approval"]')).toBeTruthy();
    expect(text()).not.toContain('需求正文');
  });

  it('任务验收 carries the verdict card and the products', async () => {
    await render();
    await click(tab('任务验收')!);

    expect(text()).toContain('/work/pub');
    expect(text()).toContain('src/app.py');
    expect(container.querySelector('[data-testid="collab-open-project-dir"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="collab-open-file"]')).toBeTruthy();
    expect(text()).not.toContain('需求正文');
  });
});

describe('the thread is its own window', () => {
  it('is not in the body until it is opened, then scrolls on its own', async () => {
    await render();

    // 正文里没有讨论，也没有输入框
    expect(text()).not.toContain('讨论内容丁');
    expect(container.querySelector('textarea')).toBeNull();

    await click(container.querySelector('[data-testid="collab-open-thread"]')!);

    const modal = container.querySelector('[data-testid="collab-thread-modal"]');
    expect(modal, '讨论模态没打开').toBeTruthy();
    expect(text()).toContain('讨论内容丁');
    // 输入框随讨论一起搬进模态
    expect(modal!.querySelector('textarea')).toBeTruthy();
    // 内容区自己带滚动
    const scroller = modal!.querySelector('[data-testid="collab-thread-scroll"]');
    expect(scroller).toBeTruthy();
    expect(scroller!.className).toContain('overflow-y-auto');
  });
});
