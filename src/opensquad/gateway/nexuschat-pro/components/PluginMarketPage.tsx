import React, { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import {
  Search,
  Filter,
  ArrowUpDown,
  Download,
  Trash2,
  ExternalLink,
  Github,
  CheckCircle,
  AlertCircle,
  MoreVertical,
  ChevronLeft,
  ChevronRight,
  Heart,
  User,
  Tag,
  Package,
  Calendar,
  X,
  Plus,
  Copy,
  Check,
  GitPullRequest,
  RefreshCw,
  Terminal,
  Cpu,
  ChevronDown,
  BadgeCheck,
  ShieldAlert,
  Store,
  Languages,
  Globe,
  GitBranch,
  ArrowUpCircle,
  Zap,
  Hammer,
  Puzzle,
  Briefcase,
  Code2,
  MessageSquare,
  Sparkles,
  Film,
  LineChart,
  Link2,
  LayoutTemplate,
} from 'lucide-react';

import { pluginMarketAPI, MarketPlugin, PluginListResponse, InstalledPluginInfo } from '../services/api';
import { useTranslation } from 'react-i18next';
import { OpenSquadLoader } from './OpenSquadLoader';

interface PluginMarketPageProps {
  onBack?: () => void;
}

// Plugin description language: 'zh' | 'en' | 'auto'
type PluginLang = 'zh' | 'en' | 'auto';
const PLUGIN_LANG_KEY = 'opensquad_plugin_lang';
const PLUGIN_LIKED_KEY = 'opensquad_liked_plugins';

/** Get plugin display text (name or description) with language fallback chain */
function getPluginText(
  plugin: MarketPlugin & { name_zh?: string; description_zh?: string; description_en?: string },
  field: 'name' | 'description',
  pluginLang: PluginLang,
  appLang: string,
): string {
  const effectiveLang = pluginLang === 'auto' ? appLang : pluginLang;
  if (field === 'name') {
    if (effectiveLang === 'zh' && plugin.name_zh) return plugin.name_zh;
    return plugin.name;
  }
  // description
  if (effectiveLang === 'zh' && plugin.description_zh) return plugin.description_zh;
  if (effectiveLang === 'en' && plugin.description_en) return plugin.description_en;
  return plugin.description;
}

/** Returns true if plugin has both zh and en description */
function hasBilingual(plugin: MarketPlugin & { description_zh?: string; description_en?: string }): boolean {
  return !!(plugin.description_zh && plugin.description_en);
}

// ---- Categories ----

interface CategoryDef {
  id: string;
  label: string;
  icon: React.ReactNode;
}

const CATEGORY_KEYS: Record<string, string> = {
  all: 'pluginMarket.catAll',
  productivity: 'pluginMarket.catOffice',
  development: 'pluginMarket.catDev',
  communication: 'pluginMarket.catComm',
  ai: 'pluginMarket.catAI',
  media: 'pluginMarket.catMedia',
  search: 'pluginMarket.catSearch',
  language: 'pluginMarket.catTranslate',
  analytics: 'pluginMarket.catData',
  integration: 'pluginMarket.catIntegration',
  demo: 'pluginMarket.catDemo',
};

const CATEGORIES: CategoryDef[] = [
  { id: 'all',           label: 'pluginMarket.catAll',   icon: <Puzzle size={14} /> },
  { id: 'productivity',  label: 'pluginMarket.catOffice',   icon: <Briefcase size={14} /> },
  { id: 'development',   label: 'pluginMarket.catDev',   icon: <Code2 size={14} /> },
  { id: 'communication', label: 'pluginMarket.catComm',   icon: <MessageSquare size={14} /> },
  { id: 'ai',            label: 'pluginMarket.catAI',    icon: <Sparkles size={14} /> },
  { id: 'media',         label: 'pluginMarket.catMedia',   icon: <Film size={14} /> },
  { id: 'search',        label: 'pluginMarket.catSearch',   icon: <Search size={14} /> },
  { id: 'language',      label: 'pluginMarket.catTranslate',   icon: <Languages size={14} /> },
  { id: 'analytics',     label: 'pluginMarket.catData',   icon: <LineChart size={14} /> },
  { id: 'integration',   label: 'pluginMarket.catIntegration',   icon: <Link2 size={14} /> },
  { id: 'demo',          label: 'pluginMarket.catDemo',   icon: <LayoutTemplate size={14} /> },
];

// ---- helpers ----

const TYPE_COLORS: Record<string, string> = {
  tool: 'bg-blue-100 text-blue-700',
  platform: 'bg-purple-100 text-purple-700',
  hook: 'bg-orange-100 text-orange-700',
};

const AVATAR_COLORS = [
  'bg-blue-500', 'bg-purple-500', 'bg-green-500', 'bg-orange-500',
  'bg-pink-500', 'bg-teal-500', 'bg-red-500', 'bg-indigo-500',
];

function hashCode(str: string): number {
  let h = 0;
  for (let i = 0; i < str.length; i++) h = (Math.imul(31, h) + str.charCodeAt(i)) | 0;
  return Math.abs(h);
}

function avatarColor(id: string): string {
  return AVATAR_COLORS[hashCode(id) % AVATAR_COLORS.length];
}

function formatDate(iso: string): string {
  if (!iso) return '-';
  try {
    const d = new Date(iso);
    if (isNaN(d.getTime())) return '-';
    return d.toLocaleDateString('zh-CN', { year: 'numeric', month: 'short', day: 'numeric' });
  } catch { return '-'; }
}

/** Returns true if v1 > v2 (semver-ish comparison) */
function versionGt(v1: string, v2: string): boolean {
  const parse = (v: string) => v.split('.').map(s => parseInt(s, 10) || 0);
  const a = parse(v1);
  const b = parse(v2);
  for (let i = 0; i < Math.max(a.length, b.length); i++) {
    const ai = a[i] ?? 0, bi = b[i] ?? 0;
    if (ai !== bi) return ai > bi;
  }
  return false;
}

// ---- PluginCard ----

interface PluginCardProps {
  plugin: MarketPlugin;
  installed: InstalledPluginInfo | null;
  liked: boolean;
  onLike: (id: string) => void;
  onInstall: (id: string) => void;
  onCardClick: (plugin: MarketPlugin) => void;
  installing: boolean;
  pluginLang: PluginLang;
  appLang: string;
}

const PluginCard: React.FC<PluginCardProps> = ({ plugin, installed, liked, onLike, onInstall, onCardClick, installing, pluginLang, appLang }) => {
  const { t } = useTranslation();
  const badgeCls = TYPE_COLORS[plugin.type] || 'bg-gray-100 text-gray-600';
  const hasUpdate = installed ? versionGt(plugin.version, installed.version) : false;
  const isInstalled = !!installed && !hasUpdate;

  let btnContent: React.ReactNode;
  let btnCls: string;
  if (installing) {
    btnContent = <><OpenSquadLoader size={16} />{t('pluginMarket.installing')}</>;
    btnCls = 'bg-primary/30 text-primary cursor-not-allowed';
  } else if (hasUpdate) {
    btnContent = <><ArrowUpCircle size={13} />{t('pluginMarket.update')} v{plugin.version}</>;
    btnCls = 'bg-amber-500 text-white hover:opacity-90';
  } else if (isInstalled) {
    btnContent = <><CheckCircle size={13} />{t('pluginMarket.installed')}</>;
    btnCls = 'bg-green-100 text-green-700 cursor-default';
  } else {
    btnContent = <><Download size={13} />{t('pluginMarket.install')}</>;
    btnCls = 'bg-primary text-white hover:opacity-90';
  }

  const typeKey = `pluginMarket.type${plugin.type.charAt(0).toUpperCase() + plugin.type.slice(1)}` as const;
  const displayName = getPluginText(plugin as any, 'name', pluginLang, appLang);
  const displayDesc = getPluginText(plugin as any, 'description', pluginLang, appLang);

  return (
    <div
      className="bg-panel border border-border rounded-lg p-4 hover:shadow-lg transition-all cursor-pointer h-full overflow-hidden flex flex-col gap-3"
      onClick={() => onCardClick(plugin)}
    >
      {/* Header: Icon + Type Badge */}
      <div className="flex items-start justify-between gap-2">
        {plugin.icon_url ? (
          <img src={plugin.icon_url} alt={plugin.name} className="w-10 h-10 rounded-xl object-cover shrink-0" loading="lazy" />
        ) : (
          <div className={`w-10 h-10 rounded-xl flex items-center justify-center text-white text-lg font-bold shrink-0 ${avatarColor(plugin.id)}`}>
            {plugin.name.charAt(0).toUpperCase()}
          </div>
        )}
        <div className={`px-2 py-0.5 rounded-md text-[10px] font-medium ${badgeCls}`}>
          {t(typeKey, { defaultValue: plugin.type })}
        </div>
      </div>
      <div className="flex flex-col gap-1 flex-1 min-h-0">
        <span className="font-bold text-textMain text-sm leading-tight line-clamp-1">{displayName}</span>
        <div className="text-[10px] text-textMuted">
          {plugin.author} · v{plugin.version}
        </div>
        <p className="text-[11px] text-textMuted leading-relaxed line-clamp-2">{displayDesc}</p>
      </div>

      {plugin.tags.length > 0 && (
        <div className="flex flex-wrap gap-1 px-2.5 pb-1.5">
          {plugin.tags.slice(0, 2).map(tag => (
            <span key={tag} className="text-[9px] bg-bgLight text-textMuted px-1.5 py-0.5 rounded-md border border-border">{tag}</span>
          ))}
        </div>
      )}

      <div
        className="flex items-center justify-between px-2.5 py-1.5 border-t border-border bg-bgLight/50"
        onClick={e => e.stopPropagation()}
      >
        <button
          onClick={() => !liked && onLike(plugin.id)}
          disabled={liked}
          className={`flex items-center gap-1 transition-colors ${liked ? 'text-red-500 cursor-default' : 'hover:text-red-500 text-textMuted'}`}
        >
          <Heart size={13} fill={liked ? 'currentColor' : 'none'} /><span className="font-medium text-[10px]">{plugin.likes}</span>
        </button>
        <div className="flex items-center gap-1">
          {(plugin.homepage || plugin.git_url) && (
            <a href={plugin.homepage || plugin.git_url} target="_blank" rel="noopener noreferrer"
              className="text-textMuted hover:text-primary transition-colors p-0.5" title={t('pluginMarket.viewRepo')}>
              <ExternalLink size={13} />
            </a>
          )}
          <button
            onClick={() => !isInstalled && !installing && onInstall(plugin.id)}
            disabled={installing || isInstalled}
            className={`flex items-center gap-1 text-[10px] px-2.5 py-1 rounded-md font-medium transition-colors ${btnCls}`}
          >
            {btnContent}
          </button>
        </div>
      </div>
    </div>
  );
};

// ---- Pagination ----

interface PaginationProps { page: number; pages: number; onChange: (p: number) => void; }

const Pagination: React.FC<PaginationProps> = ({ page, pages, onChange }) => {
  const nums = (): (number | '...')[] => {
    if (pages <= 7) return Array.from({ length: pages }, (_, i) => i + 1);
    const r: (number | '...')[] = [1];
    if (page > 3) r.push('...');
    for (let i = Math.max(2, page - 1); i <= Math.min(pages - 1, page + 1); i++) r.push(i);
    if (page < pages - 2) r.push('...');
    r.push(pages);
    return r;
  };
  return (
    <div className="flex items-center gap-1">
      <button onClick={() => onChange(page - 1)} disabled={page <= 1} className="p-1.5 rounded-lg text-textMuted hover:bg-primary/10 hover:text-primary disabled:opacity-40 disabled:cursor-not-allowed transition-colors"><ChevronLeft size={16} /></button>
      {nums().map((n, i) => n === '...' ? (
        <span key={`e${i}`} className="px-1 text-textMuted text-sm">...</span>
      ) : (
        <button key={n} onClick={() => onChange(n as number)} className={`min-w-[32px] h-8 rounded-lg text-sm font-medium transition-colors ${n === page ? 'bg-primary text-white' : 'text-textMuted hover:bg-primary/10 hover:text-primary'}`}>{n}</button>
      ))}
      <button onClick={() => onChange(page + 1)} disabled={page >= pages} className="p-1.5 rounded-lg text-textMuted hover:bg-primary/10 hover:text-primary disabled:opacity-40 disabled:cursor-not-allowed transition-colors"><ChevronRight size={16} /></button>
    </div>
  );
};

// ---- Submit PR Modal ----

const PLUGIN_JSON_TEMPLATE = JSON.stringify(
  {
    id: "my_plugin",
    name: "My Plugin",
    name_zh: "我的插件",
    version: "1.0.0",
    description: "Briefly describe what this plugin does",
    description_zh: "简要描述插件功能（中文）",
    description_en: "Briefly describe what this plugin does (English)",
    author: "YourGitHubUsername",
    type: "tool",
    tags: ["example"],
    homepage: "https://github.com/yourname/my_plugin",
    git_url: "https://github.com/yourname/my_plugin",
  },
  null,
  2
);

interface SubmitPRModalProps {
  onClose: () => void;
}

const SubmitPRModal: React.FC<SubmitPRModalProps> = ({ onClose }) => {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);

  const handleBackdropClick = (e: React.MouseEvent<HTMLDivElement>) => {
    if (e.target === e.currentTarget) onClose();
  };

  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [onClose]);

  const handleCopy = () => {
    navigator.clipboard.writeText(PLUGIN_JSON_TEMPLATE).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };

  const steps = [
    {
      num: 1,
      title: t('pluginMarket.submitPR.step1Title'),
      desc: (
        <>
          {t('pluginMarket.submitPR.step1Desc')}{' '}
          <a
            href="https://github.com/opensquad-ai/opensquad-plugins"
            target="_blank"
            rel="noopener noreferrer"
            className="text-primary underline hover:opacity-80"
          >
            github.com/opensquad-ai/opensquad-plugins
          </a>
        </>
      ),
    },
    {
      num: 2,
      title: t('pluginMarket.submitPR.step2Title'),
      desc: (
        <>
          <p className="mb-2">{t('pluginMarket.submitPR.step2Desc')}</p>
          <ul className="mt-1 ml-4 space-y-0.5 list-disc text-textMuted text-xs">
            <li><code className="text-textMain">plugin.py</code> — {t('pluginMarket.submitPR.pluginPyDesc')}</li>
            <li><code className="text-textMain">plugin.json</code> — {t('pluginMarket.submitPR.pluginJsonDesc')}</li>
            <li><code className="text-textMain">README.md</code> — {t('pluginMarket.submitPR.readmeDesc')}</li>
          </ul>
        </>
      ),
    },
    {
      num: 3,
      title: t('pluginMarket.submitPR.step3Title'),
      desc: t('pluginMarket.submitPR.step3Desc'),
    },
    {
      num: 4,
      title: t('pluginMarket.submitPR.step4Title'),
      desc: t('pluginMarket.submitPR.step4Desc'),
    },
  ];

  return (
    <div
      className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center backdrop-blur-sm p-4"
      onClick={handleBackdropClick}
    >
      <div className="bg-panel rounded-2xl shadow-2xl w-full max-w-xl border border-border overflow-hidden flex flex-col max-h-[90vh]">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-border shrink-0">
          <h2 className="font-semibold text-textMain text-base flex items-center gap-2">
            <GitPullRequest size={18} className="text-primary" />
            {t('pluginMarket.submitPR.title')}
          </h2>
          <button onClick={onClose} className="text-textMuted hover:text-textMain transition-colors">
            <X size={18} />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-5 py-4 flex flex-col gap-5">
          {/* Steps */}
          <div className="flex flex-col gap-4">
            {steps.map(step => (
              <div key={step.num} className="flex gap-3">
                <div className="shrink-0 w-7 h-7 rounded-full bg-primary/10 text-primary text-sm font-bold flex items-center justify-center">
                  {step.num}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="font-semibold text-textMain text-sm mb-0.5">{step.title}</div>
                  <div className="text-sm text-textMuted leading-relaxed">{step.desc}</div>
                </div>
              </div>
            ))}
          </div>

          {/* plugin.json template */}
          <div>
            <div className="flex items-center justify-between mb-1.5">
              <span className="text-xs font-semibold text-textMuted uppercase">{t('pluginMarket.submitPR.templateLabel')}</span>
              <button
                onClick={handleCopy}
                className="flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-lg bg-primary/10 text-primary hover:bg-primary/15 transition-colors font-medium"
              >
                {copied ? <Check size={12} /> : <Copy size={12} />}
                {copied ? t('pluginMarket.submitPR.copied') : t('pluginMarket.submitPR.copyTemplate')}
              </button>
            </div>
            <pre className="bg-bgLight border border-border rounded-xl p-3 text-xs text-textMain font-mono overflow-x-auto whitespace-pre">
              {PLUGIN_JSON_TEMPLATE}
            </pre>
          </div>

          {/* Info banner */}
          <div className="flex items-start gap-2.5 bg-blue-50 border border-blue-200 rounded-xl px-3 py-3 text-xs text-blue-800">
            <AlertCircle size={14} className="shrink-0 mt-0.5 text-blue-500" />
            <span>
              {t('pluginMarket.submitPR.infoText')}
            </span>
          </div>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between gap-3 px-5 py-4 border-t border-border shrink-0">
          <a
            href="https://github.com/opensquad-ai/opensquad-plugins/issues"
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-2 px-4 py-2 rounded-xl border border-border text-textMuted text-sm font-medium hover:bg-bgLight transition-colors"
          >
            <ExternalLink size={14} />
            {t('pluginMarket.submitPR.openPluginsDir')}
          </a>
          <div className="flex items-center gap-2">
            <button
              onClick={onClose}
              className="px-4 py-2 rounded-xl border border-border text-textMuted text-sm font-medium hover:bg-bgLight transition-colors"
            >
              {t('pluginMarket.close')}
            </button>
            <a
              href="https://github.com/opensquad-ai/opensquad-plugins/issues/new/choose"
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-2 px-5 py-2 rounded-xl bg-primary text-white text-sm font-medium hover:opacity-90 transition-opacity"
            >
              <GitPullRequest size={14} />
              {t('pluginMarket.submitPR.gotoGitHub')}
            </a>
          </div>
        </div>
      </div>
    </div>
  );
};

