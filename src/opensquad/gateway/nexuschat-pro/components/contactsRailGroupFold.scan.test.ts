// @vitest-environment node
/**
 * 群折叠（「折叠的群聊」）—— 左栏群列表的分组锁。
 *
 * 折叠这件事有四条腿，全部是「不加运行时错误、却能被后续改动悄悄拆掉」的：
 *
 *   1. 折叠群必须从主列表里消失（不是排到后面、也不是变淡）。主列表若改回
 *      `visibleGroups.map(...)`，折叠群会同时出现在两处：类型检查通过、界面上
 *      也「看着有折叠区」，但主列表已经泄露了。
 *   2. 折叠群的未读角标是灰的、未折叠的是红的。角标是同一个渲染器出的，靠
 *      `renderGroupRow(g, foldedRow)` 的第二个参数分色 —— 折叠区传错 `false`
 *      就退回红点，静默失去「折叠＝不打扰」的视觉语义。
 *   3. 折叠群不响提示音。左栏只有一个真正会响的地方（ChatWindow 的 @提及
 *      chime），折叠群进它时必须提前返回；依赖数组漏了 `group.folded`，折叠
 *      恰好发生在组件已挂载之后时就不会生效。
 *   4. 折叠是 per-user 落库字段，不是纯前端状态。api.ts / types.ts / App.tsx /
 *      rail 四处任一漏掉，`folded` 就退化成「刷新即失忆」。
 *
 *   R1  折叠群只进折叠区，主列表只列未折叠的群；群行只有一个渲染器
 *   R2  折叠群的未读角标走灰色分支，未折叠的走红色分支
 *   R3  ChatWindow 的 @提及提示音在 `group.folded` 时提前返回，且依赖数组含 folded
 *   R4  folded 字段贯通 api.ts / types.ts / App.tsx / rail（含落库调用）
 *   R5  四个新文案中英齐备
 *
 * Mutations verified（每一条都会让本文件失败）：
 *   MF1 主列表改回 `{visibleGroups.map((g) => renderGroupRow(g, false))}`   → R1
 *   MF2 折叠区改传 `renderGroupRow(g, false)`                              → R2
 *   MF3 去掉 ChatWindow 的 `if (group.folded) return;`                      → R3
 *   MF4 依赖数组去掉 `group.folded`                                        → R3
 *   MF5 再复制一份群行渲染器（`contacts-rail-group-row` 出现两次）          → R1
 *   MF6 删掉任一 locale 键或 App 的 `folded: !!g.folded` 映射               → R4/R5
 *
 * Known limits — 别过度信任这个文件：
 *   - 全部是源码文本断言，不是渲染测试。「灰 ≠ 红」只验证了类名分支，实际观感
 *     要在浏览器里看（灰色角标应低于 <1 的对比度、不抢眼）。
 *   - 后端（schemas / models / ALTER TABLE 迁移）不在这里，它属于 Python 侧。
 */
import { describe, expect, it } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';

const ROOT = path.resolve(__dirname, '..');
const read = (rel: string) => fs.readFileSync(path.join(ROOT, rel), 'utf8');

const RAIL = read('components/ContactsRail.tsx');
const CHAT_WINDOW = read('components/ChatWindow.tsx');
const API = read('services/api.ts');
const APP = read('App.tsx');
const TYPES = read('types.ts');
const ZH = read('locales/zh.json');
const EN = read('locales/en.json');

/** `renderGroupRow` 的函数体（到组件返回之前）。 */
const rowRenderer = (() => {
  const from = RAIL.indexOf('const renderGroupRow');
  const to = RAIL.indexOf('if (!softMounted) return null;');
  expect(from, 'renderGroupRow not found').toBeGreaterThan(-1);
  return RAIL.slice(from, to);
})();

/** ChatWindow 里那段 @提及提示音 effect。 */
const mentionEffect = (() => {
  const from = CHAT_WINDOW.indexOf('Listen for @mentions');
  const to = CHAT_WINDOW.indexOf('// Edit State', from);
  expect(from, 'mention chime effect not found').toBeGreaterThan(-1);
  expect(to, 'effect end marker not found').toBeGreaterThan(from);
  return CHAT_WINDOW.slice(from, to);
})();

