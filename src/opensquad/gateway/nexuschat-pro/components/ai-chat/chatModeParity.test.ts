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
const PANE = read('components/ai-chat/SessionChatPane.tsx');
const MSG_BUBBLE = read('components/ai-chat/MessageBubble.tsx');
const MODE_SWITCH = read('components/ai-chat/UiModeSwitch.tsx');
const RAIL = read('components/ai-chat/ChatModeSidebar.tsx');
const DRAWER = read('components/ai-chat/ChatDetailDrawer.tsx');
const FILES = read('components/ai-chat/ProjectFilesPanel.tsx');
const SIDEBAR = read('components/ai-chat/SessionSidebar.tsx');
const COMPOSER = read('components/ai-chat/AgentWebComposer.tsx');
const HOST_PREFS = read('utils/hostUiPrefs.ts');
const AGENT_MANAGER = read('components/AgentManagerPage.tsx');
const APP = read('App.tsx');
const ZH = read('locales/zh.json');
const EN = read('locales/en.json');

describe('C1 聊天是一个独立版面开关，不是第三段 tab', () => {
  it('UiModeSwitch 只有 Work / Code 两段，聊天是旁边的开关', () => {
    expect(MODE_SWITCH).toMatch(/export type UiMode = 'classic' \| 'solo'/);
    expect(MODE_SWITCH).toMatch(/chatUi: boolean;/);
    expect(MODE_SWITCH).toMatch(/aria-pressed=\{chatUi\}/);
    for (const key of ['aiChat.uiModeClassic', 'aiChat.uiModeSolo', 'aiChat.uiModeChat']) {
      expect(MODE_SWITCH).toContain(key);
    }
    // 三段 tab 的老写法必须消失：聊天不再和 Work/Code 挤在一个 tablist 里。
    expect(MODE_SWITCH).not.toMatch(/UiMode = 'classic' \| 'solo' \| 'chat'/);
  });

  it('AIChatPage 用独立的 chatUi 状态，localStorage 键分开', () => {
    expect(PAGE).toMatch(/localStorage\.getItem\('ai_chat_ui'\) === '1'/);
    expect(PAGE).toMatch(/const isChat = chatUi;/);
    expect(PAGE).toMatch(/localStorage\.setItem\('ai_chat_ui', on \? '1' : '0'\)/);
  });

  it('host prefs 与 App 都同步这个开关', () => {
    expect(HOST_PREFS).toMatch(/chatUi\?: boolean \| null;/);
    expect(HOST_PREFS).toMatch(/localStorage\.setItem\(CHAT_UI_KEY, prefs\.chatUi \? '1' : '0'\)/);
    expect(APP).toMatch(/localStorage\.getItem\('ai_chat_ui'\) === '1'/);
    expect(APP).toMatch(/variant=\{chatUi \? 'contacts' : 'full'\}/);
  });
});

