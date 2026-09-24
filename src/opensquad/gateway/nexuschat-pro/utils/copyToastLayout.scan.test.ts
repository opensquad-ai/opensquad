/**
 * Composer 底部项目路径 chip 的「已复制」提示：必须是横排。
 *
 * Bug (2026-09-24 user report): 提示渲染成一字一行（竖排）。
 * 该 chip 的容器是 `relative … max-w-[min(28%,180px)] shrink`，宽度被夹到几十 px；
 * 而提示是 `absolute left-0` 不写宽度的浮层 —— CSS 2.1 §10.3.7 的 shrink-to-fit
 * 在可用宽度不足时取 min-content，中文每个字都是断点，于是被压成一字一行。
 *
 * 真实 Chromium 实测（同 markup/CSS，容器 42px）：
 *   无宽度           → 浮层 42×46，文本 14×42（3 行竖排）
 *   whitespace-nowrap → 浮层 61×20，文本 33×14（横排）
 *   w-max             → 同上
 *
 * 两条都加，任一条都够用 —— 这里锁住的是"别再退回无约束宽度"。
 */

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { describe, expect, it } from 'vitest';

const SRC = readFileSync(
  resolve(__dirname, '..', 'components', 'ai-chat', 'SoloContextFooter.tsx'),
  'utf8',
);

describe('cwd copy toast layout', () => {
  /** The toast block: anchored above the chip and carrying the copied text. */
  const toastBlock = (): string => {
    const m = /className="absolute bottom-\[calc\(100%\+6px\)\] left-0[\s\S]{0,500}?common\.copied/.exec(
      SRC,
    );
    expect(m, 'copy toast block not found in SoloContextFooter').toBeTruthy();
    return m![0];
  };

  it('constrains its own width so a narrow chip cannot wrap the text', () => {
    const block = toastBlock();
    expect(block).toMatch(/w-max/);
    expect(block).toMatch(/whitespace-nowrap/);
  });

  it('stays absolutely anchored above the chip (not in flow)', () => {
    expect(toastBlock()).toMatch(/className="absolute bottom-\[calc\(100%\+6px\)\]/);
  });
});
