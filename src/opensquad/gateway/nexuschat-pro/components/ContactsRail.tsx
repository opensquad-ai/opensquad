/**
 * ContactsRail — 通讯录左栏，**群聊与聊天共用的唯一一套**。
 *
 * 之前有两份：群聊那份是 `ChatList`（顶部短信/邮箱图标行 + 搜索框 + 群列表 +
 * 底部账号栏），聊天那份是 `ChatModeSidebar`（通讯录 + 智能体 + 底部账号栏）。
 * 同一件事两套渲染，两边一直在漂移。现在只有这一份：上「Work / Code / 聊天」
 * 开关，中间「群聊」+「智能体」，底部账号栏（保留）。
 *
 * 群管理入口（加群 / 创建群）放在「通讯录 + 刷新」左边 —— 它们属于列表本身，
 * 而不是某种版面的专有物。
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import { Bot, Layers, MoreHorizontal, RefreshCw, UserPlus, Users } from 'lucide-react';
import { groupAPI, type GroupListItem } from '../services/api';
import { SOFT_PRESENCE_MS, useSoftPresence } from '../utils/useSoftPresence';
import { getLocalAvatarFallback } from '../utils/image';
import { agentAvatar, agentLabel, agentRowKey, agentStatusOf, AGENT_STATUS_DOT } from '../utils/agentStatus';
import { useChatContacts } from '../hooks/useChatContacts';
import { UiModeSwitch, type UiMode } from './ai-chat/UiModeSwitch';
import { POPOVER_SURFACE_CLASS } from './ai-chat/popoverSurface';
import { AccountRailFooter, type AccountUser } from './AccountRailFooter';
import { Collapse, FoldChevron } from './Collapse';
import { OpenSquadLoader } from './OpenSquadLoader';

export interface ContactsRailProps {
  uiMode: UiMode;
  onUiModeChange?: (mode: UiMode) => void;
  /** 聊天（通讯录）版面是否开启。 */
  chatUi: boolean;
  onChatUiChange?: (on: boolean) => void;
  isOpen: boolean;
  /** 右侧正在看的会话，用于高亮（群 / agent 二选一）。 */
  activeGroupId?: string | null;
  activeAgentId?: string | null;
  onSelectGroup?: (groupId: string) => void;
  /** 群折叠开关的落库入口；不给就由 rail 自己调 groupAPI（它本来就有一份群列表）。 */
  onSetGroupFolded?: (groupId: string, folded: boolean) => Promise<void> | void;
  /** agent_id（事件桥/映射键）+ dir_name（文件与工作目录 API）+ 运行状态。 */
  onPickAgent?: (agentId: string, dirName: string, status: string) => void;
  /** 底部账号栏右侧的额外按钮（聊天版面的「详细」）。 */
  railActions?: React.ReactNode;
  currentUser?: AccountUser;
  onOpenProfile?: () => void;
  onOpenSettings?: () => void;
}

const RAIL_WIDTH = 264;
/** 群行小菜单宽度，用于右对齐到「···」按钮、以及贴边时的夹取。 */
const GROUP_MENU_W = 148;

/** A–Z bucket for the contact list; everything else lands in '#'. */
function bucketOf(label: string): string {
  const first = (label.trim()[0] || '').toUpperCase();
  return /[A-Z]/.test(first) ? first : '#';
}