describe('R1 — 折叠群只在折叠区里，主列表只列未折叠的群', () => {
  it('两组列表由 folded 派生', () => {
    expect(RAIL).toMatch(/const openGroups = useMemo\(\(\) => visibleGroups\.filter\(\(g\) => !g\.folded\)/);
    expect(RAIL).toMatch(/const foldedGroups = useMemo\(\(\) => visibleGroups\.filter\(\(g\) => !!g\.folded\)/);
  });

  it('主列表渲染 openGroups，而不是把折叠群一起铺出来', () => {
    expect(RAIL).toMatch(/\{openGroups\.map\(\(g\) => renderGroupRow\(g, false\)\)\}/);
    expect(RAIL).not.toMatch(/\{visibleGroups\.map\(\(g\) => renderGroupRow/);
  });

  it('折叠群落在折叠区的 <Collapse> 体里，且折叠区只在有折叠群时出现', () => {
    expect(RAIL).toMatch(/\{foldedGroups\.length > 0 \? \(/);
    const foldAt = RAIL.indexOf('data-testid="contacts-rail-fold"');
    const collapseAt = RAIL.indexOf('<Collapse open={foldExpanded}>', foldAt);
    const rowsAt = RAIL.indexOf('{foldedGroups.map((g) => renderGroupRow(g, true))}', collapseAt);
    expect(foldAt).toBeGreaterThan(-1);
    expect(collapseAt).toBeGreaterThan(foldAt);
    expect(rowsAt).toBeGreaterThan(collapseAt);
  });

  it('群行只有一个渲染器（两处列表共用它，不许再抄一份）', () => {
    expect(RAIL.match(/data-testid="contacts-rail-group-row"/g)?.length).toBe(1);
    expect(RAIL.match(/const renderGroupRow/g)?.length).toBe(1);
  });
});

describe('R2 — 折叠群的未读角标是灰的，未折叠的是红的', () => {
  it('角标颜色跟着 foldedRow 分支，而不是写死红色', () => {
    expect(rowRenderer).toMatch(
      /foldedRow\s*\?\s*'bg-black\/\[0\.08\] text-textMuted dark:bg-white\/\[0\.14\]'\s*:\s*'bg-red-500 text-white'/,
    );
  });

  it('两个分支各有自己的 testid，便于按折叠态取样', () => {
    expect(rowRenderer).toMatch(
      /data-testid=\{foldedRow \? 'contacts-rail-group-unread-folded' : 'contacts-rail-group-unread'\}/,
    );
  });

  it('折叠区的汇总角标也是灰的', () => {
    const foldAt = RAIL.indexOf('data-testid="contacts-rail-fold-unread"');
    expect(foldAt).toBeGreaterThan(-1);
    expect(RAIL.slice(foldAt, foldAt + 260)).toContain('text-textMuted');
    expect(RAIL.slice(foldAt, foldAt + 260)).not.toContain('bg-red-500');
  });
});

describe('R3 — 折叠群不出声', () => {
  it('@提及提示音在 group.folded 时提前返回', () => {
    const guardAt = mentionEffect.indexOf('if (group.folded) return;');
    const chimeAt = mentionEffect.indexOf('playGentleNotificationSound()');
    expect(guardAt).toBeGreaterThan(-1);
    expect(chimeAt).toBeGreaterThan(guardAt);
  });

  it('依赖数组带上 folded，否则挂载后改折叠不会重跑', () => {
    expect(mentionEffect).toMatch(/\}, \[group\.folded, group\.hasUnreadMention/);
  });
});

describe('R4 — folded 是 per-user 落库字段，四处贯通', () => {
  it('api.ts 的列表 / 详情 / 更新载荷三处都带 folded', () => {
    // GroupListItem、GroupResponse、updateGroup 的 payload —— 漏掉任一处都会
    // 让「刷新后折叠没了」或「折叠点了不生效」其中的一种回来。
    expect(API.match(/^[ \t]*folded: boolean;[ \t]*\r?$/gm)?.length).toBe(3);
  });

  it('types.ts 的 Group 带 folded', () => {
    expect(TYPES).toMatch(/folded: boolean;/);
  });

  it('App 把接口字段映进 state，并把折叠落库入口交给 rail', () => {
    expect(APP).toMatch(/folded: !!g\.folded,/);
    expect(APP).toMatch(/groupAPI\.updateGroup\(id, \{ folded \}\)/);
    expect(APP).toMatch(/onSetGroupFolded=\{handleSetGroupFolded\}/);
  });

  it('rail 自己也能落库（不给 prop 时不至于只能干看着）', () => {
    expect(RAIL).toMatch(/onSetGroupFolded\?: \(groupId: string, folded: boolean\) => Promise<void> \| void;/);
    expect(RAIL).toMatch(/await groupAPI\.updateGroup\(groupId, \{ folded \}\);/);
    expect(RAIL).toMatch(/await reload\(\);/);
  });

  it('菜单是 portal 到 body 的浮层，且点别处会收掉', () => {
    expect(RAIL).toMatch(/createPortal\(/);
    expect(RAIL).toMatch(/data-testid="contacts-rail-group-menu"/);
    expect(RAIL).toMatch(/addEventListener\('mousedown', onDown\)/);
  });
});

describe('R5 — 中英文案齐备', () => {
  it('四个新键两个语言档都在', () => {
    for (const dict of [ZH, EN]) {
      for (const k of ['foldedChats', 'foldGroup', 'unfoldGroup', 'groupMore']) {
        expect(dict, `missing "${k}"`).toContain(`"${k}"`);
      }
    }
  });

  it('折叠区标题用的是 foldedChats 而不是硬编码中文', () => {
    expect(RAIL).toMatch(/t\('aiChat\.chat\.foldedChats'\)/);
    expect(RAIL).toMatch(/t\('aiChat\.chat\.foldGroup'\)/);
    expect(RAIL).toMatch(/t\('aiChat\.chat\.unfoldGroup'\)/);
  });
});
