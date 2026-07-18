import React, { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import {
  LayoutGrid, List, ArrowLeft, RefreshCw, Plus, Power, PowerOff, RotateCcw,
  Settings, FileText, Terminal, X, Save, ChevronDown, ChevronUp,
  Monitor, Code, PenTool, BarChart3, Globe, Bot, Wrench,
  Circle, Loader2, MessageSquare, Trash2, Pencil, Eye, EyeOff,
  FolderOpen, Menu, Shield,
} from 'lucide-react';
import { marked } from 'marked';
import { adminAPI, AdminAgent, TokenStats, ChatProfile, userAPI, pluginAPI, PluginInfo, modelCardAPI, ModelCardInfo, ModelCardDetail } from '../services/api';
import { resolveChatAvatar, resolveChatName } from '../utils/image';
import { useTranslation, Trans } from 'react-i18next';
import {
  adminHeaderBar,
  adminHeaderCta,
  adminHeaderGhostBtn,
  adminHeaderIcon,
  adminHeaderIconBox,
  adminHeaderNavBtn,
  adminHeaderSubtitle,
  adminHeaderTitle,
} from './admin/adminShellStyles';

// ---- 类型 ----

interface AgentManagerPageProps {
  onBack: () => void;
  onChat?: (agentId: string) => void;
}

const LAYOUT_KEY = 'agent_manager_layout';
type AgentLayoutMode = 'grid' | 'list';

function loadLayoutMode(): AgentLayoutMode {
  try {
    const raw = localStorage.getItem(LAYOUT_KEY);
    return raw === 'list' ? 'list' : 'grid';
  } catch {
    return 'grid';
  }
}

type DetailTab = 'config' | 'role' | 'logs';

// ---- 常量 ----

const TYPE_ICONS: Record<string, React.ReactNode> = {
  coder:      <Code size={20} />,
  writer:     <PenTool size={20} />,
  analyst:    <BarChart3 size={20} />,
  translator: <Globe size={20} />,
  general:    <Bot size={20} />,
};

const STATUS_COLORS: Record<string, string> = {
  running:  'bg-green-500',
  stopped:  'bg-gray-400',
  crashed:  'bg-red-500',
  starting: 'bg-yellow-400',
  external: 'bg-blue-400',
};

const STATUS_LABELS: Record<string, string> = {
  running:  'agentManager.statusRunning',
  stopped:  'agentManager.statusStopped',
  crashed:  'agentManager.statusCrashed',
  starting: 'agentManager.statusStarting',
  external: 'agentManager.statusExternal',
};

// System built-in tools: always registered, user cannot disable, shown with builtin badge
const SYSTEM_TOOLS = [
  'system', 'filesystem', 'agent_setup', 'im',
  'collaboration', 'delegate_task', 'workspace', 'task_watch',
  'websearch', 'reminder', 'vision', 'mcp_query', 'plugin_admin',
];
// Tools that are only available as built-in (not plugins): the rest are plugins
// managed by PluginManagerPage per-agent toggle.

// ---- 小组件 ----

const Toggle: React.FC<{ value: boolean; onChange: (v: boolean) => void }> = ({ value, onChange }) => (
  <button
    type="button"
    onClick={() => onChange(!value)}
    className={`relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors ${value ? 'bg-primary' : 'bg-gray-300'}`}
  >
    <span className={`inline-block h-3.5 w-3.5 transform rounded-full bg-white shadow transition-transform ${value ? 'translate-x-[19px]' : 'translate-x-0.5'}`} />
  </button>
);

// ---- 工具函数 ----

function getAgentKey(a: AdminAgent): string {
  return a.dir_name || a.agent_id || a.agent_name;
}

/**
 * Pick the description that matches the user's interface language.
 * Mirrors the plugin-market `getPluginText` helper (see PluginMarketPage):
 *   - per-language override if present
 *   - else the base `description` (backend falls back to that for us, but
 *     we also guard here in case the field is undefined).
 */
function getAgentDescription(agent: AdminAgent, appLang: string): string {
  if (appLang === 'zh') return agent.description_zh || agent.description || '';
  if (appLang === 'en') return agent.description_en || agent.description || '';
  // Unknown language code: prefer English (international default), then zh.
  return agent.description_en || agent.description_zh || agent.description || '';
}


/** Process is running but not registered to Gateway yet */
function isAgentStarting(agent: AdminAgent): boolean {
  return agent.process_status === 'running' && !agent.ready;
}

/** Process is running AND registered to Gateway */
function isAgentReady(agent: AdminAgent): boolean {
  return agent.process_status === 'running' && agent.ready;
}

function getBreathClass(agent: AdminAgent): string {
  if (isAgentStarting(agent)) return 'animate-breathe-sleeping';
  if (agent.process_status !== 'running') return '';
  const rs = agent.registry_status;
  if (rs === 'busy') return 'animate-breathe-working';
  if (rs === 'offline' || rs === 'sleeping') return 'animate-breathe-sleeping';
  return 'animate-breathe-idle';
}

function getBreathColor(agent: AdminAgent): string {
  if (isAgentStarting(agent)) return 'bg-yellow-400';
  if (agent.process_status !== 'running') return STATUS_COLORS[agent.process_status] || 'bg-gray-400';
  const rs = agent.registry_status;
  if (rs === 'busy') return 'bg-amber-400';
  if (rs === 'offline' || rs === 'sleeping') return 'bg-indigo-400';
  return 'bg-green-500';
}

function fmtTokens(n: number): string {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M';
  if (n >= 1_000) return (n / 1_000).toFixed(1) + 'K';
  return String(n);
}

// ---- 主组件 ----

export const AgentManagerPage: React.FC<AgentManagerPageProps> = ({ onBack, onChat }) => {
  const { t, i18n } = useTranslation();
  const [agents, setAgents] = useState<AdminAgent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [layoutMode, setLayoutMode] = useState<AgentLayoutMode>(loadLayoutMode);
  const setLayout = useCallback((mode: AgentLayoutMode) => {
    setLayoutMode(mode);
    try { localStorage.setItem(LAYOUT_KEY, mode); } catch { /* ignore */ }
  }, []);

  // 详情面板
  const [detailAgent, setDetailAgent] = useState<AdminAgent | null>(null);
  const [detailTab, setDetailTab] = useState<DetailTab>('config');
  const [configObj, setConfigObj] = useState<any>({});
  const [roleText, setRoleText] = useState('');
  const [roleOriginal, setRoleOriginal] = useState('');
  const [roleEditing, setRoleEditing] = useState(false);
  const [showApiKey, setShowApiKey] = useState(false);
  const [logs, setLogs] = useState<string[]>([]);
  const [detailLoading, setDetailLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveSuccess, setSaveSuccess] = useState(false);
  const [actionLoading, setActionLoading] = useState<Record<string, boolean>>({});
  const [fsNewDir, setFsNewDir] = useState('');

  // 创建新 Agent
  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName] = useState('');
  const [newType, setNewType] = useState('general');
  const [newDesc, setNewDesc] = useState('');
  const [newEmail, setNewEmail] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [newEmailUser, setNewEmailUser] = useState<{ id: string; name: string } | null>(null);
  const [emailLookupLoading, setEmailLookupLoading] = useState(false);
  const [creating, setCreating] = useState(false);

  const logEndRef = useRef<HTMLDivElement>(null);
  const [autoRefresh, setAutoRefresh] = useState(false);
  const autoRefreshRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const [availablePlugins, setAvailablePlugins] = useState<PluginInfo[]>([]);
  const [modelCards, setModelCards] = useState<ModelCardInfo[]>([]);
  const [modelCardLoading, setModelCardLoading] = useState(false);
  const [voiceAdvancedOpen, setVoiceAdvancedOpen] = useState(false);
  const voiceHydratedRef = useRef<string | null>(null);

  // 后台预取缓存：key → config（避免触发不必要重渲染，用 ref）
  const configCacheRef = useRef<Record<string, any>>({});

  // ---- config 工具函数 ----

  const cfgGet = useCallback((path: string[], def: any = '') => {
    const val = path.reduce((obj: any, key: string) => (obj != null && typeof obj === 'object' ? obj[key] : undefined), configObj);
    return val ?? def;
  }, [configObj]);

  const cfgSet = useCallback((path: string[], value: any) => {
    setConfigObj((prev: any) => {
      const clone = JSON.parse(JSON.stringify(prev || {}));
      let node = clone;
      for (let i = 0; i < path.length - 1; i++) {
        if (node[path[i]] == null || typeof node[path[i]] !== 'object') node[path[i]] = {};
        node = node[path[i]];
      }
      node[path[path.length - 1]] = value;
      return clone;
    });
  }, []);

  const cfgToggleTool = useCallback((tool: string) => {
    setConfigObj((prev: any) => {
      const clone = JSON.parse(JSON.stringify(prev || {}));
      const tools: string[] = clone.tools || [];
      clone.tools = tools.includes(tool) ? tools.filter((t: string) => t !== tool) : [...tools, tool];
      return clone;
    });
  }, []);

  // When config only has legacy *_card bindings, hydrate inline url/key/model from those cards.
  useEffect(() => {
    if (!detailAgent || detailTab !== 'config' || detailLoading) return;
    const agentKey = getAgentKey(detailAgent);
    if (voiceHydratedRef.current === agentKey) return;
    const voice = (configObj && configObj.voice) || {};
    const hasInline = !!(
      voice.base_url || voice.api_key || voice.asr_model || voice.tts_model || voice.realtime_model
    );
    if (hasInline) {
      voiceHydratedRef.current = agentKey;
      return;
    }
    const cardNames = [voice.asr_card, voice.tts_card, voice.realtime_card].filter(Boolean) as string[];
    if (cardNames.length === 0) {
      voiceHydratedRef.current = agentKey;
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const pairs: Array<['asr' | 'tts' | 'realtime', string]> = [];
        if (voice.asr_card) pairs.push(['asr', voice.asr_card]);
        if (voice.tts_card) pairs.push(['tts', voice.tts_card]);
        if (voice.realtime_card) pairs.push(['realtime', voice.realtime_card]);
        const updates: Record<string, string> = {};
        for (const [kind, name] of pairs) {
          try {
            const res = await modelCardAPI.getCard(name);
            const c = res.card || (res as any);
            if (!updates.base_url && c.base_url) updates.base_url = String(c.base_url);
            if (!updates.api_key && c.api_key) updates.api_key = String(c.api_key);
            if (kind === 'asr' && c.model_name) updates.asr_model = String(c.model_name);
            if (kind === 'tts' && c.model_name) updates.tts_model = String(c.model_name);
            if (kind === 'realtime' && c.model_name) updates.realtime_model = String(c.model_name);
            if (!updates.realtime_voice && c.audio_output_voice) {
              updates.realtime_voice = String(c.audio_output_voice);
            }
          } catch { /* skip missing card */ }
        }
        if (cancelled || Object.keys(updates).length === 0) return;
        setConfigObj((prev: any) => {
          const clone = JSON.parse(JSON.stringify(prev || {}));
          if (!clone.voice || typeof clone.voice !== 'object') clone.voice = {};
          for (const [k, v] of Object.entries(updates)) {
            if (!clone.voice[k]) clone.voice[k] = v;
          }
          return clone;
        });
      } finally {
        if (!cancelled) voiceHydratedRef.current = agentKey;
      }
    })();
    return () => { cancelled = true; };
  }, [detailAgent, detailTab, detailLoading, configObj]);

  // 渲染 Role Prompt markdown
  const renderedRole = useMemo(() => {
    if (!roleText) return `<p class="text-textMuted text-sm">${t('agentManager.empty')}</p>`;
    try { return marked.parse(roleText) as string; }
    catch { return `<pre>${roleText}</pre>`; }
  }, [roleText]);

  // ---- 数据加载 ----

  const fetchAgents = useCallback(async () => {
    try {
      setError(null);
      const data = await adminAPI.getAgents();
      const agentList: AdminAgent[] = data.agents || [];
      setAgents(agentList);
      // 后台静默预取所有 agent 的 config（不阻塞 UI）
      agentList.forEach(agent => {
        const key = getAgentKey(agent);
        adminAPI.getConfig(key)
          .then(cfgData => { configCacheRef.current[key] = cfgData.config || {}; })
          .catch(() => {});
      });
    } catch (e: any) {
      setError(e.message || 'Failed to load agents');
    } finally {
      setLoading(false);
    }
  }, []);

  // 挂载时预加载 plugins 和 model cards（不等用户点开）
  useEffect(() => {
    pluginAPI.getPlugins().then(d => setAvailablePlugins(d.plugins || [])).catch(() => {});
    modelCardAPI.getCards().then(d => setModelCards(d.cards || [])).catch(() => {});
  }, []);

  useEffect(() => {
    fetchAgents();
    const timer = setInterval(fetchAgents, 30000);
    return () => clearInterval(timer);
  }, [fetchAgents]);

  // ── 短时高频轮询：用户操作后捕捉 starting→running 过渡 ──
  // 默认 30s 轮询太慢，用户点 Start/Restart 后 agent 还在 starting
  // 阶段（Popen 已起但尚未 register 到 Gateway），下次自动刷新要等
  // 最多 30s。这里在操作后用 2s 间隔轮询 30s 窗口，让 UI 近实时反映
  // starting→ready 的过渡。比 WS 推送轻量，且无需后端改动。
  const fastPollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const fastPollUntilStable = useCallback(() => {
    if (fastPollRef.current) clearInterval(fastPollRef.current);
    let elapsed = 0;
    const intervalMs = 2000;
    const maxMs = 30000;
    fastPollRef.current = setInterval(async () => {
      elapsed += intervalMs;
      await fetchAgents();
      if (elapsed >= maxMs && fastPollRef.current) {
        clearInterval(fastPollRef.current);
        fastPollRef.current = null;
      }
    }, intervalMs);
  }, [fetchAgents]);

  // 卸载时清理快轮询定时器，避免泄漏
  useEffect(() => {
    return () => {
      if (fastPollRef.current) clearInterval(fastPollRef.current);
    };
  }, []);

  // ---- Agent 操作 ----

  const doAction = async (agent: AdminAgent, action: 'start' | 'stop' | 'restart') => {
    const name = getAgentKey(agent);
    setActionLoading(prev => ({ ...prev, [name]: true }));
    try {
      if (action === 'start') await adminAPI.startAgent(name);
      else if (action === 'stop') await adminAPI.stopAgent(name);
      else await adminAPI.restartAgent(name);
      await fetchAgents();
      // start/restart 有 starting→ready 过渡，触发快轮询捕捉状态变化；
      // stop 是立即生效的，不需要补偿轮询。
      if (action === 'start' || action === 'restart') {
        fastPollUntilStable();
      }
    } catch (e: any) {
      alert(`${action} failed: ${e.message}`);
    } finally {
      setActionLoading(prev => ({ ...prev, [name]: false }));
    }
  };

  // ---- 详情面板 ----

  const openDetail = async (agent: AdminAgent, tab: DetailTab = 'config') => {
    const key = getAgentKey(agent);
    setDetailAgent(agent);
    setDetailTab(tab);
    setRoleEditing(false);
    setShowApiKey(false);
    setVoiceAdvancedOpen(false);
    voiceHydratedRef.current = null;

    if (tab === 'config') {
      const cached = configCacheRef.current[key];
      if (cached) {
        // 缓存命中：立即展示，无需 loading
        setConfigObj(cached);
        setDetailLoading(false);
        // 后台静默刷新（更新缓存和展示）
        Promise.all([
          adminAPI.getConfig(key),
          pluginAPI.getPlugins().catch(() => ({ plugins: [] })),
          modelCardAPI.getCards().catch(() => ({ cards: [] })),
        ]).then(([cfgData, pluginData, cardData]) => {
          const cfg = cfgData.config || {};
          configCacheRef.current[key] = cfg;
          setConfigObj(cfg);
          setAvailablePlugins(pluginData.plugins || []);
          setModelCards(cardData.cards || []);
        }).catch(() => {});
      } else {
        // 首次（缓存未命中）：显示 loading
        setDetailLoading(true);
        try {
          const [cfgData, pluginData, cardData] = await Promise.all([
            adminAPI.getConfig(key),
            pluginAPI.getPlugins().catch(() => ({ plugins: [] })),
            modelCardAPI.getCards().catch(() => ({ cards: [] })),
          ]);
          const cfg = cfgData.config || {};
          configCacheRef.current[key] = cfg;
          setConfigObj(cfg);
          setAvailablePlugins(pluginData.plugins || []);
          setModelCards(cardData.cards || []);
        } catch (e: any) {
          setConfigObj({});
        } finally {
          setDetailLoading(false);
        }
      }
    } else if (tab === 'role') {
      setDetailLoading(true);
      try {
        const data = await adminAPI.getRole(key);
        setRoleText(data.content || '');
        setRoleOriginal(data.content || '');
      } catch (e: any) {
        setRoleText(`Error: ${e.message}`);
        setRoleOriginal('');
      } finally {
        setDetailLoading(false);
      }
    } else {
      setDetailLoading(true);
      try {
        const data = await adminAPI.getLogs(key, 300);
        setLogs(data.logs || []);
        setTimeout(() => logEndRef.current?.scrollIntoView({ behavior: 'smooth' }), 100);
      } catch (e: any) {
        setLogs([`Error: ${e.message}`]);
      } finally {
        setDetailLoading(false);
      }
    }
  };

  const switchTab = (tab: DetailTab) => {
    if (tab !== 'role') setRoleEditing(false);
    if (detailAgent) openDetail(detailAgent, tab);
  };

  const saveConfig = async () => {
    if (!detailAgent) return;
    setSaving(true);
    setSaveSuccess(false);

    // 若输入框有未提交的目录，自动先加入列表再保存
    let finalConfigObj = configObj;
    if (fsNewDir.trim()) {
      const dirs = (cfgGet(['filesystem', 'workspace_dirs'], []) as string[]);
      const newEntry = fsNewDir.trim();
      if (!dirs.includes(newEntry)) {
        const newDirs = [...dirs, newEntry];
        // 同步更新 configObj（避免异步 setState 竞态）
        const clone = JSON.parse(JSON.stringify(configObj || {}));
        if (!clone.filesystem || typeof clone.filesystem !== 'object') clone.filesystem = {};
        clone.filesystem.workspace_dirs = newDirs;
        finalConfigObj = clone;
        cfgSet(['filesystem', 'workspace_dirs'], newDirs);
      }
      setFsNewDir('');
    }

    try {
      await adminAPI.updateConfig(getAgentKey(detailAgent), finalConfigObj);
      window.dispatchEvent(new CustomEvent('plugin-nav-changed'));
      window.dispatchEvent(new CustomEvent('agent-nav-changed'));
      setSaveSuccess(true);
      setTimeout(() => setSaveSuccess(false), 3000);
    } catch (e: any) {
      alert(`Save failed: ${e.message}`);
    } finally {
      setSaving(false);
    }
  };

  const saveRole = async () => {
    if (!detailAgent) return;
    setSaving(true);
    try {
      await adminAPI.updateRole(getAgentKey(detailAgent), roleText);
      setRoleOriginal(roleText);
      setRoleEditing(false);
    } catch (e: any) {
      alert(`Save failed: ${e.message}`);
    } finally {
      setSaving(false);
    }
  };

  const refreshLogs = async () => {
    if (!detailAgent) return;
    setDetailLoading(true);
    try {
      const data = await adminAPI.getLogs(getAgentKey(detailAgent), 300);
      setLogs(data.logs || []);
      setTimeout(() => logEndRef.current?.scrollIntoView({ behavior: 'smooth' }), 100);
    } catch (e: any) {
      setLogs([`Error: ${e.message}`]);
    } finally {
      setDetailLoading(false);
    }
  };

  // ---- 日志自动刷新 ----

  useEffect(() => {
    if (autoRefresh && detailTab === 'logs' && detailAgent) {
      autoRefreshRef.current = setInterval(async () => {
        if (!detailAgent) return;
        try {
          const data = await adminAPI.getLogs(getAgentKey(detailAgent), 300);
          setLogs(data.logs || []);
          setTimeout(() => logEndRef.current?.scrollIntoView({ behavior: 'smooth' }), 50);
        } catch { /* 静默 */ }
      }, 3000);
    }
    return () => {
      if (autoRefreshRef.current) { clearInterval(autoRefreshRef.current); autoRefreshRef.current = null; }
    };
  }, [autoRefresh, detailTab, detailAgent]);

  const closeDetail = () => {
    setAutoRefresh(false);
    setRoleEditing(false);
    setDetailAgent(null);
  };

  // ---- 创建 Agent ----

  // 输入邮箱后自动查找账号
  const handleEmailBlur = useCallback(async () => {
    const email = newEmail.trim();
    if (!email) { setNewEmailUser(null); return; }
    setEmailLookupLoading(true);
    try {
      const users = await userAPI.searchUsers(email);
      const matched = users.find((u: any) => u.email === email);
      setNewEmailUser(matched ? { id: matched.id, name: matched.name } : null);
    } catch {
      setNewEmailUser(null);
    } finally {
      setEmailLookupLoading(false);
    }
  }, [newEmail]);

  const handleCreate = async () => {
    if (!newName.trim()) { alert('Directory name is required'); return; }
    if (!newEmail.trim()) { alert('Chat email is required'); return; }
    if (!newPassword.trim()) { alert('Chat password is required'); return; }
    setCreating(true);
    try {
      await adminAPI.createAgent({
        name: newName.trim(),
        agent_type: newType,
        description: newDesc.trim(),
        chat_email: newEmail.trim(),
        chat_password: newPassword.trim(),
      });
      setShowCreate(false);
      setNewName(''); setNewType('general'); setNewDesc(''); setNewEmail(''); setNewPassword(''); setNewEmailUser(null);
      await fetchAgents();
    } catch (e: any) {
      alert(`Create failed: ${e.message}`);
    } finally {
      setCreating(false);
    }
  };

  const handleDelete = async (agent: AdminAgent) => {
    const key = getAgentKey(agent);
    const displayName = agent.agent_name || key;
    if (!confirm(`Are you sure you want to delete "${displayName}"?\n\nThis will permanently remove the agent directory and all its configuration files. This action cannot be undone.`)) return;
    setActionLoading(prev => ({ ...prev, [key]: true }));
    try {
      await adminAPI.deleteAgent(key);
      if (detailAgent && getAgentKey(detailAgent) === key) closeDetail();
      await fetchAgents();
    } catch (e: any) {
      alert(`Delete failed: ${e.message}`);
    } finally {
      setActionLoading(prev => ({ ...prev, [key]: false }));
    }
  };

  // ---- 统计 ----
  const runningCount = agents.filter(a => a.process_status === 'running').length;
  const readyCount = agents.filter(a => a.ready).length;
  const startingCount = agents.filter(a => isAgentStarting(a)).length;
  const totalCount = agents.length;

  // ---- 样式常量 ----
  const inputCls = 'w-full px-2.5 py-1.5 bg-panel border border-border rounded-lg text-xs text-textMain focus:outline-none focus:ring-1 focus:ring-primary/40 placeholder:text-textMuted/60';
  const sectionTitleCls = 'text-[11px] font-bold text-textMuted uppercase tracking-wider mb-2 flex items-center gap-1.5';

  // ---- Config 表单渲染 ----
  const renderConfigForm = () => {
    const tools: string[] = cfgGet(['tools'], []);
    const toolLevels = cfgGet(['tool_levels'], {}) as Record<string, string>;

    // 所有已启用的插件，按 tool_levels 分组
    const allPluginTools = availablePlugins.filter(p => p.enabled && !SYSTEM_TOOLS.includes(p.name));
    // 插件被显式设为 core → 显示在核心工具
    const corePluginTools = allPluginTools.filter(p => toolLevels[p.name] === 'core');
    // 插件未设置或设为 extended → 显示在拓展工具
    const extendedPluginTools = allPluginTools.filter(p => toolLevels[p.name] !== 'core');

    // 已在 agent config 但未在 Plugin Manager 中出现的孤立工具（插件被删除/禁用后残留）
    const orphanTools = tools.filter(
      t => !SYSTEM_TOOLS.includes(t) && !allPluginTools.find(p => p.name === t)
    );

    return (
      <div className="space-y-5 pb-4">

        {/* ── 基本信息 ── */}
        <div>
          <div className={sectionTitleCls}><Bot size={11} /> {t('agentManager.basicInfo')}</div>
          <div className="bg-bgLight rounded-lg p-3 space-y-2.5">
            <div className="grid grid-cols-2 gap-2">
              <div>
                <label className="block text-[10px] font-semibold text-textMuted mb-1">Agent ID</label>
                <input value={cfgGet(['agent_id'])} readOnly className={`${inputCls} opacity-50 cursor-not-allowed`} />
              </div>
              <div>
                <label className="block text-[10px] font-semibold text-textMuted mb-1">Type</label>
                <select value={cfgGet(['agent_type'], 'general')} onChange={e => cfgSet(['agent_type'], e.target.value)} className={inputCls}>
                  {['general', 'coder', 'writer', 'analyst', 'translator'].map(v => <option key={v} value={v}>{v}</option>)}
                </select>
              </div>
            </div>
            <div>
              <label className="block text-[10px] font-semibold text-textMuted mb-1">
                Display Name
                <span className="font-normal text-textMuted/60 ml-1">{t('agentManager.displayNameSyncHint')}</span>
              </label>
              <input value={cfgGet(['agent_name'])} onChange={e => cfgSet(['agent_name'], e.target.value)} className={inputCls} />
            </div>
            <div>
              <label className="block text-[10px] font-semibold text-textMuted mb-1">Description</label>
              <textarea value={cfgGet(['description'])} onChange={e => cfgSet(['description'], e.target.value)} rows={2} className={`${inputCls} resize-none`} />
            </div>
            <div className="flex items-center justify-between pt-1">
              <span className="text-xs font-medium text-textMain">{t('agentManager.navShortcut')}</span>
              <Toggle
                value={cfgGet(['ui', 'nav_shortcut'], false)}
                onChange={v => cfgSet(['ui', 'nav_shortcut'], v)}
              />
            </div>
            <p className="text-[10px] text-textMuted/70 -mt-1">
              {t('agentManager.navShortcutHint')}
            </p>
            <div className="flex items-center justify-between pt-1">
              <span className="text-xs font-medium text-textMain">{t('agentManager.autoStartOnBoot')}</span>
              <Toggle
                value={cfgGet(['ui', 'auto_start_on_boot'], false)}
                onChange={v => cfgSet(['ui', 'auto_start_on_boot'], v)}
              />
            </div>
            <p className="text-[10px] text-textMuted/70 -mt-1">
              {t('agentManager.autoStartOnBootHint')}
            </p>
            <div>
              <label className="block text-[10px] font-semibold text-textMuted mb-1">Capabilities <span className="font-normal text-textMuted/60">{t('agentManager.commaSeparated')}</span></label>
              <input
                value={(cfgGet(['capabilities'], []) as string[]).join(', ')}
                onChange={e => cfgSet(['capabilities'], e.target.value.split(',').map((s: string) => s.trim()).filter(Boolean))}
                placeholder="e.g. python, debug, code_review"
                className={inputCls}
              />
              {(cfgGet(['capabilities'], []) as string[]).length > 0 && (
                <div className="flex flex-wrap gap-1 mt-1.5">
                  {(cfgGet(['capabilities'], []) as string[]).map((cap: string) => (
                    <span key={cap} className="text-[10px] bg-primary/10 text-primary px-2 py-0.5 rounded-full">{cap}</span>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>

        {/* ── 模型设置 ── */}
        <div>
          <div className={sectionTitleCls}><Wrench size={11} /> {t('agentManager.modelSettings')}</div>
          <div className="bg-bgLight rounded-lg p-3 space-y-2.5">
            {(detailAgent?.model_card || cfgGet(['model', '_card'])) && (
              <div className="flex items-center gap-2 text-xs">
                <span className="text-textMuted">{t('agentManager.importFromModelCard')}:</span>
                <span className="px-2 py-0.5 rounded-full bg-primary/10 text-primary font-medium truncate">
                  {detailAgent?.model_card || cfgGet(['model', '_card'])}
                </span>
              </div>
            )}
            {/* 模型卡导入选择器 */}
            {modelCards.length > 0 && (
              <div>
                <label className="block text-[10px] font-semibold text-textMuted mb-1">{t('agentManager.importFromModelCard')}</label>
                <select
                  value={cfgGet(['model', '_card'], '') || detailAgent?.model_card || ''}
                  disabled={modelCardLoading}
                  onChange={async e => {
                    const cardName = e.target.value;
                    if (!cardName) return;
                    setModelCardLoading(true);
                    try {
                      const res = await modelCardAPI.getCard(cardName);
                      const c = res.card;
                      cfgSet(['model', '_card'], cardName);
                      cfgSet(['model', 'api_protocol'], c.api_protocol);
                      cfgSet(['model', 'provider'], c.provider ?? '');
                      cfgSet(['model', 'model_name'], c.model_name);
                      cfgSet(['model', 'api_key'], c.api_key);
                      cfgSet(['model', 'base_url'], c.base_url);
                      cfgSet(['model', 'token_max'], c.token_max);
                      cfgSet(['model', 'tool_output_max_chars'], c.tool_output_max_chars ?? 50000);
                      cfgSet(['model', 'temperature'], c.temperature);
                      cfgSet(['model', 'frequency_penalty'], c.frequency_penalty ?? 0);
                      cfgSet(['model', 'presence_penalty'], c.presence_penalty ?? 0);
                      cfgSet(['model', 'top_k'], c.top_k ?? 0);
                      if (c.tool_call_mode) cfgSet(['model', 'tool_call_mode'], c.tool_call_mode);
                      if (c.render_mode) cfgSet(['model', 'render_mode'], c.render_mode);
                      cfgSet(['model', 'is_think'], c.is_think);
                      cfgSet(['model', 'is_image'], c.is_image);
                      cfgSet(['model', 'is_audio_model'], c.is_audio ?? false);
                      cfgSet(['model', 'is_video'], c.is_video);
                      cfgSet(['model', 'is_audio_output'], c.is_audio_output ?? false);
                      cfgSet(['model', 'is_image_output'], c.is_image_output ?? false);
                      cfgSet(['model', 'audio_output_voice'], c.audio_output_voice ?? 'alloy');
                    } catch (err: any) {
                      alert(t('agentManager.loadModelCardFailed', { message: err.message }));
                    } finally {
                      setModelCardLoading(false);
                    }
                  }}
                  className={inputCls}
                >
                  <option value="">{t('agentManager.selectModelCard')}</option>
                  {modelCards.map(card => (
                    <option key={card.name} value={card.name}>{card.title || card.name}</option>
                  ))}
                </select>
              </div>
            )}
            <div className="grid grid-cols-2 gap-2">
              <div>
                <label className="block text-[10px] font-semibold text-textMuted mb-1">API Protocol</label>
                <input value={cfgGet(['model', 'api_protocol'])} onChange={e => cfgSet(['model', 'api_protocol'], e.target.value)} placeholder="openai_compat" className={inputCls} />
              </div>
              <div>
                <label className="block text-[10px] font-semibold text-textMuted mb-1">Provider</label>
                <input value={cfgGet(['model', 'provider'])} onChange={e => cfgSet(['model', 'provider'], e.target.value)} placeholder="DeepSeek / OpenAI / Google Gemini" className={inputCls} />
              </div>
            </div>
            <div>
              <label className="block text-[10px] font-semibold text-textMuted mb-1">Model Name</label>
              <input value={cfgGet(['model', 'model_name'])} onChange={e => cfgSet(['model', 'model_name'], e.target.value)} placeholder="gpt-4o" className={inputCls} />
            </div>
            <div>
              <label className="block text-[10px] font-semibold text-textMuted mb-1">API Key</label>
              <div className="relative">
                <input
                  type={showApiKey ? 'text' : 'password'}
                  value={cfgGet(['model', 'api_key'])}
                  onChange={e => cfgSet(['model', 'api_key'], e.target.value)}
                  placeholder="sk-..."
                  className={`${inputCls} pr-8`}
                />
                <button
                  type="button"
                  onClick={() => setShowApiKey(v => !v)}
                  className="absolute right-2 top-1/2 -translate-y-1/2 text-textMuted hover:text-textMain transition-colors"
                >
                  {showApiKey ? <EyeOff size={12} /> : <Eye size={12} />}
                </button>
              </div>
            </div>
            <div>
              <label className="block text-[10px] font-semibold text-textMuted mb-1">Base URL</label>
              <input value={cfgGet(['model', 'base_url'])} onChange={e => cfgSet(['model', 'base_url'], e.target.value)} placeholder="https://api.openai.com/v1" className={inputCls} />
            </div>
            <div className="grid grid-cols-3 gap-2">
              <div>
                <label className="block text-[10px] font-semibold text-textMuted mb-1">Token Max</label>
                <input
                  type="number"
                  value={cfgGet(['model', 'token_max'], '')}
                  onChange={e => cfgSet(['model', 'token_max'], parseInt(e.target.value) || 0)}
                  className={inputCls}
                />
              </div>
              <div>
                <label className="block text-[10px] font-semibold text-textMuted mb-1" title={t('agentManager.toolOutputMaxHint') || 'Per-tool-call output char limit (0=unlimited)'}>
                  Tool Output Limit
                </label>
                <input
                  type="number"
                  min="0" step="100"
                  value={cfgGet(['model', 'tool_output_max_chars'], 50000)}
                  onChange={e => cfgSet(['model', 'tool_output_max_chars'], Math.max(0, parseInt(e.target.value) || 0))}
                  className={inputCls}
                />
              </div>
              <div>
                <label className="block text-[10px] font-semibold text-textMuted mb-1">Temperature</label>
                <input
                  type="number"
                  step="0.1" min="0" max="2"
                  value={cfgGet(['model', 'temperature'], '')}
                  onChange={e => cfgSet(['model', 'temperature'], parseFloat(e.target.value) || 0)}
                  className={inputCls}
                />
              </div>
              <div>
                <label className="block text-[10px] font-semibold text-textMuted mb-1" title={t('agentManager.freqPenaltyHint')}>Freq.Penalty</label>
                <input
                  type="number" step="0.1" min="-2" max="2"
                  value={cfgGet(['model', 'frequency_penalty'], 0)}
                  onChange={e => cfgSet(['model', 'frequency_penalty'], parseFloat(e.target.value) || 0)}
                  className={inputCls}
                />
              </div>
              <div>
                <label className="block text-[10px] font-semibold text-textMuted mb-1" title={t('agentManager.presPenaltyHint')}>Pres.Penalty</label>
                <input
                  type="number" step="0.1" min="-2" max="2"
                  value={cfgGet(['model', 'presence_penalty'], 0)}
                  onChange={e => cfgSet(['model', 'presence_penalty'], parseFloat(e.target.value) || 0)}
                  className={inputCls}
                />
              </div>
              <div>
                <label className="block text-[10px] font-semibold text-textMuted mb-1" title={t('agentManager.topKHint')}>Top-K</label>
                <input
                  type="number" step="1" min="0"
                  value={cfgGet(['model', 'top_k'], 0)}
                  onChange={e => cfgSet(['model', 'top_k'], Math.max(0, Math.floor(parseInt(e.target.value) || 0)))}
                  className={inputCls}
                />
              </div>
              <div>
                <label className="block text-[10px] font-semibold text-textMuted mb-1">Tool Call Mode</label>
                <select
                  value={cfgGet(['model', 'tool_call_mode'], 'auto')}
                  onChange={e => cfgSet(['model', 'tool_call_mode'], e.target.value)}
                  className={inputCls}
                  title={t('agentManager.toolCallModeHint')}
                >
                  <option value="auto">Auto</option>
                  <option value="native">Native FC</option>
                  <option value="xml">XML</option>
                </select>
              </div>
              <div>
                <label className="block text-[10px] font-semibold text-textMuted mb-1">Render Mode</label>
                <select
                  value={cfgGet(['model', 'render_mode'], 'strict')}
                  onChange={e => cfgSet(['model', 'render_mode'], e.target.value)}
                  className={inputCls}
                  title={t('agentManager.renderModeHint')}
                >
                  <option value="strict">{t('agentManager.standardOutput')}</option>
                  <option value="full">{t('agentManager.fullOutput')}</option>
                </select>
              </div>
              <div>
                <label className="block text-[10px] font-semibold text-textMuted mb-1">Deep Think</label>
                <div className="flex items-center h-[28px]">
                  <Toggle
                    value={cfgGet(['model', 'is_think'], false)}
                    onChange={v => cfgSet(['model', 'is_think'], v)}
                  />
                </div>
              </div>
              <div>
                <label className="block text-[10px] font-semibold text-textMuted mb-1">Image</label>
                <div className="flex items-center h-[28px]">
                  <Toggle
                    value={cfgGet(['model', 'is_image'], false)}
                    onChange={v => cfgSet(['model', 'is_image'], v)}
                  />
                </div>
              </div>
              <div>
                <label className="block text-[10px] font-semibold text-textMuted mb-1">Audio</label>
                <div className="flex items-center h-[28px]">
                  <Toggle
                    value={cfgGet(['model', 'is_audio_model'], false)}
                    onChange={v => cfgSet(['model', 'is_audio_model'], v)}
                  />
                </div>
              </div>
              <div>
                <label className="block text-[10px] font-semibold text-textMuted mb-1">Video</label>
                <div className="flex items-center h-[28px]">
                  <Toggle
                    value={cfgGet(['model', 'is_video'], false)}
                    onChange={v => cfgSet(['model', 'is_video'], v)}
                  />
                </div>
              </div>
              <div>
                <label className="block text-[10px] font-semibold text-textMuted mb-1">AudioOut</label>
                <div className="flex items-center h-[28px]">
                  <Toggle
                    value={cfgGet(['model', 'is_audio_output'], false)}
                    onChange={v => cfgSet(['model', 'is_audio_output'], v)}
                  />
                </div>
              </div>
              <div>
                <label className="block text-[10px] font-semibold text-textMuted mb-1">ImgOut</label>
                <div className="flex items-center h-[28px]">
                  <Toggle
                    value={cfgGet(['model', 'is_image_output'], false)}
                    onChange={v => cfgSet(['model', 'is_image_output'], v)}
                  />
                </div>
              </div>
              <div>
                <label className="block text-[10px] font-semibold text-textMuted mb-1" title={t('agentManager.repetitionDetectHint')}>Repetition Detect</label>
                <div className="flex items-center h-[28px]">
                  <Toggle
                    value={cfgGet(['enable_repetition_detection'], true)}
                    onChange={v => cfgSet(['enable_repetition_detection'], v)}
                  />
                </div>
              </div>
              {cfgGet(['model', 'is_audio_output'], false) && (
                <div className="col-span-2">
                  <label className="block text-[10px] font-semibold text-textMuted mb-1">Voice</label>
                  <select
                    value={cfgGet(['model', 'audio_output_voice'], 'alloy')}
                    onChange={e => cfgSet(['model', 'audio_output_voice'], e.target.value)}
                    className={inputCls}
                  >
                    {['alloy', 'echo', 'fable', 'onyx', 'nova', 'shimmer'].map(v => (
                      <option key={v} value={v}>{v}</option>
                    ))}
                  </select>
                </div>
              )}
            </div>
          </div>
        </div>

        {/* ── 语音能力：三项各自选模型卡 ── */}
        <div>
          <div className={sectionTitleCls}><Wrench size={11} /> Voice</div>
          <div className="bg-bgLight rounded-lg p-3 space-y-2">
            <p className="text-[10px] text-textMuted">
              ASR（语音输入）/ TTS（语音输出）/ Realtime（双向）各自独立选择模型卡。
              先在「模型」面板创建卡（只需 url / api_key / model），再在此绑定。
            </p>
            <div className="grid grid-cols-1 gap-2">
              {([
                { key: 'asr_card' as const, label: 'ASR 模型卡（语音输入）', hint: 'is_audio' },
                { key: 'tts_card' as const, label: 'TTS 模型卡（语音输出）', hint: 'is_audio_output' },
                { key: 'realtime_card' as const, label: 'Realtime 模型卡（双向）', hint: 'audio in+out' },
              ]).map(({ key, label, hint }) => (
                <div key={key}>
                  <label className="block text-[10px] font-semibold text-textMuted mb-1">
                    {label} <span className="font-normal opacity-60">({hint})</span>
                  </label>
                  <select
                    value={cfgGet(['voice', key], '')}
                    onChange={(e) => cfgSet(['voice', key], e.target.value)}
                    className={inputCls}
                  >
                    <option value="">(none)</option>
                    {modelCards.map(card => (
                      <option key={card.name} value={card.name}>
                        {card.title || card.name}
                        {card.model_name ? ` · ${card.model_name}` : ''}
                      </option>
                    ))}
                  </select>
                </div>
              ))}
              <div>
                <label className="block text-[10px] font-semibold text-textMuted mb-1">realtime_voice（音色，可选）</label>
                <input
                  value={cfgGet(['voice', 'realtime_voice'], '')}
                  onChange={e => cfgSet(['voice', 'realtime_voice'], e.target.value)}
                  placeholder={t('common.optional') || 'optional'}
                  className={inputCls}
                />
              </div>
            </div>

            <button
              type="button"
              onClick={() => setVoiceAdvancedOpen(v => !v)}
              className="flex items-center gap-1 text-[10px] text-textMuted hover:text-primary border-0 bg-transparent cursor-pointer px-0 py-1"
            >
              {voiceAdvancedOpen ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
              高级：内联 url / api_key / model（无模型卡时的兜底）
            </button>
            {voiceAdvancedOpen && (
              <div className="grid grid-cols-1 gap-2 pt-1 border-t border-border/50">
                <p className="text-[10px] text-textMuted">
                  若上方未选模型卡，则使用这里的共享凭证 + 各 model。有模型卡时以卡为准（url / key / model 均来自模型卡，勿在前端写死）。
                </p>
                <div>
                  <label className="block text-[10px] font-semibold text-textMuted mb-1">base_url</label>
                  <input
                    value={cfgGet(['voice', 'base_url'], '')}
                    onChange={e => cfgSet(['voice', 'base_url'], e.target.value)}
                    placeholder="https://..."
                    className={inputCls}
                  />
                </div>
                <div>
                  <label className="block text-[10px] font-semibold text-textMuted mb-1">api_key</label>
                  <input
                    type={showApiKey ? 'text' : 'password'}
                    value={cfgGet(['voice', 'api_key'], '')}
                    onChange={e => cfgSet(['voice', 'api_key'], e.target.value)}
                    placeholder="sk-..."
                    className={inputCls}
                  />
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
                  <div>
                    <label className="block text-[10px] font-semibold text-textMuted mb-1">asr_model</label>
                    <input
                      value={cfgGet(['voice', 'asr_model'], '')}
                      onChange={e => cfgSet(['voice', 'asr_model'], e.target.value)}
                      className={inputCls}
                    />
                  </div>
                  <div>
                    <label className="block text-[10px] font-semibold text-textMuted mb-1">tts_model</label>
                    <input
                      value={cfgGet(['voice', 'tts_model'], '')}
                      onChange={e => cfgSet(['voice', 'tts_model'], e.target.value)}
                      className={inputCls}
                    />
                  </div>
                  <div>
                    <label className="block text-[10px] font-semibold text-textMuted mb-1">realtime_model</label>
                    <input
                      value={cfgGet(['voice', 'realtime_model'], '')}
                      onChange={e => cfgSet(['voice', 'realtime_model'], e.target.value)}
                      className={inputCls}
                    />
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>

        {/* ── 系统内置工具（不可关闭） ── */}
        <div>
          <div className={sectionTitleCls}><Shield size={11} /> {t('agentManager.systemBuiltIn')}</div>
          <div className="bg-bgLight rounded-lg p-3">
            <div className="grid grid-cols-2 gap-x-3 gap-y-1.5">
              {SYSTEM_TOOLS.map(tool => (
                <div key={tool} className="flex items-center gap-1.5">
                  <span className="text-xs text-textMain truncate">{tool}</span>
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* ── 核心工具（插件设为 core 级别） ── */}
        <div>
          <div className={sectionTitleCls}><Settings size={11} /> {t('agentManager.coreTools')}</div>
          <div className="bg-bgLight rounded-lg p-3">
            <div className="grid grid-cols-2 gap-x-3 gap-y-1.5">
              {corePluginTools.map(p => (
                <label key={p.name} className="flex items-center gap-1.5 cursor-pointer group" title={p.description}>
                  <input
                    type="checkbox"
                    checked={tools.includes(p.name)}
                    onChange={() => cfgToggleTool(p.name)}
                    className="accent-primary w-3 h-3 shrink-0"
                  />
                  <span className="text-xs text-textMain group-hover:text-primary transition-colors truncate">
                    {p.display_name || p.name}
                  </span>
                </label>
              ))}
            </div>
          </div>
        </div>

        {/* ── 拓展工具（来自 Plugin Manager 的插件） ── */}
        <div>
          <div className={sectionTitleCls}><Wrench size={11} /> {t('agentManager.extendedTools')}</div>
          <div className="bg-bgLight rounded-lg p-3">
            {extendedPluginTools.length === 0 && orphanTools.length === 0 ? (
              <p className="text-xs text-textMuted">{t('agentManager.noEnabledPlugins')}</p>
            ) : (
              <div className="grid grid-cols-2 gap-x-3 gap-y-1.5">
                {extendedPluginTools.map(p => (
                  <label key={p.name} className="flex items-center gap-1.5 cursor-pointer group" title={p.description}>
                    <input
                      type="checkbox"
                      checked={tools.includes(p.name)}
                      onChange={() => cfgToggleTool(p.name)}
                      className="accent-primary w-3 h-3 shrink-0"
                    />
                    <span className="text-xs text-textMain group-hover:text-primary transition-colors truncate">
                      {p.display_name || p.name}
                    </span>
                  </label>
                ))}
                {/* 孤立工具：插件已删除/禁用，但 agent config 中仍残留 */}
                {orphanTools.map(toolName => (
                  <label key={toolName} className="flex items-center gap-1.5 cursor-pointer group opacity-50" title={t('agentManager.pluginDisabledOrNotInstalled')}>
                    <input
                      type="checkbox"
                      checked
                      onChange={() => cfgToggleTool(toolName)}
                      className="accent-primary w-3 h-3 shrink-0"
                    />
                    <span className="text-xs text-amber-500 line-through truncate">{toolName}</span>
                  </label>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* ── 群聊 ── */}
        <div>
          <div className={sectionTitleCls}><MessageSquare size={11} /> {t('agentManager.groupChat')}</div>
          <div className="bg-bgLight rounded-lg p-3 space-y-2.5">
            <div className="flex items-center justify-between">
              <span className="text-xs font-medium text-textMain">{t('agentManager.enableGroupChat')}</span>
              <Toggle value={cfgGet(['group_chat', 'enabled'], true)} onChange={v => cfgSet(['group_chat', 'enabled'], v)} />
            </div>
            <div className="grid grid-cols-2 gap-2">
              <div>
                <label className="block text-[10px] font-semibold text-textMuted mb-1">Email</label>
                <input value={cfgGet(['group_chat', 'email'])} onChange={e => cfgSet(['group_chat', 'email'], e.target.value)} placeholder="agent@ai" className={inputCls} />
              </div>
              <div>
                <label className="block text-[10px] font-semibold text-textMuted mb-1">Password</label>
                <input type="password" value={cfgGet(['group_chat', 'password'])} onChange={e => cfgSet(['group_chat', 'password'], e.target.value)} className={inputCls} />
              </div>
            </div>
            <div>
              <label className="block text-[10px] font-semibold text-textMuted mb-1">Groups <span className="font-normal text-textMuted/60">{t('agentManager.commaSeparated')}</span></label>
              <input
                value={(cfgGet(['group_chat', 'groups'], []) as string[]).join(', ')}
                onChange={e => cfgSet(['group_chat', 'groups'], e.target.value.split(',').map((s: string) => s.trim()).filter(Boolean))}
                placeholder="gzrtrp, abc123"
                className={inputCls}
              />
            </div>
          </div>
        </div>

        {/* ── 服务 ── */}
        <div>
          <div className={sectionTitleCls}><Globe size={11} /> {t('agentManager.services')}</div>
          <div className="bg-bgLight rounded-lg p-3 space-y-3">
            {/* Web Server */}
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <span className="text-xs font-medium text-textMain">Web Server</span>
                <Toggle value={cfgGet(['web_server', 'enabled'], false)} onChange={v => cfgSet(['web_server', 'enabled'], v)} />
              </div>
              <div>
                <label className="block text-[10px] font-semibold text-textMuted mb-1">Port</label>
                <input
                  type="number"
                  value={cfgGet(['web_server', 'port'], '')}
                  onChange={e => cfgSet(['web_server', 'port'], parseInt(e.target.value) || 0)}
                  className={inputCls}
                />
              </div>
            </div>
            {/* Gateway */}
            <div className="space-y-2 pt-3 border-t border-border">
              <div className="flex items-center justify-between">
                <span className="text-xs font-medium text-textMain">Gateway</span>
                <Toggle value={cfgGet(['gateway', 'enabled'], false)} onChange={v => cfgSet(['gateway', 'enabled'], v)} />
              </div>
              <div>
                <label className="block text-[10px] font-semibold text-textMuted mb-1">WebSocket URL</label>
                <input value={cfgGet(['gateway', 'url'])} onChange={e => cfgSet(['gateway', 'url'], e.target.value)} placeholder="ws://127.0.0.1:9555/ai-ws/register" className={inputCls} />
              </div>
            </div>
          </div>
        </div>

        {/* ── 文件系统 ── */}
        <div>
          <div className={sectionTitleCls}><FolderOpen size={11} /> {t('agentManager.filesystemWorkDirs')}</div>
          <div className="bg-bgLight rounded-lg p-3 space-y-2.5">
            <p className="text-[10px] text-textMuted leading-relaxed">
              {t('agentManager.whitelistHint')}
            </p>
            {/* 已有目录列表 */}
            <div className="flex flex-col gap-1.5">
              {(cfgGet(['filesystem', 'workspace_dirs'], []) as string[]).length === 0 && (
                <p className="text-[10px] text-textMuted/60 italic">{t('agentManager.noExtraDirs')}</p>
              )}
              {(cfgGet(['filesystem', 'workspace_dirs'], []) as string[]).map((dir: string, idx: number) => (
                <div key={idx} className="flex items-center gap-1.5 bg-panel border border-border rounded-md px-2.5 py-1.5 group">
                  <FolderOpen size={11} className="text-primary shrink-0" />
                  <span className="flex-1 text-xs text-textMain font-mono truncate" title={dir}>{dir}</span>
                  <button
                    type="button"
                    onClick={() => {
                      const dirs = (cfgGet(['filesystem', 'workspace_dirs'], []) as string[]).filter((_: string, i: number) => i !== idx);
                      cfgSet(['filesystem', 'workspace_dirs'], dirs);
                    }}
                    className="opacity-0 group-hover:opacity-100 text-textMuted hover:text-red-500 transition-all shrink-0"
                  >
                    <X size={12} />
                  </button>
                </div>
              ))}
            </div>
            {/* 添加新目录 */}
            <div className="flex gap-1.5">
              <input
                value={fsNewDir}
                onChange={e => setFsNewDir(e.target.value)}
                onKeyDown={e => {
                  if (e.key === 'Enter' && fsNewDir.trim()) {
                    const dirs = cfgGet(['filesystem', 'workspace_dirs'], []) as string[];
                    if (!dirs.includes(fsNewDir.trim())) {
                      cfgSet(['filesystem', 'workspace_dirs'], [...dirs, fsNewDir.trim()]);
                    }
                    setFsNewDir('');
                  }
                }}
                placeholder={t('agentManager.addDirPlaceholder')}
                className={`${inputCls} font-mono flex-1`}
              />
              <button
                type="button"
                disabled={!fsNewDir.trim()}
                onClick={() => {
                  const dirs = cfgGet(['filesystem', 'workspace_dirs'], []) as string[];
                  if (fsNewDir.trim() && !dirs.includes(fsNewDir.trim())) {
                    cfgSet(['filesystem', 'workspace_dirs'], [...dirs, fsNewDir.trim()]);
                  }
                  setFsNewDir('');
                }}
                className="px-2.5 py-1.5 bg-primary/10 text-primary rounded-lg hover:bg-primary/20 transition-colors disabled:opacity-40 shrink-0"
              >
                <Plus size={14} />
              </button>
            </div>
          </div>
        </div>

        {/* ── 高级 ── */}
        <div>
          <div className={sectionTitleCls}><Monitor size={11} /> {t('agentManager.advanced')}</div>
          <div className="bg-bgLight rounded-lg p-3 space-y-2.5">
            <div className="flex items-center justify-between">
              <span className="text-xs font-medium text-textMain">Wake Mode</span>
              <select
                value={cfgGet(['default_wake_mode'], 'normal')}
                onChange={e => cfgSet(['default_wake_mode'], e.target.value)}
                className="px-2 py-1 bg-panel border border-border rounded-md text-xs text-textMain focus:outline-none focus:ring-1 focus:ring-primary/40"
              >
                <option value="strict">strict</option>
                <option value="normal">normal</option>
              </select>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-xs font-medium text-textMain">Skills</span>
              <Toggle value={cfgGet(['skills', 'enabled'], false)} onChange={v => cfgSet(['skills', 'enabled'], v)} />
            </div>
            {cfgGet(['skills', 'enabled'], false) && (
              <>
              <div>
                <label className="block text-[10px] font-semibold text-textMuted mb-1">{t('agentManager.privateSkillInject')}</label>
                <input
                  value={(cfgGet(['skills', 'active'], []) as string[]).join(', ')}
                  onChange={e => cfgSet(['skills', 'active'], e.target.value.split(',').map((s: string) => s.trim()).filter(Boolean))}
                  placeholder="agent_management"
                  className={inputCls}
                />
              </div>
              <div>
                <label className="block text-[10px] font-semibold text-textMuted mb-1">{t('agentManager.publicSkillInject')}</label>
                <input
                  value={(cfgGet(['prompt_preload', 'full_skills'], []) as string[]).join(', ')}
                  onChange={e => cfgSet(['prompt_preload', 'full_skills'], e.target.value.split(',').map((s: string) => s.trim()).filter(Boolean))}
                  placeholder={t('agentManager.skillPlaceholder')}
                  className={inputCls}
                />
                <p className="text-[10px] text-textMuted/70 mt-1">{t('agentManager.fullContentInjectHint')}</p>
              </div>
              </>
            )}
          </div>
        </div>

        {/* 保存按钮 */}
        <button
          onClick={saveConfig}
          disabled={saving}
          className={`w-full py-2.5 rounded-lg text-sm font-medium transition-all disabled:opacity-50 flex items-center justify-center gap-1.5 ${
            saveSuccess
              ? 'bg-green-500 text-white'
              : 'bg-primary text-white hover:opacity-90'
          }`}
        >
          {saving ? (
            <><Loader2 size={14} className="animate-spin" /> Saving...</>
          ) : saveSuccess ? (
            <><span className="text-base leading-none">&#10003;</span> Saved!</>
          ) : (
            <><Save size={14} /> Save Config</>
          )}
        </button>
      </div>
    );
  };

  // ---- 渲染 ----

  return (
    <div className="flex-1 bg-bgLight flex flex-col overflow-hidden">
      {/* 头部栏 */}
      <div className={`${adminHeaderBar} justify-between`}>
        <div className="flex items-center gap-2 md:gap-2.5">
          <button
            onClick={() => window.dispatchEvent(new CustomEvent('openMobileNav'))}
            className={`${adminHeaderNavBtn} md:hidden`}
            aria-label="Navigation menu"
          >
            <Menu size={16} />
          </button>
          <div className={`hidden md:flex ${adminHeaderIconBox}`}>
            <LayoutGrid size={14} className={adminHeaderIcon} />
          </div>
          <div className="flex items-baseline gap-1.5 shrink-0">
            <h2 className={adminHeaderTitle}>Agent Workstation</h2>
            <p className={adminHeaderSubtitle}>Management Panel</p>
          </div>
        </div>
        <div className="flex items-center gap-1.5">
          <div className="hidden sm:flex items-center gap-2.5 mr-2 text-[10px] text-textMuted/70">
            <span className="flex items-center gap-1"><Circle size={7} className="text-green-500/80 fill-green-500/80" /> {readyCount} ready</span>
            {startingCount > 0 && (
              <span className="flex items-center gap-1"><Circle size={7} className="text-yellow-400/80 fill-yellow-400/80 animate-pulse" /> {startingCount} starting</span>
            )}
            <span>{totalCount} total</span>
          </div>
          <div className="flex items-center rounded-lg border border-border bg-bgLight p-0.5 shrink-0">
            <button
              onClick={() => setLayout('grid')}
              title={t('agentManager.layoutGrid')}
              className={`p-1 rounded-md transition-colors ${
                layoutMode === 'grid' ? 'bg-primary/15 text-primary' : 'text-textMuted hover:text-textMain'
              }`}
            >
              <LayoutGrid size={13} />
            </button>
            <button
              onClick={() => setLayout('list')}
              title={t('agentManager.layoutList')}
              className={`p-1 rounded-md transition-colors ${
                layoutMode === 'list' ? 'bg-primary/15 text-primary' : 'text-textMuted hover:text-textMain'
              }`}
            >
              <List size={13} />
            </button>
          </div>
          <button
            onClick={() => setShowCreate(true)}
            className={adminHeaderCta}
          >
            <Plus size={13} /> New
          </button>
          <button
            onClick={() => { setLoading(true); fetchAgents(); }}
            className={adminHeaderGhostBtn}
          >
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
          </button>
          <button
            onClick={onBack}
            className={`${adminHeaderGhostBtn} px-2.5 text-xs font-medium`}
          >
            Back
          </button>
        </div>
      </div>

      {/* 错误提示 */}
      {error && (
        <div className="mx-4 mt-3 p-3 bg-red-50 border border-red-200 rounded-lg text-red-700 text-sm flex justify-between items-center">
          <span>{error}</span>
          <button onClick={() => setError(null)} className="text-red-400 hover:text-red-600"><X size={16} /></button>
        </div>
      )}

      {/* 工作站矩阵网格 */}
      <div className="flex-1 overflow-y-auto p-4">
        {loading && agents.length === 0 ? (
          <div className="flex items-center justify-center h-64 text-textMuted">
            <Loader2 className="animate-spin mr-2" size={20} /> Loading agents...
          </div>
        ) : agents.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-64 text-textMuted">
            <Bot size={48} className="mb-4 opacity-30" />
            <p className="text-lg font-medium mb-1">No agents found</p>
            <p className="text-sm">Click "New" to create your first agent, or start launcher.py</p>
          </div>
        ) : (
          <div className={
            layoutMode === 'list'
              ? 'grid grid-cols-1 xl:grid-cols-2 gap-1.5'
              : 'grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4'
          }>
            {agents.map(agent => {
              const key = getAgentKey(agent);
              const isRunning = agent.process_status === 'running';
              const isLoading = actionLoading[key] || false;
              const starting = isAgentStarting(agent);
              const ready = isAgentReady(agent);
              const displayName = agent.agent_name || resolveChatName(agent.chat_profile) || key;
              const avatarUrl = resolveChatAvatar(agent.chat_profile);
              const statusLabel = starting
                ? t('agentManager.statusStarting')
                : (STATUS_LABELS[agent.process_status] ? t(STATUS_LABELS[agent.process_status]) : agent.process_status);

              const actionButtons = (
                <>
                  {isRunning ? (
                    <>
                      <button onClick={() => doAction(agent, 'stop')} disabled={isLoading || starting} className={`py-1 px-2 text-[11px] font-medium rounded-md bg-red-50 text-red-600 transition-colors flex items-center justify-center gap-1 ${starting ? 'opacity-40 cursor-not-allowed' : 'hover:bg-red-100'}`}>
                        {isLoading ? <Loader2 size={11} className="animate-spin" /> : <PowerOff size={11} />} Stop
                      </button>
                      <button onClick={() => doAction(agent, 'restart')} disabled={isLoading || starting} className={`py-1 px-2 text-[11px] font-medium rounded-md bg-yellow-50 text-yellow-700 transition-colors flex items-center justify-center gap-1 ${starting ? 'opacity-40 cursor-not-allowed' : 'hover:bg-yellow-100'}`}>
                        <RotateCcw size={11} /> Restart
                      </button>
                    </>
                  ) : agent.process_status !== 'external' ? (
                    <button onClick={() => doAction(agent, 'start')} disabled={isLoading} className="py-1 px-2 text-[11px] font-medium rounded-md bg-green-50 text-green-700 hover:bg-green-100 transition-colors disabled:opacity-50 flex items-center justify-center gap-1">
                      {isLoading ? <Loader2 size={11} className="animate-spin" /> : <Power size={11} />} Start
                    </button>
                  ) : (
                    <span className="py-1 px-2 text-[11px] text-textMuted">External</span>
                  )}
                  {isRunning && onChat && (
                    <button onClick={() => onChat(agent.agent_id)} disabled={starting} className={`py-1 px-2 text-[11px] font-medium rounded-md bg-blue-50 text-blue-600 transition-colors flex items-center justify-center gap-1 ${starting ? 'opacity-40 cursor-not-allowed' : 'hover:bg-blue-100'}`}>
                      <MessageSquare size={11} /> {t('agentManager.chat')}
                    </button>
                  )}
                  <button onClick={() => openDetail(agent, 'config')} className="py-1 px-1.5 text-[11px] rounded-md bg-primary/10 text-primary hover:bg-primary/20 transition-colors" title="Settings">
                    <Settings size={12} />
                  </button>
                  {isRunning && (
                    <button onClick={() => openDetail(agent, 'logs')} className="py-1 px-1.5 text-[11px] rounded-md bg-primary/10 text-primary hover:bg-primary/20 transition-colors" title="Logs">
                      <Terminal size={12} />
                    </button>
                  )}
                  {!isRunning && agent.process_status !== 'external' && (
                    <button onClick={() => handleDelete(agent)} disabled={isLoading} className="py-1 px-1.5 text-[11px] rounded-md bg-red-50 text-red-400 hover:bg-red-100 hover:text-red-600 transition-colors disabled:opacity-50" title="Delete">
                      <Trash2 size={12} />
                    </button>
                  )}
                </>
              );

              // ── Compact list row ──
              if (layoutMode === 'list') {
                return (
                  <div
                    key={key}
                    className={`bg-panel border rounded-lg px-3 py-2 flex items-center gap-3 transition-all hover:border-primary/30 ${
                      ready ? 'border-green-500/30' : starting ? 'border-yellow-400/30' : 'border-border'
                    }`}
                  >
                    {avatarUrl ? (
                      <img src={avatarUrl} alt={displayName} className="w-8 h-8 rounded-lg object-cover shrink-0" loading="lazy" />
                    ) : (
                      <div className={`w-8 h-8 rounded-lg flex items-center justify-center shrink-0 ${ready ? 'bg-green-500/10 text-green-600' : starting ? 'bg-yellow-400/10 text-yellow-600' : 'bg-primary/10 text-primary'}`}>
                        {TYPE_ICONS[agent.agent_type] || <Wrench size={16} />}
                      </div>
                    )}
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-1.5 min-w-0">
                        <h3 className="text-[13px] font-semibold text-textMain truncate leading-tight">{displayName}</h3>
                        <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${getBreathColor(agent)} ${getBreathClass(agent)}`} />
                        <span className="text-[10px] text-textMuted shrink-0">{statusLabel}</span>
                        {(agent.node_label || agent.node_id) && (
                          <span className="text-[9px] px-1.5 py-0.5 bg-primary/10 text-primary rounded-full truncate max-w-[80px] shrink-0" title={agent.node_id}>
                            {agent.node_label || agent.node_id}
                          </span>
                        )}
                      </div>
                      <p className="text-[11px] text-textMuted truncate leading-tight mt-0.5">
                        {agent.agent_type} · {key}
                        {isRunning && !starting ? ` · Load ${agent.load_percent}% · ${agent.today_chats} chats` : ''}
                        {agent.model_card ? ` · ${agent.model_card}` : ''}
                      </p>
                    </div>
                    <div className="flex items-center gap-1 shrink-0">
                      {actionButtons}
                    </div>
                  </div>
                );
              }

              // ── Grid card ──
              return (
                <div
                  key={key}
                  className={`relative bg-panel border rounded-xl p-4 transition-all hover:shadow-lg group ${ready ? 'border-green-500/30 shadow-green-500/5' : starting ? 'border-yellow-400/30 shadow-yellow-400/5' : 'border-border'}`}
                >
                  {/* 状态指示灯 + 节点 badge */}
                  <div className="absolute top-3 right-3 flex flex-col items-end gap-1">
                    <div className="flex items-center gap-1.5">
                      <span className={`w-2.5 h-2.5 rounded-full ${getBreathColor(agent)} ${getBreathClass(agent)}`} />
                      <span className="text-[10px] text-textMuted">{statusLabel}</span>
                    </div>
                    {(agent.node_label || agent.node_id) && (
                      <span className="text-[9px] px-1.5 py-0.5 bg-primary/10 text-primary rounded-full truncate max-w-[90px]" title={agent.node_id}>
                        {agent.node_label || agent.node_id}
                      </span>
                    )}
                  </div>

                  {/* Agent 头像 + 名称 */}
                  <div className="flex items-center gap-3 mb-3">
                    {avatarUrl ? (
                      <img src={avatarUrl} alt={displayName} className="w-10 h-10 rounded-lg object-cover shrink-0" loading="lazy" />
                    ) : (
                      <div className={`w-10 h-10 rounded-lg flex items-center justify-center shrink-0 ${ready ? 'bg-green-500/10 text-green-600' : starting ? 'bg-yellow-400/10 text-yellow-600' : 'bg-primary/10 text-primary'}`}>
                        {TYPE_ICONS[agent.agent_type] || <Wrench size={20} />}
                      </div>
                    )}
                    <div className="min-w-0">
                      <h3 className="font-semibold text-textMain text-sm truncate">{displayName}</h3>
                      <p className="text-[10px] text-textMuted truncate">{agent.agent_type} | {key}</p>
                      {agent.model_card && (
                        <p className="text-[10px] text-primary truncate" title={agent.model_card}>
                          {agent.model_card}
                        </p>
                      )}
                    </div>
                  </div>

                  <p className="text-xs text-textMuted mb-3 line-clamp-2 h-8">
                    {getAgentDescription(agent, i18n.language) || t('agentManager.noDescription')}
                  </p>

                  {isRunning && (
                    <div className="flex items-center gap-3 text-[10px] text-textMuted mb-3">
                      {starting ? (
                        <span className="text-yellow-500 flex items-center gap-1">
                          <Loader2 size={10} className="animate-spin" /> {t('agentManager.statusStarting')}...
                        </span>
                      ) : (
                        <>
                          <span>Load: {agent.load_percent}%</span>
                          <span>Chats: {agent.today_chats}</span>
                        </>
                      )}
                      {agent.pid && <span>PID: {agent.pid}</span>}
                      {agent.restart_count > 0 && <span className="text-yellow-500">Restarts: {agent.restart_count}</span>}
                    </div>
                  )}

                  {isRunning && agent.token_stats && (
                    <div className="mb-3">
                      <div className="flex items-center justify-between text-[10px] text-textMuted mb-1">
                        <span>Context</span>
                        <span>{fmtTokens(agent.token_stats.used)} / {fmtTokens(agent.token_stats.max)}</span>
                      </div>
                      <div className="w-full h-1.5 bg-bgLight rounded-full overflow-hidden">
                        <div
                          className={`h-full rounded-full transition-all duration-500 ${
                            (agent.token_stats.used / agent.token_stats.max) > 0.85 ? 'bg-red-500' :
                            (agent.token_stats.used / agent.token_stats.max) > 0.6 ? 'bg-amber-400' : 'bg-green-500'
                          }`}
                          style={{ width: `${Math.min((agent.token_stats.used / agent.token_stats.max) * 100, 100)}%` }}
                        />
                      </div>
                    </div>
                  )}

                  <div className="flex items-center gap-1.5 pt-2 border-t border-border flex-wrap">
                    {actionButtons}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* ========== 详情面板 ========== */}
      {detailAgent && (
        <div className="fixed inset-0 z-50 flex">
          <div className="flex-1 bg-black/30 backdrop-blur-sm" onClick={closeDetail} />

          <div className="w-full max-w-lg bg-panel border-l border-border h-full flex flex-col shadow-2xl">
            {/* 面板头 */}
            <div className="p-4 border-b border-border flex justify-between items-center shrink-0">
              <div className="flex items-center gap-2">
                {resolveChatAvatar(detailAgent.chat_profile) ? (
                  <img src={resolveChatAvatar(detailAgent.chat_profile)!} alt={resolveChatName(detailAgent.chat_profile)} className="w-8 h-8 rounded-lg object-cover" loading="lazy" />
                ) : (
                  <div className={`w-8 h-8 rounded-lg flex items-center justify-center ${detailAgent.process_status === 'running' ? 'bg-green-500/10 text-green-600' : 'bg-primary/10 text-primary'}`}>
                    {TYPE_ICONS[detailAgent.agent_type] || <Wrench size={18} />}
                  </div>
                )}
                <div>
                  <h3 className="font-semibold text-textMain text-sm">{detailAgent.agent_name || resolveChatName(detailAgent.chat_profile) || getAgentKey(detailAgent)}</h3>
                  <p className="text-[10px] text-textMuted">{getAgentKey(detailAgent)}</p>
                </div>
              </div>
              <button onClick={closeDetail} className="text-textMuted hover:text-textMain"><X size={20} /></button>
            </div>

            {/* Tab 栏 */}
            <div className="flex border-b border-border shrink-0">
              {([
                { key: 'config' as DetailTab, label: 'Config', icon: <Settings size={14} /> },
                { key: 'role' as DetailTab, label: 'Role Prompt', icon: <FileText size={14} /> },
                { key: 'logs' as DetailTab, label: 'Logs', icon: <Terminal size={14} /> },
              ]).map(tab => (
                <button
                  key={tab.key}
                  onClick={() => switchTab(tab.key)}
                  className={`flex-1 py-2.5 text-xs font-medium flex items-center justify-center gap-1 transition-colors
                    ${detailTab === tab.key ? 'text-primary border-b-2 border-primary' : 'text-textMuted hover:text-textMain'}`}
                >
                  {tab.icon} {tab.label}
                </button>
              ))}
            </div>

            {/* Tab 内容 */}
            <div className="flex-1 overflow-y-auto p-4">
              {detailLoading ? (
                <div className="flex items-center justify-center h-32 text-textMuted">
                  <Loader2 className="animate-spin mr-2" size={18} /> Loading...
                </div>
              ) : detailTab === 'config' ? (
                renderConfigForm()
              ) : detailTab === 'role' ? (
                roleEditing ? (
                  /* 编辑模式 */
                  <div className="flex flex-col h-full">
                    <div className="flex items-center justify-between mb-2 shrink-0">
                      <span className="text-xs text-textMuted font-medium">{t('agentManager.editRolePrompt')}</span>
                      <span className="text-[10px] text-textMuted">{t('agentManager.markdownSyntax')}</span>
                    </div>
                    <textarea
                      value={roleText}
                      onChange={e => setRoleText(e.target.value)}
                      className="flex-1 min-h-[420px] w-full bg-bgLight border border-border rounded-lg p-3 font-mono text-xs text-textMain resize-none focus:outline-none focus:ring-2 focus:ring-primary/30"
                      spellCheck={false}
                    />
                    <div className="flex gap-2 mt-3 shrink-0">
                      <button
                        onClick={() => { setRoleText(roleOriginal); setRoleEditing(false); }}
                        className="flex-1 py-2 bg-bgLight border border-border text-textMuted rounded-lg text-sm font-medium hover:text-textMain transition-colors"
                      >
                        Cancel
                      </button>
                      <button
                        onClick={saveRole}
                        disabled={saving}
                        className="flex-1 py-2 bg-primary text-white rounded-lg text-sm font-medium hover:opacity-90 transition-all disabled:opacity-50 flex items-center justify-center gap-1"
                      >
                        {saving ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />} Save
                      </button>
                    </div>
                  </div>
                ) : (
                  /* 预览模式 */
                  <div className="flex flex-col">
                    <div className="flex justify-end mb-3 shrink-0">
                      <button
                        onClick={() => setRoleEditing(true)}
                        className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-primary/10 text-primary rounded-lg hover:bg-primary/20 transition-colors"
                      >
                        <Pencil size={12} /> Edit
                      </button>
                    </div>
                    <div
                      className="prose text-sm text-textMain leading-relaxed ai-markdown"
                      dangerouslySetInnerHTML={{ __html: renderedRole }}
                    />
                  </div>
                )
              ) : (
                /* Logs tab */
                <div className="flex flex-col h-full">
                  <div className="flex justify-between items-center mb-2">
                    <button
                      onClick={() => setAutoRefresh(prev => !prev)}
                      className={`text-xs flex items-center gap-1 px-2 py-1 rounded-md transition-colors ${autoRefresh ? 'bg-green-100 text-green-700' : 'bg-primary/10 text-textMuted hover:text-primary'}`}
                    >
                      <RefreshCw size={12} className={autoRefresh ? 'animate-spin' : ''} />
                      {autoRefresh ? 'Auto ON' : 'Auto OFF'}
                    </button>
                    <button onClick={refreshLogs} className="text-xs text-primary hover:underline flex items-center gap-1">
                      <RefreshCw size={12} /> Refresh
                    </button>
                  </div>
                  <div className="flex-1 min-h-[300px] bg-gray-900 rounded-lg p-3 overflow-y-auto font-mono text-[11px] text-green-300 leading-relaxed">
                    {logs.length === 0 ? (
                      <span className="text-gray-500">No logs available</span>
                    ) : (
                      logs.map((line, i) => (
                        <div key={i} className="whitespace-pre-wrap break-all hover:bg-white/5">{line}</div>
                      ))
                    )}
                    <div ref={logEndRef} />
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* ========== 创建新 Agent 弹窗 ========== */}
      {showCreate && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm">
          <div className="bg-panel rounded-xl border border-border shadow-2xl w-full max-w-md mx-4">
            <div className="p-4 border-b border-border flex justify-between items-center">
              <h3 className="font-semibold text-textMain">Create New Agent</h3>
              <button onClick={() => setShowCreate(false)} className="text-textMuted hover:text-textMain"><X size={18} /></button>
            </div>
            <div className="p-4 space-y-3">
              <div>
                <label className="block text-xs font-semibold text-textMuted mb-1">Directory Name *</label>
                <input value={newName} onChange={e => setNewName(e.target.value)} placeholder="e.g. my_agent" className="w-full px-3 py-2 bg-bgLight border border-border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary/30" />
              </div>
              <div>
                <label className="block text-xs font-semibold text-textMuted mb-1">Type</label>
                <select value={newType} onChange={e => setNewType(e.target.value)} className="w-full px-3 py-2 bg-bgLight border border-border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary/30">
                  <option value="general">General</option>
                  <option value="coder">Coder</option>
                  <option value="writer">Writer</option>
                  <option value="analyst">Analyst</option>
                  <option value="translator">Translator</option>
                </select>
              </div>
              <div>
                <label className="block text-xs font-semibold text-textMuted mb-1">Description</label>
                <textarea value={newDesc} onChange={e => setNewDesc(e.target.value)} placeholder="What does this agent do?" rows={2} className="w-full px-3 py-2 bg-bgLight border border-border rounded-lg text-sm resize-none focus:outline-none focus:ring-2 focus:ring-primary/30" />
              </div>
              <div>
                <label className="block text-xs font-semibold text-textMuted mb-1">Bind Email Account *</label>
                <input
                  value={newEmail}
                  onChange={e => { setNewEmail(e.target.value); setNewEmailUser(null); }}
                  onBlur={handleEmailBlur}
                  placeholder="e.g. agent@ai"
                  type="email"
                  className="w-full px-3 py-2 bg-bgLight border border-border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary/30"
                />
                {emailLookupLoading && (
                  <p className="text-[10px] text-textMuted mt-1 flex items-center gap-1"><Loader2 size={10} className="animate-spin" /> {t('agentManager.searchingAccount')}</p>
                )}
                {!emailLookupLoading && newEmail && newEmailUser && (
                  <p className="text-[10px] text-green-500 mt-1">
                    <Trans i18nKey="agentManager.accountFound" values={{ id: newEmailUser.id, name: newEmailUser.name }}>
                      Account found: ID={{id}}, name=<strong>{{name}}</strong> (will be used as agent_id and agent_name)
                    </Trans>
                  </p>
                )}
                {!emailLookupLoading && newEmail && newEmailUser === null && !emailLookupLoading && (
                  <p className="text-[10px] text-textMuted mt-1">{t('agentManager.emailLookupHint')}</p>
                )}
              </div>
              <div>
                <label className="block text-xs font-semibold text-textMuted mb-1">Account Password *</label>
                <input
                  value={newPassword}
                  onChange={e => setNewPassword(e.target.value)}
                  placeholder={t('agentManager.accountPasswordPlaceholder')}
                  type="password"
                  className="w-full px-3 py-2 bg-bgLight border border-border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary/30"
                />
                <p className="text-[10px] text-textMuted mt-1">{t('agentManager.accountPasswordHint')}</p>
              </div>
            </div>
            <div className="p-4 border-t border-border flex justify-end gap-2">
              <button onClick={() => setShowCreate(false)} className="px-4 py-2 text-sm text-textMuted hover:text-textMain transition-colors">Cancel</button>
              <button
                onClick={handleCreate}
                disabled={creating || !newName.trim() || !newEmail.trim() || !newPassword.trim()}
                className="px-4 py-2 bg-primary text-white rounded-lg text-sm font-medium hover:opacity-90 transition-all disabled:opacity-50 flex items-center gap-1"
              >
                {creating ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />} Create
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