// ---- Security Warning Modal Component ----

interface SecurityWarningModalProps {
  plugin: MarketPlugin;
  onConfirm: () => void;
  onCancel: () => void;
}

const SecurityWarningModal: React.FC<SecurityWarningModalProps> = ({ plugin, onConfirm, onCancel }) => {
  const { t } = useTranslation();

  return (
    <div className="fixed inset-0 bg-black/60 z-[70] flex items-center justify-center p-4 backdrop-blur-md" onClick={onCancel}>
      <div className="bg-panel w-full max-w-md rounded-2xl shadow-2xl overflow-hidden border border-red-100 animate-in zoom-in-95 duration-200" onClick={e => e.stopPropagation()}>
        <div className="px-6 py-5 flex flex-col items-center text-center">
          <div className="w-16 h-16 rounded-full bg-red-50 text-red-500 flex items-center justify-center mb-4">
            <ShieldAlert size={32} />
          </div>
          <h2 className="text-xl font-bold text-textMain mb-2">{t('pluginMarket.securityWarningTitle')}</h2>
          <p className="text-sm text-textMuted leading-relaxed">
            {t('pluginMarket.securityWarningDesc', { author: plugin.author })}
          </p>

          <div className="mt-6 w-full p-4 bg-bgLight rounded-xl border border-border text-left">
            <div className="text-xs font-bold text-textMuted uppercase mb-1">{t('pluginMarket.sourceLabel')}</div>
            <div className="text-sm text-textMain font-mono break-all">{plugin.git_url || plugin.download_url}</div>
          </div>
        </div>

        <div className="px-6 py-4 border-t border-border flex flex-col gap-2 bg-bgLight/30">
          <button
            onClick={onConfirm}
            className="w-full py-3 rounded-xl bg-red-500 text-white font-bold hover:bg-red-600 transition-colors"
          >
            {t('pluginMarket.securityWarningConfirm')}
          </button>
          <button
            onClick={onCancel}
            className="w-full py-3 rounded-xl text-textMuted font-medium hover:bg-bgLight transition-colors"
          >
            {t('pluginMarket.securityWarningCancel')}
          </button>
        </div>
      </div>
    </div>
  );
};

