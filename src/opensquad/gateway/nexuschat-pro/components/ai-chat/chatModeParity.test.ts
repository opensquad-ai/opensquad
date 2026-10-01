/**
 * 聊天（通讯录）版面锁。
 *
 * 这个版面的价值全在「只有一个」：一个入口、一份左栏、一条数据通道。它最容易
 * 被后续改动悄悄侵蚀 —— 有人再给 agent-web 加一套聊天渲染、把会话列表放回左栏、
 * 或者让 DM 窗口去读 agent 的 `to_user` 输出，都不会有运行时报错。所以用源码扫描
 * 锁住这几条边界（与 conversationRenderParity 同一套做法）：
 *
 *   C1  聊天是独立入口：agent-web 里不得再有聊天版面（它只切视图）+ 无遗留开关键；
 *   C2  左栏只有一份 ContactsRail，App 的群聊版面与它共用；
 *   C3  DM 窗口只画对话：走私信通道、气泡渲染，不碰 agent-web 的 session/timeline；
 *   C4  「详细」抽屉挂在 DM 窗口一侧，历史检索搜私信记录；
 *   C5  agent 管理页在聊天版面是联系人列表。
 */
import { describe, expect, it } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';

const read = (rel: string) => fs.readFileSync(path.join(process.cwd(), rel), 'utf8');

const PAGE = read('components/AIChatPage.tsx');
const MSG_BUBBLE = read('components/ai-chat/MessageBubble.tsx');
const MODE_SWITCH = read('components/ai-chat/UiModeSwitch.tsx');
const RAIL = read('components/ContactsRail.tsx');
const DM = read('components/DirectChatWindow.tsx');
const DRAWER = read('components/ai-chat/ChatDetailDrawer.tsx');
const FILES = read('components/ai-chat/ProjectFilesPanel.tsx');
const COMPOSER = read('components/ai-chat/AgentWebComposer.tsx');
const HOST_PREFS = read('utils/hostUiPrefs.ts');
const AGENT_MANAGER = read('components/AgentManagerPage.tsx');
const APP = read('App.tsx');
const ZH = read('locales/zh.json');
const EN = read('locales/en.json');

