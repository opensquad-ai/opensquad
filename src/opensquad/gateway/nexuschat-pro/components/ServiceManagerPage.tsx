import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  ArrowLeft, RefreshCw, Server, Play, StopCircle, RotateCw,
  Terminal, ChevronDown, ChevronUp, Loader2, Zap, Globe, Wrench,
  Activity, Clock, Hash, Save, Check, Settings, ToggleLeft, ToggleRight,
  LayoutGrid, List, AlertTriangle, Copy, Download, Database, HardDrive, CheckCircle2,
  Trash2, Folder, Cpu, X,
} from 'lucide-react';
import { servicesAPI, pluginServiceAPI, pluginAPI, ServiceStatus } from '../services/api';
import { useTranslation } from 'react-i18next';
import { HoverTooltip } from './HoverTooltip';
import {
  adminHeaderBar,
  adminHeaderGhostBtn,
  adminHeaderIcon,
  adminHeaderIconBox,
  adminHeaderNavBtn,
  adminHeaderSubtitle,
  adminHeaderTitle,
} from './admin/adminShellStyles';

interface ServiceManagerPageProps {
  onBack: () => void;
}

const LAYOUT_KEY = 'service_manager_layout';
type ServiceLayoutMode = 'grid' | 'list';

function loadLayoutMode(): ServiceLayoutMode {
  try {
    const raw = localStorage.getItem(LAYOUT_KEY);
    return raw === 'list' ? 'list' : 'grid';
  } catch {
    return 'grid';
  }
}

const TYPE_COLORS: Record<string, string> = {
  platform: 'bg-blue-500/15 text-blue-400 border-blue-500/30',
  tool:     'bg-emerald-500/15 text-emerald-400 border-emerald-500/30',
  hook:     'bg-purple-500/15 text-purple-400 border-purple-500/30',
};

const TYPE_ICONS: Record<string, React.ReactNode> = {
  platform: <Globe size={14} />,
  tool:     <Wrench size={14} />,
  hook:     <Activity size={14} />,
};

function formatUptime(seconds: number): string {
  if (seconds < 60) return `${Math.floor(seconds)}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${Math.floor(seconds % 60)}s`;
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return `${h}h ${m}m`;
}

const inputClass = 'w-full bg-bgLight border border-border rounded-lg px-2.5 py-1 text-[11px] text-textMain font-mono placeholder-textMuted focus:outline-none focus:border-primary/50 transition-colors';

function SetupHintBanner({ svc }: { svc: ServiceStatus }) {
  const { t: tr, i18n } = useTranslation();
  const zh = (i18n.language || '').startsWith('zh');
  const ps = svc.plugin_status || {};
  if (svc.plugin_id !== 'websearch' || !ps.needs_bing_login) return null;
  const cmd = typeof ps.setup_command === 'string' ? ps.setup_command : '';
  const msg = zh
    ? (ps.message_zh || '首次部署请完成 Bing 登录，以获得更接近手动浏览器的搜索质量。')
    : (ps.message_en || 'Complete Bing login once after first deploy for better search quality.');

  const onCopy = async () => {
    if (!cmd) return;
    try {
      await navigator.clipboard.writeText(cmd);
    } catch {
      /* ignore */
    }
  };

  return (
    <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-2.5 py-2 text-[11px] text-amber-100/95 space-y-1.5">
      <div className="flex items-start gap-1.5">
        <AlertTriangle size={13} className="shrink-0 mt-0.5 text-amber-400" />
        <div className="min-w-0 leading-relaxed">{msg}</div>
      </div>
      <div className="text-[10px] text-amber-200/80 pl-5">
        {zh
          ? '可在侧栏 / 插件中打开「Web Search」设置页一键登录，或复制命令手动运行。'
          : 'Open the Web Search setup page for guided login, or copy the CLI command.'}
      </div>
      {cmd ? (
        <div className="flex items-center gap-1.5 pl-5">
          <code className="flex-1 min-w-0 truncate font-mono text-[10px] bg-black/25 rounded px-1.5 py-1">{cmd}</code>
          <button
            type="button"
            onClick={() => void onCopy()}
            className="shrink-0 inline-flex items-center gap-1 px-1.5 py-1 rounded bg-amber-500/20 hover:bg-amber-500/30 text-amber-100"
            title={tr('common.copy') || 'Copy'}
          >
            <Copy size={11} />
          </button>
        </div>
      ) : null}
    </div>
  );
}

