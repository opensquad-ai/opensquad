/**
 * 聊天（用户）模式锁：Work / Code 之外第三种模式，像通讯软件一样聊天。
 *
 * 这个模式的价值全在「少」—— 少一栏、少一层过程、少一堆按钮。因此它最容易
 * 被后续改动悄悄侵蚀：有人给 composer 加回 token 环形图、给时间线放回工具流、
 * 或者把右栏工作区重新显示出来，都不会有运行时报错。所以用源码扫描锁住这几条
 * 边界（与 conversationRenderParity 同一套做法）：
 *
 *   C1  模式是三段，且聊天模式的取值 / 持久化白名单都包含 'chat'；
 *   C2  聊天模式左栏是 ChatModeSidebar（会话 + 通讯录），不是项目会话侧栏；
 *   C3  聊天模式时间线只画 message，composer 走 simple；
 *   C4  聊天模式右栏是「详细」抽屉，工作区面板不参与渲染。
 */
import { describe, expect, it } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';

const read = (rel: string) => fs.readFileSync(path.join(process.cwd(), rel), 'utf8');

const PAGE = read('components/AIChatPage.tsx');
const MODE_SWITCH = read('components/ai-chat/UiModeSwitch.tsx');
const RAIL = read('components/ai-chat/ChatModeSidebar.tsx');
const DRAWER = read('components/ai-chat/ChatDetailDrawer.tsx');
const SIDEBAR = read('components/ai-chat/SessionSidebar.tsx');
const COMPOSER = read('components/ai-chat/AgentWebComposer.tsx');
const HOST_PREFS = read('utils/hostUiPrefs.ts');
const AGENT_MANAGER = read('components/AgentManagerPage.tsx');
const APP = read('App.tsx');
const ZH = read('locales/zh.json');
const EN = read('locales/en.json');

describe('C1 三段模式与持久化', () => {
  it('UiModeSwitch 提供 work / code / 聊天 三段', () => {
    expect(MODE_SWITCH).toMatch(/export type UiMode = 'classic' \| 'solo' \| 'chat'/);
    for (const key of ['aiChat.uiModeClassic', 'aiChat.uiModeSolo', 'aiChat.uiModeChat']) {
      expect(MODE_SWITCH).toContain(key);
    }
  });

  it('AIChatPage 能从 localStorage 恢复 chat', () => {
    expect(PAGE).toMatch(/stored === 'solo' \|\| stored === 'chat'/);
    expect(PAGE).toMatch(/const isChat = uiMode === 'chat'/);
  });

  it('host prefs 白名单认 chat（读写两处）', () => {
    const hits = HOST_PREFS.match(/uiMode === 'classic' \|\| [^\n]*'chat'/g) || [];
    expect(hits.length).toBeGreaterThanOrEqual(2);
  });

  it('SessionSidebar 复用同一个模式开关（不各写一份）', () => {
    expect(SIDEBAR).toMatch(/<UiModeSwitch uiMode=\{uiMode\} onUiModeChange=\{onUiModeChange\} \/>/);
  });
});

