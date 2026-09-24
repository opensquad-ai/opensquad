/**
 * 插话只在"这一轮真的在跑"时成立 —— 任务结束后按普通消息发。
 *
 * 排队（park）和插话（steer）是两件事，代码里却共用了一个入口：任何被 park 的消息
 * 都会立刻走 `steerPendingSnapshot` 注入。而 park 的触发条件有三个 ——
 * `isSessionBusy(sid) || isOutboundPending(sid) || 本地已有排队条目` —— 后两个跟
 * "agent 是否在跑"无关：`isOutboundPending` 是本地乐观锁（8s 兜底超时），残留的
 * 排队条目也照样命中。于是任务已经结束时发消息，照样被当成插话注入。
 *
 * 那样做的后果不是"稍微难看"，而是消息在界面上消失：runner 只在工具边界把插话塞进
 * 上下文，回合已结束时它把这批残留**重排成一个正常新回合**，而前端等的是
 * `steer_consumed` —— 收不到就不补用户气泡（`steered: true` 的条目还会被回合结束
 * 时的兜底清理直接删掉）。
 *
 * 所以注入必须由"本会话这一轮真的在跑"把关；不满足就保持未注入（不标 steered），
 * 让 auto-drain 用 `flushPendingMessage` 当普通消息发出去 —— 它本来就只挑非 steered
 * 的条目，且会跳过还在忙的会话。
 *
 * Rules:
 *   R1 注入前必须过 `isSessionBusy(本条目会话)` 这道闸；不满足直接返回，不标 steered、
 *      不发 steer 帧；
 *   R2 标记与发送都在这道闸之后（先标后判 = 闸门形同不存在）；
 *   R3 `steer: true` 这个投递只有这一个入口：五个 park 点全都汇到这里，没有旁路；
 *   R4 auto-drain 仍然只挑未注入的条目、且跳过忙的会话 —— 这是"任务结束就正常发送"
 *      能够成立的前提。
 *
 * Mutations verified:
 *   MA1 去掉闸（直接标 steered + 注入）           → R1
 *   MA2 闸门取反                                  → R1
 *   MA3 闸门改成 `if (false && …)`                → R2
 *   MA4 把标记移到闸门之前                        → R2
 *   MA5 旁路：在别处直接投递 steer: true          → R3
 *   MA6 auto-drain 放行已注入条目                 → R4
 */
import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';

const ROOT = path.resolve(__dirname, '..');
const PAGE = fs
  .readFileSync(path.join(ROOT, 'components', 'AIChatPage.tsx'), 'utf8')
  .replace(/\/\*[\s\S]*?\*\//g, '')
  .replace(/^\s*\/\/.*$/gm, '');

const steerFnBody = (() => {
  const at = PAGE.indexOf('const steerPendingSnapshot = ');
  if (at < 0) return '';
  return PAGE.slice(at, PAGE.indexOf('}, [deliverMessage, isSessionBusy]);', at) + 34);
})();

const drainBody = (() => {
  // 注释已被剥掉，锚在 drain 自己的代码上
  const at = PAGE.indexOf('const next = queue.find((m) => {');
  return at < 0 ? '' : PAGE.slice(at, at + 700);
})();

describe('R1 — 注入前必须确认真在跑', () => {
  it('the guard exists and asks about this entry’s own session', () => {
    expect(steerFnBody).toMatch(
      /if \(!isSessionBusy\(\(snapshot\.sessionId \|\| ''\)\.trim\(\)\)\) return;/,
    );
  });

  it('and the injection still happens when the turn is running', () => {
    expect(steerFnBody).toContain("{ clearInputState: false, salvageStream: false, steer: true }");
  });
});

describe('R2 — 闸门在标记与发送之前', () => {
  it('marks steered only after the guard', () => {
    const guard = steerFnBody.indexOf('isSessionBusy(');
    const mark = steerFnBody.indexOf('steered: true');
    const send = steerFnBody.indexOf('steer: true');
    expect(guard).toBeGreaterThan(-1);
    expect(mark).toBeGreaterThan(-1);
    expect(send).toBeGreaterThan(-1);
    // 先标 steered 再判断 = 判定结果没人用（标记已经落库，drain 也不会再发它）
    expect(guard).toBeLessThan(mark);
    expect(guard).toBeLessThan(send);
  });
});

describe('R3 — 只有一个注入入口', () => {
  it('no call site bypasses the funnel with its own steer delivery', () => {
    const deliveries = PAGE.match(/steer: true/g) || [];
    expect(deliveries).toHaveLength(1);
    expect(steerFnBody).toContain('steer: true');
  });

  it('every park site goes through steerPendingSnapshot', () => {
    const calls = PAGE.match(/steerPendingSnapshot\(snapshot\)/g) || [];
    // plan / goal / 普通文本 / 表单提交 / 跨窗格发送
    expect(calls.length).toBe(5);
  });

  it('“已引导注入”只在真的注入过之后才标上', () => {
    const marks = PAGE.match(/\{ \.\.\.m, steered: true \}/g) || [];
    expect(marks).toHaveLength(1);
    expect(steerFnBody).toContain('{ ...m, steered: true }');
  });
});

describe('R4 — 未注入的条目由 auto-drain 正常发送', () => {
  it('the drain skips injected entries but takes the rest', () => {
    expect(drainBody).toMatch(/if \(m\.steered\) return false;/);
  });

  it('and still refuses to send into a running turn', () => {
    expect(drainBody).toMatch(/if \(sid && isSessionBusy\(sid\)\) return false;/);
    expect(drainBody).toContain('flushPendingMessage(next)');
  });
});
