/**
 * 回合结束后 Stop 必须松开：agent 的"空闲"必须真的落到界面上。
 *
 * 症状：agent 已经给出最终回复，输入框右侧还是红色停止按钮 —— 像卡住了。抓 agent
 * 日志可以看到回合其实早就结束了（`to_user_end_task` / `turn_elapsed` / `state: idle`
 * 都在），而且之后每 ~5s 还在广播 `busy_sessions {"sessions": []}`。所以卡的是前端：
 *
 *  1. runner 的 `_emit_busy_sessions` 发的是 `status {data: "busy"|"online"}`，而
 *     `handleStatus` 只认 idle/ready/complete/working/thinking —— `data` 这个键读得到
 *     （`msg.content || msg.data`），但两个值都不匹配任何分支，于是 `agentStatus` 一直
 *     停在 `working`。
 *  2. `isSessionBusy(sid)` 在本地 busy 列表为空时会回退到 `isStreaming || agentStatus
 *     ∈ {working, thinking}` —— 上面那个残留的 `working` 就此变成"永远忙"，停止按钮
 *     一直红着（终帧的 sid 只要和界面认的会话 id 对不上，`clearSessionRunState` 就不
 *     会重置全局标志）。
 *
 * 这两条都由 agent 反复广播的空快照兜底：空快照 = 没有任何会话在跑，是权威的"空闲"。
 *
 * Rules:
 *   R1 `isAgentIdleStatus` 只认 runner 的 agent 级空闲状态 `online`（大小写/空白无关），
 *      不把 `busy`/`idle`/空值混进来；
 *   R2 空快照把残留的 working/thinking 落回 connected，非空快照不动状态；
 *   R3 接线：handleStatus 认 `online` 并直接释放；busy_sessions 处理器用快照收敛状态。
 *
 * Mutations verified:
 *   MA1 `isAgentIdleStatus` 收 'busy' 也算空闲        → R1
 *   MA2 `isAgentIdleStatus` 大小写敏感                → R1
 *   MA3 非空快照也强行落回 connected                  → R2
 *   MA4 空快照不收敛 working                          → R2
 *   MA5 handleStatus 不认 online（去掉该分支）        → R3
 *   MA6 busy_sessions 处理器去掉收敛调用              → R3
 */
import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import {
  isAgentIdleStatus,
  settleAgentStatusOnBusySnapshot,
} from './agentWebChatHelpers';

const ROOT = path.resolve(__dirname, '..');
const HOOK = fs
  .readFileSync(path.join(ROOT, 'hooks', 'useAgentWebSocket.ts'), 'utf8')
  .replace(/\/\*[\s\S]*?\*\//g, '')
  .replace(/^\s*\/\/.*$/gm, '');

const handleStatusBody = (() => {
  const at = HOOK.indexOf('const handleStatus = ');
  return at < 0 ? '' : HOOK.slice(at, at + 2600);
})();

const busySessionsBody = (() => {
  const at = HOOK.indexOf("aiWsService.on('busy_sessions'");
  return at < 0 ? '' : HOOK.slice(at, at + 1200);
})();

describe('R1 — 只把 runner 的 online 当作"整个 agent 空闲"', () => {
  it('accepts the runner’s idle status', () => {
    expect(isAgentIdleStatus('online')).toBe(true);
    // 帧里可能带空白 / 大小写差异
    expect(isAgentIdleStatus(' Online ')).toBe(true);
    expect(isAgentIdleStatus('ONLINE')).toBe(true);
  });

  it('does not mistake a busy or unknown status for idle', () => {
    // 'busy' 是同一路广播的另一半：认成空闲会让正在跑的回合被提前松开
    expect(isAgentIdleStatus('busy')).toBe(false);
    // 'idle' 走 handleStatus 自己的分支（还要看别的窗格），不是这条
    expect(isAgentIdleStatus('idle')).toBe(false);
    expect(isAgentIdleStatus('')).toBe(false);
    expect(isAgentIdleStatus(null)).toBe(false);
    expect(isAgentIdleStatus(undefined)).toBe(false);
  });
});

describe('R2 — 空快照收敛 agent 级状态', () => {
  it('settles a stale working/thinking when the agent reports nothing running', () => {
    expect(settleAgentStatusOnBusySnapshot([], 'working')).toBe('connected');
    expect(settleAgentStatusOnBusySnapshot([], 'thinking')).toBe('connected');
  });

  it('leaves other statuses alone', () => {
    expect(settleAgentStatusOnBusySnapshot([], 'idle')).toBe('idle');
    expect(settleAgentStatusOnBusySnapshot([], 'connected')).toBe('connected');
    expect(settleAgentStatusOnBusySnapshot([], 'sleeping')).toBe('sleeping');
  });

  it('never settles while a session is still listed', () => {
    expect(settleAgentStatusOnBusySnapshot(['s1'], 'working')).toBe('working');
  });
});

describe('R3 — 接线', () => {
  it('handleStatus releases on the agent-wide idle status', () => {
    // 必须是真的条件分支：`false && isAgentIdleStatus(…)` 这种"看起来认了"不算
    expect(handleStatusBody).toMatch(/if \(isAgentIdleStatus\(data\)\) \{/);
    // 释放必须真的清掉当前会话的运行态（只改 agentStatus 会留下 per-sid 的 streaming 标志）
    expect(handleStatusBody).toMatch(/clearSessionRunState\(currentSessionIdRef\.current \|\| ''\)/);
  });

  it('and the release comes before the busy-list guesswork', () => {
    // online 是 agent 级结论，不该先被 otherBusy 的判断拦住
    const idleAt = handleStatusBody.indexOf('isAgentIdleStatus(data)');
    const guessAt = handleStatusBody.indexOf('const otherBusy');
    expect(idleAt).toBeGreaterThan(-1);
    expect(guessAt).toBeGreaterThan(-1);
    expect(idleAt).toBeLessThan(guessAt);
  });

  it('the busy_sessions snapshot settles the agent-wide status', () => {
    expect(busySessionsBody).toContain('settleAgentStatusOnBusySnapshot(');
    expect(busySessionsBody).toMatch(/setAgentStatus\(\(prev\) => settleAgentStatusOnBusySnapshot\(sessions, prev\)\)/);
  });
});