// ── Service model deploy strip ───────────────────────────────────────
// Per-service model download / uninstall card modeled on the SenseVoice
// "本地" design: clean title row, single line status, one primary
// button. Specifics (model name, size hint, install / uninstall action,
// status selectors) are wired up below in MODEL_SERVICES.
const MODEL_SERVICES: Record<string, {
  action: string;
  uninstallAction: string;
  /** Human-readable model name (e.g. "SenseVoice Small", "Whisper base"). */
  modelName: (d: any) => string;
  /** Localized model name (English). */
  modelNameEn: string;
  /** Badge next to the title, e.g. "本地" (local). */
  badgeZh?: string;
  badgeEn?: string;
  readySel: (d: any) => boolean;
  missingSel: (d: any) => string[];
  dirSel: (d: any) => string;
  /** Where the persisted download state lives in the plugin data payload. */
  downloadStateSel: (d: any) => any;
}> = {
  websearch: {
    action: 'download_reranker',
    uninstallAction: 'uninstall_reranker',
    modelName: (d) => 'Qwen3-Reranker 0.6B',
    modelNameEn: 'Qwen3-Reranker 0.6B',
    badgeZh: '本地',
    badgeEn: 'Local',
    readySel: (d) => !!(d?.reranker?.ready),
    missingSel: (d) => d?.reranker?.missing || [],
    dirSel: (d) => (d?.reranker?.snapshot_dir) || (d?.reranker?.model_dir) || '',
    downloadStateSel: (d) => d?.reranker?.download || {},
  },
  whisper: {
    action: 'download_model',
    uninstallAction: 'uninstall_model',
    modelName: (d) => `Whisper ${d?.model || 'base'}`,
    modelNameEn: 'Whisper',
    badgeZh: '本地',
    badgeEn: 'Local',
    readySel: (d) => !!d?.ready,
    missingSel: () => [],
    dirSel: (d) => d?.model_dir || '',
    downloadStateSel: (d) => d?.download || {},
  },
  sensevoice: {
    action: 'download_model',
    uninstallAction: 'uninstall_model',
    modelName: () => 'SenseVoice Small',
    modelNameEn: 'SenseVoice Small',
    badgeZh: '本地',
    badgeEn: 'Local',
    readySel: (d) => !!d?.ready,
    missingSel: (d) => d?.missing || [],
    dirSel: (d) => d?.model_dir || '',
    downloadStateSel: (d) => d?.download || {},
  },
};

function ServiceModelDeploy({ pluginId }: { pluginId: string }) {
  const { i18n } = useTranslation();
  const zh = (i18n.language || '').startsWith('zh');
  const spec = MODEL_SERVICES[pluginId];
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [copiedDir, setCopiedDir] = useState(false);

  const refresh = useCallback(async () => {
    // Only plugins registered in MODEL_SERVICES expose a model query module;
    // skip the rest to avoid 404 noise for e.g. telegram / external_api.
    if (!MODEL_SERVICES[pluginId]) return;
    try {
      setError(null);
      const d = await pluginAPI.getPluginData(pluginId);
      setData(d);
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setLoading(false);
    }
  }, [pluginId]);

  useEffect(() => { void refresh(); }, [refresh]);

  if (!spec) return null;
  const ready = spec.readySel(data);
  const dir = spec.dirSel(data);
  const downloadState = spec.downloadStateSel(data) || {};
  const downloading = downloadState.state === 'downloading';
  const progress = Number(downloadState.progress || 0);
  const downloadStateKey = downloadState.state;

  // Poll while downloading
  useEffect(() => {
    if (downloadStateKey !== 'downloading') return;
    const t = window.setInterval(() => { void refresh(); }, 1500);
    return () => window.clearInterval(t);
  }, [downloadStateKey, refresh]);

  const onDownload = async () => {
    setBusy(true);
    setError(null);
    setInfo(null);
    try {
      const res = await pluginAPI.pluginAction(pluginId, spec.action, { force: false });
      // show immediate "started" feedback even if the response is just
      // an idempotent "already present" message
      setInfo(res?.message || (zh ? '已开始下载' : 'Download started'));
      await refresh();
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setBusy(false);
      void refresh();
    }
  };

  const onUninstall = async () => {
    if (!window.confirm(
      zh
        ? `确定要卸载 ${spec.modelName(data)} 吗？\n\n模型文件将从磁盘删除并释放空间。`
        : `Uninstall ${spec.modelNameEn}?\n\nThe model files will be removed from disk to free space.`,
    )) {
      return;
    }
    setBusy(true);
    setError(null);
    setInfo(null);
    try {
      const res = await pluginAPI.pluginAction(pluginId, spec.uninstallAction, {});
      setInfo(res?.message || (zh ? '已卸载' : 'Uninstalled'));
      await refresh();
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setBusy(false);
      void refresh();
    }
  };

  const onCancel = async () => {
    setBusy(true);
    setError(null);
    setInfo(null);
    try {
      const res = await pluginAPI.pluginAction(pluginId, 'cancel_download', {});
      setInfo(res?.message || (zh ? '已取消下载' : 'Download cancelled'));
      await refresh();
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setBusy(false);
      void refresh();
    }
  };

  const modelName = zh ? spec.modelName(data) : spec.modelNameEn;
  const badge = zh ? spec.badgeZh : spec.badgeEn;

  const onCopyDir = async () => {
    if (!dir) return;
    try {
      await navigator.clipboard.writeText(dir);
      setCopiedDir(true);
      window.setTimeout(() => setCopiedDir(false), 1500);
    } catch {
      /* clipboard unavailable — ignore */
    }
  };

  return (
    <div className={`mt-2 rounded-lg border ${
      ready ? 'border-emerald-500/25 bg-emerald-500/[0.04]' : 'border-border bg-bgLight/30'
    } px-3 py-2.5`}>
      {/* Title row: model name + status badge */}
      <div className="flex items-center gap-2 min-w-0">
        <Cpu size={13} className={ready ? 'text-emerald-400 shrink-0' : 'text-textMuted shrink-0'} />
        <span className="text-[12px] font-semibold text-textMain truncate">{modelName}</span>
        {badge ? (
          <span className={`inline-flex items-center px-1.5 py-0.5 rounded text-[9px] font-medium shrink-0 ${
            ready
              ? 'bg-emerald-500/15 text-emerald-400 border border-emerald-500/25'
              : 'bg-bgDark/60 text-textMuted border border-border'
          }`}>
            {badge}
          </span>
        ) : null}
      </div>

      {/* Status row: path tooltip */}
      <div className="mt-1.5 flex items-center gap-1.5 text-[10.5px] text-textMuted min-w-0">
        {ready
          ? <CheckCircle2 size={11} className="text-emerald-400 shrink-0" />
          : <Download size={11} className="text-textMuted shrink-0" />}
        {ready && dir ? (
          <HoverTooltip text={dir} maxWidth="24rem">
            <button
              type="button"
              tabIndex={0}
              onClick={() => void onCopyDir()}
              title={zh ? '点击复制地址' : 'Click to copy path'}
              className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[10px] text-textMuted/80 hover:text-primary hover:bg-bgLight/60 border border-dashed border-border transition-colors shrink-0"
            >
              {copiedDir
                ? <Check size={9} className="text-emerald-400 shrink-0" />
                : <Folder size={9} className="shrink-0" />}
              <span className="whitespace-nowrap">
                {copiedDir ? (zh ? '已复制' : 'Copied') : (zh ? '地址' : 'Path')}
              </span>
            </button>
          </HoverTooltip>
        ) : null}
      </div>

      {/* Progress bar (only when actively downloading or errored) */}
      {(downloading || downloadState.state === 'error') ? (
        <div className="mt-2 space-y-1">
          <div className="flex justify-between gap-2 text-[10px] text-textMuted min-w-0">
            <span className="truncate min-w-0">
              {downloadState.message || downloadState.state}
              {downloadState.source ? (
                <span className="text-textMuted/70">
                  {' '}({downloadState.mirror_index || 1}/{downloadState.mirror_total || 1} {zh ? '镜像' : 'mirror'}: {downloadState.source})
                </span>
              ) : null}
            </span>
            <span className="shrink-0">{progress.toFixed(0)}%</span>
          </div>
          <div className="h-1 rounded-full bg-bgDark overflow-hidden">
            <div
              className="h-full bg-primary transition-all"
              style={{ width: `${Math.min(100, Math.max(0, progress))}%` }}
            />
          </div>
          {downloadState.file ? (
            <p className="text-[10px] text-textMuted break-all">
              {zh ? '当前文件：' : 'Current file: '}
              {downloadState.file}
            </p>
          ) : null}
        </div>
      ) : null}

      {/* Inline feedback (info / error) — clearly visible, doesn't get clipped */}
      {error ? (
        <p className="mt-2 text-[10.5px] text-red-400 break-all">{error}</p>
      ) : null}
      {info && !error ? (
        <p className="mt-2 text-[10.5px] text-textMuted break-all">{info}</p>
      ) : null}

      {/* Single primary action button */}
      <div className="mt-2.5 flex items-center gap-1.5">
        {!ready ? (
          <>
            <button
              type="button"
              disabled={busy || downloading}
              onClick={() => void onCancel()}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md border border-border text-[11px] font-medium text-textMuted hover:border-red-500/40 disabled:opacity-50 transition-colors"
              title={zh ? '取消当前下载' : 'Cancel current download'}
            >
              <X size={11} />
              {zh ? '取消' : 'Cancel'}
            </button>
            <button
              type="button"
              disabled={busy || downloading}
              onClick={() => void onDownload()}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-primary text-white text-[11px] font-medium hover:bg-primary/90 disabled:opacity-50 transition-colors"
            >
              {busy || downloading
                ? <Loader2 size={11} className="animate-spin" />
                : <Download size={11} />}
              {zh ? '下载模型' : 'Download model'}
            </button>
          </>
        ) : (
          <>
            <button
              type="button"
              onClick={() => void onUninstall()}
              disabled={busy || downloading}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-red-500/10 text-red-400 hover:bg-red-500/20 text-[11px] font-medium disabled:opacity-50 transition-colors"
              title={zh ? '卸载模型文件并释放磁盘空间' : 'Remove model files from disk'}
            >
              {busy
                ? <Loader2 size={11} className="animate-spin" />
                : <Trash2 size={11} />}
              {zh ? '卸载模型' : 'Uninstall'}
            </button>
          </>
        )}
      </div>
    </div>
  );
}

