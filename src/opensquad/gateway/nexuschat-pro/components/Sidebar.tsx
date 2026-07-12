import React, { useEffect, useState } from 'react';
import { MessageCircle, Users, Settings, Calendar, Star, Puzzle, Server, BookOpen, UserCircle, Cpu, ScrollText, Store, LayoutGrid, History, Zap, Bot, Layers, KanbanSquare, Radio, Loader2 } from 'lucide-react';
import { Tooltip } from './Tooltip';
import { User } from '../types';
import { getAvatarUrl, getLocalAvatarFallback, resolveChatAvatar, resolveChatName } from '../utils/image';
import { useTranslation } from 'react-i18next';
import { setLanguage } from '../i18n';
import { pluginAPI, PluginInfo, adminAPI, AdminAgent } from '../services/api';
import { hasPluginViewAdapter } from './plugin-views/registry';
import * as LucideIcons from 'lucide-react';

interface SidebarProps {
  currentUser: User | null;
  onUpdateUser: (updatedUser: User) => void;
  onLogout: () => void;
  theme: string;
  onToggleTheme: () => void;
  onOpenProfile: () => void;
  onOpenSettings: () => void;
  currentView?: 'chat' | 'ai-chat' | 'admin' | 'plugins' | 'services' | 'mcp' | 'skills' | 'roles' | 'models' | 'logs' | 'market' | 'collab-board' | string;
}

interface AgentShortcutItem {
  agent_id: string;
  label: string;
  avatar: string | null;
}

const AGENT_SHORTCUTS_CACHE_KEY = 'agent_nav_shortcuts_cache_v1';

