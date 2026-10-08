/**
 * 两个协作轮询器在应用重新获焦时要立刻拉一次。
 *
 * 仓库其余地方都是这个约定（SessionSidebar / ProjectFilesPanel / GitRepoBar /
 * AgentManagerPage）：定时器照常走，另加 focus + visibilitychange(visible) 监听，回到
 * 应用的一瞬间补一次，用户不会对着"最多一个轮询周期前的旧数据"发呆。这两个协作轮询器
 * 之前没有。
 *
 * 卡片用的是递归 setTimeout 排下一次读，所以"立刻拉一次"必须先把待定的 timer 清掉，
 * 否则每次唤醒都会分叉出第二条轮询链。这两点都在下面钉住。
 *
 * 只读源码文本；真渲染的那一份见 collabTaskCardRespond.dom.test.ts。
 */
import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';

const read = (rel: string) => fs.readFileSync(path.resolve(__dirname, rel), 'utf8');
const card = read('CollabTaskCard.tsx');
const win = read('CollabTaskWindow.tsx');

describe('a collaboration poller refreshes when the app comes back', () => {
  it('the card listens for focus and for becoming visible', () => {
    expect(card).toContain("window.addEventListener('focus', awake)");
    expect(card).toContain("document.addEventListener('visibilitychange', awake)");
    expect(card).toMatch(/if \(document\.visibilityState === 'visible'\) void read\(\)/);
    // ...and unsubscribes on unmount
    expect(card).toContain("window.removeEventListener('focus', awake)");
    expect(card).toContain("document.removeEventListener('visibilitychange', awake)");
  });

  it('the card cannot fork a second polling chain on wake-up', () => {
    // read() clears the pending timer before it fetches, and again before it reschedules
    expect(card).toContain('const stopTimer = (): void => {');
    expect(card).toMatch(/const read = async \(\): Promise<void> => \{\s*stopTimer\(\);/);
    expect(card).toMatch(/if \(alive\) \{\s*stopTimer\(\);\s*timer = window\.setTimeout/);
  });

  it('the task window listens too', () => {
    expect(win).toContain("window.addEventListener('focus', awake)");
    expect(win).toContain("document.addEventListener('visibilitychange', awake)");
    expect(win).toMatch(/if \(document\.visibilityState === 'visible'\) void load\(true\)/);
    expect(win).toContain("window.removeEventListener('focus', awake)");
    expect(win).toContain("document.removeEventListener('visibilitychange', awake)");
  });
});