export const ContactsRail: React.FC<ContactsRailProps> = ({
  uiMode,
  onUiModeChange,
  chatUi,
  onChatUiChange,
  isOpen,
  activeGroupId = null,
  activeAgentId = null,
  onSelectGroup,
  onSetGroupFolded,
  onPickAgent,
  railActions,
  currentUser = null,
  onOpenProfile,
  onOpenSettings,
}) => {
  const { t } = useTranslation();
  const { agents, groups, loading, reload } = useChatContacts({ enabled: isOpen });
  const [groupFilter, setGroupFilter] = useState('');
  /** 加群 / 创建群的小面板：留在 rail 里，用应用的样式，不用浏览器原生 prompt。 */
  const [groupPrompt, setGroupPrompt] = useState<null | 'join' | 'create'>(null);
  const [groupPromptValue, setGroupPromptValue] = useState('');
  /** 「折叠的群聊」展开态；搜索时强制展开，否则搜到的折叠群看不见。 */
  const [foldOpen, setFoldOpen] = useState(false);
  /** 群行右键 / hover「···」的小菜单（viewport 坐标）。 */
  const [groupMenu, setGroupMenu] = useState<null | { groupId: string; x: number; y: number }>(null);

  const { mounted: softMounted, visible: softVisible } = useSoftPresence(isOpen, SOFT_PRESENCE_MS);

  const buckets = useMemo(() => {
    const map = new Map<string, typeof agents>();
    for (const a of agents) {
      const key = bucketOf(agentLabel(a));
      const list = map.get(key);
      if (list) list.push(a);
      else map.set(key, [a]);
    }
    return [...map.entries()]
      .sort(([a], [b]) => (a === '#' ? 1 : b === '#' ? -1 : a.localeCompare(b)))
      .map(([letter, list]) => ({
        letter,
        list: [...list].sort((a, b) => agentLabel(a).localeCompare(agentLabel(b))),
      }));
  }, [agents]);

  const visibleGroups: GroupListItem[] = useMemo(() => {
    const q = groupFilter.trim().toLowerCase();
    if (!q) return groups;
    return groups.filter((g) => g.name.toLowerCase().includes(q));
  }, [groups, groupFilter]);

  /** 折叠的群不进主列表 —— 它们只出现在「折叠的群聊」里。 */
  const openGroups = useMemo(() => visibleGroups.filter((g) => !g.folded), [visibleGroups]);
  const foldedGroups = useMemo(() => visibleGroups.filter((g) => !!g.folded), [visibleGroups]);
  const foldedUnread = useMemo(
    () => foldedGroups.reduce((sum, g) => sum + (g.unread_count || 0), 0),
    [foldedGroups],
  );
  const foldExpanded = foldOpen || groupFilter.trim().length > 0;

  const setGroupFolded = useCallback(
    async (groupId: string, folded: boolean) => {
      setGroupMenu(null);
      try {
        if (onSetGroupFolded) await onSetGroupFolded(groupId, folded);
        else await groupAPI.updateGroup(groupId, { folded });
        await reload();
      } catch (e) {
        console.error('[ContactsRail] fold group failed:', e);
      }
    },
    [onSetGroupFolded, reload],
  );

  // 菜单是浮层（portal 到 body）：点别处 / Esc / 列表滚动都该把它收掉。
  useEffect(() => {
    if (!groupMenu) return;
    const onDown = (e: MouseEvent) => {
      const el = e.target as Element | null;
      // 菜单自身不算「别处」，否则点菜单项之前就先被关掉。
      if (el?.closest?.('[data-testid="contacts-rail-group-menu"]')) return;
      setGroupMenu(null);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setGroupMenu(null);
    };
    const onScroll = () => setGroupMenu(null);
    window.addEventListener('mousedown', onDown);
    window.addEventListener('keydown', onKey);
    window.addEventListener('scroll', onScroll, true);
    return () => {
      window.removeEventListener('mousedown', onDown);
      window.removeEventListener('keydown', onKey);
      window.removeEventListener('scroll', onScroll, true);
    };
  }, [groupMenu]);

  /** 群会话：由宿主接管，没给就退回事件桥（App 监听）。 */
  const selectGroup = useCallback(
    (groupId: string) => {
      if (onSelectGroup) {
        onSelectGroup(groupId);
        return;
      }
      window.dispatchEvent(new CustomEvent('opensquad-select-group', { detail: { groupId } }));
      window.dispatchEvent(new CustomEvent('switchView', { detail: 'chat' }));
    },
    [onSelectGroup],
  );

  const pickAgent = useCallback(
    (agentId: string, dirName: string, status: string, ready: boolean) => {
      if (!ready) {
        window.alert(t('agentManager.agentStartingHint'));
        return;
      }
      if (onPickAgent) {
        onPickAgent(agentId, dirName, status);
        return;
      }
      window.dispatchEvent(new CustomEvent('openAgentChat', { detail: { agentId } }));
      window.dispatchEvent(new CustomEvent('switchView', { detail: 'ai-chat' }));
    },
    [onPickAgent, t],
  );

  const requestJoinGroup = useCallback(() => {
    setGroupPromptValue('');
    setGroupPrompt('join');
  }, []);

  const requestCreateGroup = useCallback(() => {
    setGroupPromptValue('');
    setGroupPrompt('create');
  }, []);

  const submitGroupPrompt = useCallback(() => {
    const value = groupPromptValue.trim();
    if (!value) return;
    if (groupPrompt === 'join') {
      window.dispatchEvent(new CustomEvent('opensquad-join-group', { detail: { groupId: value } }));
    } else if (groupPrompt === 'create') {
      window.dispatchEvent(new CustomEvent('opensquad-create-group', { detail: { name: value } }));
    }
    setGroupPrompt(null);
    setGroupPromptValue('');
  }, [groupPrompt, groupPromptValue]);

  /** 群行：主列表与「折叠的群聊」共用。`foldedRow` 只改未读角标的颜色。 */
  const renderGroupRow = (g: GroupListItem, foldedRow: boolean) => {
    const active = !!activeGroupId && g.id === activeGroupId;
    return (
      <div
        key={`rail-group-${g.id}`}
        data-testid="contacts-rail-group-row"
        data-group-id={g.id}
        data-folded={foldedRow ? '1' : undefined}
        onClick={() => selectGroup(g.id)}
        onContextMenu={(e) => {
          e.preventDefault();
          setGroupMenu({ groupId: g.id, x: e.clientX, y: e.clientY });
        }}
        className={`group/row flex items-center gap-2.5 mx-1.5 px-2 py-2 rounded-xl cursor-pointer transition-colors ${
          active ? 'bg-primary/10' : 'hover:bg-black/[0.04] dark:hover:bg-white/[0.06]'
        }`}
      >
        <div className="relative shrink-0">
          <img
            src={g.avatar || getLocalAvatarFallback(g.id, g.name)}
            alt=""
            className="h-9 w-9 rounded-full object-cover bg-border"
            loading="lazy"
            onError={(e) => {
              const img = e.currentTarget;
              if (img.dataset.fallbackApplied) return;
              img.dataset.fallbackApplied = '1';
              img.src = getLocalAvatarFallback(g.id, g.name);
            }}
          />
          {g.unread_count > 0 ? (
            <span
              data-testid={foldedRow ? 'contacts-rail-group-unread-folded' : 'contacts-rail-group-unread'}
              className={`absolute -top-1 -right-1 min-w-[16px] rounded-full px-1 text-center text-[9px] font-bold ${
                foldedRow
                  ? 'bg-black/[0.08] text-textMuted dark:bg-white/[0.14]'
                  : 'bg-red-500 text-white'
              }`}
            >
              {g.unread_count}
            </span>
          ) : null}
        </div>
        <div className="min-w-0 flex-1">
          <div className="truncate text-[13px] text-textMain">{g.name}</div>
          <div className="mt-0.5 truncate text-[11px] text-textMuted">
            {g.last_message?.content || t('aiChat.chat.noPreview')}
          </div>
        </div>
        <button
          type="button"
          data-testid="contacts-rail-group-more"
          aria-label={t('aiChat.chat.groupMore')}
          title={t('aiChat.chat.groupMore')}
          onClick={(e) => {
            e.stopPropagation();
            const r = e.currentTarget.getBoundingClientRect();
            setGroupMenu({
              groupId: g.id,
              x: Math.max(8, r.right - GROUP_MENU_W),
              y: r.bottom + 4,
            });
          }}
          // 触屏没有 hover（也没有右键），所以窄屏常显、md+ 才 hover 浮现。
          className="shrink-0 rounded-lg p-1 text-textMuted opacity-100 transition-opacity hover:bg-black/[0.06] focus-visible:opacity-100 md:opacity-0 md:group-hover/row:opacity-100 md:focus-visible:opacity-100 dark:hover:bg-white/10"
        >
          <MoreHorizontal size={14} />
        </button>
      </div>
    );
  };

  if (!softMounted) return null;

  return (
    <div
      className={`os-soft-rail ${softVisible ? 'is-open' : ''}`}
      style={{ width: softVisible ? RAIL_WIDTH : 0 }}
      aria-hidden={!softVisible}
    >
      <div
        className="relative h-full flex flex-col os-depth-card os-soft-rail-inner"
        style={{ width: RAIL_WIDTH }}
      >
        <div className="h-11 px-2 border-b border-border box-border flex items-center shrink-0">
          <UiModeSwitch
            uiMode={uiMode}
            onUiModeChange={onUiModeChange}
            chatUi={chatUi}
            onChatUiChange={onChatUiChange}
          />
        </div>

        <div className="px-3 py-1.5 flex items-center gap-1 border-b border-border/60 shrink-0">
          <span className="min-w-0 flex-1 truncate text-[10px] font-semibold uppercase tracking-wide text-textMuted">
            {t('aiChat.chat.contacts')}
          </span>
          <button
            type="button"
            onClick={requestJoinGroup}
            className="p-1 rounded text-textMuted hover:bg-black/5 dark:hover:bg-white/10 shrink-0"
            title={t('aiChat.chat.joinGroup')}
            aria-label={t('aiChat.chat.joinGroup')}
          >
            <Users size={13} />
          </button>
          <button
            type="button"
            onClick={requestCreateGroup}
            className="p-1 rounded text-textMuted hover:bg-black/5 dark:hover:bg-white/10 shrink-0"
            title={t('aiChat.chat.createGroup')}
            aria-label={t('aiChat.chat.createGroup')}
          >
            <UserPlus size={13} />
          </button>
          <button
            type="button"
            onClick={() => void reload()}
            className="p-1 rounded text-textMuted hover:bg-black/5 dark:hover:bg-white/10 shrink-0"
            title={t('aiChat.agentRefresh')}
            aria-label={t('aiChat.agentRefresh')}
          >
            {loading ? <OpenSquadLoader size={13} /> : <RefreshCw size={13} />}
          </button>
        </div>

        {groupPrompt ? (
          <div className="px-2 pt-2 shrink-0" data-testid="contacts-rail-group-prompt">
            <div className="rounded-xl border border-border bg-panel p-2 shadow-sm">
              <div className="px-0.5 pb-1.5 text-[11px] text-textMuted">
                {groupPrompt === 'join' ? t('aiChat.chat.joinGroupPrompt') : t('aiChat.chat.createGroupPrompt')}
              </div>
              <input
                autoFocus
                value={groupPromptValue}
                onChange={(e) => setGroupPromptValue(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') submitGroupPrompt();
                  if (e.key === 'Escape') setGroupPrompt(null);
                }}
                className="w-full rounded-lg border border-border bg-bgLight px-2 py-1.5 text-[12px] text-textMain outline-none focus:border-primary/40"
              />
              <div className="mt-1.5 flex items-center justify-end gap-1">
                <button
                  type="button"
                  onClick={() => setGroupPrompt(null)}
                  className="rounded-md px-2 py-1 text-[11px] text-textMuted hover:bg-black/[0.05] dark:hover:bg-white/10"
                >
                  {t('common.cancel')}
                </button>
                <button
                  type="button"
                  onClick={submitGroupPrompt}
                  disabled={!groupPromptValue.trim()}
                  className="rounded-md bg-primary px-2.5 py-1 text-[11px] font-medium text-white disabled:opacity-40"
                >
                  {t('common.confirm')}
                </button>
              </div>
            </div>
          </div>
        ) : null}

        <div className="flex-1 min-h-0 overflow-y-auto os-depth-nest os-depth-nest--flush">
          <div className="px-3 pt-2 pb-1 flex items-center gap-2">
            <span className="text-[10px] font-semibold uppercase tracking-wide text-textMuted">
              {t('aiChat.chat.groups')}
            </span>
            <input
              value={groupFilter}
              onChange={(e) => setGroupFilter(e.target.value)}
              placeholder={t('aiChat.chat.searchGroups')}
              className="min-w-0 flex-1 bg-transparent text-[10px] text-textMain outline-none placeholder:text-textMuted/50"
            />
          </div>
          {visibleGroups.length === 0 ? (
            <div className="px-3 py-1 text-[10px] text-textMuted/50">{t('aiChat.none')}</div>
          ) : (
            <>
              {openGroups.map((g) => renderGroupRow(g, false))}
              {foldedGroups.length > 0 ? (
                <div data-testid="contacts-rail-fold">
                  <div className="mx-1.5">
                    <button
                      type="button"
                      data-testid="contacts-rail-fold-toggle"
                      aria-expanded={foldExpanded}
                      onClick={() => setFoldOpen((o) => !o)}
                      className="flex w-full items-center gap-2.5 rounded-xl px-2 py-2 text-left transition-colors hover:bg-black/[0.04] dark:hover:bg-white/[0.06]"
                    >
                      {/* 堆叠图标而不是迷你头像：1~4 个群时头像格子会参差不齐，
                          且缩略头像没有 onError 兜底，坏图会漏成空白。 */}
                      <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-black/[0.06] dark:bg-white/[0.1]">
                        <Layers size={16} strokeWidth={1.75} className="text-textMuted" />
                      </span>
                      <span className="min-w-0 flex-1 truncate text-[13px] text-textMain">
                        {t('aiChat.chat.foldedChats')}
                      </span>
                      {foldedUnread > 0 ? (
                        <span
                          data-testid="contacts-rail-fold-unread"
                          className="shrink-0 min-w-[16px] rounded-full bg-black/[0.08] px-1 text-center text-[9px] font-bold text-textMuted dark:bg-white/[0.14]"
                        >
                          {foldedUnread}
                        </span>
                      ) : null}
                      <FoldChevron open={foldExpanded} size={14} />
                    </button>
                  </div>
                  <Collapse open={foldExpanded}>
                    {foldedGroups.map((g) => renderGroupRow(g, true))}
                  </Collapse>
                </div>
              ) : null}
            </>
          )}

          <div className="px-3 pt-3 pb-1 text-[10px] font-semibold uppercase tracking-wide text-textMuted">
            {t('aiChat.chat.agents')}
          </div>
          {agents.length === 0 ? (
            <div className="px-3 py-1 text-[10px] text-textMuted/50">
              {loading ? t('common.loading') : t('aiChat.agentEmpty')}
            </div>
          ) : (
            buckets.map(({ letter, list }) => (
              <div key={`rail-bucket-${letter}`}>
                <div className="sticky top-0 z-10 bg-panel/90 px-3 py-0.5 text-[10px] font-semibold text-textMuted backdrop-blur">
                  {letter}
                </div>
                {list.map((a) => {
                  const st = agentStatusOf(a);
                  const label = agentLabel(a);
                  const isCurrent = a.agent_id === activeAgentId;
                  return (
                    <div
                      key={`rail-agent-${a.agent_id || a.dir_name}`}
                      data-testid="contacts-rail-agent-row"
                      onClick={() => pickAgent(a.agent_id, a.dir_name, st, !!a.ready)}
                      className={`flex items-center gap-2.5 mx-1.5 px-2 py-2 rounded-xl cursor-pointer transition-colors ${
                        isCurrent ? 'bg-primary/[0.07]' : 'hover:bg-black/[0.04] dark:hover:bg-white/[0.06]'
                      }`}
                    >
                      <div className="relative shrink-0">
                        <img
                          src={agentAvatar(a) || getLocalAvatarFallback(agentRowKey(a), label)}
                          alt=""
                          className="h-9 w-9 rounded-full object-cover bg-border"
                          loading="lazy"
                          onError={(e) => {
                            const img = e.currentTarget;
                            if (img.dataset.fallbackApplied) return;
                            img.dataset.fallbackApplied = '1';
                            img.src = getLocalAvatarFallback(agentRowKey(a), label);
                          }}
                        />
                        <span
                          className={`absolute -bottom-0.5 -right-0.5 h-2.5 w-2.5 rounded-full border-2 border-panel ${
                            AGENT_STATUS_DOT[st] || 'bg-gray-400'
                          }`}
                        />
                      </div>
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-1.5 min-w-0">
                          <span className="truncate text-[13px] text-textMain">{label}</span>
                          {isCurrent ? (
                            <span className="shrink-0 rounded-full bg-primary/10 px-1.5 py-[1px] text-[9px] text-primary">
                              {t('aiChat.agentCurrent')}
                            </span>
                          ) : null}
                        </div>
                        <div className="mt-0.5 flex items-center gap-1 truncate text-[11px] text-textMuted">
                          {a.agent_type ? (
                            <span className="inline-flex items-center gap-0.5">
                              <Bot size={10} strokeWidth={1.75} className="shrink-0" />
                              {a.agent_type}
                            </span>
                          ) : null}
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            ))
          )}
        </div>

        {(onOpenProfile || onOpenSettings) && (
          <AccountRailFooter
            currentUser={currentUser}
            onOpenProfile={() => onOpenProfile?.()}
            onOpenSettings={() => onOpenSettings?.()}
            actions={railActions}
          />
        )}
      </div>

      {groupMenu ? (
        (() => {
          const g = groups.find((x) => x.id === groupMenu.groupId);
          if (!g) return null;
          // 贴边夹取：右/下溢出会跑出 viewport 之外。
          const left = Math.max(8, Math.min(groupMenu.x, window.innerWidth - GROUP_MENU_W - 8));
          const top = Math.max(8, Math.min(groupMenu.y, window.innerHeight - 52));
          return createPortal(
            <div
              data-testid="contacts-rail-group-menu"
              className={`fixed z-[220] rounded-xl border border-border py-1 shadow-xl ${POPOVER_SURFACE_CLASS}`}
              style={{ left, top, width: GROUP_MENU_W }}
              onContextMenu={(e) => e.preventDefault()}
            >
              <button
                type="button"
                data-testid="contacts-rail-group-fold-toggle"
                onClick={() => void setGroupFolded(g.id, !g.folded)}
                className="w-full px-3 py-1.5 text-left text-[12px] text-textMain hover:bg-black/[0.06] dark:hover:bg-white/10"
              >
                {g.folded ? t('aiChat.chat.unfoldGroup') : t('aiChat.chat.foldGroup')}
              </button>
            </div>,
            document.body,
          );
        })()
      ) : null}
    </div>
  );
};

export default ContactsRail;
