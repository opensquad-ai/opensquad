/**
 * 串会话（旧回合的事件/标题落到别的会话页）在协议层只有三个漏点，这里逐个锁住。
 *
 * 症状（2026-09-28 用户报）："旧会话任务流中我创建新会话，或到别的会话继续任务流，
 * 会出现串会话页 —— 别的会话串进这新会话；并且之前任务流的会话也可能加载不了前面的
 * 消息。" 前端两条已验证的机制：
 *
 *   ① 实时工作流事件（tool_call / thought / plan …）曾被**延迟**入队（rAF、66ms 防抖），
 *      而 sid 是在入队时现读 `eventSidRef`。延迟到帧外时 ref 已被清空，只剩一个
 *      `|| currentSessionIdRef.current` 兜底 —— 于是 A 的工具调用落进了用户刚切到的
 *      B。sid 必须在产生事件的那一帧定死；定不下来就丢弃（宁缺勿串）。
 *   ② `session_title` 的 `setCurrentSessionId(prev => prev || sessionId)` 只在当前
 *      会话号为空（新建会话窗口）时才真的赋值 —— 而**刚被放弃的那个会话**的异步标题
 *      也在几秒后到达，一旦它先到，就把面板拽回旧会话，下一条消息顺手发进了旧流。
 *
 * 同一类病灶（agent 级状态冒充某个会话的状态）还有两处，一并锁住：
 *   ③ 全局 `isStreaming` 只在"收尾帧的 sid 恰好是聚焦会话"时才释放。用户在回合跑完前
 *      切走，这个标志就永久为真；`isSessionBusy()` 空列表分支读它 → 一个完全空闲的
 *      会话被判成"在跑"，它的发送只会排队（「发送没反应」），侧栏选中行也跟着转。
 *   ④ 侧栏行首的进度动画曾用 `agentBusy && sessionId === 当前选中会话` 兜底 —— 选中
 *      不等于在跑，于是"点哪个会话哪个就动画"。行的忙态只能来自**这一行**的证据。
 *
 * Rules:
 *   S1 实时工作流事件一律带"产生它的那一帧"的 sid；拿不到 sid 就丢弃并告警，绝不再
 *      回落 `currentSessionIdRef`；
 *   S2 66ms 防抖的 markup 嗅探必须把所属 sid 一起记下来，投递时显式带上（延迟回调里
 *      已经没有帧上下文了）；
 *   S3 新建会话窗口里，`session_title` 不得采纳"刚被放弃的那个会话"的 sid（标题本身
 *      照常写入，它属于那条会话）；
 *   S4 全局 `isStreaming` 按"还有没有会话在流"释放，不按"收尾帧是不是聚焦会话"；
 *   S5 侧栏行的忙态只看本行证据（后端 busy 列表 ∪ 本端该会话的流证据），不得引用
 *      agent 级忙态或"当前选中会话"。
 *   S6 **非聚焦会话**的一页历史（`allowNonCurrent` / 后台拉取）绝不能写进 `setTimeline`
 *      —— 那是聚焦面板的时间线，写进去就是"福州天气会话的输出跑到当前会话的过程输出 /
 *      凭空多出一个工作流"。
 *   S7 非聚焦会话的页只能落到它自己的 live 桶（`liveTimelinesBySession[sid]`），且不得
 *      用磁盘页盖掉更丰富的 WS 桶（磁盘永远滞后于实时回合）。
 *
 * Mutations verified:
 *   MS1 把 enqueue 的 sid 兜底改回 `|| currentSessionIdRef.current` → S1
 *   MS2 把丢弃分支删掉（空 sid 照常入队）                        → S1
 *   MS3 嗅探投递去掉第四个实参（markupSniffSid）                → S2
 *   MS4 去掉 `abandoned` 判断，直接 setCurrentSessionId(prev||)  → S3
 *   MS5 把局部标记还给聚焦会话分支 / 去掉全局释放分支            → S4
 *   MS6 sessionBusy 里加回 agentBusy 或 currentSessionId          → S5
 *   MS7 去掉 applySessionPayload 的 background 分支（照写 setTimeline）→ S6
 *   MS8 缓存命中分支把 setTimeline(cached) 挪回 allowNonCurrent 之外 → S6
 *   MS9 parkSessionTimeline 去掉"更丰富就不覆盖"的判断           → S7
 */
