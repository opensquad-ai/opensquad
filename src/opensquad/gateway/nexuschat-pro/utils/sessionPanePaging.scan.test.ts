/**
 * 刷新后"只能看到上一个任务流，更早的加载不出来"。
 *
 * 症状（2026-09-28 用户报）：刷新页面后，会话里只剩最后一轮任务流；往上滚也不
 * 加载更早的消息。
 *
 * 两条独立的缺口，缺一不可：
 *
 *   ① 会话 tab（`SessionChatPane`）**从来没接过分页**：它只取第一页
 *      （`SESSION_HISTORY_PAGE_SIZE` 条），它的 `ChatScrollHud` 只传了 `onUnpin` ——
 *      没有 `onNearTop`，所以往上滚什么也不会发生；父级的 `loadMoreHistory` 挂在
 *      另一个（chat slot）HUD 上。长会话因此永远只能看到最新一页。
 *   ② 父级的分页状态是**单会话**的（`hasMoreHistory` / offset / anchor 只描述一个
 *      session）。镜像（live）时间线由父级拥有，所以那条路径必须由父级翻页。实测
 *      （2026-09-28，点开一个 1000 条的旧会话）：`hasMoreHistorySid` 描述的是**另一个**
 *      会话，于是面板既不自己翻页（`useLive`），父级也不给回调 —— 打开旧会话永远只
 *      剩最后一轮。修法是把分页状态按会话分桶（`pagingBySid` + `pagingCursorRef`），
 *      并且翻页按 sidi 写回**它自己的**时间线：焦点会话 = `setTimeline`，镜像会话 =
 *      `liveTimelinesBySession[sid]`（写错就会串会话）。
 *
 * Rules:
 *   P1 `SessionChatPane` 的 HUD 必须同时接 `onNearTop` 与 `nearTopEnabled`；
 *   P2 面板自己的 `loadEarlier` 用 offset + anchor 取**严格更早**的一页，按
 *      drop→demote→merge 的顺序拼接，写回缓存时带上 `oldestMessageId`，并在 rAF 里
 *      恢复滚动位置（保持读者视线）；
 *   P3 镜像时间线不自己翻页：`useLive` 时走父级回调；
 *   P4 切走会话后到达的页必须丢弃（`sessionIdRef` 校验）；
 *   P5 父级的分页状态按会话分桶：面板/chat slot 都只读**自己那个 sid** 的那一格；翻页
 *      结果写回该会话自己的时间线（焦点 = setTimeline，镜像 = 自己的桶）；
 *   P6 单会话的全局那对（`hasMoreHistory`/`hasMoreHistorySid`/`isLoadingMore`）必须
 *      消失；`setPaging` / `setPagingCursor` 一律带 sid 实参（WS hydrate 也要带）；
 *   P7 第一页**装不满视口**时（`scrollHeight === clientHeight`，实测 623/623）
 *      根本没有滚动事件，`onNearTop` 永远不触发 —— 两个面板都必须再给一个显式的
 *      `data-load-earlier` 入口，接各自那套翻页回调。
 *
 * Mutations verified:
 *   MP1 HUD 去掉 onNearTop/nearTopEnabled                     → P1
 *   MP2 loadEarlier 改用裸 offset（去掉 anchor 实参）           → P2
 *   MP3 去掉 useLive 早返回（镜像路径自己翻页）                 → P3
 *   MP4 去掉 sessionIdRef 校验                                  → P4
 *   MP5 镜像分支也写 setTimeline（不分桶）                      → P5
 *   MP6 面板开关改读一个全局 hasMore（不按 sid）                → P5
 *   MP7 面板不渲染 loadEarlierButton（只留 near-top）           → P7
 *   MP8 chat slot 的 header 不分支                              → P7
 *   MP9 hook 的 hydrate 退回 setHasMoreHistory（漏 sid）        → P6
 */
import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';

const ROOT = path.resolve(__dirname, '..');
const strip = (src: string) =>
  src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');

const PANE = strip(
  fs.readFileSync(path.join(ROOT, 'components', 'ai-chat', 'SessionChatPane.tsx'), 'utf8'),
);
const PAGE = strip(
  fs.readFileSync(path.join(ROOT, 'components', 'AIChatPage.tsx'), 'utf8'),
);
const HOOK = strip(
  fs.readFileSync(path.join(ROOT, 'hooks', 'useAgentWebSocket.ts'), 'utf8'),
);