// ---- Git Install Modal Component ----

interface GitInstallModalProps {
  onClose: () => void;
  onSuccess: (jobId: string, pluginId: string) => void;
}

const GitInstallModal: React.FC<GitInstallModalProps> = ({ onClose, onSuccess }) => {
  const { t } = useTranslation();
  const [url, setUrl] = useState('');
  const [loading, setLoading] = useState<'smart' | 'build' | null>(null);
  const [error, setError] = useState('');

  const handleInstall = async (mode: 'smart' | 'build') => {
    if (!url.trim()) return;
    setLoading(mode);
    setError('');
    try {
      const res = await pluginMarketAPI.installPluginFromGit(url.trim(), undefined, mode);
      onSuccess(res.job_id, res.plugin_id);
    } catch (e: any) {
      setError(e?.message || 'Git install failed');
    } finally {
      setLoading(null);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center backdrop-blur-sm p-4" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="bg-panel w-full max-w-md rounded-2xl shadow-2xl overflow-hidden border border-border animate-in zoom-in-95 duration-200">
        <div className="px-6 py-4 border-b border-border flex items-center justify-between bg-bgLight/50">
          <div className="flex items-center gap-2 font-bold text-textMain">
            <Github size={18} />
            <span>{t('pluginMarket.installFromGitTitle')}</span>
          </div>
          <button onClick={onClose} className="p-1.5 rounded-lg hover:bg-bgLight text-textMuted transition-colors">
            <X size={20} />
          </button>
        </div>
        <div className="p-6 space-y-4">
          <div>
            <label className="block text-xs font-semibold text-textMuted uppercase mb-1.5">{t('pluginMarket.gitUrlLabel')}</label>
            <input
              type="text"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://github.com/user/repo"
              className="w-full bg-bgLight border border-border rounded-xl px-4 py-2.5 text-sm text-textMain focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary transition-all"
              autoFocus
              onKeyDown={(e) => e.key === 'Enter' && handleInstall('smart')}
            />
          </div>
          {error && (
            <div className="flex items-start gap-2.5 bg-red-50 border border-red-100 rounded-xl px-3 py-2.5 text-xs text-red-700">
              <AlertCircle size={14} className="shrink-0 mt-0.5" />
              <span>{error}</span>
            </div>
          )}
          <p className="text-xs text-textMuted leading-relaxed">
            {t('pluginMarket.gitInstallHint')}
          </p>
        </div>
        <div className="px-6 py-4 border-t border-border flex justify-end gap-3 bg-bgLight/30">
          <button onClick={onClose} className="px-4 py-2 rounded-xl border border-border text-textMuted text-sm font-medium hover:bg-bgLight transition-colors">
            {t('pluginMarket.close')}
          </button>
          <button
            onClick={() => handleInstall('build')}
            disabled={!!loading || !url.trim()}
            className="flex items-center gap-2 px-4 py-2 rounded-xl border border-border bg-bgLight text-textMain text-sm font-medium hover:bg-bgLight/80 transition-colors disabled:opacity-50"
          >
            {loading === 'build' ? <OpenSquadLoader size={16} /> : <Hammer size={14} />}
            {t('pluginMarket.installBuild')}
          </button>
          <button
            onClick={() => handleInstall('smart')}
            disabled={!!loading || !url.trim()}
            className="flex items-center gap-2 px-5 py-2 rounded-xl bg-primary text-white text-sm font-medium hover:opacity-90 transition-opacity disabled:opacity-50"
          >
            {loading === 'smart' ? <OpenSquadLoader size={14} /> : <Zap size={14} />}
            {t('pluginMarket.installSmart')}
          </button>
        </div>
      </div>
    </div>
  );
};

// ---- Install Job Console Component ----

interface InstallJobConsoleProps {
  jobId: string;
  pluginId: string;
  onClose: () => void;
}

const STEP_LABELS: Record<string, string> = {
  pending: 'pluginMarket.stepPending',
  cloning: 'pluginMarket.stepClone',
  checking: 'pluginMarket.stepCheck',
  building: 'pluginMarket.stepBuild',
  done: 'pluginMarket.stepDone',
  failed: 'pluginMarket.stepFailed',
};

const InstallJobConsole: React.FC<InstallJobConsoleProps> = ({ jobId, pluginId, onClose }) => {
  const { t } = useTranslation();
  const [job, setJob] = useState<any>(null);
  const [buildLog, setBuildLog] = useState('');
  const [buildLogDone, setBuildLogDone] = useState(false); // 构建日志是否已获取完毕
  const [selectedStep, setSelectedStep] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  // 记录每个步骤的日志（用于回看）
  const stepLogsRef = useRef<Record<string, string>>({});

  const fetchJob = useCallback(async () => {
    try {
      const j = await pluginMarketAPI.getGitInstallJob(jobId);
      // 记录当前步骤的日志（供后续回看）
      if (j?.step && j?.message && stepLogsRef.current[j.step] !== j.message) {
        stepLogsRef.current[j.step] = j.message;
      }
      setJob(j);
      if (j?.status === 'building') {
        try {
          const logRes = await pluginMarketAPI.getBuildLog(pluginId);
          const log = logRes.log || '';
          setBuildLog(log);
          if (log.trim()) {
            stepLogsRef.current['building'] = log;
          }
        } catch { /* silent */ }
      }
      if (j?.status === 'done') {
        // 构建完成，再拉一次日志确保获取到
        if (!buildLogDone) {
          try {
            const logRes = await pluginMarketAPI.getBuildLog(pluginId);
            const log = logRes.log || '';
            if (log.trim()) {
              setBuildLog(log);
              stepLogsRef.current['building'] = log;
            }
            setBuildLogDone(true);
          } catch { /* silent */ }
        }
        // 记录完成信息
        stepLogsRef.current['done'] = JSON.stringify({
          message: j.message || t('pluginMarket.installDone'),
          dist_found: j.dist_found,
          plugin_dir: j.plugin_dir,
          dist_path: j.dist_path,
        });
      }
    } catch { /* silent */ }
  }, [jobId, pluginId, t]);

  useEffect(() => {
    fetchJob();
    const timer = setInterval(() => {
      fetchJob();
    }, 1500);
    return () => clearInterval(timer);
  }, [fetchJob]);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [buildLog, job]);

  const status = job?.status ?? 'pending';
  const isDone = status === 'done' || status === 'failed';
  const steps = ['cloning', 'checking', 'building', 'done'];
  const currentStepIdx = steps.indexOf(status === 'failed' ? 'done' : status);

  // 获取步骤图标
  const getStepIcon = (step: string, idx: number) => {
    const isActive = step === status || (status === 'failed' && step === 'done');
    const isPast = idx < currentStepIdx;
    const isFailed = status === 'failed' && step === 'done';
    if (isFailed) return <AlertCircle size={11} />;
    if (isPast || (isDone && !isFailed && idx <= currentStepIdx)) return <CheckCircle size={11} />;
    if (isActive && !isDone) return <OpenSquadLoader size={14} />;
    return null;
  };

  // 获取步骤样式
  const getStepCls = (step: string, idx: number) => {
    const isActive = step === status || (status === 'failed' && step === 'done');
    const isPast = idx < currentStepIdx;
    const isFailed = status === 'failed' && step === 'done';
    const isSelected = selectedStep === step;
    let cls = 'flex items-center gap-1.5 text-xs font-medium px-2.5 py-1 rounded-full transition-colors cursor-pointer ';
    if (isFailed) cls += 'bg-red-500/20 text-red-400 hover:bg-red-500/30';
    else if (isActive && !isDone) cls += 'bg-primary/20 text-primary hover:bg-primary/30';
    else if (isPast || (isDone && !isFailed && idx <= currentStepIdx)) cls += 'bg-green-500/20 text-green-400 hover:bg-green-500/30';
    else cls += 'text-white/30 hover:text-white/50 hover:bg-white/5';
    if (isSelected) cls += ' ring-1 ring-white/30';
    return cls;
  };

  // 渲染日志内容（根据选中的步骤或当前状态）
  const renderLogContent = () => {
    const displayStep = selectedStep || status;
    const stepLog = stepLogsRef.current[displayStep];

    if (displayStep === 'building') {
      if (buildLog) {
        return <pre className="whitespace-pre-wrap break-all">{buildLog}</pre>;
      }
      return <div className="flex items-center justify-center h-full text-white/40 text-sm">等待构建日志...</div>;
    }

    if (displayStep === 'done' && status === 'done') {
      let doneInfo: any = {};
      try { doneInfo = stepLog ? JSON.parse(stepLog) : {}; } catch {}
      return (
        <div className="flex flex-col items-center justify-center h-full gap-4 px-4">
          <CheckCircle size={40} className="text-green-400" />
          <span className="text-base font-semibold text-green-400">{t('pluginMarket.installDone')}</span>
          <div className="w-full max-w-sm bg-white/5 rounded-xl p-4 space-y-2 text-xs text-white/60">
            {doneInfo.plugin_dir && (
              <div className="flex justify-between">
                <span>安装目录</span>
                <span className="text-white/40 font-mono truncate ml-2 max-w-[200px]">{String(doneInfo.plugin_dir).split('/').pop() || doneInfo.plugin_dir}</span>
              </div>
            )}
            {doneInfo.dist_found !== undefined && (
              <div className="flex justify-between">
                <span>预构建文件</span>
                <span className={doneInfo.dist_found ? 'text-green-400' : 'text-white/40'}>{doneInfo.dist_found ? '✅ 已找到' : '未找到'}</span>
              </div>
            )}
            {doneInfo.dist_path && (
              <div className="flex justify-between">
                <span>构建输出</span>
                <span className="text-white/40 font-mono truncate ml-2 max-w-[200px]">{String(doneInfo.dist_path)}</span>
              </div>
            )}
            <div className="flex justify-between pt-2 border-t border-white/10">
              <span>安装结果</span>
              <span className="text-green-400">✓ 成功</span>
            </div>
          </div>
          {buildLog && (
            <details className="w-full max-w-sm text-xs">
              <summary className="text-white/40 cursor-pointer hover:text-white/70">查看构建日志</summary>
              <pre className="mt-2 p-3 bg-white/5 rounded-lg whitespace-pre-wrap break-all max-h-40 overflow-y-auto text-white/50">{buildLog}</pre>
            </details>
          )}
        </div>
      );
    }

    if (displayStep === 'done' && status === 'failed') {
      return (
        <div className="flex flex-col items-center justify-center h-full gap-3 text-red-400">
          <AlertCircle size={40} />
          <span className="text-base font-semibold">{t('pluginMarket.installFailed')}</span>
          {job?.error && <pre className="text-xs text-red-300/70 whitespace-pre-wrap break-all max-w-full px-4">{job.error}</pre>}
        </div>
      );
    }

    // 其他步骤：显示步骤的日志文字
    if (stepLog) {
      return <div className="flex flex-col items-center justify-center h-full gap-2 text-white/40 text-sm"><pre className="whitespace-pre-wrap break-all text-center">{stepLog}</pre></div>;
    }

    // 正在进行的步骤（还没日志）
    return (
      <div className="flex items-center justify-center h-full text-white/30 text-sm gap-2">
        <OpenSquadLoader size={18} />
        {STEP_LABELS[displayStep] ? t(STEP_LABELS[displayStep]) : t('pluginMarket.waitingForLogs')}...
      </div>
    );
  };

  return (
    <div className="fixed inset-0 bg-black/60 z-[60] flex items-center justify-center p-4 backdrop-blur-sm">
      <div className="bg-[#1e1e1e] w-full max-w-2xl rounded-2xl shadow-2xl flex flex-col overflow-hidden border border-white/10" style={{ height: 520 }}>
        {/* Header */}
        <div className="px-5 py-4 border-b border-white/5 flex items-center justify-between bg-[#252525]">
          <div className="flex items-center gap-3 text-white">
            <Terminal size={18} className="text-primary" />
            <span className="font-bold">{t('pluginMarket.installingPlugin', { id: pluginId })}</span>
            {!isDone && <OpenSquadLoader size={14} />}
          </div>
          <button onClick={onClose} className="p-1.5 rounded-lg hover:bg-white/10 text-white/50 transition-colors">
            <X size={20} />
          </button>
        </div>

        {/* Step indicator — 可点击 */}
        <div className="px-5 py-3 bg-[#252525] border-b border-white/5 flex items-center gap-2">
          {steps.map((step, idx) => (
            <React.Fragment key={step}>
              <button
                onClick={() => setSelectedStep(selectedStep === step ? null : step)}
                title={selectedStep === step ? '返回实时视图' : `查看 ${t(STEP_LABELS[step])} 详情`}
                className={getStepCls(step, idx)}
              >
                {getStepIcon(step, idx)}
                {t(STEP_LABELS[step])}
              </button>
              {idx < steps.length - 1 && <div className="w-4 h-px bg-white/15 shrink-0" />}
            </React.Fragment>
          ))}
          {selectedStep && (
            <button
              onClick={() => setSelectedStep(null)}
              className="ml-1 text-[10px] text-white/30 hover:text-white/60 transition-colors"
            >
              ↻
            </button>
          )}
        </div>

        {/* Log area */}
        <div ref={scrollRef} className="flex-1 overflow-y-auto p-5 font-mono text-sm leading-relaxed text-gray-300">
          {renderLogContent()}
        </div>

        {/* Footer */}
        <div className="px-5 py-3 border-t border-white/5 bg-[#252525] flex items-center justify-between">
          <div className="flex items-center gap-2">
            <div className={`w-2 h-2 rounded-full ${
              status === 'done' ? 'bg-green-500' :
              status === 'failed' ? 'bg-red-500' :
              'bg-yellow-400 animate-pulse'
            }`} />
            <span className="text-xs font-medium uppercase tracking-wider text-white/60">
              {selectedStep ? t(STEP_LABELS[selectedStep]) : (STEP_LABELS[status] ? t(STEP_LABELS[status]) : status)}
            </span>
          </div>
          {isDone && (
            <button onClick={onClose} className="px-4 py-1.5 rounded-lg bg-white/10 hover:bg-white/15 text-white text-sm font-medium transition-colors">
              {t('pluginMarket.close')}
            </button>
          )}
        </div>
      </div>
    </div>
  );
};

