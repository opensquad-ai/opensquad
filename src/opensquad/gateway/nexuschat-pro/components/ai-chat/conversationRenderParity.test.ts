/**
 * 渲染一致性锁：Code(solo) 模式的对话部分必须与 Work(classic) 同源。
 *
 * 背景：渲染优化（工作流中文工具统计、消息行的复制/喇叭布局、滚动提示）
 * 长期只在 classic 分支上做，solo 分支逐条落后。现在两种模式共用一条管线：
 * 消息一律 `MessageBubble`（classic 变体），工作流一律 `uiMode="classic"`。
 * 一旦有人在调用点重新引入 `isSolo ? <SoloMessage/> : <MessageBubble/>`，
 * 两个模式的渲染就会再次漂移，且没有任何运行时报错能发现 —— 因此用源码扫描锁住。
 *
 * L1  对话调用点不得再按 UI 模式分叉消息组件；
 * L2  工作流行必须显式传 classic；
 * L3  Work 模式的「变动区」= 产物视图，Code 模式仍为 git 变更视图；
 * L4  左侧会话菜单必须有「智能体」入口。
 */
import { describe, expect, it } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';

const read = (rel: string) => fs.readFileSync(path.join(process.cwd(), rel), 'utf8');

const PAGE = read('components/AIChatPage.tsx');
const PANE = read('components/ai-chat/SessionChatPane.tsx');
const FILES = read('components/ai-chat/ProjectFilesPanel.tsx');
const SIDEBAR = read('components/ai-chat/SessionSidebar.tsx');

describe('L1 对话渲染不再按 UI 模式分叉', () => {
  it('AIChatPage 不再引用 SoloMessage', () => {
    expect(PAGE).not.toMatch(/SoloMessage/);
  });

  it('SessionChatPane 不再引用 SoloMessage，也不再消费 isSolo', () => {
    expect(PANE).not.toMatch(/SoloMessage/);
    // prop 本身保留为 @deprecated 兼容位（ExecWorkflowView 仍在传），
    // 但不得再被解构 / 作为渲染判据使用。
    expect(PANE).not.toMatch(/isSolo = true/);
    expect(PANE).not.toMatch(/\{isSolo\b/);
    expect(PANE).toMatch(/@deprecated[\s\S]{0,240}isSolo\?: boolean;/);
  });

  it('两个调用点都不再出现 mode 三元选消息组件', () => {
    for (const src of [PAGE, PANE]) {
      expect(src).not.toMatch(/isSolo\s*\?[\s\S]{0,80}<SoloMessage/);
      expect(src).not.toMatch(/isSolo\s*\?[\s\S]{0,120}<MessageBubble/);
    }
  });

  it('流式消息固定 classic 变体', () => {
    expect(PAGE).not.toMatch(/variant=\{isSolo/);
    expect(PAGE).toMatch(/variant="classic"/);
  });
});

describe('L2 工作流行统一走 classic', () => {
  it('不再把当前 uiMode 直传给 SoloActivityRow', () => {
    // 注意：`uiMode={uiMode}` 在 SessionSidebar（模式开关）上是**正确**的，
    // 这里只锁工作流行 —— 它必须拿到的不是"当前模式"，而是常量 classic。
    expect(PAGE).not.toMatch(/<SoloActivityRow[\s\S]{0,500}?uiMode=\{uiMode\}/);
    expect(PANE).not.toMatch(/uiMode=\{isSolo/);
  });

  it('每个工作流渲染点都显式 classic（主时间线 + 任务折叠内）', () => {
    const hits = PAGE.match(/uiMode="classic"/g) || [];
    expect(hits.length).toBeGreaterThanOrEqual(2);
    expect(PANE).toMatch(/uiMode="classic"/);
  });

  it('滚动提示在两种模式下都渲染（不再被 !isSolo 包住）', () => {
    expect(PAGE).not.toMatch(/\{!isSolo && \(\s*<ChatScrollComposerHint/);
    expect(PANE).not.toMatch(/\{!isSolo && \(\s*<ChatScrollComposerHint/);
  });
});

describe('L3 Work 模式变动区 = 产物', () => {
  it('classic 模式下页签切成产物，而不是 git 变更', () => {
    expect(FILES).toMatch(/isArtifactsMode\s*\?\s*t\('aiChat\.artifacts'\)\s*:\s*t\('aiChat\.changedFiles'\)/);
  });

  it('产物模式下不渲染 会话/全部 范围切换（git 语义）', () => {
    expect(FILES).toMatch(/tab === 'changed' && rootPath && !isArtifactsMode/);
  });

  it('产物列表只列文件、不挂 diff 视图', () => {
    expect(FILES).toMatch(/<ArtifactsList/);
    expect(FILES).toMatch(/data-testid="artifacts-list"/);
    expect(FILES).toMatch(/aiChat\.noArtifacts/);
  });

  it('AIChatPage 把当前模式透传给右栏', () => {
    expect(PAGE).toMatch(/<ProjectFilesPanel[\s\S]{0,2500}?uiMode=\{uiMode\}/);
  });
});

describe('L4 左侧菜单智能体入口', () => {
  it('SessionSidebar 暴露 onOpenAgents 并渲染菜单项', () => {
    expect(SIDEBAR).toMatch(/onOpenAgents\?: \(\) => void;/);
    expect(SIDEBAR).toMatch(/onOpenAgents\?\.\(\)/);
    expect(SIDEBAR).toMatch(/t\('aiChat\.agents'\)/);
  });

  it('AIChatPage 接线并挂载弹窗', () => {
    expect(PAGE).toMatch(/onOpenAgents=\{handleOpenAgents\}/);
    expect(PAGE).toMatch(/agentsActive=\{agentSwitcherOpen\}/);
    expect(PAGE).toMatch(/<AgentSwitcherDialog/);
  });

  it('弹窗提供状态展示与 开启/停止/重启', () => {
    const dialog = read('components/ai-chat/AgentSwitcherDialog.tsx');
    expect(dialog).toMatch(/adminAPI\.startAgent/);
    expect(dialog).toMatch(/adminAPI\.stopAgent/);
    expect(dialog).toMatch(/adminAPI\.restartAgent/);
    expect(dialog).toMatch(/agentManager\.statusRunning/);
    // 切换必须用 agent_id 走 App 的事件桥（目录名不是合法 agentId）
    expect(dialog).toMatch(/detail: \{ agentId: a\.agent_id \}/);
  });
});