export const ServiceManagerPage: React.FC<ServiceManagerPageProps> = ({ onBack }) => {
  const { t: tr } = useTranslation();
  const [services, setServices] = useState<ServiceStatus[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [acting, setActing] = useState<Record<string, boolean>>({});
  const [logsOpen, setLogsOpen] = useState<Record<string, boolean>>({});
  const [logsData, setLogsData] = useState<Record<string, string[]>>({});
  const [logsLoading, setLogsLoading] = useState<Record<string, boolean>>({});

  // Config editing state – per service
  const [configs, setConfigs] = useState<Record<string, Record<string, any>>>({});
  const [editPorts, setEditPorts] = useState<Record<string, string>>({});
  const [editHosts, setEditHosts] = useState<Record<string, string>>({});
  const [editAutoStart, setEditAutoStart] = useState<Record<string, boolean>>({});
  const [configDirty, setConfigDirty] = useState<Record<string, boolean>>({});
  const [savingConfig, setSavingConfig] = useState<Record<string, boolean>>({});
  const [configSaved, setConfigSaved] = useState<Record<string, boolean>>({});
  const [togglingAutoStart, setTogglingAutoStart] = useState<Record<string, boolean>>({});

  const [layoutMode, setLayoutMode] = useState<ServiceLayoutMode>(loadLayoutMode);
  const setLayout = useCallback((mode: ServiceLayoutMode) => {
    setLayoutMode(mode);
    try { localStorage.setItem(LAYOUT_KEY, mode); } catch { /* ignore */ }
  }, []);

  const fetchServices = useCallback(async () => {
    try {
      setError(null);
      const data = await servicesAPI.list();
      setServices(data.services || []);
    } catch (e: any) {
      setError(e.message || 'Failed to load services');
    } finally {
      setLoading(false);
    }
  }, []);

  // Load config for each service on mount
  useEffect(() => {
    services.forEach(svc => {
      const pid = svc.plugin_id;
      if (configs[pid] === undefined) {
        pluginAPI.getPluginConfig(pid).then(data => {
          const cfg = data.config || {};
          setConfigs(prev => ({ ...prev, [pid]: cfg }));
          setEditPorts(prev => {
            if (prev[pid] !== undefined) return prev;
            return { ...prev, [pid]: String(cfg.port ?? svc.port) };
          });
          setEditHosts(prev => {
            if (prev[pid] !== undefined) return prev;
            return { ...prev, [pid]: String(cfg.host ?? cfg.host ?? '0.0.0.0') };
          });
          setEditAutoStart(prev => {
            if (prev[pid] !== undefined) return prev;
            return { ...prev, [pid]: svc.auto_start };
          });
        }).catch(() => {
          const fallback: Record<string, any> = {};
          setConfigs(prev => ({ ...prev, [pid]: fallback }));
          setEditPorts(prev => ({ ...prev, [pid]: String(svc.port) }));
          setEditHosts(prev => ({ ...prev, [pid]: '0.0.0.0' }));
          setEditAutoStart(prev => ({ ...prev, [pid]: svc.auto_start }));
        });
      }
    });
  }, [services]);

  useEffect(() => { fetchServices(); }, [fetchServices]);

  // Auto-refresh every 10 seconds
  useEffect(() => {
    const interval = setInterval(() => { fetchServices(); }, 10000);
    return () => clearInterval(interval);
  }, [fetchServices]);

  // ── Short-burst fast poll after a user action ──
  // The default 10s poll is too slow to reflect "starting → running"
  // transitions (which can take 1-5s for Popen + health-check). After a
  // Start/Restart, we run a 2s-interval poll for up to 30s, stopping early
  // once no service is in `starting` state. Cheaper than a WebSocket/SSE
  // and gives near-real-time feedback after the user's click.
  const fastPollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const fastPollUntilStable = useCallback(() => {
    // Clear any previous burst poll before starting a new one
    if (fastPollRef.current) clearInterval(fastPollRef.current);
    let elapsed = 0;
    const intervalMs = 2000;
    const maxMs = 30000;
    fastPollRef.current = setInterval(async () => {
      elapsed += intervalMs;
      await fetchServices();
      if (elapsed >= maxMs && fastPollRef.current) {
        clearInterval(fastPollRef.current);
        fastPollRef.current = null;
      }
    }, intervalMs);
  }, [fetchServices]);

  // Cleanup fast-poll on unmount
  useEffect(() => {
    return () => {
      if (fastPollRef.current) clearInterval(fastPollRef.current);
    };
  }, []);

  const handleStart = async (pluginId: string) => {
    setActing(prev => ({ ...prev, [pluginId]: true }));
    try {
      await pluginServiceAPI.start(pluginId);
      await fetchServices();
      // Start a short-burst fast poll to catch the starting→running
      // transition without waiting for the next 10s tick.
      fastPollUntilStable();
    } catch (e: any) {
      // P0-3: backend now returns 200 for "already running" (idempotent),
      // so this alert should only fire on genuine errors (404 not found,
      // port conflict, etc.) — no longer on duplicate Start clicks.
      alert(`Start failed: ${e.message}`);
    } finally {
      setActing(prev => ({ ...prev, [pluginId]: false }));
    }
  };

  const handleStop = async (pluginId: string) => {
    setActing(prev => ({ ...prev, [pluginId]: true }));
    try {
      await pluginServiceAPI.stop(pluginId);
      await fetchServices();
    } catch (e: any) {
      alert(`Stop failed: ${e.message}`);
    } finally {
      setActing(prev => ({ ...prev, [pluginId]: false }));
    }
  };

  const handleAutoStartToggle = async (pluginId: string) => {
    const current = editAutoStart[pluginId] ?? true;
    setTogglingAutoStart(prev => ({ ...prev, [pluginId]: true }));
    try {
      await servicesAPI.setAutoStart(pluginId, !current);
      setEditAutoStart(prev => ({ ...prev, [pluginId]: !current }));
    } catch (e: any) {
      alert(`Toggle failed: ${e.message}`);
    } finally {
      setTogglingAutoStart(prev => ({ ...prev, [pluginId]: false }));
    }
  };

  const handleRestart = async (pluginId: string) => {
    setActing(prev => ({ ...prev, [pluginId]: true }));
    try {
      await pluginServiceAPI.restart(pluginId);
      await fetchServices();
      // Restart cycles through stop→start, so poll for the starting→running
      // transition the same way as handleStart.
      fastPollUntilStable();
    } catch (e: any) {
      alert(`Restart failed: ${e.message}`);
    } finally {
      setActing(prev => ({ ...prev, [pluginId]: false }));
    }
  };

  const handleConfigChange = (pluginId: string, key: string, value: string) => {
    if (key === 'port') {
      setEditPorts(prev => ({ ...prev, [pluginId]: value }));
    } else if (key === 'host') {
      setEditHosts(prev => ({ ...prev, [pluginId]: value }));
    }
    setConfigDirty(prev => ({ ...prev, [pluginId]: true }));
    setConfigSaved(prev => ({ ...prev, [pluginId]: false }));
  };

  const handleSaveConfig = async (pluginId: string) => {
    setSavingConfig(prev => ({ ...prev, [pluginId]: true }));
    try {
      const cfg: Record<string, any> = { ...(configs[pluginId] || {}) };
      const portVal = parseInt(editPorts[pluginId], 10);
      if (!isNaN(portVal) && portVal > 0) cfg.port = portVal;
      const hostVal = (editHosts[pluginId] || '').trim();
      if (hostVal) cfg.host = hostVal;

      await pluginAPI.savePluginConfig(pluginId, cfg);
      setConfigs(prev => ({ ...prev, [pluginId]: cfg }));
      setConfigDirty(prev => ({ ...prev, [pluginId]: false }));
      setConfigSaved(prev => ({ ...prev, [pluginId]: true }));
      setTimeout(() => setConfigSaved(prev => ({ ...prev, [pluginId]: false })), 2000);
    } catch (e: any) {
      alert(`Save failed: ${e.message}`);
    } finally {
      setSavingConfig(prev => ({ ...prev, [pluginId]: false }));
    }
  };

  const handleSaveAndRestartConfig = async (pluginId: string) => {
    setSavingConfig(prev => ({ ...prev, [pluginId]: true }));
    setActing(prev => ({ ...prev, [pluginId]: true }));
    try {
      const cfg: Record<string, any> = { ...(configs[pluginId] || {}) };
      const portVal = parseInt(editPorts[pluginId], 10);
      if (!isNaN(portVal) && portVal > 0) cfg.port = portVal;
      const hostVal = (editHosts[pluginId] || '').trim();
      if (hostVal) cfg.host = hostVal;

      await pluginAPI.savePluginConfig(pluginId, cfg);
      await pluginServiceAPI.restart(pluginId);
      setConfigs(prev => ({ ...prev, [pluginId]: cfg }));
      setConfigDirty(prev => ({ ...prev, [pluginId]: false }));
      setConfigSaved(prev => ({ ...prev, [pluginId]: true }));
      setTimeout(() => setConfigSaved(prev => ({ ...prev, [pluginId]: false })), 2000);
      await fetchServices();
    } catch (e: any) {
      alert(tr('pluginManager.saveAndRestartFailedMsg', { error: e.message }));
    } finally {
      setSavingConfig(prev => ({ ...prev, [pluginId]: false }));
      setActing(prev => ({ ...prev, [pluginId]: false }));
    }
  };

  const toggleLogs = async (pluginId: string) => {
    const isOpen = logsOpen[pluginId];
    if (!isOpen) {
      setLogsLoading(prev => ({ ...prev, [pluginId]: true }));
      try {
        const data = await pluginServiceAPI.getLogs(pluginId, 200);
        setLogsData(prev => ({ ...prev, [pluginId]: data.logs }));
      } catch {
        setLogsData(prev => ({ ...prev, [pluginId]: ['(Failed to load logs)'] }));
      } finally {
        setLogsLoading(prev => ({ ...prev, [pluginId]: false }));
      }
    }
    setLogsOpen(prev => ({ ...prev, [pluginId]: !isOpen }));
  };

  const aliveCount = services.filter(s => s.alive).length;
  const totalCount = services.length;

  if (loading) {
    return (
      <div className="flex-1 h-full bg-bgLight flex flex-col w-full max-w-full overflow-hidden">
        <div className="flex-1 flex items-center justify-center">
          <Loader2 className="animate-spin text-primary" size={32} />
        </div>
      </div>
    );
  }

  return (
    <div className="flex-1 h-full bg-bgLight flex flex-col w-full max-w-full overflow-hidden">

      {/* Header */}
      <div className={`${adminHeaderBar} gap-2.5`}>
        <button
          onClick={onBack}
          className={adminHeaderNavBtn}
        >
          <ArrowLeft size={16} />
        </button>
        <div className="flex items-center gap-2 flex-1 min-w-0">
          <div className={adminHeaderIconBox}>
            <Server className={`${adminHeaderIcon} w-3.5 h-3.5`} />
          </div>
          <div>
            <h1 className={adminHeaderTitle}>{tr('nav.services') || '服务管理'}</h1>
            <p className={adminHeaderSubtitle}>
              <span className="text-emerald-400/80 font-medium">{aliveCount}</span> {tr('pluginManager.statusRunning') || '运行中'} / {totalCount} {tr('pluginManager.title', '服务') || '服务'}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-1.5 shrink-0">
          <div className="flex items-center rounded-lg border border-border bg-bgLight p-0.5">
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
          <button
            onClick={() => { setLoading(true); fetchServices(); }}
            className={adminHeaderGhostBtn}
            title="Refresh"
          >
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
          </button>
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 min-h-0 overflow-y-auto p-4 md:p-6 w-full">
        {error ? (
          <div className="flex flex-col items-center justify-center h-64 gap-3">
            <p className="text-red-400 text-sm">{error}</p>
            <button onClick={() => { setLoading(true); fetchServices(); }} className="text-xs text-primary hover:underline">Retry</button>
          </div>
        ) : totalCount === 0 ? (
          <div className="flex flex-col items-center justify-center h-64 gap-3">
            <Server className="text-textMuted" size={32} />
            <p className="text-textMuted text-sm">{tr('pluginManager.noServices') || 'No services configured'}</p>
          </div>
        ) : (
          <div className={
            layoutMode === 'list'
              ? 'grid grid-cols-1 xl:grid-cols-2 gap-1.5'
              : 'grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 gap-3 md:gap-4'
          }>
            {services.map(svc => (
              <ServiceCard
                key={svc.plugin_id}
                svc={svc}
                layout={layoutMode}
                acting={acting[svc.plugin_id] || false}
                onStart={() => handleStart(svc.plugin_id)}
                onStop={() => handleStop(svc.plugin_id)}
                onRestart={() => handleRestart(svc.plugin_id)}
                onToggleLogs={() => toggleLogs(svc.plugin_id)}
                logsOpen={logsOpen[svc.plugin_id] || false}
                logs={logsData[svc.plugin_id] || []}
                logsLoading={logsLoading[svc.plugin_id] || false}
                // Config editing
                editPort={editPorts[svc.plugin_id] ?? String(svc.port)}
                editHost={editHosts[svc.plugin_id] ?? '0.0.0.0'}
                configDirty={configDirty[svc.plugin_id] || false}
                savingConfig={savingConfig[svc.plugin_id] || false}
                configSaved={configSaved[svc.plugin_id] || false}
                onConfigChange={(key, value) => handleConfigChange(svc.plugin_id, key, value)}
                onSaveConfig={() => handleSaveConfig(svc.plugin_id)}
                onSaveAndRestartConfig={() => handleSaveAndRestartConfig(svc.plugin_id)}
                // Auto-start
                editAutoStart={editAutoStart[svc.plugin_id] ?? svc.auto_start}
                togglingAutoStart={togglingAutoStart[svc.plugin_id] || false}
                onAutoStartToggle={() => handleAutoStartToggle(svc.plugin_id)}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
};

// ── Service Card ──

interface ServiceCardProps {
  svc: ServiceStatus;
  layout?: ServiceLayoutMode;
  acting: boolean;
  onStart: () => void;
  onStop: () => void;
  onRestart: () => void;
  onToggleLogs: () => void;
  logsOpen: boolean;
  logs: string[];
  logsLoading: boolean;
  // Config editing
  editPort: string;
  editHost: string;
  configDirty: boolean;
  savingConfig: boolean;
  configSaved: boolean;
  onConfigChange: (key: string, value: string) => void;
  onSaveConfig: () => void;
  onSaveAndRestartConfig: () => void;
  // Auto-start
  editAutoStart: boolean;
  togglingAutoStart: boolean;
  onAutoStartToggle: () => void;
}

const ServiceCard: React.FC<ServiceCardProps> = ({
  svc, layout = 'grid', acting, onStart, onStop, onRestart, onToggleLogs,
  logsOpen, logs, logsLoading,
  editPort, editHost, configDirty, savingConfig, configSaved,
  onConfigChange, onSaveConfig, onSaveAndRestartConfig,
  editAutoStart, togglingAutoStart, onAutoStartToggle,
}) => {
  const { t: tr } = useTranslation();
  const [configExpanded, setConfigExpanded] = useState(false);
  const typeClass = TYPE_COLORS[svc.plugin_type] || TYPE_COLORS.tool;
  const portChanged = parseInt(editPort, 10) !== svc.port && !isNaN(parseInt(editPort, 10));
  const needsRestart = configDirty && portChanged;
  const hasPort = svc.port > 0;
  const isList = layout === 'list';
  const state = svc.state ?? (svc.alive ? 'running' : 'stopped');

  const statusBadge = (() => {
    const badgeMap: Record<string, { cls: string; dot: string; label: string; spin?: boolean }> = {
      running:   { cls: 'bg-emerald-500/15 text-emerald-400', dot: 'bg-emerald-400 animate-pulse', label: tr('pluginManager.statusRunning') || 'Running' },
      starting:  { cls: 'bg-amber-500/15 text-amber-400',      dot: 'bg-amber-400 animate-pulse',  label: 'Starting...', spin: true },
      error:     { cls: 'bg-red-500/15 text-red-400',         dot: 'bg-red-400',                  label: 'Error' },
      stopped:   { cls: 'bg-slate-500/15 text-slate-400',     dot: 'bg-slate-500',                label: tr('pluginManager.statusStopped') || 'Stopped' },
    };
    const b = badgeMap[state] || badgeMap.stopped;
    return (
      <span className={`inline-flex items-center gap-1 text-[10px] font-medium px-2 py-0.5 rounded-full shrink-0 ${b.cls}`}>
        {b.spin
          ? <Loader2 size={9} className="animate-spin" />
          : <span className={`w-1.5 h-1.5 rounded-full ${b.dot}`} />}
        {b.label}
      </span>
    );
  })();

  const actionButtons = (() => {
    if (state === 'starting') {
      return (
        <button
          disabled
          className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[11px] font-medium bg-amber-500/10 text-amber-400 cursor-not-allowed opacity-75"
        >
          <Loader2 size={11} className="animate-spin" />
          Starting...
        </button>
      );
    }
    if (state === 'running') {
      return (
        <>
          <button
            onClick={onStop}
            disabled={acting}
            className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[11px] font-medium bg-red-500/10 text-red-400 hover:bg-red-500/20 transition-colors disabled:opacity-50"
          >
            {acting ? <Loader2 size={11} className="animate-spin" /> : <StopCircle size={11} />}
            Stop
          </button>
          <button
            onClick={onRestart}
            disabled={acting}
            className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[11px] font-medium transition-colors disabled:opacity-50 ${
              needsRestart
                ? 'bg-primary/20 text-primary hover:bg-primary/30 ring-1 ring-primary/40'
                : 'bg-amber-500/10 text-amber-400 hover:bg-amber-500/20'
            }`}
            title={needsRestart ? 'Restart to apply config changes' : 'Restart service'}
          >
            {acting ? <Loader2 size={11} className="animate-spin" /> : <RotateCw size={11} />}
            Restart{needsRestart ? ' *' : ''}
          </button>
        </>
      );
    }
    return (
      <button
        onClick={onStart}
        disabled={acting}
        className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[11px] font-medium transition-colors disabled:opacity-50 ${
          state === 'error'
            ? 'bg-red-500/10 text-red-400 hover:bg-red-500/20'
            : 'bg-emerald-500/10 text-emerald-400 hover:bg-emerald-500/20'
        }`}
        title={state === 'error' ? 'Retry start (previous attempt failed)' : 'Start service'}
      >
        {acting ? <Loader2 size={11} className="animate-spin" /> : <Play size={11} />}
        {state === 'error' ? 'Retry' : 'Start'}
      </button>
    );
  })();

  const settingsBtn = (
    <button
      onClick={() => setConfigExpanded(v => !v)}
      className={`p-1 rounded transition-colors ${
        configExpanded
          ? 'bg-primary/15 text-primary'
          : configDirty
            ? 'text-amber-400 hover:bg-primary/10'
            : 'text-textMuted hover:text-primary hover:bg-primary/10'
      }`}
      title={configDirty ? 'Service settings (unsaved changes)' : 'Service settings'}
    >
      <Settings size={14} />
    </button>
  );

  const configPanel = configExpanded && (
    <div className="bg-bgLight/50 rounded-lg border border-border/40 px-3 py-2.5 space-y-2">
      <div className="flex items-center gap-1.5 text-[10px] text-textMuted font-medium">
        <Settings size={11} className="shrink-0" />
        <span>Service Config</span>
        {configDirty && (
          <div className="ml-auto flex items-center gap-1">
            <button
              onClick={onSaveConfig}
              disabled={savingConfig}
              className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-medium transition-colors ${
                configSaved
                  ? 'bg-emerald-500/15 text-emerald-400'
                  : 'bg-primary/15 text-primary hover:bg-primary/25'
              }`}
            >
              {savingConfig && !acting ? <Loader2 size={10} className="animate-spin" />
               : configSaved ? <><Check size={10} /> Saved</>
               : <><Save size={10} /> Save</>}
            </button>
            {hasPort && (
              <button
                onClick={onSaveAndRestartConfig}
                disabled={savingConfig || acting}
                className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-medium bg-amber-500/15 text-amber-400 hover:bg-amber-500/25 transition-colors disabled:opacity-50"
              >
                {(savingConfig && acting) ? <Loader2 size={10} className="animate-spin" />
                 : <><RotateCw size={10} /> {tr('pluginManager.saveAndRestart')}</>}
              </button>
            )}
          </div>
        )}
      </div>

      <div className="grid grid-cols-2 gap-2">
        <div className="space-y-0.5">
          <label className="text-[10px] text-textMuted flex items-center gap-1">
            <Hash size={10} className="shrink-0" />Port
            {portChanged && (
              <span className="text-[9px] text-amber-400">(was {svc.port || '—'})</span>
            )}
          </label>
          {hasPort ? (
            <input
              type="number"
              value={editPort}
              onChange={e => onConfigChange('port', e.target.value)}
              className={inputClass}
              placeholder="9001"
              min={1}
              max={65535}
            />
          ) : (
            <span className="text-[11px] text-textMuted italic py-1 block">N/A (client adapter)</span>
          )}
        </div>
        <div className="space-y-0.5">
          <label className="text-[10px] text-textMuted flex items-center gap-1">
            <Globe size={10} className="shrink-0" />Host
          </label>
          <input
            type="text"
            value={editHost}
            onChange={e => onConfigChange('host', e.target.value)}
            className={inputClass}
            placeholder="0.0.0.0"
          />
        </div>
      </div>

      <div className="flex items-center justify-between">
        <span className="text-[10px] text-textMuted">Auto-start on boot</span>
        <button
          onClick={onAutoStartToggle}
          disabled={togglingAutoStart}
          className="transition-colors disabled:opacity-50"
          title={editAutoStart ? 'Disable auto-start' : 'Enable auto-start'}
        >
          {togglingAutoStart ? (
            <Loader2 size={18} className="animate-spin text-textMuted" />
          ) : editAutoStart ? (
            <ToggleRight size={22} className="text-primary" />
          ) : (
            <ToggleLeft size={22} className="text-textMuted" />
          )}
        </button>
      </div>

      {needsRestart && (
        <div className="flex items-center gap-1.5 text-[10px] text-amber-400 bg-amber-500/10 rounded-md px-2 py-1">
          <RotateCw size={10} className="shrink-0" />
          <span>{tr('pluginManager.portChangedRestartHint')}</span>
        </div>
      )}
    </div>
  );

  const logsPanel = logsOpen && (
    <div className={`border-t border-border/40 bg-black/30 px-3 py-2 max-h-48 overflow-y-auto ${
      isList ? 'rounded-b-lg -mx-3 -mb-2' : 'rounded-b-lg -mx-5 -mb-5 rounded-t-none'
    }`}>
      {logsLoading ? (
        <div className="flex justify-center py-2">
          <Loader2 size={13} className="animate-spin text-textMuted" />
        </div>
      ) : logs.length === 0 ? (
        <p className="text-[10px] text-textMuted">No logs</p>
      ) : (
        <pre className="text-[10px] text-slate-300 whitespace-pre-wrap leading-relaxed font-mono">
          {logs.join('\n')}
        </pre>
      )}
    </div>
  );

  // ── Compact list row ──
  if (isList) {
    return (
      <div className="bg-panel rounded-lg border border-border px-3 py-2 flex flex-col gap-2 transition-all hover:border-primary/30">
        <div className="flex items-center gap-3 min-w-0">
          <div className={`w-8 h-8 rounded-lg flex items-center justify-center shrink-0 ${
            svc.alive ? 'bg-emerald-500/15' : 'bg-slate-500/15'
          }`}>
            <Server size={16} className={svc.alive ? 'text-emerald-400' : 'text-slate-400'} />
          </div>

          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-1.5 min-w-0">
              <h3 className="text-[13px] font-semibold text-textMain truncate leading-tight">{svc.display_name}</h3>
              {statusBadge}
              {svc.auto_start && (
                <span className="inline-flex items-center gap-0.5 text-[9px] font-medium px-1.5 py-0.5 rounded-full bg-primary/10 text-primary border border-primary/20 shrink-0" title="Auto-start">
                  <Zap size={9} />
                </span>
              )}
            </div>
            <p className="text-[11px] text-textMuted truncate leading-tight mt-0.5">
              {svc.plugin_id}
              {hasPort ? ` · :${svc.port}` : ''}
              {svc.alive && svc.uptime_seconds != null ? ` · ${formatUptime(svc.uptime_seconds)}` : ''}
            </p>
          </div>

          <div className="flex items-center gap-1 shrink-0">
            {actionButtons}
            {settingsBtn}
            <button
              onClick={onToggleLogs}
              className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded-md text-[11px] font-medium text-textMuted hover:text-textMain hover:bg-slate-500/10 transition-colors"
            >
              <Terminal size={11} />
              {logsOpen ? <ChevronUp size={11} /> : <ChevronDown size={11} />}
            </button>
          </div>
        </div>
        <SetupHintBanner svc={svc} />
        <ServiceModelDeploy pluginId={svc.plugin_id} />
        {configPanel}
        {logsPanel}
      </div>
    );
  }

  // ── Grid card ──
  return (
    <div className="bg-panel rounded-xl border border-border p-5 flex flex-col gap-3 transition-all hover:shadow-md">
      {/* Header */}
      <div className="flex items-start gap-3">
        <div className={`w-10 h-10 rounded-xl flex items-center justify-center shrink-0 ${
          svc.alive ? 'bg-emerald-500/15' : 'bg-slate-500/15'
        }`}>
          <Server size={20} className={svc.alive ? 'text-emerald-400' : 'text-slate-400'} />
        </div>

        <div className="flex-1 min-w-0">
          <h3 className="text-sm font-bold text-textMain truncate">{svc.display_name}</h3>
          <p className="text-[10px] text-textMuted">{svc.plugin_id}</p>
        </div>

        <div className="flex items-center gap-1 shrink-0">
          {statusBadge}
          {svc.auto_start && (
            <span
              className="inline-flex items-center gap-0.5 text-[9px] font-medium px-1.5 py-0.5 rounded-full bg-primary/10 text-primary border border-primary/20"
              title="This service starts automatically when OpenSquad launches"
            >
              <Zap size={9} />
              Auto
            </span>
          )}
          {settingsBtn}
        </div>
      </div>

      <SetupHintBanner svc={svc} />
      <ServiceModelDeploy pluginId={svc.plugin_id} />

      {configPanel}

      {/* Details grid */}
      <div className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-[11px]">
        <div className="flex items-center gap-1.5 text-textMuted">
          <Hash size={12} className="shrink-0" />
          <span>Port</span>
        </div>
        <span className="text-textMain font-mono text-right">
          {hasPort ? svc.port : '—'}
        </span>

        <div className="flex items-center gap-1.5 text-textMuted">
          <Globe size={12} className="shrink-0" />
          <span>Host</span>
        </div>
        <span className="text-textMain font-mono text-right">{svc.host || '0.0.0.0'}</span>

        {svc.alive && svc.pid && (
          <>
            <div className="flex items-center gap-1.5 text-textMuted">
              <Zap size={12} className="shrink-0" />
              <span>PID</span>
            </div>
            <span className="text-textMain font-mono text-right">{svc.pid}</span>
          </>
        )}

        {svc.alive && svc.uptime_seconds != null && (
          <>
            <div className="flex items-center gap-1.5 text-textMuted">
              <Clock size={12} className="shrink-0" />
              <span>Uptime</span>
            </div>
            <span className="text-textMain text-right">{formatUptime(svc.uptime_seconds)}</span>
          </>
        )}

        {svc.restart_count > 0 && (
          <>
            <div className="flex items-center gap-1.5 text-textMuted">
              <RotateCw size={12} className="shrink-0" />
              <span>Restarts</span>
            </div>
            <span className={`text-right ${svc.restart_count >= svc.max_restarts ? 'text-red-400' : 'text-amber-400'}`}>
              {svc.restart_count}/{svc.max_restarts}
            </span>
          </>
        )}

        <div className="flex items-center gap-1.5 text-textMuted">
          <Activity size={12} className="shrink-0" />
          <span>Health</span>
        </div>
        <span className={`text-right font-medium ${
          svc.health_ok === true ? 'text-emerald-400' :
          svc.health_ok === false ? 'text-red-400' :
          'text-textMuted'
        }`}>
          {svc.health_ok === true ? 'OK' :
           svc.health_ok === false ? 'Fail' :
           '-'}
        </span>
      </div>

      <div className="flex items-center gap-2">
        <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[10px] font-medium border ${typeClass}`}>
          {TYPE_ICONS[svc.plugin_type]}
          {svc.plugin_type}
        </span>
      </div>

      <div className="flex items-center gap-2 mt-auto pt-2 border-t border-border/50">
        {actionButtons}
        <button
          onClick={onToggleLogs}
          className="ml-auto inline-flex items-center gap-1 px-2 py-1 rounded-md text-[11px] font-medium text-textMuted hover:text-textMain hover:bg-slate-500/10 transition-colors"
        >
          <Terminal size={11} />
          Logs
          {logsOpen ? <ChevronUp size={11} /> : <ChevronDown size={11} />}
        </button>
      </div>

      {logsPanel}
    </div>
  );
};

export default ServiceManagerPage;