// ---- Plugin Detail Modal ----

interface DetailModalProps {
  plugin: MarketPlugin;
  installed: InstalledPluginInfo | null;
  installing: boolean;
  uninstalling: boolean;
  liked: boolean;
  onClose: () => void;
  onLike: (id: string) => void;
  onInstallWithMode: (id: string, mode: 'smart' | 'build') => void;
  onUninstall: (id: string) => void;
  pluginLang: PluginLang;
  appLang: string;
}

const DetailModal: React.FC<DetailModalProps> = ({
  plugin, installed, installing, uninstalling, liked, onClose, onLike, onInstallWithMode, onUninstall, pluginLang, appLang,
}) => {
  const { t } = useTranslation();
  const [showSecurityWarning, setShowSecurityWarning] = useState(false);

  const handleInstallClick = (mode: 'smart' | 'build') => {
    if (plugin.is_featured) {
      onInstallWithMode(plugin.id, mode);
    } else {
      setShowSecurityWarning(true);
    }
  };

  // Store pending mode for security warning confirm
  const [pendingMode, setPendingMode] = useState<'smart' | 'build'>('smart');

  const handleInstallClickWithWarning = (mode: 'smart' | 'build') => {
    if (plugin.is_featured) {
      onInstallWithMode(plugin.id, mode);
    } else {
      setPendingMode(mode);
      setShowSecurityWarning(true);
    }
  };

  const badgeCls = TYPE_COLORS[plugin.type] || 'bg-gray-100 text-gray-600';
  const hasUpdate = installed ? versionGt(plugin.version, installed.version) : false;
  const isInstalled = !!installed && !hasUpdate;
  const typeKey = `pluginMarket.type${plugin.type.charAt(0).toUpperCase() + plugin.type.slice(1)}` as const;

  const displayName = getPluginText(plugin as any, 'name', pluginLang, appLang);
  const displayDesc = getPluginText(plugin as any, 'description', pluginLang, appLang);

  // Close on backdrop click
  const handleBackdropClick = (e: React.MouseEvent<HTMLDivElement>) => {
    if (e.target === e.currentTarget) onClose();
  };

  // Close on Escape
  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center backdrop-blur-sm p-4"
      onClick={handleBackdropClick}
    >
      <div className="bg-panel rounded-2xl shadow-2xl w-full max-w-xl border border-border overflow-hidden flex flex-col max-h-[85vh]">
        {/* Header */}
        <div className="flex items-start gap-4 md:gap-6 px-6 py-5 border-b border-border">
          {plugin.icon_url ? (
            <img src={plugin.icon_url} alt={plugin.name} className="w-20 h-20 sm:w-24 sm:h-24 md:w-28 md:h-28 rounded-2xl object-cover shrink-0" loading="lazy" />
          ) : (
            <div className={`w-20 h-20 sm:w-24 sm:h-24 md:w-28 md:h-28 rounded-2xl flex items-center justify-center text-white text-2xl sm:text-3xl md:text-4xl font-bold shrink-0 ${avatarColor(plugin.id)}`}>
              {plugin.name.charAt(0).toUpperCase()}
            </div>
          )}
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <h2 className="text-lg font-bold text-textMain leading-tight flex items-center gap-1.5">
                {displayName}
                {plugin.is_featured && (
                  <BadgeCheck size={16} className="text-blue-500 fill-blue-500/10 shrink-0" />
                )}
              </h2>
              <span className={`text-xs px-2 py-0.5 rounded-full font-medium shrink-0 ${badgeCls}`}>
                {t(typeKey, { defaultValue: plugin.type })}
              </span>
              {isInstalled && (
                <span className="text-xs px-2 py-0.5 rounded-full bg-green-100 text-green-700 font-medium shrink-0">
                  {t('pluginMarket.installed')}
                </span>
              )}
              {hasUpdate && (
                <span className="text-xs px-2 py-0.5 rounded-full bg-amber-100 text-amber-700 font-medium shrink-0">
                  {t('pluginMarket.update')}
                </span>
              )}
            </div>
            <div className="flex items-center gap-3 mt-1.5 text-xs text-textMuted flex-wrap">
              <span className="flex items-center gap-1"><User size={11} />{plugin.author}</span>
              <span className="flex items-center gap-1"><Package size={11} />v{plugin.version}</span>
              {installed && (
                <span className="flex items-center gap-1 text-textMuted/70">
                  ({t('pluginMarket.alreadyInstalled', { version: installed.version })})
                </span>
              )}
              <span className="flex items-center gap-1"><Calendar size={11} />{formatDate(plugin.created_at)}</span>
            </div>
          </div>
          <button onClick={onClose} className="text-textMuted hover:text-textMain transition-colors shrink-0 mt-0.5">
            <X size={20} />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-6 py-4 flex flex-col gap-4">
          {/* Description */}
          <div>
            <h3 className="text-xs font-semibold text-textMuted uppercase mb-1.5">{t('pluginMarket.description')}</h3>
            <p className="text-sm text-textMain leading-relaxed whitespace-pre-wrap">{displayDesc}</p>
          </div>

          {/* Tags */}
          {plugin.tags.length > 0 && (
            <div>
              <h3 className="text-xs font-semibold text-textMuted uppercase mb-1.5 flex items-center gap-1"><Tag size={11} />{t('pluginMarket.tags')}</h3>
              <div className="flex flex-wrap gap-1.5">
                {plugin.tags.map(tag => (
                  <span key={tag} className="text-xs bg-bgLight text-textMuted px-2.5 py-1 rounded-full border border-border">{tag}</span>
                ))}
              </div>
            </div>
          )}

          {/* Links */}
          {(plugin.homepage || plugin.git_url) && (
            <div>
              <h3 className="text-xs font-semibold text-textMuted uppercase mb-1.5">{t('pluginMarket.links')}</h3>
              <div className="flex flex-col gap-1.5">
                {plugin.homepage && (
                  <a href={plugin.homepage} target="_blank" rel="noopener noreferrer"
                    className="flex items-center gap-2 text-sm text-primary hover:underline">
                    <Globe size={13} />{plugin.homepage}
                  </a>
                )}
                {plugin.git_url && plugin.git_url !== plugin.homepage && (
                  <a href={plugin.git_url} target="_blank" rel="noopener noreferrer"
                    className="flex items-center gap-2 text-sm text-primary hover:underline">
                    <GitBranch size={13} />{plugin.git_url}
                  </a>
                )}
              </div>
            </div>
          )}

          {/* Stats */}
          <div className="flex items-center gap-4 py-3 px-4 bg-bgLight rounded-xl border border-border">
            <button
              onClick={() => !liked && onLike(plugin.id)}
              disabled={liked}
              className={`flex items-center gap-1.5 text-sm transition-colors ${liked ? 'text-red-500 cursor-default' : 'text-textMuted hover:text-red-500'}`}
            >
              <Heart size={15} fill={liked ? 'currentColor' : 'none'} />
              <span className="font-medium">{plugin.likes}</span>
              <span className="text-xs">{t('pluginMarket.likes')}</span>
            </button>
            <div className="w-px h-4 bg-border" />
            <span className="text-sm text-textMuted">ID: <code className="font-mono text-textMain text-xs bg-panel px-1.5 py-0.5 rounded border border-border">{plugin.id}</code></span>
          </div>
        </div>

        {/* Footer — action buttons */}
        <div className="flex items-center gap-3 px-6 py-4 border-t border-border bg-panel/50">
          {installed && (
            <button
              onClick={() => onUninstall(plugin.id)}
              disabled={uninstalling || installing}
              className="flex items-center gap-2 px-4 py-2.5 rounded-xl border border-red-200 text-red-600 text-sm font-medium hover:bg-red-50 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {uninstalling ? <OpenSquadLoader size={16} /> : <Trash2 size={14} />}
              {uninstalling ? t('pluginMarket.uninstalling') : t('pluginMarket.uninstall')}
            </button>
          )}
          <div className="flex-1" />
          <button onClick={onClose} className="px-4 py-2.5 rounded-xl border border-border text-textMuted text-sm font-medium hover:bg-bgLight transition-colors">
            {t('pluginMarket.close')}
          </button>
          {!isInstalled && (
            <div className="flex items-center gap-2">
              <button
                onClick={() => handleInstallClickWithWarning('build')}
                disabled={installing || uninstalling}
                className="flex items-center gap-2 px-4 py-2.5 rounded-xl border border-border bg-bgLight text-textMain text-sm font-medium hover:bg-bgLight/80 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {installing ? <OpenSquadLoader size={16} /> : <Hammer size={14} />}
                {t('pluginMarket.installBuild')}
              </button>
              <button
                onClick={() => handleInstallClickWithWarning('smart')}
                disabled={installing || uninstalling}
                className={`flex items-center gap-2 px-5 py-2.5 rounded-xl text-sm font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed ${
                  hasUpdate
                    ? 'bg-amber-500 text-white hover:opacity-90'
                    : 'bg-primary text-white hover:opacity-90'
                }`}
              >
                {installing ? <OpenSquadLoader size={14} /> : <Zap size={14} />}
                {installing ? t('pluginMarket.installing') : hasUpdate ? t('pluginMarket.updateNow') : t('pluginMarket.installSmart')}
              </button>
            </div>
          )}
        </div>
      </div>

      {showSecurityWarning && (
        <SecurityWarningModal
          plugin={plugin}
          onConfirm={() => { setShowSecurityWarning(false); onInstallWithMode(plugin.id, pendingMode); }}
          onCancel={() => setShowSecurityWarning(false)}
        />
      )}
    </div>
  );
};


