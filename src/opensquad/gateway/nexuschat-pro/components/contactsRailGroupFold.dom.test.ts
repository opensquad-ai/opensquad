// @vitest-environment jsdom
/**
 * 「折叠的群聊」行为锁 —— 真渲染、真点击。
 *
 * 同目录的 `contactsRailGroupFold.scan.test.ts` 只读源码文本；这一份把 rail 真的
 * 挂到 DOM 上，用它自己的渲染结果回答三个问题（都曾有"看着对、其实不是"的坑）：
 *
 *   1. **折叠群到底在不在主列表里**。`<Collapse>` 是「保持挂载、靠 0fr 收起」的
 *      折叠原语（见 `Collapse.tsx`），所以折叠群的行**始终在 DOM 里** —— 只看
 *      "行存在吗"根本区分不出主列表和折叠区。判据必须是容器归属：
 *      折叠群的行落在 `[data-testid="contacts-rail-fold"]` 里，未折叠的不落在里面。
 *   2. **灰色角标**。同一个渲染器出两种角标，靠 `data-testid` 的 folded 变体 +
 *      类名区分红/灰。折叠群的角标里不允许出现 `bg-red-500`。
 *   3. **右键 / hover「···」都能折叠**。菜单是 portal 到 body 的，必须能在
 *      `document.body` 上找到；点完要真的把 `(groupId, folded)` 交出去。
 *
 * 另外锁住一个刻意的选择：**搜索时折叠区自动展开** —— 否则搜到的折叠群被收起
 * 的折叠体藏住，搜索看起来"搜不到"。
 *
 * `React.createElement` 而非 JSX：vitest 的 include 是 `**\/*.test.ts`。
 */
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import i18n from '../i18n';
import { ContactsRail } from './ContactsRail';

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}

const h = React.createElement;

/** 4 个群：2 个在列表里、2 个折叠；折叠的两个未读 4 + 6 = 10。 */
const { groups, reloadMock } = vi.hoisted(() => {
  const mk = (id: string, name: string, folded: boolean, unread: number) => ({
    id,
    name,
    avatar: null,
    description: null,
    unread_count: unread,
    has_unread_mention: false,
    is_private: false,
    created_at: null,
    notification_sound_enabled: true,
    folded,
    pinned_message_id: null,
  });
  return {
    groups: [
      mk('g-open', '开发协作组', false, 3),
      mk('g-open2', 'Frontend Team', false, 0),
      mk('g-fold1', '题材小组', true, 4),
      mk('g-fold2', 'test', true, 6),
    ],
    reloadMock: vi.fn(async () => undefined),
  };
});

vi.mock('../hooks/useChatContacts', () => ({
  useChatContacts: () => ({ agents: [], groups, loading: false, error: null, reload: reloadMock }),
}));

let container: HTMLDivElement;
let root: Root;
let onSetGroupFolded: ReturnType<typeof vi.fn>;

beforeEach(async () => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  // jsdom 不带 matchMedia，而 useSoftPresence 在挂载时就读它。
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
  onSetGroupFolded = vi.fn();
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  void i18n.changeLanguage('zh');
});

function render(extra: Record<string, unknown> = {}) {
  act(() => {
    root.render(
      h(ContactsRail, {
        uiMode: 'classic',
        chatUi: true,
        isOpen: true,
        onSetGroupFolded,
        ...extra,
      }),
    );
  });
}

const rows = () => [...container.querySelectorAll<HTMLElement>('[data-testid="contacts-rail-group-row"]')];
const rowById = (id: string) => container.querySelector<HTMLElement>(`[data-testid="contacts-rail-group-row"][data-group-id="${id}"]`);
const fold = () => container.querySelector<HTMLElement>('[data-testid="contacts-rail-fold"]');
const foldBody = () => fold()!.querySelector<HTMLElement>('.os-collapse')!;
const toggle = () => container.querySelector<HTMLElement>('[data-testid="contacts-rail-fold-toggle"]')!;
const menu = () => document.body.querySelector<HTMLElement>('[data-testid="contacts-rail-group-menu"]');
const click = (el: Element, type = 'click') => act(() => void el.dispatchEvent(new MouseEvent(type, { bubbles: true })));

