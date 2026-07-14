import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import {
  ArrowLeft, RefreshCw, Puzzle, Globe, Wrench, Activity, Shield, ShieldCheck,
  ToggleLeft, ToggleRight, Loader2, AlertCircle, Settings,
  Package, Save, Check, BarChart3,
  Search, ArrowUpAZ, ArrowDownAZ, Star, Bot, Eye, EyeOff,
  Briefcase, Code2, MessageSquare, Sparkles, Film, SearchIcon,
  Languages, LineChart, Link2, LayoutTemplate, MoreHorizontal,
  Server, Play, StopCircle, RotateCw, Terminal, ChevronDown, ChevronUp,
  Plus, Trash2, FolderOpen, Menu, Upload, LayoutGrid, List,
} from 'lucide-react';
import * as LucideIcons from 'lucide-react';
import { pluginAPI, pluginServiceAPI, PluginInfo, PluginConfigField, PluginServiceStatus, adminAPI, AdminAgent } from '../services/api';
import { hasPluginViewAdapter } from './plugin-views/registry';
import { PluginViewContainer } from './plugin-views/PluginViewContainer';
import { GenericPluginView } from './plugin-views/GenericPluginView';
import { PluginViewErrorBoundary } from './plugin-views/PluginViewErrorBoundary';
import { useTranslation } from 'react-i18next';
import {
  adminHeaderBar,
  adminHeaderGhostBtn,
  adminHeaderIcon,
  adminHeaderIconBox,
  adminHeaderNavBtn,
  adminHeaderSubtitle,
  adminHeaderTitle,
} from './admin/adminShellStyles';

// ---- Props ----

interface PluginManagerPageProps {
  onBack: () => void;
}

// ---- Helpers ----

const TYPE_COLORS: Record<string, string> = {
  platform: 'bg-blue-500/15 text-blue-400 border-blue-500/30',
  tool:     'bg-emerald-500/15 text-emerald-400 border-emerald-500/30',
  hook:     'bg-purple-500/15 text-purple-400 border-purple-500/30',
};

const TYPE_ICONS: Record<string, React.ReactNode> = {
  platform: <Globe size={16} />,
  tool:     <Wrench size={16} />,
  hook:     <Activity size={16} />,
};

const AVATAR_COLORS = [
  'bg-violet-500', 'bg-blue-500', 'bg-emerald-500', 'bg-rose-500',
  'bg-amber-500',  'bg-cyan-500',  'bg-pink-500',   'bg-indigo-500',
];

function avatarColor(id: string): string {
  let hash = 0;
  for (let i = 0; i < id.length; i++) hash = (hash * 31 + id.charCodeAt(i)) >>> 0;
  return AVATAR_COLORS[hash % AVATAR_COLORS.length];
}

/**
 * 检查插件是否有可用的 UI 视图（能被 registry 解析的 view key）。
 * 显式配置了 navigation.view 且合法，或声明了 contributes.views 条目，才允许加入导航。
 */
function pluginHasNavView(plugin: PluginInfo): boolean {
  const nav = plugin.contributes?.navigation;
  if (nav) return hasPluginViewAdapter(nav.view);
  return !!(plugin.contributes?.views && plugin.contributes.views.length > 0);
}

function getPluginIcon(plugin: PluginInfo, compact = false): React.ReactNode {
  const box = compact ? 'w-8 h-8 rounded-lg' : 'w-10 h-10 rounded-xl';
  const iconSize = compact ? 16 : 20;
  const letterSize = compact ? 'text-sm' : 'text-base';
  const nav = plugin.contributes?.navigation;
  if (nav?.iconType === 'image' && nav.iconUrl) {
    return (
      <img
        src={nav.iconUrl}
        alt={plugin.display_name || plugin.name}
        className={`${box} object-cover shrink-0`}
        loading="lazy"
      />
    );
  }
  if (nav?.icon) {
    const Icon = (LucideIcons as any)[nav.icon] as React.FC<{ size?: number; className?: string }> | undefined;
    if (Icon) {
      return (
        <div className={`${box} bg-primary/15 flex items-center justify-center shrink-0`}>
          <Icon size={iconSize} className="text-primary" />
        </div>
      );
    }
  }
  // Fallback: colored letter
  return (
    <div className={`${box} flex items-center justify-center text-white ${letterSize} font-bold shrink-0 ${avatarColor(plugin.name)}`}>
      {(plugin.display_name || plugin.name).charAt(0).toUpperCase()}
    </div>
  );
}

const TYPE_LABELS: Record<string, string> = {
  platform: 'pluginManager.typePlatform',
  tool:     'pluginManager.typeTool',
  hook:     'pluginManager.typeHook',
};

const FAVORITES_KEY = 'plugin_manager_favorites';
const LAYOUT_KEY = 'plugin_manager_layout';

type PluginLayoutMode = 'grid' | 'list';

function loadLayoutMode(): PluginLayoutMode {
  try {
    const raw = localStorage.getItem(LAYOUT_KEY);
    return raw === 'list' ? 'list' : 'grid';
  } catch {
    return 'grid';
  }
}

// ---- Categories ----

interface CategoryDef {
  id: string;
  label: string;
  icon: React.ReactNode;
}

const CATEGORIES: CategoryDef[] = [
  { id: 'all',           label: 'pluginManager.categoryAll',           icon: <Puzzle size={14} /> },
  { id: 'productivity',  label: 'pluginManager.categoryProductivity',  icon: <Briefcase size={14} /> },
  { id: 'development',   label: 'pluginManager.categoryDevelopment',   icon: <Code2 size={14} /> },
  { id: 'communication', label: 'pluginManager.categoryCommunication', icon: <MessageSquare size={14} /> },
  { id: 'ai',            label: 'pluginManager.categoryAI',            icon: <Sparkles size={14} /> },
  { id: 'media',         label: 'pluginManager.categoryMedia',         icon: <Film size={14} /> },
  { id: 'search',        label: 'pluginManager.categorySearch',        icon: <SearchIcon size={14} /> },
  { id: 'language',      label: 'pluginManager.categoryLanguage',      icon: <Languages size={14} /> },
  { id: 'analytics',     label: 'pluginManager.categoryAnalytics',     icon: <LineChart size={14} /> },
  { id: 'integration',   label: 'pluginManager.categoryIntegration',   icon: <Link2 size={14} /> },
  { id: 'demo',          label: 'pluginManager.categoryDemo',          icon: <LayoutTemplate size={14} /> },
  { id: 'other',         label: 'pluginManager.categoryOther',         icon: <MoreHorizontal size={14} /> },
];

function loadFavorites(): Set<string> {
  try {
    const raw = localStorage.getItem(FAVORITES_KEY);
    return raw ? new Set(JSON.parse(raw)) : new Set();
  } catch {
    return new Set();
  }
}

function saveFavorites(favs: Set<string>) {
  localStorage.setItem(FAVORITES_KEY, JSON.stringify([...favs]));
}

// System built-in tools: always registered, per-agent toggle hidden
const SYSTEM_TOOLS = [
  'system', 'filesystem', 'agent_setup', 'im',
  'collaboration', 'delegate_task', 'workspace', 'task_watch',
  'websearch', 'reminder', 'vision', 'mcp_query', 'plugin_admin',
];

// ---- Main Component ----