describe('C2 聊天模式左栏 = 会话 + 通讯录', () => {
  it('AIChatPage 在 chat 模式换掉项目会话侧栏', () => {
    expect(PAGE).toMatch(/\{isChat \? \(\s*<ChatModeSidebar/);
  });

  it('左栏有两个页签，点会话即切换整个会话页', () => {
    expect(RAIL).toMatch(/t\('aiChat\.chat\.sessions'\)/);
    expect(RAIL).toMatch(/t\('aiChat\.chat\.contacts'\)/);
    expect(RAIL).toMatch(/onViewSession\(s\.id\)/);
  });

  it('通讯录列出 agent（按字母分组）与群聊', () => {
    expect(RAIL).toMatch(/t\('aiChat\.chat\.agents'\)/);
    expect(RAIL).toMatch(/t\('aiChat\.chat\.groups'\)/);
    expect(RAIL).toMatch(/useChatContacts/);
    // 点击 agent 走 App 的事件桥，用 agent_id 而不是目录名
    expect(RAIL).toMatch(/detail: \{ agentId: agentIdToOpen \}/);
    expect(RAIL).toMatch(/opensquad-select-group/);
  });
});

describe('C3 只有对话，没有过程', () => {
  it('时间线只渲染 message，其余条目一律不画', () => {
    expect(PAGE).toMatch(/if \(isChat && entry\.kind !== 'message'\) return null/);
  });

  it('聊天模式不挂本次改动卡片与可视化嵌入', () => {
    expect(PAGE).toMatch(/!isChat && entry\.data\.role === 'assistant'[\s\S]{0,80}collectTurnChangedFilesBefore/);
    expect(PAGE).toMatch(/!isChat && entry\.data\.role === 'assistant'[\s\S]{0,80}htmlEmbedsByAssistantIndex/);
  });

  it('composer 走 simple，并收起计划卡与审批卡', () => {
    expect(PAGE).toMatch(/simple=\{isChat\}/);
    expect(PAGE).toMatch(/!isChat && runningShellJobs\.length > 0/);
    expect(PAGE).toMatch(/if \(isChat \|\| focusedPaneId !== paneId\) return null/);
    expect(PAGE).toMatch(/!isChat && sessionId === currentSessionId && effectivePlanSteps\.length > 0/);
  });

  it('AgentWebComposer 的 simple 真的收掉模型/强度/模式与上下文底栏', () => {
    expect(COMPOSER).toMatch(/simple\?: boolean;/);
    expect(COMPOSER).toMatch(/\{!simple \? \(\s*<SoloContextFooter/);
    expect(COMPOSER).toMatch(/\{!simple \? \(\s*<div[^>]*>\s*<ModePicker/);
    expect(COMPOSER).toMatch(/\{!simple \? \(\s*<div[^>]*>\s*<SoloModelPicker/);
    expect(COMPOSER).toMatch(/\{!simple \? \(\s*<MobileComposerMenu/);
  });
});

describe('C4 右栏换成「详细」抽屉', () => {
  it('chat 模式渲染 ChatDetailDrawer，而不是工作区面板', () => {
    expect(PAGE).toMatch(/\{isChat \? \(\s*<ChatDetailDrawer/);
    expect(PAGE).toMatch(/setChatDetailOpen\(true\)/);
  });

  it('抽屉里有 agent 信息 + 文件树 + 历史搜索', () => {
    expect(DRAWER).toMatch(/<ProjectFilesPanel[\s\S]{0,600}?treeOnly/);
    expect(DRAWER).toMatch(/agentSessionAPI\.searchSessions/);
    expect(DRAWER).toMatch(/t\('aiChat\.chat\.files'\)/);
    expect(DRAWER).toMatch(/t\('aiChat\.chat\.history'\)/);
  });
});

describe('C5 联系人化：Agent 管理页与群聊', () => {
  it('AgentManagerPage 有 contacts 变体，只留状态与聊天入口', () => {
    expect(AGENT_MANAGER).toMatch(/variant\?: 'full' \| 'contacts'/);
    expect(AGENT_MANAGER).toMatch(/if \(variant === 'contacts'\)/);
    expect(AGENT_MANAGER).toMatch(/data-testid="agent-manager-contact-row"/);
  });

  it('App 按当前模式决定 Agent 管理页形态，并接住群聊选择', () => {
    expect(APP).toMatch(/variant=\{chatUiMode === 'chat' \? 'contacts' : 'full'\}/);
    expect(APP).toMatch(/opensquad-select-group/);
    expect(APP).toMatch(/opensquad-ui-mode-changed/);
  });

  it('中英文案齐备', () => {
    for (const dict of [ZH, EN]) {
      expect(dict).toContain('"uiModeChat"');
      expect(dict).toContain('"uiModeChatHint"');
      for (const k of ['sessions', 'contacts', 'groups', 'agents', 'detail', 'files', 'history', 'noPreview']) {
        expect(dict).toContain(`"${k}"`);
      }
    }
  });
});
