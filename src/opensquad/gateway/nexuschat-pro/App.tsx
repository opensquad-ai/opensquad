import React, { useState, useEffect, useRef, useCallback, useMemo, Suspense } from 'react';

import { MessageSquare, Sun, Moon, X, Camera, Save, LogOut } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { ChatList } from './components/ChatList';
import { ChatWindow } from './components/ChatWindow';
import { RightPanel } from './components/RightPanel';
import { AuthScreen } from './components/AuthScreen';
import { LanguageSelectScreen } from './components/LanguageSelectScreen';
import { ElectronShell } from './components/ElectronShell';
import { DesktopUpdateOverlay } from './components/DesktopUpdateOverlay';
import { SoftOverlay } from './components/SoftOverlay';
import { ChatState, Message, MessageType, Attachment, Group, User } from './types';
import { authAPI, userAPI, groupAPI, messageAPI, uploadAPI, getAuthToken, directMessageAPI } from './services/api';
import { preloadSystemConfig } from './services/configCache';
import { wsService } from './services/websocket';
import { AvatarImg } from './components/AvatarImg';
import { setLanguage } from './i18n';
import { isSettingsAppView } from './utils/appNavItems';

// First-launch wizard — driven by the BACKEND, not localStorage.
//
//   - `registrationStatus` is the source of truth: when the backend reports
//     `registration_required=true` it means no web user has ever been created
//     on this deployment, so the first-run wizard must run.
//   - `langPicked` is per-session state in this component: it tracks
//     whether the user has clicked a language button THIS session. Once
//     they pick, we transition to the auth screen. We deliberately do NOT
//     consult localStorage for this decision: a key from a previous
//     deployment on the same browser must NOT skip the wizard on a fresh
//     deployment.
//   - The screen to render is derived from
//     `currentUser + registrationStatus + langPicked`.

// Mirrors the backend's /auth/registration-status response. `unknown` while
// the request is in flight; `error` if the request failed (we then default
// to "assume a user exists" so the user at least sees the login form).
type RegistrationStatus = 'unknown' | 'required' | 'closed' | 'error';

// 路由级懒加载页面组件
const AIChatPage = React.lazy(() => import('./components/AIChatPage').then(m => ({ default: m.AIChatPage })));
const AgentManagerPage = React.lazy(() => import('./components/AgentManagerPage').then(m => ({ default: m.AgentManagerPage })));
const CollabBoardPage = React.lazy(() => import('./components/CollabBoardPage').then(m => ({ default: m.CollabBoardPage })));
const SystemConfigPage = React.lazy(() => import('./components/SystemConfigPage').then(m => ({ default: m.SystemConfigPage })));