/** React 只在走 native setter 时认这个 input 事件。 */
function typeFilter(value: string) {
  const input = container.querySelector<HTMLInputElement>('input')!;
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')!.set!;
  act(() => {
    setter.call(input, value);
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
}

const ownIds = (inside: boolean) =>
  rows()
    .filter((r) => (r.closest('[data-testid="contacts-rail-fold"]') ? inside : !inside))
    .map((r) => r.getAttribute('data-group-id'));

describe('R1 — 折叠群不在群列表里，只在折叠区里', () => {
  it('主列表恰好是未折叠的那两个，顺序不变', () => {
    render();
    expect(fold(), '折叠区没渲染出来').toBeTruthy();
    expect(ownIds(false)).toEqual(['g-open', 'g-open2']);
  });

  it('折叠的两个落在折叠区的容器里', () => {
    render();
    expect(ownIds(true)).toEqual(['g-fold1', 'g-fold2']);
  });
});

describe('R2 — 折叠群的未读数量是灰的', () => {
  it('折叠群走灰色角标，不出现红点', () => {
    render();
    const badge = rowById('g-fold1')!.querySelector<HTMLElement>('[data-testid="contacts-rail-group-unread-folded"]');
    expect(badge, '折叠群没有灰色角标').toBeTruthy();
    expect(badge!.textContent).toBe('4');
    expect(badge!.className).not.toContain('bg-red-500');
    expect(rowById('g-fold1')!.querySelector('[data-testid="contacts-rail-group-unread"]')).toBeNull();
  });

  it('未折叠的群仍然是红点', () => {
    render();
    const badge = rowById('g-open')!.querySelector<HTMLElement>('[data-testid="contacts-rail-group-unread"]');
    expect(badge!.textContent).toBe('3');
    expect(badge!.className).toContain('bg-red-500');
  });

  it('折叠区头上的汇总是灰色总数（4 + 6）', () => {
    render();
    const total = container.querySelector<HTMLElement>('[data-testid="contacts-rail-fold-unread"]')!;
    expect(total.textContent).toBe('10');
    expect(total.className).not.toContain('bg-red-500');
  });
});

describe('R3 — 折叠区默认收起，点开才是展开', () => {
  it('收起时 aria-expanded=false、折叠体是 is-closed 且对 a11y 隐藏', () => {
    render();
    expect(toggle().getAttribute('aria-expanded')).toBe('false');
    expect(foldBody().className).toContain('is-closed');
    expect(foldBody().getAttribute('aria-hidden')).toBe('true');
  });

  it('点击标题行展开，再点收回', () => {
    render();
    click(toggle());
    expect(toggle().getAttribute('aria-expanded')).toBe('true');
    expect(foldBody().className).not.toContain('is-closed');
    expect(foldBody().getAttribute('aria-hidden')).toBeNull();

    click(toggle());
    expect(toggle().getAttribute('aria-expanded')).toBe('false');
    expect(foldBody().className).toContain('is-closed');
  });
});

describe('R4 — 右键 / 悬停「···」都能把群折叠或移出', () => {
  it('右键一个未折叠的群 → 菜单是「折叠该群」，点了调用 (id, true)', () => {
    render();
    click(rowById('g-open')!, 'contextmenu');
    const panel = menu();
    expect(panel, '右键没弹出菜单').toBeTruthy();
    expect(panel!.textContent).toContain('折叠该群');

    click(panel!.querySelector<HTMLElement>('[data-testid="contacts-rail-group-fold-toggle"]')!);
    expect(onSetGroupFolded).toHaveBeenCalledWith('g-open', true);
    expect(menu(), '点完菜单没收起').toBeNull();
  });

  it('折叠中的群 → 菜单是「移出折叠」，点了调用 (id, false)', () => {
    render();
    click(rowById('g-fold1')!.querySelector<HTMLElement>('[data-testid="contacts-rail-group-more"]')!);
    const panel = menu();
    expect(panel, '「···」没弹出菜单').toBeTruthy();
    expect(panel!.textContent).toContain('移出折叠');

    click(panel!.querySelector<HTMLElement>('[data-testid="contacts-rail-group-fold-toggle"]')!);
    expect(onSetGroupFolded).toHaveBeenCalledWith('g-fold1', false);
  });

  it('点菜单外面把菜单收掉，且不误触发折叠', () => {
    render();
    click(rowById('g-open')!, 'contextmenu');
    expect(menu()).toBeTruthy();
    act(() => {
      document.body.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
    });
    expect(menu()).toBeNull();
    expect(onSetGroupFolded).not.toHaveBeenCalled();
  });
});

describe('R5 — 搜索时折叠区自动展开，搜得到折叠群', () => {
  it('输入关键字后折叠区展开，且只留匹配的行', () => {
    render();
    expect(toggle().getAttribute('aria-expanded')).toBe('false');

    typeFilter('题材');
    expect(toggle().getAttribute('aria-expanded')).toBe('true');
    expect(ownIds(true)).toEqual(['g-fold1']);
    expect(ownIds(false)).toEqual([]);
  });
});
