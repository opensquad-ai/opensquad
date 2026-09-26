/**
 * 群聊图片缩略图的 hover 样式 — 图片不该跟着「整行」亮起来。
 *
 * 消息行的根节点带 `group`（整行宽度，1248px @1280 视口），图片缩略图是它的
 * 后代。Tailwind 的 `group-hover:` 匹配的是**任意** `.group` 祖先，所以当初挂在
 * 图片上的那层 `absolute inset-0 ... group-hover:bg-black/20` 遮罩，会在鼠标停
 * 在行内空白处（离图片上千像素）时把图片压暗、并亮出放大镜徽标 ——
 * 实测：指针距图片 999px，图片区 99.45% 的像素变色。
 *
 * 用户要的是「点击交互不变，但不要这种背景色变化的提示」，所以遮罩整层删除，
 * 只留下 wrapper 上的 `cursor-pointer` 与打开灯箱的 onClick。
 *
 *   R1  图片分支里不得再出现任何 hover 触发的视觉（`group-hover` / `hover:opacity`
 *       / `absolute inset-0` 遮罩）；
 *   R2  前提：消息行仍是 `group`（正因如此，后代加 `group-hover` 才会被整行触发；
 *       这条锁住「为什么不能放回图片上」）；
 *   R3  点击交互不变：wrapper 仍是 `cursor-pointer`，onClick 仍打开灯箱；
 *   R4  行级 hover 交互没被误删：操作菜单仍在行 hover 时显现。
 *
 * Mutations verified:
 *   MA1 把 `group-hover:bg-black/20` 遮罩加回图片分支   → R1
 *   MA2 删掉 wrapper 上的 onClick 灯箱                  → R3
 *   MA3 去掉行根节点的 `group`                          → R2
 *   MA4 操作菜单改成常显（`opacity-100`，无 hover 态）  → R4
 */
import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';

const ROOT = path.resolve(__dirname, '..');
const SRC = fs.readFileSync(path.join(ROOT, 'components/ChatWindow.tsx'), 'utf8');
/** Comments are prose about the fix, not the fix. */
const code = (src: string) => src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
const SRCCODE = code(SRC);

const slice = (from: string, to: string) => {
  const start = SRCCODE.indexOf(from);
  return start < 0 ? '' : SRCCODE.slice(start, SRCCODE.indexOf(to, start));
};

/** The `att.type === 'image'` branch of the attachment renderer. */
const IMAGE_BRANCH = slice("att.type === 'image' ? (", ") : att.type === 'voice' ? (");
/** The message row root — the `.group` every descendant hover hangs off. */
const ROW_ROOT = slice('className={`group flex gap-2', 'style={{').split('}')[0];
/** The action menu under the bubble (its `Action Menu` marker is a comment, and
 *  comments are stripped before slicing, so anchor on the code around it). */
const ACTION_MENU = slice('{!isEditing && (', '{!msg.isDeleted && (');

describe('R1 — the chat image has no hover chrome of its own', () => {
  it('slices the image branch and the row root (not vacuously empty)', () => {
    expect(IMAGE_BRANCH).toMatch(/alt=\{att\.name\}/);
    expect(IMAGE_BRANCH).toMatch(/setShowLightbox\(true\)/);
    expect(ROW_ROOT).toMatch(/group/);
  });

  it('no group-hover anywhere in the image branch', () => {
    expect(IMAGE_BRANCH).not.toMatch(/group-hover/);
  });

  it('no overlay layer either (that is what painted the grey)', () => {
    expect(IMAGE_BRANCH).not.toMatch(/absolute inset-0/);
  });

  it('and no hover opacity swap on the img itself', () => {
    expect(IMAGE_BRANCH).not.toMatch(/hover:opacity/);
    expect(IMAGE_BRANCH).toMatch(/className="max-w-full rounded-lg max-h-80 object-cover"/);
  });
});

describe('R2 — the row is a `group`, so a descendant hover would fire from the whole row', () => {
  it('the message row root still carries `group`', () => {
    expect(ROW_ROOT).toMatch(/`group flex gap-2/);
  });

  it('the row is full-width — i.e. most of its area is NOT the image', () => {
    expect(ROW_ROOT).toMatch(/flex gap-2 md:gap-3/);
  });
});

describe('R3 — click interactions are unchanged', () => {
  it('the wrapper stays a pointer target', () => {
    expect(IMAGE_BRANCH).toMatch(/className="relative cursor-pointer"/);
  });

  it('and a click still opens the lightbox', () => {
    expect(IMAGE_BRANCH).toMatch(/actions\.setLightboxImages\(images\)/);
    expect(IMAGE_BRANCH).toMatch(/actions\.setLightboxIndex\(imgIndex\)/);
    expect(IMAGE_BRANCH).toMatch(/actions\.setShowLightbox\(true\)/);
  });
});

describe('R4 — the row-hover interaction itself is still there', () => {
  it('the action menu is revealed by hovering the row', () => {
    expect(ACTION_MENU).toMatch(/opacity-0 group-hover:opacity-100/);
  });
});