const App: React.FC = () => {
  const { t, i18n } = useTranslation();
  const [currentView, setCurrentView] = useState<string>(() => {
    const saved = localStorage.getItem('nexus_view') as any;
    // 兼容旧值 'agent' → 重定向到 'admin'
    if (saved === 'agent') return 'admin';
    // 应用类页面改为弹窗，刷新时不要把主视图卡在全屏管理页
    if (saved && isSettingsAppView(saved)) return 'chat';
    return saved || 'chat';
  });
  const [selectedAgentId, setSelectedAgentId] = useState<string>(() => {
    return localStorage.getItem('nexus_selected_agent') || '';
  });

  // LRU 多聊天缓存：最多同时保留 3 个 agent 聊天页面（keep-alive）
  const [openAgentChats, setOpenAgentChats] = useState<string[]>(() => {
    const saved = localStorage.getItem('nexus_selected_agent');
    return saved ? [saved] : [];
  });

  const openAgentChat = useCallback((agentId: string) => {
    setOpenAgentChats(prev => {
      const deduped = prev.filter(id => id !== agentId);
      const updated = [...deduped, agentId];
      // 超过 3 个时淘汰最旧的（LRU），被淘汰的组件卸载后会自动 releaseAiWsService
      return updated.length > 3 ? updated.slice(-3) : updated;
    });
    setSelectedAgentId(agentId);
    setCurrentView('ai-chat');
  }, []);

  // 懒挂载缓存：首次访问某 view 后，永远保持其组件在 DOM 中（只隐藏不卸载）
    // 初始化时只挂载当前 view，避免 F5 刷新时 sessionStorage 旧缓存导致
    // 所有预加载面板同时挂载、Suspense fallback 全部闪现。
    // sessionStorage 写回保留，供 SPA 导航内保活使用。
    const [mountedViews, setMountedViews] = useState<Set<string>>(() => {
      return new Set([currentView]);
    });

  // mountedViews 变化时同步写入 sessionStorage
  useEffect(() => {
    sessionStorage.setItem('nexus_mounted_views', JSON.stringify([...mountedViews]));
  }, [mountedViews]);

  useEffect(() => {
    localStorage.setItem('nexus_view', currentView);
    // 首次切换到该 view 时记录，后续保持挂载
    setMountedViews(prev => {
      if (prev.has(currentView)) return prev;
      return new Set([...prev, currentView]);
    });
  }, [currentView]);

  useEffect(() => {
    if (selectedAgentId) {
      localStorage.setItem('nexus_selected_agent', selectedAgentId);
    }
  }, [selectedAgentId]);

  useEffect(() => {
    const handleSwitchView = (e: any) => {
      const view = e.detail;
      if (typeof view !== 'string' || !view.trim()) return;
      if (view === 'collab-board') {
        setIsCollabBoardOpen(true);
        return;
      }
      if (isSettingsAppView(view)) {
        setSettingsInitialAppView(view);
        setIsSettingsOpen(true);
        return;
      }
      setCurrentView(view);
    };
    const handleOpenAgentChat = (e: any) => {
      const agentId = e?.detail?.agentId;
      if (typeof agentId === 'string' && agentId.trim()) {
        openAgentChat(agentId);
      }
    };
    window.addEventListener('switchView', handleSwitchView);
    window.addEventListener('openAgentChat', handleOpenAgentChat);
    const handleOpenMobileNav = () => setIsSettingsOpen(true);
    window.addEventListener('openMobileNav', handleOpenMobileNav);
    return () => {
      window.removeEventListener('switchView', handleSwitchView);
      window.removeEventListener('openAgentChat', handleOpenAgentChat);
      window.removeEventListener('openMobileNav', handleOpenMobileNav);
    };
  }, [openAgentChat]);

    const [currentUser, setCurrentUser] = useState<User | null>(null);
  // 群聊首屏加载完成信号：loadMessages 成功后设为 true，用于门控其他面板的预加载
  const [chatReady, setChatReady] = useState(false);
  const chatReadySetRef = useRef(false); // 防止 setChatReady 重复触发
  const [settingsInitialTab, setSettingsInitialTab] = useState<'theme' | 'workspace' | 'ports' | 'advanced' | 'about' | undefined>();
  const [settingsInitialAppView, setSettingsInitialAppView] = useState<string | null>(null);

  // ─── 智能预加载系统 ───────────────────────────────────────────────
  // 规则：
  //   1. 用户点击某页面 → 立即挂载（已由 currentView useEffect 保证）
  //   2. 后台按优先级分批并发预加载，每批 BATCH_SIZE 个同时挂载
  //   3. 每批间隔 BATCH_DELAY ms，避免一次性打爆后端
  //   4. 已挂载的页面自动跳过
  //
  // 加载顺序：仅预挂载仍全屏的 Agent 管理（其它应用页改为弹窗按需加载）
  const PRELOAD_ORDER = [
    'admin',   // agent 管理面板
  ] as const;
  const BATCH_SIZE  = 3;   // 每批并发挂载数量
  const BATCH_DELAY = 2000; // 批次间隔 ms（拉长，避免和群聊争 DB 连接）

  // model-presets 刷新：登录后 500ms 就触发，与群聊加载无关，不依赖 chatReady
  useEffect(() => {
    if (!currentUser) return;
    preloadSystemConfig();
    const t = setTimeout(() => {
      fetch('/api/ai-web/model-presets/refresh', { method: 'POST' }).catch(() => {});
    }, 500);
    return () => clearTimeout(t);
  }, [currentUser?.id]);

  // 面板预加载：必须等群聊首屏加载完 (chatReady) 再启动，群聊优先
  useEffect(() => {
    if (!currentUser || !chatReady) return;

    let cancelled = false;
    let batchStart = 0;

    const loadNextBatch = () => {
      if (cancelled) return;
      if (batchStart >= PRELOAD_ORDER.length) return;

      // 取出本批次，跳过用户已主动打开的页面（mountedViews 里已有的）
      const batch = PRELOAD_ORDER.slice(batchStart, batchStart + BATCH_SIZE);
      batchStart += BATCH_SIZE;

      setMountedViews(prev => {
        const toAdd = batch.filter(v => !prev.has(v));
        if (toAdd.length === 0) return prev;
        return new Set([...prev, ...toAdd]);
      });

      if (batchStart < PRELOAD_ORDER.length) {
        setTimeout(loadNextBatch, BATCH_DELAY);
      }
    };

    // 群聊加载完后再等 1s，让聊天界面完全稳定
    const timer = setTimeout(loadNextBatch, 1000);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [currentUser?.id, chatReady]);
  // ─────────────────────────────────────────────────────────────────

  // Profile Modal State
  const [isProfileOpen, setIsProfileOpen] = useState(false);
  // Settings Modal State
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);
  const [isCollabBoardOpen, setIsCollabBoardOpen] = useState(false);

  useEffect(() => {
    const openTheme = () => {
      setSettingsInitialTab('theme');
      setIsSettingsOpen(true);
    };
    window.addEventListener('openThemeSettings', openTheme);
    return () => window.removeEventListener('openThemeSettings', openTheme);
  }, []);

  const [editName, setEditName] = useState('');
  const [editAvatar, setEditAvatar] = useState('');
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [shouldJumpToMention, setShouldJumpToMention] = useState(false);

  // First-launch wizard state — backend-driven.
  // `registrationStatus` is fetched on mount; until it resolves we treat
  // the wizard as "unknown" and keep showing the loading shell so the user
  // never sees a flash of the wrong screen.
  const [registrationStatus, setRegistrationStatus] = useState<RegistrationStatus>('unknown');
  // Per-session flag: has the user clicked a language in LanguageSelectScreen
  // during this app instance? Resets on remount (page reload, deployment
  // switch). The wizard does NOT consult localStorage for this — a stale
  // `opensquad_lang` from a previous deployment must not skip the wizard.
  const [langPicked, setLangPicked] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  // Tracks whether the current group's messages are being loaded for the first
  // time. Used by ChatWindow to show a skeleton instead of an empty panel on
  // uncached group switches. Cached groups (e.g. after a hover-prefetch)
  // never flip this to true, so the user sees zero loading UI.
  const [isMessagesLoading, setIsMessagesLoading] = useState(false);
  const [users, setUsers] = useState<Record<string, User>>({});

  const [state, setState] = useState<ChatState>({
    activeGroupId: null,
    groups: [],
    messages: {},
    users: {},
    currentUser: { id: '', name: '', avatar: '', status: 'offline' },
    isRightPanelOpen: false,
    searchQuery: { text: '', userId: null, dateFrom: null, dateTo: null }
  });

  const loadGroups = useCallback(async (showAlert = false) => {
    try {
      const groupsData = await groupAPI.getGroups();
      const formattedGroups: Group[] = groupsData.map(g => ({
        id: g.id,
        name: g.name,
        avatar: g.avatar || '',
        description: g.description || '',
        members: [],
        unreadCount: g.unread_count,
        hasUnreadMention: g.has_unread_mention,
        isPrivate: g.is_private,
        createdAt: new Date(g.created_at || Date.now()).getTime(),
        notificationSoundEnabled: g.notification_sound_enabled,
        pinnedMessageId: g.pinned_message_id,
      }));

      // Extract last messages from group list
      const initialMessages: Record<string, Message[]> = {};
      groupsData.forEach(g => {
        if (g.last_message) {
          initialMessages[g.id] = [{
            id: g.last_message.id,
            senderId: g.last_message.sender_id,
            content: g.last_message.content,
            timestamp: new Date(g.last_message.timestamp).getTime(),
            type: MessageType.TEXT, // Default to text for preview
            attachments: [],
            isPinned: false,
            isEdited: false
          }];
        }
      });

      setState(prev => ({
        ...prev,
        groups: formattedGroups,
        messages: { ...initialMessages, ...prev.messages } // Keep existing messages if any
      }));

    } catch (error) {
      console.error('Failed to load groups:', error);
      if (showAlert) alert(t('chat.groupLoadFailed'));
    }
  }, [t]);


  const loadGroupDetails = async (groupId: string) => {
    try {
      const group = await groupAPI.getGroup(groupId);
      const memberIds = group.members.map(m => m.id);
      const newUsers: Record<string, User> = {};
      group.members.forEach(m => {
        newUsers[m.id] = {
          id: m.id,
          name: m.name,
          avatar: m.avatar || '',
          status: m.status as 'online' | 'offline' | 'busy',
          is_agent: m.is_agent ?? false
        };
      });
      setUsers(prev => ({ ...prev, ...newUsers }));
      setState(prev => ({
        ...prev,
        users: { ...prev.users, ...newUsers },
        groups: prev.groups.map(g => g.id === groupId ? { ...g, members: memberIds } : g)
      }));
    } catch (error: any) {
      const status = error?.status;
      if (status === 404 || status === 403) {
        // Group deleted or user removed — clean up stale reference
        console.warn(`Group ${groupId} no longer accessible (${status}), removing from list`);
        localStorage.removeItem('nexus_active_group');
        setState(prev => ({
          ...prev,
          groups: prev.groups.filter(g => g.id !== groupId),
          activeGroupId: prev.activeGroupId === groupId ? null : prev.activeGroupId,
        }));
      } else {
        console.error('Failed to load group details:', error);
      }
    }
  };

  // Tracks in-flight loadMessages(groupId) promises to dedupe rapid clicks/
  // prefetches for the same group. Without this, hovering a group while a
  // fetch is already in flight would start a second concurrent request.
  const inFlightMessagesRef = useRef<Map<string, Promise<void>>>(new Map());
  // Tracks groups already prefetched (via hover). Ensures each group is
  // fetched at most once per session for prefetch purposes — the click
  // handler still calls loadMessages which dedupes via inFlightMessagesRef.
  const prefetchedRef = useRef<Set<string>>(new Set());

  const loadMessages = useCallback(async (groupId: string) => {
    // In-flight dedup: share the promise if a fetch for this group is already
    // running. This is what makes hover-prefetch + click safe — both paths
    // converge on the same promise.
    const inflight = inFlightMessagesRef.current.get(groupId);
    if (inflight) return inflight;

    // Only flip the loading flag if the cache has no real messages yet.
    // Cached groups (e.g. from a previous visit or from a hover-prefetch that
    // already completed) render instantly with no skeleton flash.
    const cached = state.messages[groupId];
    const needsLoading = !cached || cached.length <= 1;
    if (needsLoading) setIsMessagesLoading(true);

    const promise = (async () => {
      try {
        const messages = await messageAPI.getMessages(groupId, undefined, 20);
        const formattedMessages: Message[] = messages.map(m => ({
          id: m.id,
          senderId: m.sender_id,
          content: m.content,
          timestamp: new Date(m.timestamp).getTime(),
          type: m.type as MessageType,
          attachments: m.attachments?.map(a => ({
            id: a.id,
            name: a.name,
            size: a.size,
            url: a.url,
            type: a.type as 'image' | 'video' | 'file' | 'folder' | 'voice',
            duration: a.duration
          })),
          replyToId: m.reply_to_id,
          isPinned: m.is_pinned,
          isEdited: m.is_edited,
          isDeleted: m.is_deleted,
          canUndo: m.can_undo,
          deletedAt: m.deleted_at ? new Date(m.deleted_at).getTime() : undefined,
          mentions: m.mentions
        }));
        setState(prev => {
          const existing = prev.messages[groupId] || [];
          const merged = new Map<string, Message>();
          // HTTP 数据先入 map
          for (const m of formattedMessages) merged.set(m.id, m);
          // 已有 state 中不在 HTTP 结果里的消息（WS 实时推送的新消息）保留
          for (const m of existing) if (!merged.has(m.id)) merged.set(m.id, m);
          const combined = Array.from(merged.values()).sort((a, b) => a.timestamp - b.timestamp);
          return { ...prev, messages: { ...prev.messages, [groupId]: combined } };
        });
        // 群聊首屏加载完成，放行导航面板预加载（只触发一次）
        if (!chatReadySetRef.current) {
          chatReadySetRef.current = true;
          setChatReady(true);
        }
      } catch (error: any) {
        if (error?.status === 404 || error?.status === 403) {
          // Group no longer accessible — silently skip loading messages
          console.warn(`Cannot load messages for group ${groupId}: ${error?.status}`);
        } else {
          console.error('Failed to load messages:', error);
        }
      }
    })();

    inFlightMessagesRef.current.set(groupId, promise);
    try {
      await promise;
    } finally {
      inFlightMessagesRef.current.delete(groupId);
      if (needsLoading) setIsMessagesLoading(false);
    }
  }, [state.messages]);

  useEffect(() => {
    const init = async () => {
      // 1) Try to rehydrate a previous session from a stored token.
      const token = getAuthToken();
      if (token) {
        try {
          const [user] = await Promise.all([
            authAPI.getMe(),
            loadGroups()
          ]);
          setCurrentUser(user);
          setState(prev => ({ ...prev, currentUser: user }));
          wsService.connect();
          // Restore previously selected group
          const savedGroupId = localStorage.getItem('nexus_active_group');
          if (savedGroupId) {
            // Delay slightly to let groups populate in state
            setTimeout(() => handleSelectGroup(savedGroupId), 100);
          }
        } catch (error) {
          console.error('Failed to restore session:', error);
          authAPI.logout();
        }
      }

      // 2) Always query the backend for the first-launch wizard decision.
      //    ``registration_required`` from the server is the source of truth
      //    for "is this a fresh deployment?" — not localStorage.
      try {
        const status = await authAPI.getRegistrationStatus();
        if (!status.registration_required) {
          setRegistrationStatus('closed');
        } else {
          setRegistrationStatus('required');
        }
      } catch (err: any) {
        // Non-fatal: fall back to "assume a user exists" so the user lands
        // on the login screen rather than a stuck loading state. A 403
        // on /auth/register will surface the real problem.
        console.warn('[App] registration-status failed:', err);
        setRegistrationStatus('error');
      }

      setIsLoading(false);
    };
    init();
  }, [loadGroups]);

  useEffect(() => {
    // 注册 WebSocket 消息处理器
    const unsubscribeNewMessage = wsService.on('new_message', (message) => {
      console.log('[WebSocket] Received new_message:', message);
      const msg = message.data;
      const formattedMsg: Message = {
        id: msg.id,
        senderId: msg.sender_id,
        content: msg.content,
        timestamp: typeof msg.timestamp === 'number' ? msg.timestamp : new Date(msg.timestamp).getTime(),
        type: msg.type as MessageType,
        attachments: msg.attachments?.map((a: any) => ({
          id: a.id,
          name: a.name,
          size: a.size,
          url: a.url,
          type: a.type as 'image' | 'video' | 'file' | 'folder' | 'voice',
          duration: a.duration
        })),
        replyToId: msg.reply_to_id,
        isPinned: msg.is_pinned,
        isEdited: msg.is_edited,
        isDeleted: msg.is_deleted,
        canUndo: msg.can_undo,
        deletedAt: msg.deleted_at ? new Date(msg.deleted_at).getTime() : undefined,
        mentions: msg.mentions
      };

      setState(prev => {
        const groupMessages = prev.messages[msg.group_id] || [];
        // 避免重复添加
        if (groupMessages.some(m => m.id === formattedMsg.id)) {
          return prev;
        }
        console.log('[WebSocket] Adding message to state:', formattedMsg.id);
        return {
          ...prev,
          messages: {
            ...prev.messages,
            [msg.group_id]: [...groupMessages, formattedMsg]
          }
        };
      });
    });

    const unsubscribeUpdateMessage = wsService.on('message_updated', (message) => {
      console.log('[WebSocket] Received message_updated:', message);
      const msg = message.data;
      setState(prev => {
        const groupMessages = prev.messages[msg.group_id] || [];
        return {
          ...prev,
          messages: {
            ...prev.messages,
            [msg.group_id]: groupMessages.map(m => m.id === msg.id ? {
              ...m,
              content: msg.content,
              isEdited: msg.is_edited,
              isPinned: msg.is_pinned,
              isDeleted: msg.is_deleted,
              attachments: msg.attachments
            } : m)
          }
        };
      });
    });

    const unsubscribeRecallMessage = wsService.on('message_recalled', (message) => {
      console.log('[WebSocket] Received message_recalled:', message);
      const { message_id, deleted_at, can_undo } = message.data;
      setState(prev => {
        const newMessages = { ...prev.messages };
        for (const gid in newMessages) {
          newMessages[gid] = newMessages[gid].map(m =>
            m.id === message_id ? { ...m, isDeleted: true, deletedAt: deleted_at, canUndo: can_undo } : m
          );
        }
        return { ...prev, messages: newMessages };
      });
    });

    // Auth error (4001 invalid token / 4002 user not found) — force logout
    const unsubscribeAuthError = wsService.onAuthError(() => {
      console.warn('[App] wsService auth error — logging out');
      authAPI.logout();
      wsService.disconnect();
      setCurrentUser(null);
    });

    // Member join/leave — refresh group details so member list + online status update in real time
    const unsubscribeMemberJoin = wsService.on('member_join', async (message) => {
      const groupId = message?.data?.group_id;
      if (groupId) {
        console.log('[App] Member joined group', groupId, message.data);
        try {
          const groupDetail = await groupAPI.getGroup(groupId);
          setState(prev => ({
            ...prev,
            groups: prev.groups.map(g => g.id === groupId ? {
              ...g,
              members: groupDetail.members?.map((m: any) => m.id) || [],
            } : g),
          }));
        } catch (e) {
          console.warn('[App] Failed to refresh group after member_join:', e);
        }
      }
    });
    const unsubscribeMemberLeave = wsService.on('member_leave', async (message) => {
      const groupId = message?.data?.group_id;
      if (groupId) {
        console.log('[App] Member left group', groupId, message.data);
        try {
          const groupDetail = await groupAPI.getGroup(groupId);
          setState(prev => ({
            ...prev,
            groups: prev.groups.map(g => g.id === groupId ? {
              ...g,
              members: groupDetail.members?.map((m: any) => m.id) || [],
            } : g),
          }));
        } catch (e) {
          console.warn('[App] Failed to refresh group after member_leave:', e);
        }
      }
    });

    // Presence/user_online/user_offline — update user online status in real time
    const unsubscribePresence = wsService.on('presence', (message) => {
      const userData = message?.data;
      const userId = userData?.user_id || userData?.id;
      if (userId && userData?.status) {
        setState(prev => ({
          ...prev,
          users: { ...prev.users, [userId]: { ...(prev.users[userId] || {}), id: userId, status: userData.status } },
        }));
      }
    });
    const unsubscribeUserOnline = wsService.on('user_online', (message) => {
      const userData = message?.data;
      const userId = userData?.user_id || userData?.id;
      if (userId) {
        setState(prev => ({
          ...prev,
          users: { ...prev.users, [userId]: { ...(prev.users[userId] || { id: userId, name: userId }), status: 'online' } },
        }));
      }
    });
    const unsubscribeUserOffline = wsService.on('user_offline', (message) => {
      const userData = message?.data;
      const userId = userData?.user_id || userData?.id;
      if (userId) {
        setState(prev => ({
          ...prev,
          users: { ...prev.users, [userId]: { ...(prev.users[userId] || { id: userId, name: userId }), status: 'offline' } },
        }));
      }
    });

    return () => {
      unsubscribeNewMessage();
      unsubscribeUpdateMessage();
      unsubscribeRecallMessage();
      unsubscribeAuthError();
      unsubscribeMemberJoin();
      unsubscribeMemberLeave();
      unsubscribePresence();
      unsubscribeUserOnline();
      unsubscribeUserOffline();
    };
  }, []);

  const handleLogin = async (email: string, password: string) => {
    let lastError: any;
    for (let attempt = 0; attempt < 10; attempt++) {
      try {
        const response = await authAPI.login(email, password, i18n.language);
        setCurrentUser(response.user);
        setState(prev => ({
          ...prev,
          currentUser: response.user,
          users: { ...prev.users, [response.user.id]: response.user },
        }));
        await loadGroups();
        wsService.connect();
        return;
      } catch (error: any) {
        lastError = error;
        const status = error?.status || error?.response?.status || 0;
        // Retry on 502/503/0(connection refused) — backend still starting
        if (status === 502 || status === 503 || status === 0) {
          await new Promise(r => setTimeout(r, 300));
          continue;
        }
        throw error;
      }
    }
    throw lastError;
  };

  const handleRegister = async (name: string, email: string, password: string) => {
    let lastError: any;
    for (let attempt = 0; attempt < 10; attempt++) {
      try {
        // Carry the current i18n language so the backend can localize the
        // default collaboration group + welcome message created on first
        // registration. The wizard already set this; pass it explicitly so
        // the bootstrap doesn't fall back to a neutral default.
        const response = await authAPI.register(name, email, password, i18n.language);
        setCurrentUser(response.user);
        setState(prev => ({
          ...prev,
          currentUser: response.user,
          users: { ...prev.users, [response.user.id]: response.user },
        }));
        await loadGroups();
        wsService.connect();
        return;
      } catch (error: any) {
        lastError = error;
        const status = error?.status || error?.response?.status || 0;
        // Retry on 502/503/0(connection refused) — backend still starting
        if (status === 502 || status === 503 || status === 0) {
          await new Promise(r => setTimeout(r, 300));
          continue;
        }
        throw error;
      }
    }
    throw lastError;
  };

  const handleLogout = () => {
    authAPI.logout();
    wsService.disconnect();
    setCurrentUser(null);
    // No need to reset the wizard state — the render is derived from
    // (currentUser, registrationStatus, langPicked). On the next render
    // the AuthScreen will appear in the appropriate mode (login vs register)
    // based on the already-fetched registrationStatus.
    // Re-fetch in case the DB changed (different deployment, etc).
    setRegistrationStatus('unknown');
    (async () => {
      try {
        const status = await authAPI.getRegistrationStatus();
        setRegistrationStatus(status.registration_required ? 'required' : 'closed');
      } catch {
        setRegistrationStatus('error');
      }
    })();
  };

  // First-launch wizard: user picked a language on LanguageSelectScreen.
  // Persist the choice (drives i18n) and flip the per-session flag so the
  // next render swaps the picker for the registration form.
  const handleLanguagePicked = useCallback((lang: 'zh' | 'en') => {
    setLanguage(lang);
    setLangPicked(true);
  }, []);

  const handleUpdateUser = async (updatedUser: User) => {
    try {
      const user = await userAPI.updateUser({
        name: updatedUser.name,
        avatar: updatedUser.avatar,
        status: updatedUser.status
      });
      setCurrentUser(user);
      setState(prev => ({
        ...prev,
        currentUser: user,
        users: { ...prev.users, [user.id]: user }
      }));
    } catch (error) {
      console.error('Failed to update user:', error);
    }
  };

  const handleSelectGroup = async (id: string, jumpToMention = false) => {
    setShouldJumpToMention(jumpToMention);
    setState(prev => ({
      ...prev,
      activeGroupId: id,
      groups: prev.groups.map(g => g.id === id ? { ...g, unreadCount: 0 } : g)
    }));
    localStorage.setItem('nexus_active_group', id);
    wsService.subscribe(id);      // 先订阅，避免加载期间丢失新消息
    loadGroupDetails(id);          // 懒加载成员信息，不 await（不阻塞消息显示）
    await loadMessages(id);
  };

  // Hover-prefetch a group's messages in the background. By the time the user
  // actually clicks, the messages are already in state and the click renders
  // instantly with no skeleton flash. Prefetched set ensures we only fetch
  // each group once per session for prefetch purposes.
  const handlePrefetchGroup = useCallback((id: string) => {
    if (prefetchedRef.current.has(id)) return;
    prefetchedRef.current.add(id);
    loadMessages(id);
  }, [loadMessages]);

  const handleJoinGroup = async (groupId: string) => {
    try {
      await groupAPI.joinGroup(groupId);
      await loadGroups();
      handleSelectGroup(groupId);
    } catch (error) {
      console.error('Failed to join group:', error);
        alert(t('chat.joinFailed'));
    }
  };

  const handleSendMessage = async (content: string, type: MessageType, attachments?: Attachment[], replyToId?: string) => {
    if (!state.activeGroupId || !currentUser) return;
    const groupId = state.activeGroupId;
    try {
      const response = await messageAPI.sendMessage(groupId, content, type, attachments, replyToId);
      // HTTP 响应已包含持久化消息，立即写入 state；WS 广播到达时 dedup 会跳过
      const newMsg: Message = {
        id: response.id,
        senderId: response.sender_id,
        content: response.content,
        timestamp: typeof response.timestamp === 'number'
          ? response.timestamp
          : new Date(response.timestamp).getTime(),
        type: response.type as MessageType,
        attachments: response.attachments?.map(a => ({
          id: a.id,
          name: a.name,
          size: a.size,
          url: a.url,
          type: a.type as 'image' | 'video' | 'file' | 'folder' | 'voice',
          duration: a.duration
        })),
        replyToId: response.reply_to_id,
        isPinned: response.is_pinned,
        isEdited: response.is_edited,
        isDeleted: response.is_deleted,
        canUndo: response.can_undo,
        deletedAt: response.deleted_at ? new Date(response.deleted_at).getTime() : undefined,
        mentions: response.mentions,
      };
      setState(prev => {
        const groupMessages = prev.messages[groupId] || [];
        if (groupMessages.some(m => m.id === newMsg.id)) return prev;
        return {
          ...prev,
          messages: { ...prev.messages, [groupId]: [...groupMessages, newMsg] }
        };
      });
    } catch (error: any) {
      console.error('Failed to send message:', error);
      // Show user-friendly error message based on HTTP status
      const status = error?.status;
      let userMessage = error?.message || 'Unknown error';
      if (status === 403) {
        userMessage = 'You are not a member of this group. Please join the group first before sending messages.';
      } else if (status === 401) {
        userMessage = 'Session expired. Please log in again.';
      } else if (status === 429) {
        userMessage = 'Too many requests. Please wait a moment and try again.';
      }
      setState(prev => ({
        ...prev,
        error: userMessage,
      }));
      // Auto-dismiss error after 5 seconds
      setTimeout(() => setState(prev => {
        if (prev.error === userMessage) return { ...prev, error: undefined };
        return prev;
      }), 5000);
    }
  };

  const handleOpenProfile = () => {
    if (currentUser) {
        setEditName(currentUser.name);
        setEditAvatar(currentUser.avatar);
        setSelectedFile(null);
        setIsProfileOpen(true);
    }
  };

  const handleSaveProfile = async () => {
    if (!currentUser) return;
    try {
        let newAvatarUrl = editAvatar;
        if (selectedFile) {
            const result = await uploadAPI.uploadFile(selectedFile);
            newAvatarUrl = result.url;
        }
        await handleUpdateUser({ ...currentUser, name: editName, avatar: newAvatarUrl });
        setIsProfileOpen(false);
    } catch (error) {
        console.error('Failed to save profile:', error);
        alert(t('profile.saveFailed'));
    }
  };

  const lastMessages = useMemo(() => {
    const map: Record<string, { content: string, timestamp: number }> = {};
    state.groups.forEach(g => {
      const groupMsgs = state.messages[g.id];
      if (groupMsgs && groupMsgs.length > 0) {
        const last = groupMsgs[groupMsgs.length - 1];
        map[g.id] = {
          content: last.isDeleted ? t('chat.messageRecalled') : last.content.replace(/<[^>]+>/g, ''),
          timestamp: last.timestamp
        };
      } else {
        map[g.id] = { content: g.description || '', timestamp: g.createdAt };
      }
    });
    return map;
  }, [state.groups, state.messages, t]);

  // Render gates — derived from (isLoading, currentUser, registrationStatus,
  // langPicked). No explicit stage state machine.
  if (isLoading || registrationStatus === 'unknown') {
    return (
      <ElectronShell className="bg-bgLight text-textMuted">
        <div className="h-full w-full flex items-center justify-center">{t('common.loading')}</div>
      </ElectronShell>
    );
  }

  if (currentUser) {
    // Signed in → main app (handled below by the main render block).
  } else {
    // First-launch wizard: show language picker when the backend says
    // ``registration_required=true`` AND the user has not yet clicked a
    // language button in this session. We deliberately do NOT consult
    // localStorage here — a stale ``opensquad_lang`` from a previous
    // deployment on the same browser must not skip the wizard on a
    // fresh deployment.
    if (registrationStatus === 'required' && !langPicked) {
      return (
        <ElectronShell>
          <LanguageSelectScreen onSelect={handleLanguagePicked} />
        </ElectronShell>
      );
    }

    // Pre-auth screen. Mode is driven by the registration-status probe
    // against the backend:
    //   - Fresh deploy + language already picked  → sign-up form (the
    //     first web user creates their own account here).
    //   - A web user already exists, or the status
    //     request failed → sign-in form, and the
    //     "no account yet? sign up" toggle is hidden so the user can't
    //     try to register a second time (the backend would 403 anyway).
    // We deliberately do not surface the old "first-time" / "registration
    // closed" hint banners inside the form — those are the "提示文本" the
    // first-time-registration cleanup removed and the user wanted gone.
    const isFirstTime = registrationStatus === 'required' && langPicked;
    const registrationClosed =
      registrationStatus === 'closed' || registrationStatus === 'error';

    return (
      <ElectronShell>
        <AuthScreen
          onLogin={handleLogin}
          onRegister={handleRegister}
          isFirstTime={isFirstTime}
          registrationClosed={registrationClosed}
        />
      </ElectronShell>
    );
  }

  const activeGroup = state.groups.find(g => g.id === state.activeGroupId);
  const activeMessages = state.activeGroupId ? (state.messages[state.activeGroupId] || []) : [];


  return (
    <ElectronShell className="transition-colors duration-300">
    <div className={`h-full w-full flex overflow-hidden bg-stage`}>

      {/* === Group Chat View === */}
      <div style={{ display: currentView === 'chat' ? 'contents' : 'none' }}>
          {/* Error Banner */}
          {state.error && (
            <div style={{
              position: 'fixed',
              top: '12px',
              left: '50%',
              transform: 'translateX(-50%)',
              zIndex: 9999,
              maxWidth: '600px',
              width: 'calc(100% - 24px)',
            }}>
              <div style={{
                background: 'linear-gradient(135deg, #dc2626, #b91c1c)',
                color: 'white',
                padding: '12px 20px',
                borderRadius: '10px',
                boxShadow: '0 4px 20px rgba(220,38,38,0.3)',
                display: 'flex',
                alignItems: 'center',
                gap: '10px',
                fontSize: '14px',
                lineHeight: '1.4',
              }}>
                <span style={{ fontSize: '18px' }}>⚠️</span>
                <span style={{ flex: 1 }}>{state.error}</span>
                <button
                  onClick={() => setState(prev => ({ ...prev, error: undefined }))}
                  style={{
                    background: 'rgba(255,255,255,0.2)',
                    border: 'none',
                    color: 'white',
                    cursor: 'pointer',
                    borderRadius: '6px',
                    padding: '4px 10px',
                    fontSize: '13px',
                    whiteSpace: 'nowrap',
                  }}
                >
                  Dismiss
                </button>
              </div>
            </div>
          )}
          <div className={`${state.activeGroupId ? 'hidden md:flex' : 'flex'} w-full md:w-80 h-full shrink-0 border-r border-border bg-bgLight`}>
            <ChatList

              groups={state.groups}
              activeGroupId={state.activeGroupId}
              onSelectGroup={handleSelectGroup}
              onJoinGroup={handleJoinGroup}
              onCreateGroup={async (name) => {
                  try {
                      const g = await groupAPI.createGroup(name);
                      // 立即把新群插入列表顶部，不等待 loadGroups，避免竞态丢失
                      setState(prev => ({
                          ...prev,
                          groups: [{
                              id: g.id,
                              name: g.name,
                              avatar: g.avatar || '',
                              description: g.description || '',
                              members: [],
                              unreadCount: 0,
                              hasUnreadMention: false,
                              isPrivate: g.is_private,
                              createdAt: Date.now(),
                              notificationSoundEnabled: true,
                          }, ...prev.groups]
                      }));
                      handleSelectGroup(g.id);
                      // 后台静默刷新，拉取完整数据（含 unread、排序等）
                      loadGroups();
                  } catch (error) {
                      console.error('Failed to create group:', error);
                      alert(t('chat.createFailed'));
                  }
              }}
              onToggleGroupSound={async (id) => {
                  const g = state.groups.find(x => x.id === id);
                  if (g) await groupAPI.updateGroup(id, { notification_sound_enabled: !g.notificationSoundEnabled });
          await loadGroups(true);
              }}
              lastMessages={lastMessages}
               currentUser={currentUser}

              onUpdateUser={handleUpdateUser}
              onLogout={handleLogout}
              onSwitchView={setCurrentView}
              onPrefetchGroup={handlePrefetchGroup}
              onOpenSettings={() => setIsSettingsOpen(true)}
              onOpenCollabBoard={() => setIsCollabBoardOpen(true)}
            />
          </div>
          <div className={`${!state.activeGroupId ? 'hidden md:flex' : 'flex'} flex-1 h-full min-w-0`}>
            {activeGroup ? (
              <ChatWindow
                group={activeGroup}
                groups={state.groups}
                messages={activeMessages}
                users={state.users}
                currentUser={currentUser}
                onSendMessage={handleSendMessage}
                onDeleteMessage={async (id) => { await messageAPI.deleteMessage(id); await loadMessages(activeGroup.id); }}
                onUndoRecall={async (id) => { await messageAPI.undoRecall(id); await loadMessages(activeGroup.id); }}
                onPermanentDelete={async (id) => { await messageAPI.permanentDeleteMessage(id); await loadMessages(activeGroup.id); }}
                onEditMessage={async (id, content) => { await messageAPI.editMessage(id, content); await loadMessages(activeGroup.id); }}
                onPinMessage={async (id) => { await messageAPI.pinMessage(id); await loadMessages(activeGroup.id); }}
                onPrependMessages={async (gid, ts) => {
                    const firstMsg = state.messages[gid]?.[0];
                    if (!firstMsg) return 0;

                    const msgs = await messageAPI.getMessages(gid, firstMsg.id, 20);
                    if (msgs.length > 0) {
                        const formattedMessages: Message[] = msgs.map(m => ({
                            id: m.id,
                            senderId: m.sender_id,
                            content: m.content,
                            timestamp: new Date(m.timestamp).getTime(),
                            type: m.type as MessageType,
                            attachments: m.attachments?.map((a: any) => ({
                                id: a.id,
                                name: a.name,
                                size: a.size,
                                url: a.url,
                                type: a.type as 'image' | 'video' | 'file' | 'folder' | 'voice',
                                duration: a.duration
                            })),
                            replyToId: m.reply_to_id,
                            isPinned: m.is_pinned,
                            isEdited: m.is_edited,
                            isDeleted: m.is_deleted,
                            canUndo: m.can_undo,
                            deletedAt: m.deleted_at ? new Date(m.deleted_at).getTime() : undefined,
                            mentions: m.mentions
                        }));

                        setState(prev => ({
                            ...prev,
                            messages: {
                                ...prev.messages,
                                [gid]: [...formattedMessages, ...(prev.messages[gid] || [])]
                            }
                        }));
                        return msgs.length;
                    }
                    return 0;
                }}
                 onConsumeMention={async (groupId) => {
                   try {
                     await groupAPI.markAsRead(groupId);
                     // Update local state to clear mention indicator
                     setState(prev => ({
                       ...prev,
                       groups: prev.groups.map(g =>
                         g.id === groupId ? { ...g, hasUnreadMention: false } : g
                       )
                     }));
                   } catch (error) {
                     console.error('Failed to consume mention:', error);
                   }
                 }}
                toggleRightPanel={() => setState(p => ({ ...p, isRightPanelOpen: !p.isRightPanelOpen }))}
                onOpenGroupSettings={() => setState(p => ({ ...p, isRightPanelOpen: true }))}
                filter={{ text: '', userId: null, dateFrom: null, dateTo: null }}
                 onBack={() => { localStorage.removeItem('nexus_active_group'); setState(prev => ({ ...prev, activeGroupId: null, isRightPanelOpen: false })); }}
                shouldJumpToMention={shouldJumpToMention}
                onReplaceMessages={(groupId, newMessages) => {
                  setState(prev => ({
                    ...prev,
                    messages: {
                      ...prev.messages,
                      [groupId]: newMessages
                    }
                  }));
                }}
                onLoadMessagesAround={async (groupId, messageId, timestamp) => {
                  const msgs = await messageAPI.getMessagesAround(groupId, timestamp, 20, 20);
                  const formattedMessages: Message[] = msgs.map(m => ({
                    id: m.id,
                    senderId: m.sender_id,
                    content: m.content,
                    timestamp: new Date(m.timestamp).getTime(),
                    type: m.type as MessageType,
                    attachments: m.attachments?.map((a: any) => ({
                      id: a.id,
                      name: a.name,
                      size: a.size,
                      url: a.url,
                      type: a.type as 'image' | 'video' | 'file' | 'folder'
                    })),
                    replyToId: m.reply_to_id,
                    isPinned: m.is_pinned,
                    isEdited: m.is_edited,
                    isDeleted: m.is_deleted,
                    canUndo: m.can_undo,
                    deletedAt: m.deleted_at ? new Date(m.deleted_at).getTime() : undefined,
                    mentions: m.mentions
                  }));

                  setState(prev => ({
                    ...prev,
                    messages: {
                      ...prev.messages,
                      [groupId]: formattedMessages
                    }
                  }));
                }}
                isMessagesLoading={isMessagesLoading}
              />
            ) : (
              <div className="flex-1 flex flex-col items-center justify-center bg-bgLight text-center p-8">
                <div className="w-20 h-20 bg-primary/10 rounded-full flex items-center justify-center mb-6">
                  <MessageSquare className="text-primary" size={40} />
                </div>
                <h3 className="text-xl font-bold text-textMain mb-2">{t('chat.welcome')}</h3>
                <p className="text-textMuted max-w-sm">{t('chat.selectGroup')}</p>
              </div>
            )}
          </div>
          {/* Desktop: side panel */}
          {state.isRightPanelOpen && activeGroup && (
              <div className="w-80 h-full border-l border-border shrink-0 hidden lg:block">
                  <RightPanel
                    isOpen={true}
                    onClose={() => setState(p => ({ ...p, isRightPanelOpen: false }))}
                    group={activeGroup}
                    users={state.users}
                    searchQuery={state.searchQuery}
                    onSearchChange={(q) => setState(p => ({ ...p, searchQuery: { ...p.searchQuery, ...q } }))}
                    onLeaveGroup={async (id) => { await groupAPI.leaveGroup(id); localStorage.removeItem('nexus_active_group'); setState(p => ({ ...p, activeGroupId: null, isRightPanelOpen: false })); await loadGroups(); }}
                    onToggleSound={async (id) => { const g = state.groups.find(x => x.id === id); if (g) { await groupAPI.updateGroup(id, { notification_sound_enabled: !g.notificationSoundEnabled }); await loadGroups(); } }}
                    onUpdateGroup={async (id, data) => { await groupAPI.updateGroup(id, data); await loadGroups(); if (id === activeGroup.id) loadGroupDetails(id); }}
                    messages={activeMessages}
                    onJumpToMessage={(messageId) => { window.dispatchEvent(new CustomEvent('jumpToMessage', { detail: { messageId, clearFilter: true } })); }}
                  />
              </div>
          )}
          {/* Mobile: full-screen overlay */}
          {state.isRightPanelOpen && activeGroup && (
              <div className="fixed inset-0 z-50 bg-panel lg:hidden mobile-safe-fixed">
                  <RightPanel
                    isOpen={true}
                    onClose={() => setState(p => ({ ...p, isRightPanelOpen: false }))}
                    group={activeGroup}
                    users={state.users}
                    searchQuery={state.searchQuery}
                    onSearchChange={(q) => setState(p => ({ ...p, searchQuery: { ...p.searchQuery, ...q } }))}
                    onLeaveGroup={async (id) => { await groupAPI.leaveGroup(id); localStorage.removeItem('nexus_active_group'); setState(p => ({ ...p, activeGroupId: null, isRightPanelOpen: false })); await loadGroups(); }}
                    onToggleSound={async (id) => { const g = state.groups.find(x => x.id === id); if (g) { await groupAPI.updateGroup(id, { notification_sound_enabled: !g.notificationSoundEnabled }); await loadGroups(); } }}
                    onUpdateGroup={async (id, data) => { await groupAPI.updateGroup(id, data); await loadGroups(); if (id === activeGroup.id) loadGroupDetails(id); }}
                    messages={activeMessages}
                    onJumpToMessage={(messageId) => { window.dispatchEvent(new CustomEvent('jumpToMessage', { detail: { messageId, clearFilter: true } })); }}
                  />
              </div>
          )}
      </div>

      {/* === Agent Manager View（业务全屏；设置「应用」项改为弹窗） === */}
      {mountedViews.has('admin') && (
        <div style={{ display: currentView === 'admin' ? 'contents' : 'none' }}>
          <Suspense fallback={<div className="flex-1 flex items-center justify-center bg-bgLight text-textMuted">{t('common.loading')}</div>}>
            <AgentManagerPage
              onBack={() => setCurrentView('chat')}
              onChat={openAgentChat}
              onOpenGroupChat={() => setCurrentView('chat')}
            />
          </Suspense>
        </div>
      )}

      {/* === AI Chat View (keep-alive: 最多 3 个 agent，切换时隐藏而非卸载) === */}
      {openAgentChats.map(agentId => (
          <div
            key={agentId}
            className="flex-1 min-w-0 h-full overflow-hidden"
            style={{ display: currentView === 'ai-chat' && selectedAgentId === agentId ? 'flex' : 'none', flexDirection: 'column' }}
          >
            <Suspense fallback={
              <div className="w-full h-full flex items-center justify-center bg-bgLight text-textMuted">{t('common.loading')}</div>
            }>
            <AIChatPage
              agentId={agentId}
              onBack={() => setCurrentView('admin')}
              currentUser={currentUser}
              onOpenProfile={handleOpenProfile}
              onOpenSettings={() => setIsSettingsOpen(true)}
            />
            </Suspense>
          </div>
      ))}

      {isProfileOpen && (
        <div className="fixed inset-0 bg-black/50 z-[100] flex items-center justify-center backdrop-blur-sm animate-in fade-in duration-200">
            <div className="bg-panel rounded-2xl shadow-2xl w-full max-w-sm overflow-hidden transform transition-all scale-100 mx-4 border border-border">
                <div className="bg-primary px-6 py-4 flex justify-between items-center text-white">
                    <h3 className="font-semibold text-lg">{t('profile.editProfile')}</h3>
                    <button onClick={() => setIsProfileOpen(false)} className="hover:text-white/80">
                        <X size={20} />
                    </button>
                </div>

                <div className="p-6 flex flex-col items-center text-textMain">
                    <div className="relative mb-6 group">
                        <AvatarImg avatar={editAvatar} className="w-24 h-24 rounded-full object-cover border-4 border-border shadow-lg" alt="Avatar Preview" />
                        <button
                            onClick={() => fileInputRef.current?.click()}
                            className="absolute bottom-0 right-0 p-2 bg-panel rounded-full shadow-md text-textMuted hover:text-primary transition-colors border border-border"
                        >
                            <Camera size={16} />
                        </button>
                        <input
                            type="file"
                            ref={fileInputRef}
                            className="hidden"
                            accept="image/*"
                            onChange={async (e) => {
                              if (e.target.files && e.target.files[0]) {
                                  const file = e.target.files[0];
                                  setSelectedFile(file);
                                  setEditAvatar(URL.createObjectURL(file));
                              }
                            }}
                        />
                    </div>

                    <div className="w-full space-y-4">
                        <div>
                            <label className="block text-xs font-bold text-textMuted uppercase mb-1">{t('profile.displayName')}</label>
                            <input
                                type="text"
                                value={editName}
                                onChange={(e) => setEditName(e.target.value)}
                                className="w-full px-4 py-2 bg-bgLight border border-border rounded-lg focus:outline-none focus:ring-2 focus:ring-primary/50 text-sm"
                            />
                        </div>

                        <button
                            onClick={handleSaveProfile}
                            className="w-full py-2.5 bg-primary text-white rounded-lg font-semibold hover:opacity-90 transition-colors flex items-center justify-center gap-2 mt-4"
                        >
                            <Save size={18} /> {t('profile.saveChanges')}
                        </button>

                        <button
                            onClick={() => {
                                setIsProfileOpen(false);
                                handleLogout();
                            }}
                            className="w-full py-2.5 bg-red-50 text-red-600 rounded-lg font-semibold hover:bg-red-100 transition-colors flex items-center justify-center gap-2"
                        >
                            <LogOut size={18} /> {t('profile.logOut')}
                        </button>
                    </div>
                </div>
            </div>
        </div>
      )}

      {/* Settings Modal — config tabs + embedded app panels */}
      <Suspense fallback={null}>
        <SystemConfigPage
          isOpen={isSettingsOpen}
          initialTab={settingsInitialTab}
          initialAppView={settingsInitialAppView}
          onClose={() => {
            setIsSettingsOpen(false);
            setSettingsInitialTab(undefined);
            setSettingsInitialAppView(null);
          }}
        />
      </Suspense>

      <SoftOverlay
        open={isCollabBoardOpen}
        onBackdrop={() => setIsCollabBoardOpen(false)}
        zClass="z-[110]"
        panelClassName="w-full max-w-6xl h-[min(88vh,900px)]"
      >
        <div className="os-modal-shell flex h-full w-full flex-col overflow-hidden">
          <Suspense
            fallback={
              <div className="flex flex-1 items-center justify-center text-textMuted">{t('common.loading')}</div>
            }
          >
            <CollabBoardPage onBack={() => setIsCollabBoardOpen(false)} />
          </Suspense>
        </div>
      </SoftOverlay>

      <DesktopUpdateOverlay />
    </div>
    </ElectronShell>
  );
};

export default App;