export const PluginManagerPage: React.FC<PluginManagerPageProps> = ({ onBack }) => {
  const { t: tr } = useTranslation();
  const [plugins, setPlugins]   = useState<PluginInfo[]>([]);
  const [loading, setLoading]   = useState(true);
  const [error, setError]       = useState<string | null>(null);
  const [toggling, setToggling] = useState<Record<string, boolean>>({});
  const [uninstallTarget, setUninstallTarget] = useState<PluginInfo | null>(null);
  const [uninstalling, setUninstalling] = useState(false);
  const [filter, setFilter]     = useState<'all' | 'builtin' | 'platform' | 'tool' | 'hook' | 'starred'>('all');
  const [configOpen, setConfigOpen] = useState<string | null>(null);
  const [activeView, setActiveView] = useState<string | null>(null);

  // Category filter
  const [selectedCategory, setSelectedCategory] = useState<string>('all');
  const [categoryDropdownOpen, setCategoryDropdownOpen] = useState(false);
  const categoryBtnRef = useRef<HTMLButtonElement>(null);
  const [categoryDropdownPos, setCategoryDropdownPos] = useState<{ top: number; left: number } | null>(null);

  // Mobile agent dropdown
  const [agentDropdownOpen, setAgentDropdownOpen] = useState(false);
  const agentBtnRef = useRef<HTMLButtonElement>(null);
  const [agentDropdownPos, setAgentDropdownPos] = useState<{ top: number; left: number } | null>(null);

  // Search & sort
  const [search, setSearch] = useState('');
  const [sortAZ, setSortAZ] = useState(false);

  // Layout: grid (cards) | list (compact rows) — persisted
  const [layoutMode, setLayoutMode] = useState<PluginLayoutMode>(loadLayoutMode);
  const setLayout = useCallback((mode: PluginLayoutMode) => {
    setLayoutMode(mode);
    try { localStorage.setItem(LAYOUT_KEY, mode); } catch { /* ignore */ }
  }, []);

  // Favorites — persisted in localStorage
  const [favorites, setFavorites] = useState<Set<string>>(loadFavorites);

  const toggleFavorite = useCallback((name: string) => {
    setFavorites(prev => {
      const next = new Set<string>(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      saveFavorites(next);
      return next;
    });
  }, []);

  // Upload handler
  const [uploading, setUploading] = useState(false);
  const folderInputRef = useRef<HTMLInputElement>(null);

  const handleUploadClick = () => {
    folderInputRef.current?.click();
  };

  const handleFolderChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (!files || files.length === 0) return;

    const fileList: { file: File; path: string }[] = [];
    for (let i = 0; i < files.length; i++) {
      const file = files[i];
      const path = file.webkitRelativePath || file.name;
      fileList.push({ file, path });
    }

    setUploading(true);
    try {
      // @ts-ignore - directory attribute is not standard but supported
      await pluginAPI.uploadPlugin(fileList);
      // alert('Plugin uploaded successfully!'); // Avoid alert if possible, or use a toast if available. For now alert is fine.
      await fetchPlugins();
    } catch (e: any) {
      alert(`Upload failed: ${e.message}`);
    } finally {
      setUploading(false);
      if (folderInputRef.current) {
        folderInputRef.current.value = '';
      }
    }
  };

  // ---- Per-agent plugin state ----

  const [agents, setAgents]                 = useState<AdminAgent[]>([]);
  const [loadingAgents, setLoadingAgents]   = useState(true);
  const [selectedAgent, setSelectedAgent]   = useState<string>('');
  // agentConfig 持有完整 config.json，agentTools 是 tools 数组的 Set（用于快速查找）
  const [agentConfig, setAgentConfig]       = useState<Record<string, any> | null>(null);
  const [agentTools, setAgentTools]         = useState<Set<string> | null>(null);
  const [agentToolLevels, setAgentToolLevels] = useState<Record<string, 'core' | 'extended'>>({});
  const [agentHiddenPlugins, setAgentHiddenPlugins] = useState<Set<string>>(new Set());
  const [agentToolsDirty, setAgentToolsDirty] = useState(false);
  const [savingTools, setSavingTools]       = useState(false);
  const [saveToolsOk, setSaveToolsOk]       = useState(false);

  // 加载 agent 列表
  useEffect(() => {
    (async () => {
      try {
        const data = await adminAPI.getAgents();
        setAgents(data.agents);
        if (data.agents.length > 0) setSelectedAgent(data.agents[0].dir_name);
      } catch {
        // agent 列表加载失败不影响全局插件功能
      } finally {
        setLoadingAgents(false);
      }
    })();
  }, []);

  // 选中 agent 后加载其 config.json
  useEffect(() => {
    if (!selectedAgent) return;
    setAgentConfig(null);
    setAgentTools(null);
    setAgentToolsDirty(false);
    setSaveToolsOk(false);
    (async () => {
      try {
        const data = await adminAPI.getConfig(selectedAgent);
        const cfg = data.config || {};
        setAgentConfig(cfg);
        setAgentTools(new Set<string>((cfg.tools as string[]) || []));
        setAgentToolLevels((cfg.tool_levels as Record<string, 'core' | 'extended'>) || {});
        setAgentHiddenPlugins(new Set<string>((cfg.prompt_preload?.hidden_plugins as string[]) || []));
      } catch {
        setAgentConfig(null);
        setAgentTools(null);
      }
    })();
  }, [selectedAgent, agents]);

  // toggle 某个插件是否被该 agent 加载
  const toggleAgentTool = useCallback((pluginName: string) => {
    setAgentTools(prev => {
      if (!prev) return prev;
      const next = new Set(prev);
      if (next.has(pluginName)) next.delete(pluginName);
      else next.add(pluginName);
      setAgentToolsDirty(true);
      setSaveToolsOk(false);
      return next;
    });
  }, []);

  // 设置某个插件的注入级别（core / extended / hidden）
  const setToolLevel = useCallback((pluginName: string, level: 'core' | 'extended' | 'hidden') => {
    if (level === 'hidden') {
      setAgentHiddenPlugins(prev => {
        const next = new Set(prev);
        next.add(pluginName);
        return next;
      });
      // Write tool_levels[pluginName]="hidden" so the backend ToolRegistry
      // (generate_openai_tools / generate_tool_descriptions) skips this
      // namespace's schema entirely — saving per-turn input tokens.
      // The companion hidden_plugins set below still controls prompt_preload
      // skill injection, which is an orthogonal concern.
      setAgentToolLevels(prev => ({ ...prev, [pluginName]: 'hidden' }));
    } else {
      setAgentHiddenPlugins(prev => {
        const next = new Set(prev);
        next.delete(pluginName);
        return next;
      });
      setAgentToolLevels(prev => ({ ...prev, [pluginName]: level }));
    }
    setAgentToolsDirty(true);
    setSaveToolsOk(false);
  }, []);

  // 保存 agent tools 到 config.json
  const saveAgentTools = useCallback(async () => {
    if (!selectedAgent || !agentConfig || !agentTools) return;
    setSavingTools(true);
    setSaveToolsOk(false);
    try {
      const promptPreload = {
        ...(agentConfig.prompt_preload || {}),
        hidden_plugins: [...agentHiddenPlugins],
      };
      const newConfig = { ...agentConfig, tools: [...agentTools], tool_levels: agentToolLevels, prompt_preload: promptPreload };
      await adminAPI.updateConfig(selectedAgent, newConfig);
      setAgentConfig(newConfig);
      setAgentToolsDirty(false);
      setSaveToolsOk(true);
      setTimeout(() => setSaveToolsOk(false), 2000);
    } catch (e: any) {
      alert(tr('pluginManager.saveFailedMsg', { error: e.message }));
    } finally {
      setSavingTools(false);
    }
  }, [selectedAgent, agentConfig, agentTools, agentToolLevels, agentHiddenPlugins]);

  // ---- Data loading ----

  const fetchPlugins = useCallback(async () => {
    try {
      setError(null);
      const data = await pluginAPI.getPlugins();
      setPlugins(data.plugins || []);
    } catch (e: any) {
      setError(e.message || 'Failed to load plugins');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchPlugins(); }, [fetchPlugins]);

  // 监听市场安装事件，自动刷新插件列表
  useEffect(() => {
    const handler = () => fetchPlugins();
    window.addEventListener('opensquad:market-install', handler);
    return () => window.removeEventListener('opensquad:market-install', handler);
  }, [fetchPlugins]);

  // ---- Toggle enabled ----

  const togglePlugin = async (plugin: PluginInfo) => {
    setToggling(prev => ({ ...prev, [plugin.name]: true }));
    try {
      if (plugin.enabled) await pluginAPI.disablePlugin(plugin.name);
      else                 await pluginAPI.enablePlugin(plugin.name);
      await fetchPlugins();
    } catch (e: any) {
      alert(`Toggle failed: ${e.message}`);
    } finally {
      setToggling(prev => ({ ...prev, [plugin.name]: false }));
    }
  };

  // ---- Uninstall ----

  const handleUninstall = useCallback((plugin: PluginInfo) => {
    setUninstallTarget(plugin);
  }, []);

  const confirmUninstall = useCallback(async () => {
    if (!uninstallTarget) return;
    setUninstalling(true);
    try {
      await pluginAPI.uninstall(uninstallTarget.name);
      setUninstallTarget(null);
      setLoading(true);
      await fetchPlugins();
    } catch (e: any) {
      alert(tr('pluginManager.uninstallFailedMsg', { error: e.message }));
    } finally {
      setUninstalling(false);
    }
  }, [uninstallTarget, fetchPlugins]);

  // ---- Filter + search + sort ----

  const filtered = useMemo(() => {
    let result = plugins;

    // category filter
    if (selectedCategory !== 'all') {
      const knownCats = new Set(CATEGORIES.map(c => c.id).filter(id => id !== 'all' && id !== 'other'));
      if (selectedCategory === 'other') {
        result = result.filter(p => !p.category || !knownCats.has(p.category));
      } else {
        result = result.filter(p => p.category === selectedCategory);
      }
    }

    // type / starred / builtin filter
    // "内置" tab: only true system tools (SYSTEM_TOOLS)
    // "工具"/"平台"/"钩子" tabs: exclude SYSTEM_TOOLS built-in plugins
    if (filter === 'builtin')       result = result.filter(p => !!p.builtin && SYSTEM_TOOLS.includes(p.name));
    else if (filter === 'starred')  result = result.filter(p => favorites.has(p.name));
    else if (filter !== 'all')      result = result.filter(p => p.type === filter && !(SYSTEM_TOOLS.includes(p.name) && !!p.builtin));
    // "all" shows everything (no exclusion)

    // search
    const q = search.trim().toLowerCase();
    if (q) {
      result = result.filter(p =>
        (p.display_name || p.name).toLowerCase().includes(q) ||
        p.name.toLowerCase().includes(q) ||
        (p.description || '').toLowerCase().includes(q)
      );
    }

    // sort
    if (sortAZ) {
      result = [...result].sort((a, b) =>
        (a.display_name || a.name).localeCompare(b.display_name || b.name)
      );
    }

    return result;
  }, [plugins, filter, search, sortAZ, favorites, selectedCategory]);

  const counts = useMemo(() => {
    const base = {
      all:      plugins.filter(p => !(SYSTEM_TOOLS.includes(p.name) && !!p.builtin)).length,
      builtin:  plugins.filter(p => !!p.builtin && SYSTEM_TOOLS.includes(p.name)).length,
      starred:  plugins.filter(p => favorites.has(p.name)).length,
      platform: plugins.filter(p => p.type === 'platform' && !(SYSTEM_TOOLS.includes(p.name) && !!p.builtin)).length,
      tool:     plugins.filter(p => p.type === 'tool' && !(SYSTEM_TOOLS.includes(p.name) && !!p.builtin)).length,
      hook:     plugins.filter(p => p.type === 'hook' && !(SYSTEM_TOOLS.includes(p.name) && !!p.builtin)).length,
    };
    const catCounts: Record<string, number> = { all: plugins.length };
    const knownCats = new Set(CATEGORIES.map(c => c.id).filter(id => id !== 'all' && id !== 'other'));
    CATEGORIES.forEach(cat => {
      if (cat.id === 'all') return;
      if (cat.id === 'other') {
        catCounts['other'] = plugins.filter(p => !p.category || !knownCats.has(p.category)).length;
      } else {
        catCounts[cat.id] = plugins.filter(p => p.category === cat.id).length;
      }
    });
    return { ...base, cat: catCounts };
  }, [plugins, favorites]);

  // ---- Render ----

  if (activeView) {
    if (hasPluginViewAdapter(activeView)) {
      return (
        <PluginViewErrorBoundary viewKey={activeView} onBack={() => setActiveView(null)}>
          <PluginViewContainer viewKey={activeView} onBack={() => setActiveView(null)} />
        </PluginViewErrorBoundary>
      );
    }
    const [pluginName] = activeView.split(':');
    const plugin = plugins.find(p => p.name === pluginName);
    const view   = plugin?.contributes?.views?.find(v => `${pluginName}:${v.name}` === activeView);
    return (
      <GenericPluginView
        pluginName={pluginName}
        viewTitle={view?.title || pluginName}
        onBack={() => setActiveView(null)}
      />
    );
  }

  return (
    <>
    <div className="flex-1 h-full bg-bgLight flex flex-col w-full max-w-full overflow-hidden">

      {/* Header */}
      <div className={`${adminHeaderBar} gap-2 md:gap-3 max-w-full`}>
        <button
          onClick={onBack}
          className={adminHeaderNavBtn}
        >
          <ArrowLeft size={16} />
        </button>
        <button
          onClick={() => window.dispatchEvent(new CustomEvent('openMobileNav'))}
          className={`${adminHeaderNavBtn} md:hidden`}
          aria-label="Navigation menu"
        >
          <Menu size={16} />
        </button>
        <div className="flex items-center gap-2 flex-1 min-w-0">
          <div className={adminHeaderIconBox}>
            <Puzzle className={`${adminHeaderIcon} w-3.5 h-3.5`} />
          </div>
          <div className="flex items-baseline gap-1.5 shrink-0">
            <h1 className={adminHeaderTitle}>{tr('pluginManager.title')}</h1>
            <p className={adminHeaderSubtitle}>
              {plugins.length} {tr('pluginManager.title', '插件')} / {plugins.filter(p => p.enabled).length} {tr('pluginManager.enabled', '已启用')}
            </p>
          </div>
        </div>

        {/* Mobile inline search */}
        <div className="relative w-[90px] shrink-0 md:hidden">
          <Search size={12} className="absolute left-2 top-1/2 -translate-y-1/2 text-textMuted pointer-events-none" />
          <input
            type="text"
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder={tr('pluginManager.searchMobile')}
            className="w-full pl-6 pr-2 py-1 rounded-lg text-[11px] bg-bgLight border border-border text-textMain placeholder-textMuted focus:outline-none focus:border-primary/50 transition-colors"
          />
        </div>
        <button
          onClick={handleUploadClick}
          className="p-1.5 md:p-2 rounded-lg text-textMuted hover:bg-primary/10 hover:text-primary transition-colors shrink-0"
          title="Upload Plugin"
          disabled={uploading}
        >
          {uploading ? (
            <Loader2 size={16} className="animate-spin md:w-[18px] md:h-[18px]" />
          ) : (
            <Upload size={16} className="md:w-[18px] md:h-[18px]" />
          )}
        </button>
        <button
          onClick={() => { setLoading(true); fetchPlugins(); }}
          className="p-1.5 md:p-2 rounded-lg text-textMuted hover:bg-primary/10 hover:text-primary transition-colors shrink-0"
          title="Refresh"
        >
          <RefreshCw size={16} className={`md:w-[18px] md:h-[18px] ${loading ? 'animate-spin' : ''}`} />
        </button>
      </div>

      {/* Control row: Agent + Filters + Search (single row) */}
      <div className="flex shrink-0 border-b border-border bg-panel/50 items-center">
        <div className="w-44 shrink-0 border-r border-border hidden md:flex items-center px-3 py-2">
          <span className="text-[10px] font-bold text-textMuted uppercase tracking-wider">Agent</span>
        </div>
        <div className="flex-1 min-w-0 flex items-center gap-1 px-2 py-2 flex-nowrap overflow-x-auto whitespace-nowrap">
          {/* Mobile: agent dropdown button */}
          <div className="relative shrink-0 md:hidden">
            <button
              ref={agentBtnRef}
              onClick={(e) => {
                e.stopPropagation();
                if (!agentDropdownOpen && agentBtnRef.current) {
                  const rect = agentBtnRef.current.getBoundingClientRect();
                  setAgentDropdownPos({ top: rect.bottom + 4, left: Math.min(rect.left, window.innerWidth - 200) });
                }
                setAgentDropdownOpen(v => !v);
              }}
              className="flex items-center gap-1.5 px-2 py-1 rounded-lg bg-bgLight border border-border text-[11px] font-medium text-textMain hover:border-primary/50 transition-colors"
            >
              <Bot size={12} className="text-primary shrink-0" />
              <span className="max-w-[80px] truncate">
                {agents.find(a => a.dir_name === selectedAgent)?.agent_name || selectedAgent || 'Agent'}
              </span>
              <ChevronDown size={12} className="text-textMuted shrink-0" />
            </button>
          </div>
          <div className="flex items-center gap-1 flex-nowrap">
            {(['all', 'builtin', 'starred', 'platform', 'tool', 'hook'] as const).map(t => (
              <button
                key={t}
                onClick={() => setFilter(t)}
                className={`inline-flex items-center gap-1 px-2 py-1 rounded-lg text-[11px] font-medium transition-colors ${
                  filter === t
                    ? 'bg-primary/15 text-primary'
                    : 'text-textMuted hover:bg-primary/5 hover:text-textMain'
                }`}
              >
                {t === 'builtin' && <ShieldCheck size={11} className={filter === 'builtin' ? 'text-primary' : 'text-textMuted'} />}
                {t === 'starred' && <Star size={11} className={filter === 'starred' ? 'fill-primary text-primary' : 'text-textMuted'} />}
                {t === 'all' ? tr('pluginManager.filterAll') : t === 'builtin' ? tr('pluginManager.filterBuiltin') : t === 'starred' ? tr('pluginManager.filterStarred') : (TYPE_LABELS[t] ? tr(TYPE_LABELS[t]).replace(tr('pluginManager.title'), '') : t)}
                <span className={`text-[9px] hidden sm:inline ${filter === t ? 'text-primary/70' : 'text-textMuted/50'}`}>
                  {counts[t]}
                </span>
              </button>
            ))}
          </div>

          <div className="relative shrink-0">
            <button
              ref={categoryBtnRef}
              onClick={(e) => {
                e.stopPropagation();
                if (!categoryDropdownOpen && categoryBtnRef.current) {
                  const rect = categoryBtnRef.current.getBoundingClientRect();
                  setCategoryDropdownPos({ top: rect.bottom + 4, left: Math.min(rect.left, window.innerWidth - 200) });
                }
                setCategoryDropdownOpen(v => !v);
              }}
              className="flex items-center gap-1.5 px-2 py-1 rounded-lg bg-bgLight border border-border text-[11px] font-medium text-textMain hover:border-primary/50 transition-colors"
            >
              <span className="text-primary">
                {CATEGORIES.find(c => c.id === selectedCategory)?.icon}
              </span>
              <span className="hidden sm:inline">{tr(CATEGORIES.find(c => c.id === selectedCategory)?.label ?? '')}</span>
              <ChevronDown size={12} className="text-textMuted" />
            </button>
          </div>

          {/* Search + Sort + Save */}
          <div className="relative w-40 shrink-0 hidden md:block">
            <Search size={12} className="absolute left-2 top-1/2 -translate-y-1/2 text-textMuted pointer-events-none" />
            <input
              type="text"
              value={search}
              onChange={e => setSearch(e.target.value)}
              placeholder="Search"
              className="w-full pl-6 pr-2 py-1 rounded-lg text-[11px] bg-bgLight border border-border text-textMain placeholder-textMuted focus:outline-none focus:border-primary/50 transition-colors"
            />
          </div>
          <button
            onClick={() => setSortAZ(v => !v)}
            title={tr('pluginManager.sortAZTitle')}
            className={`p-1 rounded-lg transition-colors shrink-0 ${
              sortAZ ? 'bg-primary/15 text-primary' : 'bg-bgLight border border-border text-textMuted'
            }`}
          >
            {sortAZ ? <ArrowUpAZ size={13} /> : <ArrowDownAZ size={13} />}
          </button>
          {/* Layout toggle: grid / list */}
          <div className="flex items-center rounded-lg border border-border bg-bgLight p-0.5 shrink-0">
            <button
              onClick={() => setLayout('grid')}
              title={tr('pluginManager.layoutGrid')}
              className={`p-1 rounded-md transition-colors ${
                layoutMode === 'grid' ? 'bg-primary/15 text-primary' : 'text-textMuted hover:text-textMain'
              }`}
            >
              <LayoutGrid size={13} />
            </button>
            <button
              onClick={() => setLayout('list')}
              title={tr('pluginManager.layoutList')}
              className={`p-1 rounded-md transition-colors ${
                layoutMode === 'list' ? 'bg-primary/15 text-primary' : 'text-textMuted hover:text-textMain'
              }`}
            >
              <List size={13} />
            </button>
          </div>
          {agentToolsDirty && (
            <button
              onClick={saveAgentTools}
              disabled={savingTools}
              className={`flex items-center gap-1 px-2 py-1 rounded-lg text-[11px] font-medium transition-colors shrink-0 ${
                saveToolsOk ? 'bg-emerald-500/15 text-emerald-400' : 'bg-primary text-white'
              }`}
            >
              {savingTools ? <Loader2 size={11} className="animate-spin" /> : saveToolsOk ? <Check size={11} /> : <Save size={11} />}
              <span className="hidden sm:inline">{saveToolsOk ? tr('pluginManager.saved') : tr('pluginManager.save')}</span>
            </button>
          )}
        </div>
      </div>

      {/* Body: left agent panel + right plugin list */}
      <div className="flex flex-1 min-h-0 w-full max-w-full">

        {/* Left: agent selector */}
        <div className="w-44 shrink-0 border-r border-border bg-panel flex-col overflow-y-auto hidden md:flex">
          {loadingAgents ? (
            <div className="flex items-center justify-center py-6 text-textMuted">
              <Loader2 size={16} className="animate-spin" />
            </div>
          ) : (
            agents.map(agent => (
              <button
                key={agent.dir_name}
                onClick={() => setSelectedAgent(agent.dir_name)}
                className={`w-full text-left px-3 py-2 text-xs transition-colors border-b border-border/40 flex items-start gap-1.5 ${
                  selectedAgent === agent.dir_name
                    ? 'bg-primary/10 text-primary'
                    : 'text-textMain hover:bg-primary/5'
                }`}
              >
                <Bot size={12} className="mt-0.5 shrink-0" />
                <div className="min-w-0">
                  <div className="font-medium truncate leading-tight">{agent.agent_name}</div>
                  <div className="text-[10px] text-textMuted truncate leading-tight mt-0.5">{agent.dir_name}</div>
                </div>
              </button>
            ))
          )}
        </div>

        {/* Right: plugin list */}
        <div className="flex-1 min-w-0 flex flex-col relative w-full max-w-full overflow-hidden">

          {/* Content */}
          <div className="flex-1 min-h-0 overflow-y-auto p-4 md:p-6 w-full">
            {loading ? (
              <div className="flex flex-col items-center justify-center h-64 gap-3">
                <Loader2 className="animate-spin text-primary" size={32} />
                <p className="text-textMuted text-sm">Loading plugins...</p>
              </div>
            ) : error ? (
              <div className="flex flex-col items-center justify-center h-64 gap-3">
                <AlertCircle className="text-red-400" size={32} />
                <p className="text-red-400 text-sm">{error}</p>
                <button
                  onClick={() => { setLoading(true); fetchPlugins(); }}
                  className="text-xs text-primary hover:underline"
                >
                  Retry
                </button>
              </div>
            ) : filtered.length === 0 ? (
              <div className="flex flex-col items-center justify-center h-64 gap-3">
                <Package className="text-textMuted" size={32} />
                <p className="text-textMuted text-sm">
                  {search ? `No plugins match "${search}"` : 'No plugins found'}
                </p>
              </div>
            ) : (
              <div className={
                layoutMode === 'list'
                  ? 'grid grid-cols-1 xl:grid-cols-2 gap-1.5'
                  : 'grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 gap-3 md:gap-4'
              }>
                {filtered.map(plugin => (
                  <PluginCard
                    key={plugin.name}
                    plugin={plugin}
                    layout={layoutMode}
                    toggling={toggling[plugin.name] || false}
                    onToggle={() => togglePlugin(plugin)}
                    configOpen={configOpen === plugin.name}
                    onConfigToggle={() => setConfigOpen(configOpen === plugin.name ? null : plugin.name)}
                    onOpenView={(viewName) => setActiveView(`${plugin.name}:${viewName}`)}
                    starred={favorites.has(plugin.name)}
                    onToggleStar={() => toggleFavorite(plugin.name)}
                    agentLoaded={agentTools ? agentTools.has(plugin.name) : null}
                    onAgentToggle={() => toggleAgentTool(plugin.name)}
                    agentToolLevel={agentHiddenPlugins.has(plugin.name) ? 'hidden' : (agentToolLevels[plugin.name] || 'extended')}
                    onToolLevelChange={(level) => setToolLevel(plugin.name, level)}
                    onUninstall={() => handleUninstall(plugin)}
                  />
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>

      {uninstallTarget && (
        <UninstallConfirmDialog
          plugin={uninstallTarget}
          uninstalling={uninstalling}
          onConfirm={confirmUninstall}
          onCancel={() => setUninstallTarget(null)}
        />
      )}

      {/* Mobile Agent Dropdown (Moved to avoid clipping) */}
      {agentDropdownOpen && agentDropdownPos && (
        <>
          <div className="fixed inset-0 z-[100]" onClick={() => setAgentDropdownOpen(false)} />
          <div
            className="fixed w-48 bg-panel border border-border rounded-xl shadow-2xl z-[101] py-1 max-h-[60vh] overflow-y-auto flex flex-col whitespace-normal"
            style={{ top: agentDropdownPos.top, left: agentDropdownPos.left }}
          >
            {agents.map(agent => (
              <button
                key={agent.dir_name}
                onClick={() => { setSelectedAgent(agent.dir_name); setAgentDropdownOpen(false); }}
                className={`w-full flex items-start gap-2 px-4 py-2 text-xs transition-colors ${
                  selectedAgent === agent.dir_name
                    ? 'bg-primary/10 text-primary font-medium'
                    : 'text-textMain hover:bg-primary/5'
                }`}
              >
                <Bot size={12} className="mt-0.5 shrink-0" />
                <div className="min-w-0 text-left">
                  <div className="font-medium truncate">{agent.agent_name}</div>
                  <div className="text-[10px] text-textMuted truncate">{agent.dir_name}</div>
                </div>
              </button>
            ))}
          </div>
        </>
      )}

      {/* Category Dropdown (Moved to avoid clipping) */}
      {categoryDropdownOpen && categoryDropdownPos && (
        <>
          <div className="fixed inset-0 z-[100]" onClick={() => setCategoryDropdownOpen(false)} />
          <div
            className="fixed w-48 bg-panel border border-border rounded-xl shadow-2xl z-[101] py-1 max-h-[60vh] overflow-y-auto flex flex-col whitespace-normal"
            style={{ top: categoryDropdownPos.top, left: categoryDropdownPos.left }}
          >
            {CATEGORIES.map(cat => (
              <button
                key={cat.id}
                onClick={() => {
                  setSelectedCategory(cat.id);
                  setCategoryDropdownOpen(false);
                }}
                className={`w-full flex items-center justify-between px-4 py-2 text-xs transition-colors ${
                  selectedCategory === cat.id
                    ? 'bg-primary/10 text-primary font-medium'
                    : 'text-textMain hover:bg-bgLight'
                }`}
              >
                <div className="flex items-center gap-2">
                  <span className={selectedCategory === cat.id ? 'text-primary' : 'text-textMuted'}>
                    {cat.icon}
                  </span>
                  <span>{tr(cat.label)}</span>
                </div>
                <span className={`text-[10px] ${selectedCategory === cat.id ? 'text-primary/70' : 'text-textMuted/50'}`}>
                  {counts.cat[cat.id] ?? 0}
                </span>
              </button>
            ))}
          </div>
        </>
      )}

      {/* Hidden file input for folder upload */}
      <input
        ref={folderInputRef}
        type="file"
        // @ts-ignore
        webkitdirectory=""
        directory=""
        multiple
        className="hidden"
        onChange={(e) => {
           handleFolderChange(e).catch(err => alert(err.message));
        }}
      />
    </>
  );
};

// ---- Plugin Card ----

interface PluginCardProps {
  plugin: PluginInfo;
  layout?: PluginLayoutMode;
  toggling: boolean;
  onToggle: () => void;
  configOpen: boolean;
  onConfigToggle: () => void;
  onOpenView: (viewName: string) => void;
  starred: boolean;
  onToggleStar: () => void;
  /** null = 未选中 agent；boolean = 该 agent 是否加载此插件 */
  agentLoaded: boolean | null;
  onAgentToggle: () => void;
  /** 该 agent 对此插件的 level 覆盖值；undefined 表示使用插件默认值 */
  agentToolLevel?: 'core' | 'extended' | 'hidden';
  onToolLevelChange: (level: 'core' | 'extended' | 'hidden') => void;
  onUninstall: () => void;
}

const PluginCard: React.FC<PluginCardProps> = ({
  plugin, layout = 'grid', toggling, onToggle, configOpen, onConfigToggle,
  onOpenView, starred, onToggleStar, agentLoaded, onAgentToggle,
  agentToolLevel, onToolLevelChange, onUninstall,
}) => {
  const { t: tr } = useTranslation();
  const typeClass = TYPE_COLORS[plugin.type] || TYPE_COLORS.tool;
  const hasSettings = (plugin.config_schema && Object.keys(plugin.config_schema).length > 0) || !!plugin.service || agentLoaded !== null;
  const contributedViews = plugin.contributes?.views || [];
  const showGlobalDisabledStyle = !!plugin.service_toggle && !plugin.enabled;
  const isList = layout === 'list';

  const actionButtons = (
    <div className="flex items-center gap-0.5 shrink-0">
      <button
        onClick={onToggleStar}
        title={starred ? 'Remove from favorites' : 'Add to favorites'}
        className="p-1 rounded transition-colors"
      >
        <Star
          size={isList ? 14 : 16}
          className={starred
            ? 'fill-yellow-400 text-yellow-400'
            : 'text-textMuted hover:text-yellow-400 transition-colors'
          }
        />
      </button>

      {plugin.service_toggle && (
        <button
          onClick={plugin.service_only ? undefined : onToggle}
          disabled={toggling || !!plugin.service_only}
          className="transition-colors"
          title={plugin.service_only ? tr('pluginManager.serviceOnlyTitle') : plugin.enabled ? 'Disable' : 'Enable'}
        >
          {toggling ? (
            <Loader2 size={isList ? 18 : 24} className="animate-spin text-textMuted" />
          ) : plugin.enabled ? (
            <ToggleRight size={isList ? 22 : 28} className={plugin.service_only ? 'text-textMuted opacity-30' : 'text-primary'} />
          ) : (
            <ToggleLeft size={isList ? 22 : 28} className="text-textMuted opacity-30" />
          )}
        </button>
      )}

      {agentLoaded !== null && !SYSTEM_TOOLS.includes(plugin.name) && (
        <button
          onClick={onAgentToggle}
          title={agentLoaded ? tr('pluginManager.removeFromAgent') : tr('pluginManager.addToAgent')}
          className="flex items-center gap-0.5 px-1.5 py-0.5 rounded-md border text-[10px] font-medium transition-colors ml-0.5 shrink-0"
          style={agentLoaded
            ? { background: 'rgba(var(--color-primary-rgb,99,102,241),0.12)', color: 'var(--color-primary,#6366f1)', borderColor: 'rgba(var(--color-primary-rgb,99,102,241),0.3)' }
            : { background: 'transparent', color: 'var(--tw-text-opacity,#9ca3af)', borderColor: 'rgba(156,163,175,0.3)' }
          }
        >
          <Bot size={10} />
          {agentLoaded ? 'On' : 'Off'}
        </button>
      )}

      {!plugin.builtin && (
        <button
          onClick={onUninstall}
          title={tr('pluginManager.uninstallTitle')}
          className="p-1 rounded transition-colors text-textMuted hover:text-red-400 hover:bg-red-500/10 ml-0.5"
        >
          <Trash2 size={isList ? 13 : 14} />
        </button>
      )}
    </div>
  );

  const configAndViews = (
    <>
      {hasSettings && (
        <button
          onClick={onConfigToggle}
          className={`p-1 rounded transition-colors ${
            configOpen
              ? 'bg-primary/15 text-primary'
              : 'text-textMuted hover:text-primary hover:bg-primary/10'
          }`}
          title="Settings"
        >
          <Settings size={14} />
        </button>
      )}
      {contributedViews.map(view => (
        <button
          key={view.name}
          onClick={() => onOpenView(view.name)}
          className="p-1 rounded transition-colors text-textMuted hover:text-purple-400 hover:bg-purple-500/10"
          title={view.title}
        >
          <BarChart3 size={14} />
        </button>
      ))}
    </>
  );

  const configPanel = (hasSettings || agentLoaded) && configOpen && (
    <PluginConfigPanel
      plugin={plugin}
      agentLoaded={agentLoaded}
      agentToolLevel={agentToolLevel}
      onToolLevelChange={onToolLevelChange}
    />
  );

  // ── Compact list row ──
  if (isList) {
    return (
      <div className={`bg-panel rounded-lg border border-border px-3 py-2 flex flex-col gap-2 transition-all hover:border-primary/30 ${
        showGlobalDisabledStyle ? 'opacity-60' : ''
      }`}>
        <div className="flex items-center gap-3 min-w-0">
          {getPluginIcon(plugin, true)}

          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-1.5 min-w-0">
              <h3 className="text-[13px] font-semibold text-textMain truncate leading-tight">
                {plugin.display_name || plugin.name}
              </h3>
              {plugin.builtin && SYSTEM_TOOLS.includes(plugin.name) && (
                <span className="inline-flex items-center gap-0.5 px-1 py-0 rounded text-[9px] font-semibold bg-sky-500/15 text-sky-400 border border-sky-500/25 shrink-0">
                  <Shield size={8} />
                  {tr('pluginManager.builtinBadge')}
                </span>
              )}
              <span className={`inline-flex items-center gap-0.5 px-1.5 py-0 rounded text-[9px] font-medium border shrink-0 ${typeClass}`}>
                {TYPE_LABELS[plugin.type] ? tr(TYPE_LABELS[plugin.type]) : plugin.type}
              </span>
            </div>
            <p className="text-[11px] text-textMuted truncate leading-tight mt-0.5">
              {plugin.description || 'No description'}
            </p>
          </div>

          <div className="flex items-center gap-0.5 shrink-0">
            {configAndViews}
            {actionButtons}
          </div>
        </div>
        {configPanel}
      </div>
    );
  }

  // ── Grid card ──
  return (
    <div className={`bg-panel rounded-xl border border-border p-5 flex flex-col gap-3 transition-all hover:shadow-md ${
      showGlobalDisabledStyle ? 'opacity-60' : ''
    }`}>
      {/* Header row */}
      <div className="flex items-start justify-between gap-2">
        {getPluginIcon(plugin)}

        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1">
            <h3 className="text-sm font-bold text-textMain truncate">
              {plugin.display_name || plugin.name}
            </h3>
            <span className="text-xs text-textMuted shrink-0">v{plugin.version}</span>
              {plugin.builtin && SYSTEM_TOOLS.includes(plugin.name) && (
                <span className="inline-flex items-center gap-0.5 px-1.5 py-0 rounded text-[9px] font-semibold bg-sky-500/15 text-sky-400 border border-sky-500/25 shrink-0">
                  <Shield size={9} />
                  {tr('pluginManager.builtinBadge')}
                </span>
              )}
          </div>
          {plugin.author && (
            <p className="text-xs text-textMuted">by {plugin.author}</p>
          )}
        </div>

        {actionButtons}
      </div>

      {/* Description */}
      <p className="text-xs text-textMuted leading-relaxed line-clamp-2">
        {plugin.description || 'No description'}
      </p>

      {/* Tags */}
      {(plugin.tags || []).length > 0 && (
        <div className="flex items-center gap-1 flex-wrap">
          {(plugin.tags || []).map(tag => (
            <span
              key={tag}
              className="px-1.5 py-0.5 rounded text-[10px] bg-slate-500/10 text-slate-400 border border-slate-500/20"
            >
              {tag}
            </span>
          ))}
        </div>
      )}

      {/* Footer */}
      <div className="flex items-center gap-2 mt-auto pt-2 border-t border-border/50">
        <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-xs font-medium border ${typeClass}`}>
          {TYPE_ICONS[plugin.type]}
          {TYPE_LABELS[plugin.type] ? tr(TYPE_LABELS[plugin.type]) : plugin.type}
        </span>

        {plugin.tools && plugin.tools.length > 0 && (
          <span className="text-xs text-textMuted">
            {plugin.tools.length} tool{plugin.tools.length > 1 ? 's' : ''}
          </span>
        )}

        {plugin.hooks && plugin.hooks.length > 0 && (
          <span className="text-xs text-textMuted">
            {plugin.hooks.length} hook{plugin.hooks.length > 1 ? 's' : ''}
          </span>
        )}

        <div className={`flex items-center gap-0.5 ${hasSettings || contributedViews.length > 0 ? 'ml-auto' : ''}`}>
          {configAndViews}
        </div>
      </div>

      {configPanel}
    </div>
  );
};

// ---- Plugin Config Panel ----

interface PluginConfigPanelProps {
  plugin: PluginInfo;
  agentLoaded?: boolean | null;
  agentToolLevel?: 'core' | 'extended' | 'hidden';
  onToolLevelChange?: (level: 'core' | 'extended' | 'hidden') => void;
}

const PluginConfigPanel: React.FC<PluginConfigPanelProps> = ({
  plugin, agentLoaded, agentToolLevel, onToolLevelChange,
}) => {
  const { t: tr } = useTranslation();
  const pluginName = plugin.name;
  const [schema, setSchema]   = useState<Record<string, PluginConfigField>>({});
  const [values, setValues]   = useState<Record<string, any>>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving]   = useState(false);
  const [restarting, setRestarting] = useState(false);
  const [saved, setSaved]     = useState(false);
  const [error, setError]     = useState<string | null>(null);

  // 侧边栏导航启用状态
  const [navEnabled, setNavEnabled] = useState(false);

  // 初始化时从 localStorage 读取导航启用状态
  useEffect(() => {
    try {
      const stored = localStorage.getItem(`plugin_nav_enabled_${pluginName}`);
      setNavEnabled(stored === 'true');
    } catch {
      setNavEnabled(false);
    }
  }, [pluginName]);

  // 切换侧边栏导航启用状态
  const handleNavToggle = () => {
    const newValue = !navEnabled;
    setNavEnabled(newValue);
    try {
      localStorage.setItem(`plugin_nav_enabled_${pluginName}`, String(newValue));
      // 通知 Sidebar 重新加载导航配置
      window.dispatchEvent(new Event('plugin-nav-changed'));
    } catch (e) {
      console.error('[PluginConfigPanel] Failed to save nav preference:', e);
    }
  };

  useEffect(() => {
    let mounted = true;
    (async () => {
      try {
        const data = await pluginAPI.getPluginConfig(pluginName);
        if (!mounted) return;
        setSchema(data.config_schema || {});
        setValues(data.config || {});
      } catch (e: any) {
        if (mounted) setError(e.message || 'Failed to load config');
      } finally {
        if (mounted) setLoading(false);
      }
    })();
    return () => { mounted = false; };
  }, [pluginName]);

  const handleChange = (key: string, value: any) => {
    setValues(prev => ({ ...prev, [key]: value }));
    setSaved(false);
  };

  const handleSave = async () => {
    setSaving(true);
    setSaved(false);
    try {
      await pluginAPI.savePluginConfig(pluginName, values);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (e: any) {
      alert(`Save failed: ${e.message}`);
    } finally {
      setSaving(false);
    }
  };

  const handleSaveAndRestart = async () => {
    setSaving(true);
    setRestarting(true);
    setSaved(false);
    try {
      await pluginAPI.savePluginConfig(pluginName, values);
      await pluginServiceAPI.restart(pluginName);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (e: any) {
      alert(tr('pluginManager.saveAndRestartFailedMsg', { error: e.message }));
    } finally {
      setSaving(false);
      setRestarting(false);
    }
  };

  const hasHttpService = !!plugin.service;

  if (loading) {
    return (
      <div className="flex items-center justify-center py-4">
        <Loader2 size={16} className="animate-spin text-textMuted" />
      </div>
    );
  }

  if (error) return <p className="text-xs text-red-400 py-2">{error}</p>;

  const fields = Object.entries(schema);
  const hasConfigFields = fields.length > 0;

  return (
    <div className="border-t border-border/50 pt-3 mt-1 space-y-3">
      {/* 目录名称 */}
      <div className="flex items-center gap-2 text-[11px] text-textMuted">
        <FolderOpen size={13} className="shrink-0" />
        <span>{tr('pluginManager.directoryLabel')}</span>
        <code className="font-mono bg-surface/60 border border-border/50 rounded px-1.5 py-0.5 text-[11px] text-textMain select-all">
          {plugin.dir_name}
        </code>
      </div>

      {/* 侧边栏导航控制 - 所有插件均支持，由用户决定是否加入导航栏 */}
      <div className="bg-primary/5 rounded-lg p-3 border border-primary/20">
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-2">
            <LayoutTemplate size={14} className="text-primary" />
            <p className="text-xs font-medium text-textMain">{tr('pluginManager.sidebarNav')}</p>
          </div>
          <button
            onClick={handleNavToggle}
            className="transition-colors"
            title={navEnabled ? tr('pluginManager.hideFromSidebar') : tr('pluginManager.showInSidebar')}
          >
            {navEnabled ? (
              <Eye size={18} className="text-primary" />
            ) : (
              <EyeOff size={18} className="text-textMuted" />
            )}
          </button>
        </div>
        <p className="text-[10px] text-textMuted mb-2">
          {navEnabled
            ? tr('pluginManager.navEnabled')
            : tr('pluginManager.navDisabled')}
        </p>
        {/* 图标预览：优先显示配置的图片，否则显示首字符自动图标 */}
        {plugin.contributes?.navigation?.iconType === 'image' && plugin.contributes.navigation.iconUrl ? (
          <div className="flex items-center gap-2 mt-2 text-[10px] text-textMuted">
            <img
              src={plugin.contributes.navigation.iconUrl}
              alt="icon preview"
              className="w-5 h-5 object-contain rounded border border-border"
              loading="lazy"
            />
            <span>{tr('pluginManager.customIconPreview')}</span>
          </div>
        ) : (
          <div className="flex items-center gap-2 mt-2 text-[10px] text-textMuted">
            <div className={`w-5 h-5 rounded flex items-center justify-center text-white text-[10px] font-bold shrink-0 ${avatarColor(plugin.name)}`}>
              {(plugin.display_name || plugin.name).charAt(0).toUpperCase()}
            </div>
            <span>{tr('pluginManager.autoLetterIcon')}</span>
          </div>
        )}
      </div>

      {/* Agent 注入级别 — 仅在已选中 agent 时显示（无论插件是否已为该 agent 开启）*/}
      {agentLoaded !== null && onToolLevelChange && (
        <div>
          <p className="text-[10px] text-textMuted mb-1.5">{tr('pluginManager.agentInjectLevel')}</p>
          <div className="flex items-center rounded-md border border-border overflow-hidden text-xs font-medium w-fit">
            <button
              onClick={() => onToolLevelChange('core')}
              title={tr('pluginManager.coreTitle')}
              className="px-3 py-1 transition-colors"
              style={(agentToolLevel ?? 'extended') === 'core'
                ? { background: 'rgba(34,197,94,0.15)', color: '#22c55e' }
                : { background: 'transparent', color: 'var(--tw-text-opacity,#9ca3af)' }
              }
            >
              core
            </button>
            <button
              onClick={() => onToolLevelChange('extended')}
              title={tr('pluginManager.extendedTitle')}
              className="px-3 py-1 transition-colors border-l border-border"
              style={(agentToolLevel ?? 'extended') === 'extended'
                ? { background: 'rgba(99,102,241,0.15)', color: '#818cf8' }
                : { background: 'transparent', color: 'var(--tw-text-opacity,#9ca3af)' }
              }
            >
              extended
            </button>
            <button
              onClick={() => onToolLevelChange('hidden')}
              title={tr('pluginManager.hiddenTitle')}
              className="px-3 py-1 transition-colors border-l border-border"
              style={(agentToolLevel ?? 'extended') === 'hidden'
                ? { background: 'rgba(245,158,11,0.15)', color: '#f59e0b' }
                : { background: 'transparent', color: 'var(--tw-text-opacity,#9ca3af)' }
              }
            >
              hidden
            </button>
          </div>
          <p className="text-[10px] text-textMuted mt-1">
            {(agentToolLevel ?? 'extended') === 'core'
              ? tr('pluginManager.coreDesc')
              : (agentToolLevel ?? 'extended') === 'hidden'
              ? tr('pluginManager.hiddenDesc')
              : tr('pluginManager.extendedDesc')}
          </p>
        </div>
      )}

      {/* 普通配置字段 */}
      {hasConfigFields && (
        <>
          {fields.map(([key, field]) => (
            <ConfigField
              key={key}
              fieldKey={key}
              field={field}
              value={values[key]}
              onChange={(v) => handleChange(key, v)}
            />
          ))}
          <div className="flex justify-end gap-2 pt-1">
            <button
              onClick={handleSave}
              disabled={saving || restarting}
              className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${
                saved
                  ? 'bg-emerald-500/15 text-emerald-400'
                  : 'bg-primary/15 text-primary hover:bg-primary/25'
              }`}
            >
              {saving && !restarting ? <Loader2 size={12} className="animate-spin" />
               : saved ? <Check size={12} />
               : <Save size={12} />}
              {saved ? tr('pluginManager.saveConfig') + ' ✓' : tr('pluginManager.saveConfig')}
            </button>
            {hasHttpService && (
              <button
                onClick={handleSaveAndRestart}
                disabled={saving || restarting}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-amber-500/15 text-amber-400 hover:bg-amber-500/25 transition-colors disabled:opacity-50"
              >
                {restarting ? <Loader2 size={12} className="animate-spin" />
                 : <RotateCw size={12} />}
                {tr('pluginManager.saveAndRestart')}
              </button>
            )}
          </div>
        </>
      )}

    </div>
  );
};

// ---- Service Status Card (DEPRECATED - use ServiceManagerPage instead) ----

const ServiceStatusCard: React.FC<{ pluginId: string }> = ({ pluginId }) => {
  const { t: tr } = useTranslation();
  const [status, setStatus]     = useState<PluginServiceStatus | null>(null);
  const [loading, setLoading]   = useState(true);
  const [acting, setActing]     = useState(false);
  const [logs, setLogs]         = useState<string[]>([]);
  const [showLogs, setShowLogs] = useState(false);
  const [logsLoading, setLogsLoading] = useState(false);

  const fetchStatus = useCallback(async () => {
    try {
      const data = await pluginServiceAPI.list();
      const svc = data.plugin_services.find(s => s.plugin_id === pluginId);
      setStatus(svc ?? null);
    } catch {
      setStatus(null);
    } finally {
      setLoading(false);
    }
  }, [pluginId]);

  useEffect(() => {
    fetchStatus();
  }, [fetchStatus]);

  const handleStart = async () => {
    setActing(true);
    try {
      await pluginServiceAPI.start(pluginId);
      await fetchStatus();
    } catch (e: any) {
      alert(tr('pluginManager.startFailedMsg', { error: e.message }));
    } finally {
      setActing(false);
    }
  };

  const handleStop = async () => {
    setActing(true);
    try {
      await pluginServiceAPI.stop(pluginId);
      await fetchStatus();
    } catch (e: any) {
      alert(tr('pluginManager.stopFailedMsg', { error: e.message }));
    } finally {
      setActing(false);
    }
  };

  const handleRestart = async () => {
    setActing(true);
    try {
      await pluginServiceAPI.restart(pluginId);
      await fetchStatus();
    } catch (e: any) {
      alert(tr('pluginManager.restartFailedMsg', { error: e.message }));
    } finally {
      setActing(false);
    }
  };

  const handleToggleLogs = async () => {
    if (!showLogs) {
      setLogsLoading(true);
      try {
        const data = await pluginServiceAPI.getLogs(pluginId, 100);
        setLogs(data.logs);
      } catch {
        setLogs(['(Failed to load logs)']);
      } finally {
        setLogsLoading(false);
      }
    }
    setShowLogs(s => !s);
  };

  const alive = status?.alive ?? false;

  return (
    <div className="mt-1 rounded-lg border border-border/60 bg-bgLight/50 overflow-hidden">
      {/* Header */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-border/40">
        <Server size={13} className="text-textMuted shrink-0" />
        <span className="text-xs font-semibold text-textMain flex-1">{tr('pluginManager.httpService')}</span>
        {loading ? (
          <Loader2 size={12} className="animate-spin text-textMuted" />
        ) : (
          <span className={`inline-flex items-center gap-1 text-[10px] font-medium px-1.5 py-0.5 rounded-full ${
            alive
              ? 'bg-emerald-500/15 text-emerald-400'
              : 'bg-slate-500/15 text-slate-400'
          }`}>
            <span className={`w-1.5 h-1.5 rounded-full ${alive ? 'bg-emerald-400' : 'bg-slate-500'}`} />
            {alive ? tr('pluginManager.statusRunning') : tr('pluginManager.statusStopped')}
          </span>
        )}
      </div>

      {/* Status rows */}
      {!loading && status && (
        <div className="px-3 py-2 space-y-1.5 text-[11px]">
          <div className="flex justify-between text-textMuted">
            <span>{tr('pluginManager.port')}</span>
            <span className="text-textMain font-mono">{status.port}</span>
          </div>
          {alive && status.pid && (
            <div className="flex justify-between text-textMuted">
              <span>PID</span>
              <span className="text-textMain font-mono">{status.pid}</span>
            </div>
          )}
          {alive && status.started_at && (
            <div className="flex justify-between text-textMuted">
              <span>{tr('pluginManager.startTime')}</span>
              <span className="text-textMain">{new Date(status.started_at).toLocaleTimeString()}</span>
            </div>
          )}
          {status.restart_count > 0 && (
            <div className="flex justify-between text-textMuted">
              <span>{tr('pluginManager.restartCount')}</span>
              <span className="text-amber-400">{status.restart_count}</span>
            </div>
          )}
        </div>
      )}

      {!loading && !status && (
        <p className="px-3 py-2 text-[11px] text-textMuted">{tr('pluginManager.serviceNotRegistered')}</p>
      )}

      {/* Actions */}
      {!loading && status && (
        <div className="flex items-center gap-2 px-3 pb-2">
          {alive ? (
            <>
              <button
                onClick={handleStop}
                disabled={acting}
                className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md text-[11px] font-medium bg-red-500/10 text-red-400 hover:bg-red-500/20 transition-colors disabled:opacity-50"
              >
                {acting ? <Loader2 size={11} className="animate-spin" /> : <StopCircle size={11} />}
                {tr('pluginManager.stop')}
              </button>
              <button
                onClick={handleRestart}
                disabled={acting}
                className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md text-[11px] font-medium bg-amber-500/10 text-amber-400 hover:bg-amber-500/20 transition-colors disabled:opacity-50"
              >
                {acting ? <Loader2 size={11} className="animate-spin" /> : <RotateCw size={11} />}
                {tr('pluginManager.restart')}
              </button>
            </>
          ) : (
            <button
              onClick={handleStart}
              disabled={acting}
              className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md text-[11px] font-medium bg-emerald-500/10 text-emerald-400 hover:bg-emerald-500/20 transition-colors disabled:opacity-50"
            >
              {acting ? <Loader2 size={11} className="animate-spin" /> : <Play size={11} />}
              {tr('pluginManager.start')}
            </button>
          )}
          <button
            onClick={fetchStatus}
            disabled={acting}
            className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md text-[11px] font-medium bg-slate-500/10 text-slate-400 hover:bg-slate-500/20 transition-colors disabled:opacity-50"
          >
            <RefreshCw size={11} />
            {tr('common.refresh')}
          </button>
          <button
            onClick={handleToggleLogs}
            className="ml-auto inline-flex items-center gap-1 px-2 py-1 rounded-md text-[11px] font-medium text-textMuted hover:text-textMain hover:bg-slate-500/10 transition-colors"
          >
            <Terminal size={11} />
            {tr('pluginManager.logs')}
            {showLogs ? <ChevronUp size={11} /> : <ChevronDown size={11} />}
          </button>
        </div>
      )}

      {/* Log panel */}
      {showLogs && (
        <div className="border-t border-border/40 bg-black/30 px-3 py-2 max-h-48 overflow-y-auto">
          {logsLoading ? (
            <div className="flex justify-center py-2">
              <Loader2 size={13} className="animate-spin text-textMuted" />
            </div>
          ) : logs.length === 0 ? (
            <p className="text-[10px] text-textMuted">{tr('pluginManager.noLogs')}</p>
          ) : (
            <pre className="text-[10px] text-slate-300 whitespace-pre-wrap leading-relaxed font-mono">
              {logs.join('\n')}
            </pre>
          )}
        </div>
      )}
    </div>
  );
};

// ---- Individual Config Field ----

interface ConfigFieldProps {
  fieldKey: string;
  field: PluginConfigField;
  value: any;
  onChange: (value: any) => void;
}

const SECRET_KEY_PATTERN = /api[_\-]?key|secret|token|password|passwd|auth|credential/i;

function isSecretField(fieldKey: string, field: PluginConfigField): boolean {
  return field.secret === true || SECRET_KEY_PATTERN.test(fieldKey);
}

const ConfigField: React.FC<ConfigFieldProps> = ({ fieldKey, field, value, onChange }) => {
  const { t: tr } = useTranslation();
  const [showSecret, setShowSecret] = useState(false);
  const inputClass = 'w-full bg-bgLight border border-border rounded-lg px-3 py-1.5 text-xs text-textMain placeholder-textMuted focus:outline-none focus:border-primary/50 transition-colors';

  const renderInput = () => {
    if (field.enum && field.enum.length > 0) {
      return (
        <select value={value ?? ''} onChange={(e) => onChange(e.target.value)} className={inputClass}>
          {field.enum.map((opt: any) => (
            <option key={String(opt)} value={opt}>{String(opt)}</option>
          ))}
        </select>
      );
    }

    switch (field.type) {
      case 'bot_list':
        return (
          <BotListEditor
            itemSchema={field.item_schema || {}}
            value={Array.isArray(value) ? value : []}
            onChange={onChange}
          />
        );
      case 'boolean':
        return (
          <button onClick={() => onChange(!value)} className="flex items-center gap-2">
            {value
              ? <ToggleRight size={22} className="text-primary" />
              : <ToggleLeft  size={22} className="text-textMuted" />}
            <span className="text-xs text-textMuted">{value ? 'On' : 'Off'}</span>
          </button>
        );
      case 'integer':
      case 'number':
        return (
          <input
            type="number"
            value={value ?? ''}
            onChange={(e) => {
              const v = e.target.value;
              onChange(v === '' ? null : field.type === 'integer' ? parseInt(v, 10) : parseFloat(v));
            }}
            step={field.type === 'integer' ? 1 : 'any'}
            className={inputClass}
            placeholder={field.default != null ? String(field.default) : ''}
          />
        );
      case 'string':
      default: {
        const masked = isSecretField(fieldKey, field);
        if (masked) {
          return (
            <div className="relative">
              <input
                type={showSecret ? 'text' : 'password'}
                value={value ?? ''}
                onChange={(e) => onChange(e.target.value)}
                className={`${inputClass} pr-8`}
                placeholder={field.default != null ? String(field.default) : ''}
                autoComplete="new-password"
              />
              <button
                type="button"
                onClick={() => setShowSecret(s => !s)}
                className="absolute right-2 top-1/2 -translate-y-1/2 text-textMuted hover:text-textMain transition-colors"
                tabIndex={-1}
                title={showSecret ? tr('pluginManager.hide') : tr('pluginManager.show')}
              >
                {showSecret ? <EyeOff size={13} /> : <Eye size={13} />}
              </button>
            </div>
          );
        }
        return (
          <input
            type="text"
            value={value ?? ''}
            onChange={(e) => onChange(e.target.value)}
            className={inputClass}
            placeholder={field.default != null ? String(field.default) : ''}
          />
        );
      }
    }
  };

  return (
    <div className="space-y-1">
      <label className="text-xs font-medium text-textMain flex items-center gap-1.5">
        {fieldKey}
        {isSecretField(fieldKey, field) && (
          <span className="text-[9px] bg-amber-500/15 text-amber-600 border border-amber-500/25 rounded px-1 py-0.5 font-semibold uppercase tracking-wide">
            Secret
          </span>
        )}
      </label>
      {field.description && (
        <p className="text-[10px] text-textMuted leading-tight">{field.description}</p>
      )}
      {renderInput()}
    </div>
  );
};

// ---- Bot List Editor ----

interface BotListEditorProps {
  itemSchema: Record<string, any>;
  value: Record<string, any>[];
  onChange: (value: Record<string, any>[]) => void;
}

const SECRET_BOT_FIELD_PATTERN = /api[_\-]?key|secret|token|password|passwd|auth|credential/i;

const BotListEditor: React.FC<BotListEditorProps> = ({ itemSchema, value, onChange }) => {
  const { t: tr } = useTranslation();
  const [expandedIdx, setExpandedIdx] = useState<number | null>(null);
  const inputClass = 'w-full bg-bgLight border border-border rounded-lg px-3 py-1.5 text-xs text-textMain placeholder-textMuted focus:outline-none focus:border-primary/50 transition-colors';

  const handleAddBot = () => {
    const newBot: Record<string, any> = {};
    for (const [k, f] of Object.entries(itemSchema) as [string, any][]) {
      newBot[k] = f.default !== undefined ? f.default : (f.type === 'boolean' ? false : '');
    }
    const next = [...value, newBot];
    onChange(next);
    setExpandedIdx(next.length - 1);
  };

  const handleRemoveBot = (idx: number) => {
    const next = value.filter((_, i) => i !== idx);
    onChange(next);
    if (expandedIdx === idx) setExpandedIdx(null);
    else if (expandedIdx !== null && expandedIdx > idx) setExpandedIdx(expandedIdx - 1);
  };

  const handleFieldChange = (idx: number, key: string, val: any) => {
    const next = value.map((bot, i) => i === idx ? { ...bot, [key]: val } : bot);
    onChange(next);
  };

  return (
    <div className="space-y-2">
      {value.length === 0 && (
        <p className="text-[11px] text-textMuted italic py-1">{tr('pluginManager.noBots')}</p>
      )}
      {value.map((bot, idx) => {
        const label = (bot.name as string) || `Bot ${idx + 1}`;
        const isExpanded = expandedIdx === idx;
        return (
          <div key={idx} className="rounded-lg border border-border/60 bg-bgLight/40 overflow-hidden">
            {/* Bot header */}
            <div
              className="flex items-center gap-2 px-3 py-2 cursor-pointer select-none hover:bg-bgLight/60 transition-colors"
              onClick={() => setExpandedIdx(isExpanded ? null : idx)}
            >
              <Bot size={12} className="text-textMuted shrink-0" />
              <span className="text-xs font-medium text-textMain flex-1 truncate">{label}</span>
              {/* enabled badge */}
              {bot.enabled !== undefined && (
                <span className={`text-[10px] px-1.5 py-0.5 rounded-full ${bot.enabled ? 'bg-emerald-500/15 text-emerald-400' : 'bg-slate-500/15 text-slate-400'}`}>
                  {bot.enabled ? tr('pluginManager.botEnabled') : tr('pluginManager.botDisabled')}
                </span>
              )}
              <button
                onClick={(e) => { e.stopPropagation(); handleRemoveBot(idx); }}
                className="p-0.5 rounded text-textMuted hover:text-red-400 hover:bg-red-500/10 transition-colors"
                title={tr('pluginManager.deleteBot')}
              >
                <Trash2 size={12} />
              </button>
              {isExpanded ? <ChevronUp size={12} className="text-textMuted" /> : <ChevronDown size={12} className="text-textMuted" />}
            </div>

            {/* Bot fields */}
            {isExpanded && (
              <div className="px-3 pb-3 space-y-2 border-t border-border/40 pt-2">
                {(Object.entries(itemSchema) as [string, any][]).map(([key, fieldDef]) => {
                  const isSecret = (fieldDef as any).secret === true || SECRET_BOT_FIELD_PATTERN.test(key);
                  const val = bot[key];
                  return (
                    <BotField
                      key={key}
                      fieldKey={key}
                      fieldDef={fieldDef}
                      isSecret={isSecret}
                      value={val}
                      onChange={(v) => handleFieldChange(idx, key, v)}
                      inputClass={inputClass}
                    />
                  );
                })}
              </div>
            )}
          </div>
        );
      })}

      <button
        onClick={handleAddBot}
        className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-primary/10 text-primary hover:bg-primary/20 transition-colors"
      >
        <Plus size={12} />
        {tr('pluginManager.addBot')}
      </button>
    </div>
  );
};

interface BotFieldProps {
  fieldKey: string;
  fieldDef: any;
  isSecret: boolean;
  value: any;
  onChange: (v: any) => void;
  inputClass: string;
}

const BotField: React.FC<BotFieldProps> = ({ fieldKey, fieldDef, isSecret, value, onChange, inputClass }) => {
  const [showSecret, setShowSecret] = useState(false);

  let input: React.ReactNode;
  if (fieldDef.type === 'boolean') {
    input = (
      <button onClick={() => onChange(!value)} className="flex items-center gap-2">
        {value
          ? <ToggleRight size={22} className="text-primary" />
          : <ToggleLeft  size={22} className="text-textMuted" />}
        <span className="text-xs text-textMuted">{value ? 'On' : 'Off'}</span>
      </button>
    );
  } else if (isSecret) {
    input = (
      <div className="relative">
        <input
          type={showSecret ? 'text' : 'password'}
          value={value ?? ''}
          onChange={(e) => onChange(e.target.value)}
          className={`${inputClass} pr-8`}
          autoComplete="new-password"
        />
        <button
          type="button"
          onClick={() => setShowSecret(s => !s)}
          className="absolute right-2 top-1/2 -translate-y-1/2 text-textMuted hover:text-textMain transition-colors"
          tabIndex={-1}
        >
          {showSecret ? <EyeOff size={13} /> : <Eye size={13} />}
        </button>
      </div>
    );
  } else {
    input = (
      <input
        type="text"
        value={value ?? ''}
        onChange={(e) => onChange(e.target.value)}
        className={inputClass}
      />
    );
  }

  return (
    <div className="space-y-1">
      <label className="text-[11px] font-medium text-textMain flex items-center gap-1">
        {fieldKey}
        {isSecret && (
          <span className="text-[9px] bg-amber-500/15 text-amber-600 border border-amber-500/25 rounded px-1 py-0.5 font-semibold uppercase tracking-wide">
            Secret
          </span>
        )}
      </label>
      {fieldDef.description && (
        <p className="text-[10px] text-textMuted leading-tight">{fieldDef.description}</p>
      )}
      {input}
    </div>
  );
};

// ---- Uninstall Confirm Dialog ----

interface UninstallConfirmDialogProps {
  plugin: PluginInfo;
  uninstalling: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

const UninstallConfirmDialog: React.FC<UninstallConfirmDialogProps> = ({
  plugin, uninstalling, onConfirm, onCancel,
}) => {
  const { t: tr } = useTranslation();
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm">
      <div className="bg-panel border border-border rounded-xl shadow-2xl w-full max-w-sm mx-4 p-6 flex flex-col gap-4">
        {/* Icon + title */}
        <div className="flex items-center gap-3">
          <div className="flex-shrink-0 w-10 h-10 rounded-full bg-red-500/15 flex items-center justify-center">
            <Trash2 size={18} className="text-red-400" />
          </div>
          <div>
            <h3 className="text-sm font-bold text-textMain">{tr('pluginManager.uninstallPluginTitle')}</h3>
            <p className="text-xs text-textMuted mt-0.5">{tr('pluginManager.uninstallIrreversible')}</p>
          </div>
        </div>

        {/* Plugin info */}
        <div className="bg-bgLight rounded-lg border border-border/60 px-4 py-3">
          <p className="text-sm font-medium text-textMain">
            {plugin.display_name || plugin.name}
          </p>
          <p className="text-xs text-textMuted mt-0.5">
            v{plugin.version}
            {plugin.author ? ` · by ${plugin.author}` : ''}
          </p>
        </div>

        {/* Warning */}
        <p className="text-xs text-textMuted leading-relaxed">
          {tr('pluginManager.uninstallWarning')}
        </p>

        {/* Actions */}
        <div className="flex gap-2 justify-end">
          <button
            onClick={onCancel}
            disabled={uninstalling}
            className="px-4 py-1.5 rounded-lg text-sm font-medium border border-border text-textMain hover:bg-bgLight/80 transition-colors disabled:opacity-50"
          >
            {tr('common.cancel')}
          </button>
          <button
            onClick={onConfirm}
            disabled={uninstalling}
            className="px-4 py-1.5 rounded-lg text-sm font-medium bg-red-500 hover:bg-red-600 text-white transition-colors disabled:opacity-60 flex items-center gap-1.5"
          >
            {uninstalling ? (
              <><Loader2 size={13} className="animate-spin" />{tr('pluginManager.uninstalling')}</>
            ) : (
              <><Trash2 size={13} />{tr('pluginManager.confirmUninstall')}</>
            )}
          </button>
        </div>
      </div>
    </div>
  );
};