function loadAgentShortcutsCache(): AgentShortcutItem[] {
  try {
    const raw = localStorage.getItem(AGENT_SHORTCUTS_CACHE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((x: any) => x && typeof x.agent_id === 'string').map((x: any) => ({
      agent_id: x.agent_id,
      label: typeof x.label === 'string' ? x.label : x.agent_id,
      avatar: typeof x.avatar === 'string' ? x.avatar : null,
    }));
  } catch {
    return [];
  }
}

export const Sidebar: React.FC<SidebarProps> = ({ currentUser, theme, onOpenProfile, onOpenSettings, currentView }) => {
  const { t, i18n } = useTranslation();
  const isZh = i18n.language === 'zh';
  const [pluginNavItems, setPluginNavItems] = useState<Array<{
    name: string;
    icon: string;
    label: string;
    view: string;
    iconType?: 'lucide' | 'image' | 'initial';
    iconUrl?: string;
  }>>([]);
  const [agentShortcuts, setAgentShortcuts] = useState<AgentShortcutItem[]>(() => loadAgentShortcutsCache());
  const [checkingAgentId, setCheckingAgentId] = useState<string | null>(null);

  // 首字符图标颜色（与 PluginManagerPage 保持一致）
  const AVATAR_COLORS = [
    'bg-violet-500', 'bg-blue-500', 'bg-emerald-500', 'bg-rose-500',
    'bg-amber-500',  'bg-cyan-500',  'bg-pink-500',   'bg-indigo-500',
  ];
  const avatarColor = (id: string): string => {
    let hash = 0;
    for (let i = 0; i < id.length; i++) hash = (hash * 31 + id.charCodeAt(i)) >>> 0;
    return AVATAR_COLORS[hash % AVATAR_COLORS.length];
  };

  // 从 localStorage 获取用户对插件导航的启用状态
  const getPluginNavEnabled = (pluginName: string): boolean => {
    try {
      const stored = localStorage.getItem(`plugin_nav_enabled_${pluginName}`);
      return stored === 'true';
    } catch {
      return false;
    }
  };

  // 加载启用的插件导航配置
  useEffect(() => {
    const loadPluginNav = async () => {
      try {
        const { plugins } = await pluginAPI.getPlugins();
        const navItems = plugins
          .filter(p => {
            // 所有插件都支持导航，由用户通过 localStorage 决定是否显示
            return getPluginNavEnabled(p.name);
          })
          .flatMap(p => {
            // 优先使用插件自身的 navigation 配置
            const nav = p.contributes?.navigation;
            if (nav) {
              // 显式配置：校验 view key 是否有注册的适配器（必须含 ':' 或在内置列表中）
              // 避免配置了导航但未实现 view 的插件点击后报错
              if (!hasPluginViewAdapter(nav.view)) return [];
              return [{
                name: p.name,
                icon: nav.icon || '',
                label: nav.label || p.display_name || p.name,
                view: nav.view,
                iconType: nav.iconType || ('initial' as const),
                iconUrl: nav.iconUrl,
              }];
            }

            // 自动生成：只对有 contributes.views 的插件生成，并取第一个 view
            // view key 必须是 "pluginName:viewName" 格式，才能被 registry 正确解析
            const views = p.contributes?.views;
            if (!views || views.length === 0) return [];
            const firstView = views[0];
            const viewKey = `${p.name}:${firstView.name}`;
            return [{
              name: p.name,
              icon: '',
              label: p.display_name || p.name,
              view: viewKey,
              iconType: 'initial' as const,
              iconUrl: undefined,
            }];
          });
        setPluginNavItems(navItems);
      } catch (error) {
        console.error('[Sidebar] Failed to load plugin navigation:', error);
      }
    };

    const loadAgentShortcuts = async () => {
      try {
        const { agents } = await adminAPI.getAgents();
        const withCfg = await Promise.all((agents || []).map(async (a: AdminAgent) => {
          try {
            const key = a.dir_name || a.agent_id || a.agent_name;
            const cfgRes = await adminAPI.getConfig(key);
            return { agent: a, cfg: cfgRes?.config || {} };
          } catch {
            return { agent: a, cfg: {} };
          }
        }));

        const shortcuts = withCfg
          .filter(item => !!item.agent?.agent_id)
          .filter(item => !!item.cfg?.ui?.nav_shortcut)
          .map(item => ({
            agent_id: item.agent.agent_id,
            label: item.agent.agent_name || resolveChatName(item.agent.chat_profile) || item.agent.dir_name || item.agent.agent_id,
            avatar: resolveChatAvatar(item.agent.chat_profile),
          }));
        setAgentShortcuts(shortcuts);
        try {
          localStorage.setItem(AGENT_SHORTCUTS_CACHE_KEY, JSON.stringify(shortcuts));
        } catch {}
      } catch (error) {
        console.error('[Sidebar] Failed to load agent shortcuts:', error);
      }
    };

    const loadAll = async () => {
      await Promise.all([loadPluginNav(), loadAgentShortcuts()]);
    };

    loadAll();

    // 监听插件导航配置变化
    const handleNavChange = () => loadAll();
    window.addEventListener('plugin-nav-changed', handleNavChange);
    window.addEventListener('agent-nav-changed', handleNavChange);
    return () => {
      window.removeEventListener('plugin-nav-changed', handleNavChange);
      window.removeEventListener('agent-nav-changed', handleNavChange);
    };
  }, []);


    const navBtn = (view: string | string[]) => {
        const isActive = Array.isArray(view) ? view.includes(currentView || '') : currentView === view;
        return `flex items-center md:justify-center justify-start gap-3 w-full px-4 md:px-1 py-2.5 md:py-1.5 rounded-lg transition-all duration-200 ${
            isActive
                ? 'text-primary bg-primary/10'
                : 'text-textMuted hover:bg-primary/10 hover:text-primary'
        }`;
    };

    const handleOpenAgentChat = async (agentId: string) => {
      if (checkingAgentId) return;
      setCheckingAgentId(agentId);
      try {
        const { agents } = await adminAPI.getAgents();
        const agent = (agents || []).find((a: AdminAgent) => a.agent_id === agentId);
        if (!agent) {
          alert(t('agentManager.agentNotFound') || 'Agent not found');
          return;
        }
        if (!agent.ready) {
          alert(t('agentManager.agentNotReady') || 'Agent is starting, please wait.');
          return;
        }
        window.dispatchEvent(new CustomEvent('openAgentChat', { detail: { agentId } }));
        window.dispatchEvent(new CustomEvent('switchView', { detail: 'ai-chat' }));
      } catch (error) {
        console.error('[Sidebar] Failed to check agent status:', error);
        alert(t('agentManager.loadFailed') || 'Failed to check agent status');
      } finally {
        setCheckingAgentId(null);
      }
    };

    const gradientId = `os-logo-grad-${Math.random().toString(36).substr(2, 9)}`;
    const filterId = `os-node-glow-${Math.random().toString(36).substr(2, 9)}`;

    return (
        <div className="w-full md:w-[52px] bg-bgLight h-full flex flex-col md:items-center items-start py-3 z-30 shadow-lg border-r border-border shrink-0">
            <div className="w-8 h-8 mb-3 flex-shrink-0 transition-transform hover:scale-105 cursor-pointer ml-4 md:ml-0">
                <svg viewBox="0 0 100 100" className="w-full h-full drop-shadow-md">
                    <defs>
                        <linearGradient id={gradientId} x1="0%" y1="0%" x2="100%" y2="100%">
                            <stop offset="0%" stopColor="#4338ca" />
                            <stop offset="100%" stopColor="#7c3aed" />
                        </linearGradient>
                        <filter id={filterId} x="-50%" y="-50%" width="200%" height="200%">
                            <feGaussianBlur stdDeviation="2" result="blur" />
                            <feMerge>
                                <feMergeNode in="blur" />
                                <feMergeNode in="SourceGraphic" />
                            </feMerge>
                        </filter>
                    </defs>
                    <rect width="100" height="100" rx="22" ry="22" fill={`url(#${gradientId})`} />
                    <ellipse cx="50" cy="18" rx="30" ry="15" fill="white" fillOpacity="0.08" />
                    <line x1="30" y1="30" x2="70" y2="30" stroke="white" strokeWidth="2" strokeOpacity="0.35" strokeLinecap="round" />
                    <line x1="30" y1="30" x2="30" y2="70" stroke="white" strokeWidth="2" strokeOpacity="0.35" strokeLinecap="round" />
                    <line x1="70" y1="30" x2="70" y2="70" stroke="white" strokeWidth="2" strokeOpacity="0.35" strokeLinecap="round" />
                    <line x1="30" y1="70" x2="70" y2="70" stroke="white" strokeWidth="2" strokeOpacity="0.35" strokeLinecap="round" />
                    <line x1="30" y1="30" x2="70" y2="70" stroke="white" strokeWidth="1.5" strokeOpacity="0.20" strokeLinecap="round" />
                    <line x1="70" y1="30" x2="30" y2="70" stroke="white" strokeWidth="1.5" strokeOpacity="0.20" strokeLinecap="round" />
                    <circle cx="30" cy="30" r="8" fill="white" filter={`url(#${filterId})`} />
                    <circle cx="70" cy="30" r="8" fill="white" filter={`url(#${filterId})`} />
                    <circle cx="30" cy="70" r="8" fill="white" filter={`url(#${filterId})`} />
                    <circle cx="70" cy="70" r="8" fill="white" filter={`url(#${filterId})`} />
                </svg>
            </div>


            <nav
                className="flex flex-col gap-0.5 w-full px-1 flex-1 min-h-0 overflow-y-auto [&::-webkit-scrollbar]:hidden"
                style={{ scrollbarWidth: 'none' }}
            >

                {/* Agent 快捷窗口（置顶） */}
                {agentShortcuts.length > 0 && (
                  <>
                    {agentShortcuts.map((agent) => (
                      <button
                        key={`agent-shortcut-${agent.agent_id}`}
                        onClick={() => handleOpenAgentChat(agent.agent_id)}
                        disabled={checkingAgentId === agent.agent_id}
                        className={`${navBtn('ai-chat')} ${checkingAgentId === agent.agent_id ? 'opacity-60 cursor-wait' : ''}`}
                        title={agent.label}
                      >
                        <div className="w-7 h-7 rounded-full overflow-hidden border border-border/60 bg-bgPage shrink-0">
                          {agent.avatar ? (
                            <img src={agent.avatar} alt={agent.label} className="w-full h-full object-cover" loading="lazy" />
                          ) : (
                            <div className={`w-full h-full flex items-center justify-center text-white text-xs font-bold ${avatarColor(agent.agent_id)}`}>
                              {agent.label.charAt(0).toUpperCase()}
                            </div>
                          )}
                        </div>
                        <span className="font-medium md:hidden truncate">{agent.label}</span>
                      </button>
                    ))}
                    <div className="h-px bg-border/60 my-1" />
                  </>
                )}

                {/* 聊天 */}
                <button
                    onClick={() => window.dispatchEvent(new CustomEvent('switchView', { detail: 'chat' }))}
                    className={navBtn('chat')}
                >
                    <MessageCircle size={20} className="shrink-0" />
                    <span className="font-medium md:hidden">{t('nav.chats')}</span>
                </button>

                {/* AI 助手 */}
                <button
                    onClick={() => window.dispatchEvent(new CustomEvent('switchView', { detail: 'admin' }))}
                    className={navBtn(['admin', 'ai-chat'])}
                >
                    <LayoutGrid size={20} className="shrink-0" />
                    <span className="font-medium md:hidden">{t('nav.agents')}</span>
                </button>

                {/* 协作看板 */}
                <button
                    onClick={() => window.dispatchEvent(new CustomEvent('switchView', { detail: 'collab-board' }))}
                    className={navBtn('collab-board')}
                >
                    <KanbanSquare size={20} className="shrink-0" />
                    <span className="font-medium md:hidden">{t('nav.collabBoard')}</span>
                </button>

                {/* 插件 */}
                <button
                    onClick={() => window.dispatchEvent(new CustomEvent('switchView', { detail: 'plugins' }))}
                    className={navBtn('plugins')}
                >
                    <Puzzle size={20} className="shrink-0" />
                    <span className="font-medium md:hidden">{t('nav.plugins')}</span>
                </button>

                {/* 服务管理 */}
                <button
                    onClick={() => window.dispatchEvent(new CustomEvent('switchView', { detail: 'services' }))}
                    className={navBtn('services')}
                >
                    <Radio size={20} className="shrink-0" />
                    <span className="font-medium md:hidden">{t('nav.services')}</span>
                </button>

                {/* MCP */}
                <button
                    onClick={() => window.dispatchEvent(new CustomEvent('switchView', { detail: 'mcp' }))}
                    className={navBtn('mcp')}
                >
                    <Server size={20} className="shrink-0" />
                    <span className="font-medium md:hidden">{t('nav.mcp')}</span>
                </button>

                {/* 技能 */}
                <button
                    onClick={() => window.dispatchEvent(new CustomEvent('switchView', { detail: 'skills' }))}
                    className={navBtn('skills')}
                >
                    <BookOpen size={20} className="shrink-0" />
                    <span className="font-medium md:hidden">{t('nav.skills')}</span>
                </button>

                {/* 角色 & 协作 */}
                <button
                    onClick={() => window.dispatchEvent(new CustomEvent('switchView', { detail: 'roles' }))}
                    className={navBtn('roles')}
                >
                    <UserCircle size={20} className="shrink-0" />
                    <span className="font-medium md:hidden">{t('nav.roles')}</span>
                </button>

                {/* 模型 */}
                <button
                    onClick={() => window.dispatchEvent(new CustomEvent('switchView', { detail: 'models' }))}
                    className={navBtn('models')}
                >
                    <Cpu size={20} className="shrink-0" />
                    <span className="font-medium md:hidden">{t('nav.models')}</span>
                </button>

                {/* 日志 */}
                <button
                    onClick={() => window.dispatchEvent(new CustomEvent('switchView', { detail: 'logs' }))}
                    className={navBtn('logs')}
                >
                    <ScrollText size={20} className="shrink-0" />
                    <span className="font-medium md:hidden">{t('nav.logs')}</span>
                </button>

                {/* 商店 (暂隐藏) */}
                {/*
                <button
                    onClick={() => window.dispatchEvent(new CustomEvent('switchView', { detail: 'market' }))}
                    className={navBtn('market')}
                >
                    <Store size={20} className="shrink-0" />
                    <span className="font-medium md:hidden">{t('nav.market')}</span>
                </button>
                */}

                {/* 插件动态生成的导航项 */}
                {pluginNavItems.map((item) => {
                    const isImage = item.iconType === 'image' && !!item.iconUrl;
                    const LucideIcon = (!isImage && item.icon)
                        ? (LucideIcons as any)[item.icon] as React.FC<{ size?: number; className?: string }> | undefined
                        : undefined;
                    const isInitial = !isImage && !LucideIcon;
                    return (
                        <button
                            key={item.name}
                            onClick={() => window.dispatchEvent(new CustomEvent('switchView', { detail: item.view }))}
                            className={`${navBtn(item.view)} ${isImage ? '!px-1.5 !py-1.5' : ''}`}
                        >
                            {isImage ? (
                                <img
                                    src={item.iconUrl}
                                    alt={item.label}
                                    className="w-8 h-8 rounded-md object-cover shrink-0"
                                    loading="lazy"
                                />
                            ) : LucideIcon ? (
                                <LucideIcon size={20} className="shrink-0" />
                            ) : (
                                // 首字符自动图标（插件未配置图标时系统自动生成）
                                <div className={`w-7 h-7 rounded-lg flex items-center justify-center text-white text-xs font-bold shrink-0 ${avatarColor(item.name)}`}>
                                    {item.label.charAt(0).toUpperCase()}
                                </div>
                            )}
                            <span className="font-medium md:hidden">{item.label}</span>
                        </button>
                    );
                })}
            </nav>

            {/* Bottom Actions */}
            <div className="flex flex-col gap-1 w-full px-1 mt-2 pt-2 border-t border-border shrink-0">
                {/* Language Toggle */}
                <button
                    onClick={() => setLanguage(isZh ? 'en' : 'zh')}
                    className="flex items-center md:justify-center justify-start w-full py-1.5 px-1 rounded-lg text-textMuted hover:bg-primary/10 hover:text-primary transition-all border border-border/30"
                >
                    <div className="w-6 h-6 flex items-center justify-center rounded-md bg-primary/10 text-[10px] font-bold shrink-0">
                        {isZh ? 'EN' : '中'}
                    </div>
                    <span className="font-medium md:hidden ml-3">{t('lang.switchLang')}</span>
                </button>

                <button
                    onClick={onOpenProfile}
                    className="flex items-center md:justify-center justify-start w-full p-1.5 rounded-lg text-textMuted hover:bg-primary/10 hover:text-primary transition-all"
                >
                    <div className="w-7 h-7 rounded-full overflow-hidden border border-border shrink-0">
                        <img
                            src={getAvatarUrl(currentUser?.avatar, currentUser?.id, currentUser?.name)}
                            alt=""
                            className="w-full h-full object-cover bg-border"
                            loading="lazy"
                            onError={(e) => {
                              const img = e.currentTarget;
                              if (img.dataset.fallbackApplied) return;
                              img.dataset.fallbackApplied = '1';
                              img.src = getLocalAvatarFallback(
                                currentUser?.id || 'user',
                                currentUser?.name,
                              );
                            }}
                        />
                    </div>
                    <span className="font-medium md:hidden ml-3 truncate">{currentUser?.name || 'User'}</span>
                </button>

                <button
                    onClick={onOpenSettings}
                    className="flex items-center md:justify-center justify-start gap-2 w-full px-4 md:px-1 py-1.5 rounded-lg text-textMuted hover:bg-primary/10 hover:text-primary transition-all"
                >

                    <Settings size={18} className="shrink-0" />
                    <span className="font-medium md:hidden">{t('nav.settings')}</span>
                </button>

            </div>
        </div>
    );
};
