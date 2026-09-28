/**
 * 工具流折叠框（`SoloActivityRow` 的 steps box）的贴底保持。
 *
 * 报告症状：贴底时有新的工具调用，滑块"突然上涨一截"再不动了 —— 第二根滚动条
 * （次右侧）不能保持在最新一行。
 *
 * 根因是同一类 bug 在本仓库的第三次出现：`el.scrollTop = el.scrollHeight` 这个
 * 程序写入同样会触发 `scroll` 事件，而折叠框把**每个** scroll 都当成"用户正在
 * 滚动"（`markStepsUserScrolling` + 180ms 静默），于是每次贴底都把自己关掉 180ms。
 * 新的工具调用正好落在这个窗口里就没人跟 —— 与
 * `chatTimelineFollow.test.ts` 记录的列抖动是同一个坑；ChatTimeline 与
 * FollowScrollBox 都用 lastSetTop 抑制修过，折叠框漏了。
 *
 * 第二层原因：`setStepVirt` 的虚拟窗口换挡、自动展开的行体都要**下一帧**才改变
 * 高度，只钉一次就停在那一刻的 scrollHeight 上。
 *
 * Rules:
 *   R1 程序贴底写入必须登记 `lastStepsSetTopRef`（单一写入点）；
 *   R2 onScroll 识别回声：回声不算用户滚动，但仍要更新贴底判定；
 *   R3 换挡用带回退的函数式 setStepVirt（否则反复算同一窗口会自我循环）；
 *   R4 钉完有上限的 follow 帧继续补高 —— 自终止，绝不常驻 rAF；
 *   R5 读者在看历史时窗口跟着视口走（syncStepVirt），不贴底。
 *
 * Mutations verified:
 *   MA1 onScroll 改回无条件 markStepsUserScrolling()      → R2
 *   MA2 pin 不登记 lastStepsSetTopRef                      → R1
 *   MA3 setStepVirt 换成无回退的直接写入                   → R3
 *   MA4 去掉 follow 帧（只钉一次）                         → R4
 *   MA5 看历史时也贴底（去掉 syncStepVirt 分支）           → R5
 */
import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';

const ROOT = path.resolve(__dirname, '..', '..');
const SRC = fs
  .readFileSync(path.join(ROOT, 'components', 'ai-chat', 'SoloActivityRow.tsx'), 'utf8')
  .replace(/\/\*[\s\S]*?\*\//g, '')
  .replace(/^\s*\/\/.*$/gm, '');

const pinFnBody = (() => {
  const at = SRC.indexOf('const pinStepsToBottom = ');
  if (at < 0) return '';
  return SRC.slice(at, SRC.indexOf('}, [virtSteps, displayLines.length]);', at) + 42);
})();

const scrollHandlerBody = (() => {
  const at = SRC.indexOf('onScroll={(e) => {');
  if (at < 0) return '';
  return SRC.slice(at, SRC.indexOf('}}', at) + 2);
})();

const pinEffectBody = (() => {
  const at = SRC.indexOf('useLayoutEffect(() => {', SRC.indexOf('const pinStepsToBottom = '));
  if (at < 0) return '';
  return SRC.slice(at, at + 2200);
})();

describe('R1 — 贴底只有一个写入点，并且登记回声指纹', () => {
  it('the writer records the scrollTop it wrote', () => {
    expect(pinFnBody).toMatch(/el\.scrollTop = el\.scrollHeight;/);
    expect(pinFnBody).toMatch(/lastStepsSetTopRef\.current = el\.scrollTop;/);
  });

  it('no other site writes the box scrollTop directly', () => {
    // 直接写在别处 = 没登记指纹 → 自己的回声会被当成用户滚动
    const writes = SRC.match(/stepsScrollRef\.current[^\n]*scrollTop\s*=/g) || [];
    expect(writes).toHaveLength(0);
  });
});

describe('R2 — 回声不算用户滚动', () => {
  it('the handler compares against the last programmatic write', () => {
    expect(scrollHandlerBody).toMatch(
      /const echo = el\.scrollTop === lastStepsSetTopRef\.current;/,
    );
    expect(scrollHandlerBody).toContain('if (!echo) markStepsUserScrolling();');
  });

  it('an echo still refreshes the at-bottom flag', () => {
    // 回声时也要更新贴底判定，否则贴底后 atBottom 会停在旧值
    expect(scrollHandlerBody).toMatch(
      /stepsAtBottomRef\.current =\s*el\.scrollHeight - el\.scrollTop - el\.clientHeight < 48;/,
    );
  });
});

describe('R3 — 换挡是带回退的函数式更新', () => {
  it('bails out when the window is already the tail window', () => {
    expect(pinFnBody).toMatch(/setStepVirt\(\(p\) => \{/);
    expect(pinFnBody).toMatch(/p\.start === next\.start && p\.end === next\.end \? p : next/);
  });
});

describe('R4 — 钉完继续盯几帧，且有上限', () => {
  it('the follow chain re-checks the gap and arms only within a budget', () => {
    expect(pinEffectBody).toContain('STEP_FOLLOW_FRAMES');
    expect(pinEffectBody).toMatch(/budget-- > 0/);
    expect(pinEffectBody).toMatch(/requestAnimationFrame\(tick\)/);
  });

  it('the budget is a small constant, not a permanent loop', () => {
    expect(SRC).toMatch(/const STEP_FOLLOW_FRAMES = \d+;/);
  });

  it('the chain stops as soon as the reader takes over', () => {
    expect(pinEffectBody).toMatch(/userScrollingRef\.current \|\| !stepsAtBottomRef\.current/);
  });
});

describe('R5 — 看历史时不贴底，只同步虚拟窗口', () => {
  it('the non-following branch syncs the window instead of pinning', () => {
    expect(pinEffectBody).toMatch(
      /if \(userScrollingRef\.current \|\| !stepsAtBottomRef\.current\) \{\s*if \(virtSteps\) syncStepVirt\(el\);\s*return;\s*\}/,
    );
  });
});
