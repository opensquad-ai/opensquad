/**
 * 插话是**点出来的**，不是排队自动发生的；而「立刻发一条」只能有一条在飞。
 *
 * 语义（2026-09-28 定）：agent 正在跑时，普通发送只入队，等本轮任务流结束后由
 * auto-drain 自动发出；点队列条目上的 ↗（或表头的「发送下一条」）才立即把**一条**
 * 送进正在跑的回合 —— runner 在下一次工具返回的边界塞进模型上下文，不打断工具流。
 *
 * 这一版修两件事：
 *   ① "点发送 UI 就一连串重复发送、顺序也乱"：`handleSendNextPending` 过去自己
 *      setPendingMessages + flushPendingMessage，**不占在飞闸** → 它引起的 length
 *      变化让 auto-drain 当场把下一条也发出去；条目没有会话号时（`if (sid && …)`
 *      短路）两道忙/在飞闸被整段跳过 → 整个队列在正在跑的回合里被逐条强发。
 *   ② "串会话 + 发送没反应"：忙态判定落到 agent 级（空 sid → isAgentBusy）时，新建
 *      会话窗口里别人的回合会把这条消息永久扣在队列里 —— auto-drain 跳过"忙"会话，
 *      于是永远发不出去，看上去就是点了发送没反应。判忙必须是**目标会话**的忙态：
 *      会话未知就不能拿 agent 级忙态扣住它。
 *
 * Rules:
 *   R1 注入前必须过 `isTargetSessionBusy(本条目会话)` 这道闸；不满足直接返回，不标
 *      steered、不发 steer 帧；
 *   R2 标记与发送都在这道闸之后（先标后判 = 闸门形同不存在）；
 *   R3 `steer: true` 这个投递只有 `steerPendingSnapshot` 一个入口，且只被「立即发这条」
 *      调用 —— park 点一律不注入；
 *   R4 auto-drain 只挑未注入的条目、且跳过忙 / 在飞的会话；
 *   R5 「立即发这条」忙时插话、空闲时走正常发送（同一个 `releasePending`）；
 *   R6 手动发送与 drain 共用 `releasePending`：单一在飞闸、失败回队（不丢、不乱序）；
 *   R7 忙 / 在飞两道闸**无条件**过 —— 空会话号回落到当前会话，不许短路；但「目标会话
 *      未知」不等于「agent 在忙」，不得把未知目标扣成忙（stranding）；
 *   R8 闸内与调用方用**同一个** sid 解析口径：两边结论相反时闸内会静默丢掉消息。
 *
 * Mutations verified:
 *   MA1 去掉 R1 的闸（直接标 steered + 注入）        → R1
 *   MA2 闸门取反                                     → R1
 *   MA3 把标记移到闸门之前                           → R2
 *   MA4 旁路：在 park 点直接投递 steer: true         → R3
 *   MA5 插话按钮回退成 no-op（不调 handleSteerPending） → R3
 *   MA6 手动发送绕过 releasePending（自己 flush）    → R6
 *   MA7 drain / 闸内把 isTargetSessionBusy 换回
 *       isSessionBusy（空 sid 落回 agent 级忙态）     → R7、R8
 *   MA8 isTargetSessionBusy 去掉 `!!key &&`          → R7
 *   MA9 steerPendingSnapshot 的 sid 不再回落当前会话 → R8
 */
import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';