describe('C2 聊天模式左栏 = 通讯录（没有会话列表）', () => {
  it('AIChatPage 在 chat 模式换掉项目会话侧栏', () => {
    expect(PAGE).toMatch(/\{isChat \? \(\s*<ChatModeSidebar/);
  });

  it('左栏只列联系人：没有会话列表，也不读 session API', () => {
    expect(RAIL).toMatch(/t\('aiChat\.chat\.contacts'\)/);
    expect(RAIL).toMatch(/t\('aiChat\.chat\.groups'\)/);
    expect(RAIL).toMatch(/t\('aiChat\.chat\.agents'\)/);
    // 会话列表被移除：一个联系人就一个窗口，历史走「详细 → 历史」。
    expect(RAIL).not.toMatch(/agentSessionAPI/);
    expect(RAIL).not.toMatch(/onViewSession/);
    expect(RAIL).not.toMatch(/data-testid="chat-mode-session-row"/);
  });

  it('通讯录点 agent / 群聊各自走事件桥', () => {
    expect(RAIL).toMatch(/useChatContacts/);
    // 点击 agent 走 App 的事件桥，用 agent_id 而不是目录名
    expect(RAIL).toMatch(/detail: \{ agentId: agentIdToOpen \}/);
    expect(RAIL).toMatch(/opensquad-select-group/);
  });
});

describe('C3 只有对话，没有过程', () => {
  it('live 时间线只渲染 message，其余条目一律不画', () => {
    expect(PAGE).toMatch(/if \(isChat && entry\.kind !== 'message'\) return null/);
  });

  it('会话标签/历史那条渲染路径（SessionChatPane）同样只有 message', () => {
    // 两套渲染代码：只在 AIChatPage 过滤，从历史里打开一条会话就会又看到工具流。
    expect(PANE).toMatch(/messagesOnly\?: boolean;/);
    expect(PANE).toMatch(/if \(messagesOnly && entry\.kind !== 'message'\) return null/);
    expect(PAGE).toMatch(/<SessionChatPane[\s\S]{0,600}?messagesOnly=\{isChat\}/);
  });

  it('聊天模式不挂本次改动卡片与可视化嵌入', () => {
    expect(PAGE).toMatch(/!isChat && entry\.data\.role === 'assistant'[\s\S]{0,80}collectTurnChangedFilesBefore/);
    expect(PAGE).toMatch(/!isChat && entry\.data\.role === 'assistant'[\s\S]{0,80}htmlEmbedsByAssistantIndex/);
  });

  it('两边都是气泡：agent 的最终输出也包在气泡里（和群聊一致）', () => {
    // 两条渲染路径都要气泡，否则从历史打开一条会话就退回文档流。
    expect(PAGE).toMatch(/variant: isChat \? 'messenger' as const : 'classic' as const/);
    expect(PANE).toMatch(/variant: messagesOnly \? 'messenger' as const : 'classic' as const/);
    expect(MSG_BUBBLE).toMatch(/const isMessenger = variant === 'messenger'/);
    expect(MSG_BUBBLE).toMatch(/bg-chatBubbleOther/);
    expect(MSG_BUBBLE).toMatch(/bg-chatBubbleSelf/);
    // 流式预览同样是气泡。
    expect(PAGE).toMatch(/variant=\{isChat \? 'messenger' : 'classic'\}/);
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

  it('抽屉的文件区只要本次会话的产出，不铺整棵目录树', () => {
    expect(DRAWER).toMatch(/hideAllFiles/);
    expect(FILES).toMatch(/hideAllFiles\?: boolean;/);
    // 「所有文件」页签与整树视图都要真的被挡住，不能只藏按钮。
    expect(FILES).toMatch(/\.filter\(\(tt\) => !\(hideAllFiles && tt\.id === 'all'\)\)/);
    expect(FILES).toMatch(/tab === 'all' && !hideAllFiles/);
    expect(FILES).toMatch(/if \(hideAllFiles && tab !== 'changed'\) setTab\('changed'\)/);
  });
});

describe('C5 联系人化：Agent 管理页与群聊', () => {
  it('AgentManagerPage 有 contacts 变体，只留状态与聊天入口', () => {
    expect(AGENT_MANAGER).toMatch(/variant\?: 'full' \| 'contacts'/);
    expect(AGENT_MANAGER).toMatch(/if \(variant === 'contacts'\)/);
    expect(AGENT_MANAGER).toMatch(/data-testid="agent-manager-contact-row"/);
  });

  it('App 按当前模式决定 Agent 管理页形态，并接住群聊选择', () => {
    expect(APP).toMatch(/variant=\{chatUi \? 'contacts' : 'full'\}/);
    expect(APP).toMatch(/opensquad-select-group/);
    expect(APP).toMatch(/opensquad-ui-mode-changed/);
  });

  it('中英文案齐备', () => {
    for (const dict of [ZH, EN]) {
      expect(dict).toContain('"uiModeChat"');
      expect(dict).toContain('"uiModeChatHint"');
      for (const k of ['contacts', 'groups', 'agents', 'detail', 'files', 'history', 'noPreview']) {
        expect(dict).toContain(`"${k}"`);
      }
    }
  });
});