describe('C1 聊天是独立入口，agent-web 里没有聊天版面', () => {
  it('UiModeSwitch 只有 Work / Code 两段，聊天是旁边的开关', () => {
    expect(MODE_SWITCH).toMatch(/export type UiMode = 'classic' \| 'solo'/);
    expect(MODE_SWITCH).toMatch(/chatUi: boolean;/);
    expect(MODE_SWITCH).toMatch(/aria-pressed=\{chatUi\}/);
    for (const key of ['aiChat.uiModeClassic', 'aiChat.uiModeSolo', 'aiChat.uiModeChat']) {
      expect(MODE_SWITCH).toContain(key);
    }
    expect(MODE_SWITCH).not.toMatch(/UiMode = 'classic' \| 'solo' \| 'chat'/);
  });

  it('开关只切视图，AIChatPage 不再有聊天版面状态', () => {
    expect(PAGE).toMatch(/const goToMessenger = useCallback\(\(on: boolean\) => \{/);
    expect(PAGE).toMatch(/window\.dispatchEvent\(new CustomEvent\('switchView', \{ detail: 'chat' \}\)\)/);
    // 旧版面的状态与分支必须彻底消失，否则「普通输出被当聊天」会回来。
    expect(PAGE).not.toMatch(/isChat/);
    expect(PAGE).not.toMatch(/chatDetailOpen/);
    expect(PAGE).not.toMatch(/<ContactsRail/);
    expect(PAGE).not.toMatch(/<ChatDetailDrawer/);
    // 遗留的持久化键要清掉，不再有第二个开关状态。
    expect(PAGE).toMatch(/localStorage\.removeItem\('ai_chat_ui'\)/);
    expect(HOST_PREFS).not.toMatch(/chatUi/);
  });

  it('聊天版面＝群聊视图本身，App 直接推导', () => {
    expect(APP).toMatch(/const chatUi = currentView === 'chat';/);
    expect(APP).not.toMatch(/localStorage\.getItem\('ai_chat_ui'\)/);
  });
});

describe('C2 左栏 = 唯一的通讯录（群聊 + 智能体，没有会话列表）', () => {
  it('群聊版面挂它，且不再有第二套列表', () => {
    expect(APP).toMatch(/<ContactsRail/);
    expect(APP).not.toMatch(/<ChatList/);
  });

  it('左栏只列联系人：没有会话列表，也不读 session API', () => {
    expect(RAIL).toMatch(/t\('aiChat\.chat\.contacts'\)/);
    expect(RAIL).toMatch(/t\('aiChat\.chat\.groups'\)/);
    expect(RAIL).toMatch(/t\('aiChat\.chat\.agents'\)/);
    expect(RAIL).not.toMatch(/agentSessionAPI/);
    expect(RAIL).not.toMatch(/onViewSession/);
  });

  it('群管理入口在「通讯录 + 刷新」左边（加群 / 创建群）', () => {
    expect(RAIL).toMatch(/opensquad-join-group/);
    expect(RAIL).toMatch(/opensquad-create-group/);
    expect(RAIL).toMatch(/t\('aiChat\.chat\.joinGroup'\)/);
    expect(RAIL).toMatch(/t\('aiChat\.chat\.createGroup'\)/);
    // 面板要留在应用里（浏览器原生 prompt 又生硬又不跟主题）。
    expect(RAIL).not.toMatch(/window\.prompt/);
    expect(RAIL).toMatch(/data-testid="contacts-rail-group-prompt"/);
  });

  it('通讯录点 agent / 群聊各自走事件桥', () => {
    expect(RAIL).toMatch(/useChatContacts/);
    expect(RAIL).toMatch(/detail: \{ agentId \}/);
    expect(RAIL).toMatch(/opensquad-select-group/);
  });
});

describe('C3 一对一窗口只有对话（走私信通道）', () => {
  it('数据源是私信，不是 agent-web 的 session/timeline', () => {
    expect(DM).toMatch(/directMessageAPI\.listThread/);
    expect(DM).toMatch(/directMessageAPI\.sendDirectMessage/);
    expect(DM).not.toMatch(/agentSessionAPI/);
    expect(DM).not.toMatch(/buildTimelineFromSession/);
  });

  it('两边都是气泡，且气泡底部常显时间 + 复制', () => {
    expect(DM).toMatch(/variant="messenger"/);
    expect(MSG_BUBBLE).toMatch(/const isMessenger = variant === 'messenger'/);
    expect(MSG_BUBBLE).toMatch(/bg-chatBubbleOther/);
    expect(MSG_BUBBLE).toMatch(/bg-chatBubbleSelf/);
    // 复制/时间不能是 hover-only：messenger 布局里没有 classic 的 actionRow 提示。
    expect(MSG_BUBBLE).toMatch(/data-testid="msg-meta"/);
    expect(MSG_BUBBLE).toMatch(/onClick=\{handleCopy\}/);
    expect(DM).toMatch(/new_direct_message/);
  });

  it('长线程分页取更早的私信', () => {
    expect(DM).toMatch(/limit: PAGE_SIZE/);
    expect(DM).toMatch(/offset: messagesRef\.current\.length/);
  });

  it('发送框支持文件 / 文件夹 / 图片', () => {
    expect(DM).toMatch(/uploadAPI\.uploadFile/);
    expect(DM).toMatch(/uploadAPI\.uploadFolder/);
    expect(DM).toMatch(/data-testid="dm-attach-menu"/);
    expect(DM).toMatch(/t\('aiChat\.attach\.uploadFiles'\)/);
    expect(DM).toMatch(/t\('aiChat\.attach\.uploadFolder'\)/);
    expect(DM).toMatch(/t\('aiChat\.attach\.uploadImages'\)/);
    // 附件随私信一起发出去（同一发送路径）。
    expect(DM).toMatch(/attachments\.length \? attachments : undefined/);
  });

  it('agent-web 回到纯 Work/Code：composer 没有 simple 分叉', () => {
    expect(COMPOSER).not.toMatch(/simple/);
    expect(PAGE).toMatch(/variant="classic"/);
  });
});

describe('C4 「详细」抽屉挂在 DM 窗口一侧', () => {
  it('App 挂抽屉，DM 窗口有入口', () => {
    expect(APP).toMatch(/<ChatDetailDrawer/);
    expect(DM).toMatch(/onOpenDetail/);
    expect(DM).toMatch(/t\('aiChat\.chat\.detail'\)/);
  });

  it('抽屉里有 agent 信息 + 文件树 + 历史搜索', () => {
    expect(DRAWER).toMatch(/<ProjectFilesPanel[\s\S]{0,600}?treeOnly/);
    // 历史检索搜的是私信记录（一对一就是 DM），不是 agent-web 会话。
    expect(DRAWER).toMatch(/directMessageAPI\.listThread/);
    expect(DRAWER).not.toMatch(/agentSessionAPI/);
    expect(DRAWER).toMatch(/t\('aiChat\.chat\.files'\)/);
    expect(DRAWER).toMatch(/t\('aiChat\.chat\.history'\)/);
  });

  it('私信地址来自群成员映射，不按目录名推导', () => {
    expect(DRAWER).toMatch(/groupAPI\.getGroup\('g-default'\)/);
    expect(DRAWER).toMatch(/m\.is_agent && m\.agent_id/);
    expect(APP).toMatch(/resolveDmContact/);
    expect(APP).toMatch(/m\.is_agent && m\.agent_id/);
  });

  it('抽屉的文件区只要本次会话的产出，不铺整棵目录树', () => {
    expect(DRAWER).toMatch(/hideAllFiles/);
    expect(FILES).toMatch(/hideAllFiles\?: boolean;/);
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

  it('App 按当前视图决定 Agent 管理页形态，并接住群聊选择', () => {
    expect(APP).toMatch(/variant=\{chatUi \? 'contacts' : 'full'\}/);
    expect(APP).toMatch(/opensquad-select-group/);
    // Work/Code 开关由 App 的 rail 发请求，AIChatPage 是状态所有者。
    expect(APP).toMatch(/opensquad-chat-ui-request/);
    expect(PAGE).toMatch(/opensquad-chat-ui-request/);
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