const between = (src: string, open: string, close: string) => {
  const at = src.indexOf(open);
  if (at < 0) return '';
  const end = src.indexOf(close, at);
  return end < 0 ? src.slice(at, at + 4000) : src.slice(at, end + close.length);
};

const loadEarlierBody = between(PANE, 'const loadEarlier = useCallback(', 'const nearTopHandler');

describe('P1 — 会话 tab 的滚动 HUD 接了向上翻页', () => {
  it('onNearTop + nearTopEnabled 都传了（旧代码只有 onUnpin）', () => {
    const hud = between(PANE, '<ChatScrollHud', '/>');
    expect(hud).toContain('onNearTop={nearTopHandler}');
    expect(hud).toContain('nearTopEnabled={nearTopEnabled}');
  });
});

describe('P2 — 面板自己翻页：锚点、拼接、缓存、滚动位置', () => {
  it('取的是严格更早的一页（offset + anchor）', () => {
    expect(loadEarlierBody).toMatch(
      /getSessionHistoryPaged\(\s*agentId,\s*sid,\s*earlierOffsetRef\.current,\s*SESSION_HISTORY_PAGE_SIZE,\s*earlierAnchorRef\.current \|\| undefined,/,
    );
  });

  it('按 drop → demote → merge 拼接，写回缓存带 oldestMessageId', () => {
    expect(loadEarlierBody).toContain('dropEntriesAlreadyPresent(older, prev)');
    expect(loadEarlierBody).toContain('demoteIntermediateAssistantMessages(');
    expect(loadEarlierBody).toContain('mergeAdjacentWorkflowEntries(');
    expect(loadEarlierBody).toMatch(/oldestMessageId: earlierAnchorRef\.current \|\| undefined/);
    expect(loadEarlierBody).toMatch(/earlierOffsetRef\.current \+= messages\.length/);
  });

  it('在 rAF 里把读者留在原来那条消息上', () => {
    expect(loadEarlierBody).toMatch(/requestAnimationFrame\(\(\) => \{\s*if \(el\) el\.scrollTop = el\.scrollHeight - prevScrollHeight;/);
  });
});

describe('P3 — 镜像时间线由父级翻页', () => {
  it('loadEarlier 对 useLive 直接返回', () => {
    expect(loadEarlierBody).toMatch(/if \(useLive \|\| loadingEarlier \|\| !hasMoreEarlier\) return;/);
  });

  it('HUD 的处理函数/开关按 useLive 分流', () => {
    expect(PANE).toMatch(/const nearTopHandler = useLive \? onLoadEarlier : loadEarlier;/);
    expect(PANE).toMatch(/const nearTopEnabled = useLive\s*\? \(!!onLoadEarlier && loadEarlierEnabled\)/);
  });
});

describe('P4 — 切走会话后到达的页被丢弃', () => {
  it('认 sessionIdRef', () => {
    expect(PANE).toMatch(/if \(sessionIdRef\.current !== sid\) return;/);
    expect(PANE).toMatch(/const sessionIdRef = useRef\(sessionId\);/);
  });
});

describe('P5 — 分页状态按会话隔离（镜像面板也能翻自己那本）', () => {
  it('面板的开关读它自己 sessionId 那一格', () => {
    expect(PAGE).toMatch(
      /onLoadEarlier=\{\(\) => loadMoreHistory\(sessionId\)\}/,
    );
    const props = between(PAGE, 'onLoadEarlier={() => loadMoreHistory(sessionId)}', 'onWithdrawUserMessage');
    expect(props).toMatch(/!!pagingBySid\[sessionId\]\?\.hasMore/);
    expect(props).toMatch(/!pagingBySid\[sessionId\]\?\.loading/);
  });

  it('chat slot 的 HUD / 头部读当前会话那一格', () => {
    const hud = between(PAGE, 'scrollRef={messagesContainerRef}', '/>');
    expect(hud).toMatch(/onNearTop=\{\(\) => void loadMoreHistory\(currentSessionId \|\| undefined\)\}/);
    expect(hud).toMatch(/nearTopEnabled=\{!!livePaging\?\.hasMore && !livePaging\?\.loading\}/);
    const head = between(PAGE, 'header={livePaging?.loading ? (', 'footer={(');
    expect(head).toMatch(/!!livePaging\?\.hasMore/);
    expect(head).toMatch(/loadMoreHistory\(currentSessionId \|\| undefined\)/);
  });

  it('翻页写回"这个会话自己的"时间线：焦点会话走 setTimeline，镜像会话走自己的桶', () => {
    expect(PAGE).toMatch(/if \(loadingSessionIdRef\.current === sid\) \{/);
    const at = PAGE.indexOf('const out = { ...prev, [sid]: mergeInto(prev[sid] || []) };');
    expect(at).toBeGreaterThan(0);
    const mirror = PAGE.slice(Math.max(0, at - 160), at + 200);
    expect(mirror).toMatch(/setLiveTimelinesBySession\(prev => \{/);
    expect(mirror).toMatch(/liveTimelinesBySessionRef\.current = out;/);
  });

  it('loadMoreHistory 显式吃 sid（面板按自己的会话请求，而不是全局那个）', () => {
    expect(PAGE).toMatch(/const loadMoreHistory = useCallback\(async \(sidOverride\?: string\) => \{/);
    expect(PAGE).toMatch(/const sid = String\(sidOverride \|\| loadingSessionIdRef\.current \|\| ''\)\.trim\(\);/);
  });
});

describe('P6 — 每个会话一份游标/标志', () => {
  it('单会话的那对全局状态已经不存在（它正是"面板翻不了页"的成因）', () => {
    expect(PAGE).not.toMatch(/setHasMoreHistory\(/);
    expect(PAGE).not.toMatch(/hasMoreHistorySid/);
    expect(PAGE).not.toMatch(/\bisLoadingMore\b/);
    expect(PAGE).toMatch(/const \[pagingBySid, setPagingBySid\] = useState</);
    expect(PAGE).toMatch(/const pagingCursorRef = useRef<Record<string, \{ offset: number; anchor: string \| null \}>>\(\{\}\);/);
  });

  it('setPaging / setPagingCursor 的调用一律带 sid 实参', () => {
    const calls = [
      ...(PAGE.match(/setPaging\(([^),]*)/g) || []),
      ...(PAGE.match(/setPagingCursor\(([^),]*)/g) || []),
    ];
    expect(calls.length).toBeGreaterThan(0);
    // 定义处是 `setPaging = useCallback(` 形式，调用处第一个实参必须是会话 id
    for (const call of calls) {
      const arg = call.replace(/^setPaging(Cursor)?\(/, '').trim();
      expect(arg, call).toMatch(/^[A-Za-z_$][\w$]*$/);
      expect(arg, call).not.toMatch(/^(true|false|null|undefined)$/);
    }
  });

  it('WS 的 hydrate 按 viewedSid 写（原来只 setHasMoreHistory，漏了 sid）', () => {
    expect(HOOK).toMatch(/setPagingCursor\(viewedSid, \{/);
    expect(HOOK).toMatch(/setPaging\(viewedSid, \{ hasMore: session\.has_more \?\? false \}\)/);
    expect(HOOK).not.toMatch(/setHasMoreHistory/);
  });

  it('面板的缓存写入带上 oldestMessageId（否则退回会漂移的 offset 路径）', () => {
    const writes = PANE.match(/putCachedSessionTimeline\(/g) || [];
    const anchors = PANE.match(/oldestMessageId:/g) || [];
    expect(writes.length).toBeGreaterThan(0);
    expect(anchors.length).toBe(writes.length);
  });
});

describe('P7 — 装不满视口的第一页也要有入口', () => {
  it('面板在可翻页时渲染 data-load-earlier，且调自己的 handler', () => {
    const btn = between(PANE, 'const loadEarlierButton = nearTopEnabled', 'useEffect(() => {');
    expect(btn).toMatch(/data-load-earlier="1"/);
    expect(btn).toMatch(/onClick=\{\(\) => void nearTopHandler\(\)\}/);
    expect(btn).toMatch(/nearTopEnabled && nearTopHandler \?/);
    // 光定义不算 —— 必须真的进了 ChatTimeline 的 header
    expect(PANE).toMatch(/\{loadEarlierButton\}/);
  });

  it('父级 chat slot 的 header 在"这个会话还有更早的"时给按钮', () => {
    const head = between(PAGE, 'header={livePaging?.loading ? (', 'footer={(');
    expect(head).toMatch(/data-load-earlier="1"/);
    expect(head).toMatch(/onClick=\{\(\) => void loadMoreHistory\(currentSessionId \|\| undefined\)\}/);
    // 按钮必须是那个三元分支本身：hasMore 判定后面紧跟 `) ? (`，中间不插别的条件
    expect(head).toMatch(/!!livePaging\?\.hasMore\s*\) \? \(/);
  });

  it('两个入口共用同一个文案 key', () => {
    expect(PANE).toContain("t('aiChat.loadEarlierMessages')");
    expect(PAGE).toContain("t('aiChat.loadEarlierMessages')");
  });
});