const ROOT = path.resolve(__dirname, '..');
const PAGE = fs
  .readFileSync(path.join(ROOT, 'components', 'AIChatPage.tsx'), 'utf8')
  .replace(/\/\*[\s\S]*?\*\//g, '')
  .replace(/^\s*\/\/.*$/gm, '');

/** Body of a `const X = useCallback(...)` up to its closing deps array. */
const fnBody = (name: string, depsTail: string) => {
  const at = PAGE.indexOf(`const ${name} = `);
  if (at < 0) return '';
  const end = PAGE.indexOf(depsTail, at);
  return end < 0 ? PAGE.slice(at, at + 1600) : PAGE.slice(at, end + depsTail.length);
};

const steerFnBody = fnBody('steerPendingSnapshot', '}, [deliverMessage, isTargetSessionBusy]);');
const steerClickBody = fnBody(
  'handleSteerPending',
  '}, [isTargetSessionBusy, releasePending, steerPendingSnapshot]);',
);
const sendNextBody = fnBody('handleSendNextPending', '}, [handleSteerPending]);');
const releaseBody = fnBody('releasePending', '}, [flushPendingMessage]);');

const targetBusyBody = fnBody(
  'isTargetSessionBusy',
  '},\n    [isSessionBusy],\n  );',
) || fnBody('isTargetSessionBusy', '[isSessionBusy],');

const drainBody = (() => {
  // 注释已被剥掉，锚在 drain 自己的代码上
  const at = PAGE.indexOf('const next = queue.find((m) => {');
  return at < 0 ? '' : PAGE.slice(at, at + 800);
})();

describe('R1 — 注入前必须确认真在跑', () => {
  it('the guard exists and asks about this entry’s own session', () => {
    expect(steerFnBody).toMatch(/const sid = \(snapshot\.sessionId \|\| ''\)\.trim\(\)/);
    expect(steerFnBody).toMatch(/if \(!isTargetSessionBusy\(sid\)\) return;/);
  });

  it('and the injection still happens when the turn is running', () => {
    expect(steerFnBody).toContain("{ clearInputState: false, salvageStream: false, steer: true }");
  });
});

describe('R2 — 闸门在标记与发送之前', () => {
  it('marks steered only after the guard', () => {
    const guard = steerFnBody.indexOf('isTargetSessionBusy(');
    const mark = steerFnBody.indexOf('steered: true');
    const send = steerFnBody.indexOf('steer: true');
    expect(guard).toBeGreaterThan(-1);
    expect(mark).toBeGreaterThan(-1);
    expect(send).toBeGreaterThan(-1);
    expect(guard).toBeLessThan(mark);
    expect(guard).toBeLessThan(send);
  });
});

describe('R3 — 注入只来自「立即发这条」，排队不注入', () => {
  it('no call site bypasses the funnel with its own steer delivery', () => {
    expect(PAGE.match(/steer: true/g) || []).toHaveLength(1);
    expect(steerFnBody).toContain('steer: true');
  });

  it('park 点不再注入：没有任何 `steerPendingSnapshot(snapshot)` 残留', () => {
    expect(PAGE.match(/steerPendingSnapshot\(snapshot\)/g) || []).toHaveLength(0);
  });

  it('唯一调用点是队列条目的插话按钮那一跳', () => {
    expect(PAGE.match(/steerPendingSnapshot\(pm\)/g) || []).toHaveLength(1);
    expect(steerClickBody).toContain('steerPendingSnapshot(pm)');
    expect(PAGE).toMatch(/handleSteerPending\(pm\)/);
  });

  it('“已引导注入”只在真的注入过之后才标上', () => {
    expect(PAGE.match(/\{ \.\.\.m, steered: true \}/g) || []).toHaveLength(1);
    expect(steerFnBody).toContain('{ ...m, steered: true }');
  });
});

describe('R5 — 立即发这条：忙时插话，空闲时正常发出', () => {
  it('branches on this entry’s own session, not the agent-wide busy flag', () => {
    expect(steerClickBody).toMatch(
      /const sid = \(pm\.sessionId \|\| ''\)\.trim\(\) \|\| \(currentSessionIdRef\.current \|\| ''\)\.trim\(\);/,
    );
    expect(steerClickBody).toMatch(/if \(isTargetSessionBusy\(sid\)\) \{/);
    expect(steerClickBody).toContain('releasePending(pm)');
  });

  it('an already-injected entry is never re-sent', () => {
    expect(steerClickBody).toMatch(/if \(pm\.steered\) return;/);
  });

  it('表头「发送下一条」走同一条路，不再自己 flush', () => {
    expect(sendNextBody).toContain('handleSteerPending(next)');
    expect(sendNextBody).not.toContain('flushPendingMessage');
    // 队首 = 第一条未注入的条目（顺序不跳）
    expect(sendNextBody).toMatch(/find\(\(m\) => !m\.steered\)/);
  });
});

describe('R6 — 手动与 drain 共用一条在飞路径', () => {
  it('releasePending 占住在飞闸、出队、发包、放闸', () => {
    expect(releaseBody).toMatch(/if \(isFlushingPendingRef\.current\) return;/);
    expect(releaseBody).toMatch(/isFlushingPendingRef\.current = true;/);
    expect(releaseBody).toMatch(/setPendingMessages\(\(prev\) => prev\.filter\(\(m\) => m\.id !== target\.id\)\)/);
    expect(releaseBody).toMatch(/await flushPendingMessage\(target\)/);
    expect(releaseBody).toMatch(/isFlushingPendingRef\.current = false;/);
  });

  it('发不出去就放回队首（不静默丢失、不打乱顺序）', () => {
    expect(releaseBody).toContain('[target, ...prev]');
    expect(releaseBody).toMatch(/catch \(e\) \{/);
  });

  it('drain 也只经由 releasePending 发', () => {
    expect(drainBody).toContain('releasePending(next)');
    expect(drainBody).not.toContain('flushPendingMessage(next)');
  });
});

describe('R4 / R7 — drain 的两道闸无条件过，未知会话不扣住', () => {
  it('the drain skips injected entries but takes the rest', () => {
    expect(drainBody).toMatch(/if \(m\.steered\) return false;/);
  });

  it('空会话号回落到当前会话，忙 / 在飞检查不许短路', () => {
    expect(drainBody).toMatch(
      /const sid = \(m\.sessionId \|\| ''\)\.trim\(\) \|\| \(currentSessionIdRef\.current \|\| ''\)\.trim\(\);/,
    );
    expect(drainBody).toMatch(/if \(isTargetSessionBusy\(sid\)\) return false;/);
    expect(drainBody).toMatch(/if \(isOutboundPending\(sid\)\) return false;/);
    // `if (sid && …)` 就是那个连发漏洞
    expect(drainBody).not.toMatch(/if \(sid &&/);
  });

  it('drain 的依赖里也必须是目标会话忙态（换回 isSessionBusy 就再也重算不出来）', () => {
    const at = PAGE.indexOf('const next = queue.find((m) => {');
    const deps = PAGE.slice(at, PAGE.indexOf(']);', at) + 3);
    expect(deps).toMatch(/isTargetSessionBusy/);
    expect(deps).not.toMatch(/[^t]isSessionBusy/);
  });

  it('「目标会话未知」不得被 agent 级忙态扣住（stranding → 发送没反应）', () => {
    expect(targetBusyBody).toMatch(/return !!key && isSessionBusy\(key\);/);
  });
});

describe('R8 — 闸内与调用方同一个 sid 解析口径', () => {
  it('steerPendingSnapshot 也用回落后当前会话的 sid', () => {
    expect(steerFnBody).toMatch(
      /const sid = \(snapshot\.sessionId \|\| ''\)\.trim\(\) \|\| \(currentSessionIdRef\.current \|\| ''\)\.trim\(\);/,
    );
  });

  it('闸内不再直接拿未解析的 sessionId 判忙', () => {
    expect(steerFnBody).not.toMatch(/isSessionBusy\(\(snapshot\.sessionId/);
  });
});