// ---- Main Page ----

export const PluginMarketPage: React.FC<PluginMarketPageProps> = () => {
  const { t, i18n } = useTranslation();
  const appLang = i18n.language;

  const [data, setData] = useState<PluginListResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [search, setSearch] = useState('');
  const [searchInput, setSearchInput] = useState('');
  const [typeFilter, setTypeFilter] = useState('');
  const [selectedCategory, setSelectedCategory] = useState('all');
  const [catDropdownOpen, setCatDropdownOpen] = useState(false);
  const catBtnRef = useRef<HTMLButtonElement>(null);
  const [catDropdownPos, setCatDropdownPos] = useState<{ top: number; left: number } | null>(null);

  const [sort, setSort] = useState('likes');
  const [order] = useState('desc');
  const [page, setPage] = useState(1);

  // Plugin description language toggle
  const [pluginLang, setPluginLang] = useState<PluginLang>(() => {
    return (localStorage.getItem(PLUGIN_LANG_KEY) as PluginLang) || 'auto';
  });

  // 已点赞插件 ID 集合，持久化到 localStorage
  const [likedSet, setLikedSet] = useState<Set<string>>(() => {
    try {
      const raw = localStorage.getItem(PLUGIN_LIKED_KEY);
      return raw ? new Set<string>(JSON.parse(raw)) : new Set<string>();
    } catch {
      return new Set<string>();
    }
  });

  const [allRegistryPlugins, setAllRegistryPlugins] = useState<MarketPlugin[]>([]);
  const [notification, setNotification] = useState<{ type: 'success' | 'error', msg: string } | null>(null);
  const [detailPlugin, setDetailPlugin] = useState<MarketPlugin | null>(null);
  const [showUpload, setShowUpload] = useState(false);

  const handlePluginLangChange = (lang: PluginLang) => {
    setPluginLang(lang);
    localStorage.setItem(PLUGIN_LANG_KEY, lang);
  };

  const [installedMap, setInstalledMap] = useState<Record<string, InstalledPluginInfo>>({});
  const [installingIds, setInstallingIds] = useState<Set<string>>(new Set());
  const [uninstallingIds, setUninstallingIds] = useState<Set<string>>(new Set());
  const [activeInstallJob, setActiveInstallJob] = useState<{ jobId: string; pluginId: string } | null>(null);
  const [showGitInstall, setShowGitInstall] = useState(false);
  const [installNodeResults, setInstallNodeResults] = useState<{ pluginId: string; results: Array<{ node_id: string; node_label: string; ok: boolean; action: string; message: string }> } | null>(null);

  const refreshInstalled = useCallback(async () => {
    try {
      const res = await pluginMarketAPI.getInstalled();
      setInstalledMap(res.installed);
    } catch { /* silent */ }
  }, []);

  useEffect(() => {
    refreshInstalled();
    // Fetch all registry plugins for update count
    pluginMarketAPI.listAllPlugins().then(r => setAllRegistryPlugins(r.plugins)).catch(() => {});
  }, [refreshInstalled]);

  const fetchPlugins = useCallback(async () => {

    setLoading(true);
    setError(null);
    try {
      const result = await pluginMarketAPI.listPlugins({
        page, size: 9, search, type: typeFilter,
        category: selectedCategory !== 'all' ? selectedCategory : '',
        sort, order,
      });
      setData(result);
    } catch (e: any) {
      setError(e?.message || t('pluginMarket.loadFailed'));
    } finally {
      setLoading(false);
    }
  }, [page, search, typeFilter, selectedCategory, sort, order, t]);

  useEffect(() => { fetchPlugins(); }, [fetchPlugins]);
  useEffect(() => { setPage(1); }, [search, typeFilter, selectedCategory, sort]);

  const handleSearch = (e: React.FormEvent) => { e.preventDefault(); setSearch(searchInput.trim()); };

  const showNotification = (type: 'success' | 'error', msg: string) => {
    setNotification({ type, msg });
    setTimeout(() => setNotification(null), 4000);
  };

  const handleLike = async (id: string) => {
    if (likedSet.has(id)) {
      showNotification('error', t('pluginMarket.alreadyLiked'));
      return;
    }
    try {
      const res = await pluginMarketAPI.likePlugin(id);
      if (res.already_liked) {
        // 后端判断该节点已点赞（可能跨浏览器/重装）
        showNotification('error', t('pluginMarket.alreadyLiked'));
        // 同步到本地 likedSet
        setLikedSet(prev => {
          const next = new Set(prev);
          next.add(id);
          localStorage.setItem(PLUGIN_LIKED_KEY, JSON.stringify([...next]));
          return next;
        });
        return;
      }
      // 点赞成功：更新计数 + 标记已点赞
      setData(prev => prev ? { ...prev, plugins: prev.plugins.map(p => p.id === id ? { ...p, likes: res.likes } : p) } : prev);
      setAllRegistryPlugins(prev => prev.map(p => p.id === id ? { ...p, likes: res.likes } : p));
      setDetailPlugin(prev => prev && prev.id === id ? { ...prev, likes: res.likes } : prev);
      setLikedSet(prev => {
        const next = new Set(prev);
        next.add(id);
        localStorage.setItem(PLUGIN_LIKED_KEY, JSON.stringify([...next]));
        return next;
      });
    } catch { /* silent */ }
  };

  const handleInstallWithMode = async (id: string, mode: 'smart' | 'build', gitUrl?: string) => {
    setInstallingIds(prev => new Set([...prev, id]));
    try {
      let res;
      if (gitUrl) {
        res = await pluginMarketAPI.installPluginFromGit(gitUrl, id, mode);
      } else {
        res = await pluginMarketAPI.installPlugin(id, mode);
      }
      // Git 异步安装：跳转到 job 控制台
      if (res.job_id) {
        setActiveInstallJob({ jobId: res.job_id, pluginId: res.plugin_id! });
        return;
      }
      // Zip 直接安装：显示各节点结果
      if (res.node_results && res.node_results.length > 1) {
        setInstallNodeResults({ pluginId: id, results: res.node_results });
      } else {
        showNotification('success', res.message || t('pluginMarket.installSuccess', { id }));
      }
      // 等 300ms 确保文件系统完全写入，再刷新已安装列表
      setTimeout(() => {
        refreshInstalled();
        pluginMarketAPI.listAllPlugins().then(r => setAllRegistryPlugins(r.plugins)).catch(() => {});
        // 通知 PluginManagerPage 刷新
        window.dispatchEvent(new CustomEvent('opensquad:market-install', { detail: { kind: 'plugin', id } }));
      }, 300);
    } catch (e: any) {
      showNotification('error', e?.message || t('pluginMarket.installFailed'));
    } finally {
      setInstallingIds(prev => { const n = new Set(prev); n.delete(id); return n; });
    }
  };

  const handleGitSuccess = (jobId: string, pluginId: string) => {
    setShowGitInstall(false);
    setActiveInstallJob({ jobId, pluginId });
    setInstallingIds(prev => new Set([...prev, pluginId]));
  };

  const handleInstall = async (id: string) => {
    await handleInstallWithMode(id, 'smart');
  };

  const handleUninstall = async (id: string) => {
    if (!confirm(t('pluginMarket.uninstallConfirm', { id }))) return;
    setUninstallingIds(prev => new Set([...prev, id]));
    try {
      await pluginMarketAPI.uninstallPlugin(id);
      refreshInstalled();
      showNotification('success', t('pluginMarket.uninstallSuccess', { id }));
      // Close detail modal if showing this plugin
      setDetailPlugin(prev => prev?.id === id ? null : prev);
    } catch (e: any) {
      showNotification('error', e?.message || t('pluginMarket.uninstallFailed'));
    } finally {
      setUninstallingIds(prev => { const n = new Set(prev); n.delete(id); return n; });
    }
  };

  const handleUploadSuccess = (_plugin: MarketPlugin) => {
    setShowUpload(false);
    fetchPlugins();
    // Refresh all registry
    pluginMarketAPI.listAllPlugins().then(r => setAllRegistryPlugins(r.plugins)).catch(() => {});
  };

  // Global update count: compare ALL installed plugins against allRegistryPlugins
  const totalUpdateCount = allRegistryPlugins.filter(p => {
    const inst = installedMap[p.id];
    return inst && versionGt(p.version, inst.version);
  }).length;

  // Category counts from allRegistryPlugins (all fetched, no filter)
  const catCounts = useMemo(() => {
    const counts: Record<string, number> = { all: allRegistryPlugins.length };
    CATEGORIES.forEach(cat => {
      if (cat.id !== 'all') {
        counts[cat.id] = allRegistryPlugins.filter(p => p.category === cat.id).length;
      }
    });
    return counts;
  }, [allRegistryPlugins]);

  const TYPE_FILTERS = [
    { value: '', label: t('pluginMarket.allTypes') },
    { value: 'tool', label: t('pluginMarket.typeTool') },
    { value: 'platform', label: t('pluginMarket.typePlatform') },
    { value: 'hook', label: t('pluginMarket.typeHook') },
  ];

  const SORT_OPTIONS = [
    { value: 'likes', label: t('pluginMarket.sortLikes') },
    { value: 'created_at', label: t('pluginMarket.sortTime') },
    { value: 'name', label: t('pluginMarket.sortName') },
  ];

  const PLUGIN_LANG_OPTIONS: { value: PluginLang; label: string }[] = [
    { value: 'auto', label: t('pluginMarket.pluginLang.auto') },
    { value: 'zh', label: t('pluginMarket.pluginLang.zh') },
    { value: 'en', label: t('pluginMarket.pluginLang.en') },
  ];

  const paginationEl = data && data.pages > 1 && (
    <Pagination page={page} pages={data.pages} onChange={setPage} />
  );

  return (
    <div className="flex-1 min-w-0 h-full flex flex-col bg-bgLight overflow-hidden">
      {/* Toast */}
      {notification && (
        <div className={`fixed top-4 right-4 z-50 flex items-center gap-2 px-4 py-3 rounded-xl shadow-lg text-sm font-medium animate-in slide-in-from-top-2 duration-200 ${notification.type === 'success' ? 'bg-green-50 text-green-800 border border-green-200' : 'bg-red-50 text-red-800 border border-red-200'}`}>
          {notification.type === 'success' ? <CheckCircle size={16} /> : <AlertCircle size={16} />}
          {notification.msg}
        </div>
      )}

      {showUpload && <SubmitPRModal onClose={() => setShowUpload(false)} />}
      {showGitInstall && <GitInstallModal onClose={() => setShowGitInstall(false)} onSuccess={handleGitSuccess} />}

      {activeInstallJob && (
        <InstallJobConsole
          jobId={activeInstallJob.jobId}
          pluginId={activeInstallJob.pluginId}
          onClose={() => {
            const pid = activeInstallJob.pluginId;
            setActiveInstallJob(null);
            setInstallingIds(prev => { const n = new Set(prev); n.delete(pid); return n; });
            refreshInstalled();
            pluginMarketAPI.listAllPlugins().then(r => setAllRegistryPlugins(r.plugins)).catch(() => {});
          }}
        />
      )}

      {installNodeResults && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
          <div className="bg-gray-900 border border-gray-700 rounded-xl shadow-2xl p-6 w-full max-w-md">
            <h3 className="text-lg font-semibold text-white mb-4">
              {t('pluginMarket.installNodeResults', { id: installNodeResults.pluginId })}
            </h3>
            <div className="space-y-2 max-h-64 overflow-y-auto">
              {installNodeResults.results.map(r => (
                <div key={r.node_id} className="flex items-center gap-3 px-3 py-2 rounded-lg bg-gray-800">
                  <span className={`w-2 h-2 rounded-full flex-shrink-0 ${r.ok ? 'bg-green-400' : 'bg-red-400'}`} />
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium text-white truncate">{r.node_label || r.node_id}</p>
                    <p className={`text-xs truncate ${r.ok ? 'text-gray-400' : 'text-red-400'}`}>{r.message}</p>
                  </div>
                  <span className={`text-xs px-2 py-0.5 rounded-full flex-shrink-0 ${r.ok ? 'bg-green-900/60 text-green-300' : 'bg-red-900/60 text-red-300'}`}>
                    {r.action || (r.ok ? 'ok' : 'error')}
                  </span>
                </div>
              ))}
            </div>
            <button
              onClick={() => setInstallNodeResults(null)}
              className="mt-4 w-full py-2 bg-blue-600 hover:bg-blue-500 text-white rounded-lg text-sm font-medium transition-colors"
            >
              {t('common.confirm') || 'OK'}
            </button>
          </div>
        </div>
      )}

      {detailPlugin && (
        <DetailModal
          plugin={detailPlugin}
          installed={installedMap[detailPlugin.id] ?? null}
          installing={installingIds.has(detailPlugin.id)}
          uninstalling={uninstallingIds.has(detailPlugin.id)}
          liked={likedSet.has(detailPlugin.id)}
          onClose={() => setDetailPlugin(null)}
          onLike={handleLike}
          onInstallWithMode={handleInstallWithMode}
          onUninstall={handleUninstall}
          pluginLang={pluginLang}
          appLang={appLang}
        />
      )}

      {/* Header */}
      <div className="shrink-0 bg-panel border-b border-border px-4 md:px-6 py-2 md:py-3">
        <div className="flex items-center gap-2 md:gap-4 h-9">
          <div className="flex items-center gap-2 md:gap-3 shrink-0">
            <Store size={20} className="text-primary shrink-0" />
            <h1 className="text-base md:text-xl font-bold text-textMain whitespace-nowrap hidden sm:block">{t('pluginMarket.title')}</h1>

            {/* Mobile Category Dropdown - moved to header row */}
            <div className="relative md:hidden shrink-0">
              <button
                ref={catBtnRef}
                onClick={(e) => {
                  e.stopPropagation();
                  if (!catDropdownOpen && catBtnRef.current) {
                    const rect = catBtnRef.current.getBoundingClientRect();
                    setCatDropdownPos({ top: rect.bottom + 4, left: Math.min(rect.left, window.innerWidth - 160) });
                  }
                  setCatDropdownOpen(v => !v);
                }}
                className="flex items-center gap-1 px-1.5 py-1 rounded-lg bg-bgLight border border-border text-[10px] font-medium text-textMain hover:border-primary/50"
              >
                <Puzzle size={11} className="text-primary" />
                <span>{t(CATEGORIES.find(c => c.id === selectedCategory)?.label || 'pluginMarket.categoryLabel')}</span>
                <ChevronDown size={10} className="text-textMuted" />
              </button>
            </div>

            <div className="hidden sm:flex items-center gap-2 shrink-0 border-l border-border pl-2 md:pl-4">
              <span className="text-[11px] md:text-sm font-medium text-textMain">
                {data ? `${data.total}` : ''}
              </span>
              <button onClick={fetchPlugins} disabled={loading} className="p-1 rounded-lg text-textMuted hover:text-primary hover:bg-primary/10 transition-colors disabled:opacity-50" title={t('common.refresh')}>
                {loading ? <OpenSquadLoader size={14} /> : <RefreshCw size={13} />}
              </button>
            </div>
          </div>

          {/* Search box - inline with title on mobile */}
          <form onSubmit={handleSearch} className="flex-1 flex items-center gap-1 md:gap-2 min-w-0 max-w-xl">
            <div className="relative flex-1 min-w-0">
              <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-textMuted pointer-events-none" />
              <input type="text" value={searchInput} onChange={e => setSearchInput(e.target.value)}
                placeholder={t('pluginMarket.searchPlaceholder')}
                className="w-full pl-8 pr-2 py-1.5 bg-bgLight border border-border rounded-lg text-xs md:text-sm focus:outline-none focus:border-primary/50 text-textMain placeholder-textMuted truncate" />
            </div>
            <button type="submit" className="hidden sm:block px-3 py-1.5 bg-primary text-white text-xs md:text-sm rounded-lg hover:opacity-90 font-medium shrink-0">{t('pluginMarket.searchBtn')}</button>
          </form>

          <div className="flex items-center gap-1.5 md:gap-2 shrink-0 md:ml-auto">
            {/* Mobile-only stats + refresh combined with search box row */}
            <div className="flex sm:hidden items-center gap-1">
              <span className="text-[10px] font-bold text-primary bg-primary/10 px-1.5 py-0.5 rounded-md">{data?.total || 0}</span>
              <button onClick={fetchPlugins} disabled={loading} className="p-1 rounded-lg text-textMuted hover:text-primary transition-colors disabled:opacity-50">
                {loading ? <OpenSquadLoader size={12} /> : <RefreshCw size={12} />}
              </button>
            </div>

            <button
              onClick={() => setShowGitInstall(true)}
              className="p-1 md:px-4 md:py-2 rounded-xl bg-bgLight border border-border text-textMuted hover:text-textMain transition-all text-xs md:text-sm font-medium shadow-sm"
              title={t('pluginMarket.installFromGit')}
            >
              <Github size={15} />
              <span className="hidden md:inline ml-1.5">{t('pluginMarket.installFromGit')}</span>
            </button>
            <button
              onClick={() => setShowUpload(true)}
              className="p-1 md:px-4 md:py-2 rounded-xl bg-primary/10 text-primary hover:bg-primary/20 transition-all text-xs md:text-sm font-medium shadow-sm"
              title={t('pluginMarket.publish')}
            >
              <GitPullRequest size={15} />
              <span className="hidden md:inline ml-1.5">{t('pluginMarket.publish')}</span>
            </button>
          </div>
        </div>
      </div>

      {/* Toolbar */}
      <div className="shrink-0 flex flex-col border-b border-border bg-panel/50">
        <div className="flex items-center justify-between px-4 md:px-6 py-1.5 md:py-2 gap-3 whitespace-nowrap overflow-x-auto no-scrollbar scrollbar-hide">
          <div className="flex items-center gap-3 shrink-0">
            {/* Type filter - mobile only */}
            <div className="flex md:hidden items-center gap-0.5 bg-bgLight border border-border rounded-lg p-0.5">
              {TYPE_FILTERS.map(tf => (
                <button key={tf.value} onClick={() => setTypeFilter(tf.value)} className={`px-2.5 py-0.5 md:px-3 md:py-1 rounded-md text-[10px] md:text-xs font-medium transition-colors ${typeFilter === tf.value ? 'bg-primary text-white shadow-sm' : 'text-textMuted hover:text-textMain'}`}>
                  {tf.label}
                </button>
              ))}
            </div>

            {/* Sort */}
            <div className="flex items-center gap-1 text-[10px] md:text-xs text-textMuted">
              <ArrowUpDown size={11} className="md:hidden" />
              <span className="hidden md:inline">{t('pluginMarket.sortBy')}</span>
              {SORT_OPTIONS.map(so => (
                <button key={so.value} onClick={() => setSort(so.value)} className={`px-1.5 md:px-2 py-0.5 md:py-1 rounded-md transition-colors ${sort === so.value ? 'text-primary font-medium' : 'hover:text-textMain'}`}>
                  {so.label}
                </button>
              ))}
            </div>
          </div>

          <div className="flex-1" />

          {totalUpdateCount > 0 && (
            <div className="flex items-center gap-1 text-[10px] md:text-xs text-amber-600 bg-amber-50 px-2 py-0.5 rounded-full border border-amber-200/50 shrink-0">
              <RefreshCw size={10} />
              <span>{t('pluginMarket.updatesCount', { count: totalUpdateCount })}</span>
            </div>
          )}
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 flex overflow-hidden">

        {/* Category sidebar */}
        <div className="w-40 shrink-0 border-r border-border bg-panel overflow-y-auto hidden md:flex flex-col">
          <div className="px-2 pt-4 pb-2">
            <p className="text-[10px] font-semibold text-textMuted uppercase tracking-wider px-2 mb-1">{t('pluginMarket.categoryLabel')}</p>
            {CATEGORIES.map(cat => (
              <button
                key={cat.id}
                onClick={() => setSelectedCategory(cat.id)}
                className={`w-full flex items-center gap-2 px-2 py-1.5 rounded-lg text-sm transition-colors ${
                  selectedCategory === cat.id
                    ? 'bg-primary/10 text-primary font-medium'
                    : 'text-textMuted hover:bg-bgLight hover:text-textMain'
                }`}
              >
                {cat.icon}
                <span className="truncate">{t(cat.label)}</span>
                {catCounts[cat.id] != null && catCounts[cat.id] > 0 && selectedCategory !== cat.id && (
                  <span className="ml-auto text-[10px] text-textMuted/60 shrink-0">{catCounts[cat.id]}</span>
                )}
              </button>
            ))}
          </div>
        </div>

        {/* Plugin grid */}
        <div className="flex-1 overflow-y-auto px-3 py-3">
          {error ? (
            <div className="flex flex-col items-center justify-center h-64 gap-3 text-center">
              <AlertCircle size={40} className="text-red-400" />
              <p className="text-textMuted text-sm max-w-md">{error}</p>
              <button onClick={fetchPlugins} className="px-4 py-2 bg-primary text-white rounded-lg text-sm hover:opacity-90">{t('common.retry')}</button>
            </div>
          ) : loading && !data ? (
            <div className="flex items-center justify-center h-64"><OpenSquadLoader size={44} /></div>
          ) : data && data.plugins.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-64 gap-3">
              <Store size={48} className="text-textMuted opacity-40" />
              <p className="text-textMuted text-sm">{t('pluginMarket.noPlugins')}</p>
            </div>
          ) : (
            <>
              <div className={`grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3 transition-opacity duration-150 ${loading ? 'opacity-60 pointer-events-none' : 'opacity-100'}`}>
                {data?.plugins.map(plugin => (
                  <PluginCard
                    key={plugin.id}
                    plugin={plugin}
                    installed={installedMap[plugin.id] ?? null}
                    liked={likedSet.has(plugin.id)}
                    onLike={handleLike}
                    onInstall={handleInstall}
                    onCardClick={setDetailPlugin}
                    installing={installingIds.has(plugin.id)}
                    pluginLang={pluginLang}
                    appLang={appLang}
                  />
                ))}
              </div>
              {data && data.pages > 1 && (
                <div className="flex justify-center mt-6">
                  <Pagination page={page} pages={data.pages} onChange={setPage} />
                </div>
              )}
            </>
          )}
        </div>
      </div>

      {/* Mobile Category Dropdown (Moved to bottom to avoid clipping & forced vertical) */}
      {catDropdownOpen && catDropdownPos && (
        <>
          <div className="fixed inset-0 z-[100]" onClick={() => setCatDropdownOpen(false)} />
          <div
            className="fixed w-40 bg-panel border border-border rounded-xl shadow-2xl z-[101] py-1 max-h-[60vh] overflow-y-auto flex flex-col whitespace-normal"
            style={{ top: catDropdownPos.top, left: catDropdownPos.left }}
          >
            {CATEGORIES.map(cat => (
              <button
                key={cat.id}
                onClick={() => { setSelectedCategory(cat.id); setCatDropdownOpen(false); }}
                className={`w-full flex items-center gap-2 px-3 py-2 text-xs transition-colors ${
                  selectedCategory === cat.id ? 'bg-primary/10 text-primary font-medium' : 'text-textMain hover:bg-bgLight'
                }`}
              >
                {cat.icon}
                <span className="truncate">{t(cat.label)}</span>
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  );
};

export default PluginMarketPage;