import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';

const ROOT = path.resolve(__dirname, '..');
const strip = (src: string) =>
  src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');

const WS = strip(fs.readFileSync(path.join(ROOT, 'hooks', 'useAgentWebSocket.ts'), 'utf8'));

/** Slice from `open` to the first `close` after it (inclusive). */
const between = (src: string, open: string, close: string) => {
  const at = src.indexOf(open);
  if (at < 0) return '';
  const end = src.indexOf(close, at);
  return end < 0 ? src.slice(at, at + 1400) : src.slice(at, end + close.length);
};

const enqueueBody = between(WS, 'const enqueueLiveWorkflowEvent = (', 'pendingLiveWorkflowEvents.push');
const sniffBody = between(WS, 'const sniffMarkupTool = (', '}, 66);');
const emitSniffBody = between(WS, 'const emitSniffedMarkupTool = () => {', 'markupSniffSid,\n      );');
const titleBody = between(WS, "aiWsService.on('session_title'", 'setSessionTitleUpdate({ id: sessionId, title });');

describe('S1 — 实时工作流事件的 sid 在产生它的那一帧定死', () => {
  it('sid 只来自 ownSid / eventSidRef，不回落 currentSessionIdRef', () => {
    expect(enqueueBody).toMatch(
      /const sid = \(ownSid \|\| eventSidRef\.current \|\| ''\)\.trim\(\);/,
    );
    expect(enqueueBody).not.toMatch(/currentSessionIdRef/);
  });

  it('拿不到 sid 就丢弃并告警 —— 入队必须在闸门之后', () => {
    const guard = enqueueBody.indexOf('if (!sid) {');
    const push = enqueueBody.indexOf('pendingLiveWorkflowEvents.push');
    expect(guard).toBeGreaterThan(-1);
    expect(push).toBeGreaterThan(-1);
    expect(guard).toBeLessThan(push);
    expect(enqueueBody).toMatch(/console\.warn\(\s*'\[AIChatPage\] live workflow event dropped/);
  });
});

describe('S2 — 防抖嗅探记住所属会话', () => {
  it('缓冲区旁记一个 sid，且在 WS 处理帧内捕获', () => {
    expect(WS).toMatch(/let markupSniffSid = '';/);
    expect(sniffBody).toMatch(/const sid = \(eventSidRef\.current \|\| ''\)\.trim\(\);/);
    expect(sniffBody).toMatch(/if \(sid\) markupSniffSid = sid;/);
  });

  it('投递时显式带上它（回调里已经没有帧上下文）', () => {
    expect(emitSniffBody).toContain('markupSniffSid');
    expect(emitSniffBody).not.toContain('eventSidRef');
  });
});

describe('S3 — 新建会话窗口不得被旧会话的标题拽走', () => {
  it('只采纳非"刚被放弃"的 sid，标题照常写入', () => {
    expect(titleBody).toMatch(/newSessionPendingRef\.current/);
    expect(titleBody).toMatch(/sessionId === agentCurrentSessionIdRef\.current/);
    expect(titleBody).toMatch(/if \(!abandoned\) setCurrentSessionId\(prev => prev \|\| sessionId\);/);
    expect(titleBody).toContain('setSessionTitleUpdate({ id: sessionId, title });');
  });
});

const focusClearBody = between(
  WS,
  "if (!clearSid || clearSid === (currentSessionIdRef.current || '')) {",
  '}',
);
const globalClearBody = between(
  WS,
  'if (!Object.values(isStreamingBySessionRef.current).some(Boolean)) {',
  '}',
);

describe('S4 — 全局 isStreaming 按"还有没有会话在流"释放', () => {
  it('聚焦会话那一支只清本地缓冲，不再释放全局标志（那是永久 latch 的来源）', () => {
    expect(focusClearBody).toContain('streamingTextRef.current = \'\';');
    expect(focusClearBody).not.toContain('setIsStreaming');
  });

  it('全局标志改由每会话的流证据判定', () => {
    expect(globalClearBody).toContain('setIsStreaming(false);');
    expect(globalClearBody).toContain('setAgentStatus(');
  });
});

describe('S5 — 侧栏行的忙态只看本行证据', () => {
  const SIDEBAR = strip(
    fs.readFileSync(path.join(ROOT, 'components', 'ai-chat', 'SessionSidebar.tsx'), 'utf8'),
  );
  const BUSY = strip(fs.readFileSync(path.join(ROOT, 'utils', 'sessionBusy.ts'), 'utf8'));

  it('判定函数不认识 agent 级忙态，也不认识"当前选中会话"', () => {
    expect(BUSY).not.toMatch(/agentBusy/);
    expect(BUSY).not.toMatch(/currentSessionId/);
    expect(BUSY).toMatch(/busySessionIds/);
    expect(BUSY).toMatch(/streamingSessionIds/);
  });

  it('行组件传的是每会话的流证据，不再传 agent 级忙态', () => {
    expect(SIDEBAR).not.toMatch(/agentBusy/);
    expect(SIDEBAR).toMatch(/streamingSessionIds/);
  });
});

const PAGE = strip(fs.readFileSync(path.join(ROOT, 'components', 'AIChatPage.tsx'), 'utf8'));
const applyBody = between(PAGE, 'const applySessionPayload = useCallback(', 'const loadSessionTimelineFast');
const parkBody = between(PAGE, 'const parkSessionTimeline = useCallback(', 'const applySessionPayload');
const cacheHitBody = between(PAGE, 'const cached = meta?.entries;', 'if (meta.complete && !opts?.softRefresh) return true;');

describe('S6 — 非聚焦会话的一页历史不许落到聚焦时间线', () => {
  it('applySessionPayload 先分流：非本会话/后台只进自己的桶', () => {
    const gate = applyBody.indexOf('if (opts?.background || currentSessionIdRef.current !== sessionId) {');
    const park = applyBody.indexOf('parkSessionTimeline(sessionId, entries);');
    const paint = applyBody.indexOf('setTimeline(entries);');
    expect(gate).toBeGreaterThan(-1);
    expect(park).toBeGreaterThan(gate);
    // setTimeline 必须在分流之后 —— 分流命中时直接 return，走不到它
    expect(paint).toBeGreaterThan(gate);
    // 从闸门一路切到 setTimeline：命中分流必须 park 后立刻 return，不许落到 paint
    const branch = applyBody.slice(gate, paint);
    expect(branch).toContain('parkSessionTimeline(sessionId, entries);');
    expect(branch).toContain('return;');
  });

  it('缓存命中分支同样只在聚焦会话时才 setTimeline', () => {
    expect(cacheHitBody).toMatch(/if \(!opts\?\.allowNonCurrent\) \{/);
    const branch = cacheHitBody.slice(cacheHitBody.indexOf('if (!opts?.allowNonCurrent) {'));
    const paintIdx = branch.indexOf('setTimeline(cached);');
    // setTimeline 落在聚焦分支内部（在 else 之前）
    const elseIdx = branch.indexOf('} else {');
    expect(paintIdx).toBeGreaterThan(-1);
    expect(elseIdx).toBeGreaterThan(paintIdx);
    expect(branch.slice(elseIdx, elseIdx + 200)).toContain('parkSessionTimeline(sessionId, cached);');
  });
});

describe('S7 — 非聚焦会话的页只落自己的桶，且不覆盖更丰富的实时桶', () => {
  it('桶写入前先比 richdenss，磁盘不得盖掉 WS', () => {
    expect(parkBody).toMatch(/setLiveTimelinesBySession\(\(prev\) => \{/);
    expect(parkBody).toMatch(/timelineRichness\(existing\) >= timelineRichness\(entries\)/);
    expect(parkBody).toMatch(/liveTimelinesBySessionRef\.current = out;/);
    // 只有非聚焦会话才会走到这里：桶的 key 是会话自己
    expect(parkBody).toMatch(/\[sessionId\]: entries/);
  });
});
