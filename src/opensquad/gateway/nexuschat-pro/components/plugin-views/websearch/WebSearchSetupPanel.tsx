import React, { useCallback, useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import {
  ArrowLeft, RefreshCw, AlertCircle, CheckCircle2, LogIn, Copy, Check, Ban,
  Brain, Globe,
} from 'lucide-react';
import { pluginAPI, pluginServiceAPI } from '../../../services/api';
import type { PluginViewProps } from '../registry';
import ModelDownloadCard from '../ModelDownloadCard';
import { OpenSquadLoader } from '../../OpenSquadLoader';

type SetupData = {
  needs_bing_login?: boolean;
  bing_login_ready?: boolean;
  setup_command?: string;
  message_zh?: string;
  message_en?: string;
  steps_zh?: string[];
  profile_dir?: string;
  description?: string;
  browser_config?: {
    browser?: string;
    options?: string[];
  };
  reranker?: {
    ready?: boolean;
    model_dir?: string;
    snapshot_dir?: string;
    files?: Record<string, number>;
    missing?: string[];
    repo_id?: string;
    revision?: string;
    download?: Record<string, any>;
  };
};

const WebSearchSetupPanel: React.FC<PluginViewProps> = ({ onBack, locale }) => {
  const zh = locale !== 'en';
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [data, setData] = useState<SetupData>({});
  const [copied, setCopied] = useState(false);
  const [serviceAlive, setServiceAlive] = useState<boolean | null>(null);
  const [browserSel, setBrowserSel] = useState('chrome');
  const [browserPath, setBrowserPath] = useState('');
  const [browserSaving, setBrowserSaving] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const payload = await pluginAPI.getPluginData('websearch');
      setData(payload || {});
      const bc = payload?.browser_config;
      if (bc?.browser) setBrowserSel(bc.browser);
      try {
        const svc = await pluginServiceAPI.list();
        const row = (svc.plugin_services || []).find((s: any) => s.plugin_id === 'websearch');
        setServiceAlive(row ? !!row.alive : null);
      } catch {
        setServiceAlive(null);
      }
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const copyCmd = async () => {
    const cmd = data.setup_command || '';
    if (!cmd) return;
    try {
      await navigator.clipboard.writeText(cmd);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      setError(zh ? '复制失败，请手动选择命令' : 'Copy failed');
    }
  };

  const stopService = async () => {
    setBusy(true);
    setError(null);
    setInfo(null);
    try {
      await pluginServiceAPI.stop('websearch');
      setServiceAlive(false);
      setInfo(zh ? 'WebSearch 已停止' : 'WebSearch stopped');
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setBusy(false);
    }
  };

  const openLogin = async () => {
    setBusy(true);
    setError(null);
    setInfo(null);
    try {
      if (serviceAlive) {
        await pluginServiceAPI.stop('websearch');
        setServiceAlive(false);
      }
      const res = await pluginAPI.pluginAction('websearch', 'launch_login_setup');
      if (!res?.ok) {
        setError(res?.error || (zh ? '无法打开登录窗口' : 'Failed to open login window'));
        if (res?.setup_command) setData((d) => ({ ...d, setup_command: res.setup_command }));
      } else {
        setInfo(
          zh
            ? (res.hint_zh || '已打开登录窗口。完成后请在终端按 Enter，再点「我已完成登录」。')
            : (res.hint_en || 'Login window opened. Press Enter in the terminal, then click “I finished login”.')
        );
      }
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setBusy(false);
    }
  };

  const markDone = async () => {
    setBusy(true);
    setError(null);
    try {
      await pluginAPI.pluginAction('websearch', 'mark_login_done');
      await pluginServiceAPI.start('websearch').catch(() => undefined);
      setInfo(zh ? '已标记完成，并尝试启动 WebSearch' : 'Marked done; starting WebSearch');
      await refresh();
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setBusy(false);
    }
  };

  const dismiss = async () => {
    setBusy(true);
    try {
      await pluginAPI.pluginAction('websearch', 'dismiss_banner');
      await refresh();
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setBusy(false);
    }
  };

  const saveBrowser = async () => {
    setBrowserSaving(true);
    setError(null);
    setInfo(null);
    try {
      const target = browserSel === 'custom' ? browserPath.trim() : browserSel;
      if (!target) {
        setError(zh ? '请选择浏览器或填写可执行文件路径' : 'Pick a browser or enter an executable path');
        return;
      }
      const res = await pluginAPI.pluginAction('websearch', 'set_browser', {
        browser: target,
      });
      if (!res?.ok) {
        setError(res?.error || (zh ? '保存浏览器选择失败' : 'Failed to save browser choice'));
      } else {
        setInfo(zh ? '浏览器已保存，重启服务后生效' : 'Browser saved; restart the service to apply');
        await refresh();
      }
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setBrowserSaving(false);
    }
  };

  const ready = !!data.bing_login_ready;
  const steps = data.steps_zh || [];

  return (
    <div className="h-full flex flex-col bg-bgDark text-textMain">
      <div className="flex items-center gap-3 px-4 py-3 border-b border-border/60">
        <button
          type="button"
          onClick={onBack}
          className="p-1.5 rounded-lg hover:bg-bgLight text-textMuted hover:text-textMain transition-colors"
        >
          <ArrowLeft size={16} />
        </button>
        <div className="min-w-0 flex-1">
          <div className="text-sm font-semibold truncate">
            {zh ? 'Web Search / Bing 登录' : 'Web Search / Bing login'}
          </div>
          <div className="text-[11px] text-textMuted truncate">
            {zh ? '首次部署建议完成一次浏览器登录' : 'Recommended once after first deploy'}
          </div>
        </div>
        <button
          type="button"
          onClick={() => void refresh()}
          disabled={loading || busy}
          className="p-1.5 rounded-lg hover:bg-bgLight text-textMuted disabled:opacity-50"
        >
          {loading ? <OpenSquadLoader size={14} /> : <RefreshCw size={14} />}
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {loading ? (
          <div className="flex items-center gap-2 text-textMuted text-sm">
            <OpenSquadLoader size={16} />
            Loading…
          </div>
        ) : (
          <>
            <div
              className={`rounded-xl border px-3 py-3 flex items-start gap-2 ${
                ready
                  ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300'
                  : 'border-amber-500/30 bg-amber-500/10 text-amber-200'
              }`}
            >
              {ready ? <CheckCircle2 size={18} className="shrink-0 mt-0.5" /> : <AlertCircle size={18} className="shrink-0 mt-0.5" />}
              <div className="text-[12px] leading-relaxed">
                {zh ? data.message_zh : data.message_en}
              </div>
            </div>

            {data.description && (
              <p className="text-[12px] text-textMuted leading-relaxed">{data.description}</p>
            )}

            {/* ── Browser selection ─────────────────────────────── */}
            <div className="rounded-lg border border-border bg-bgLight/40 p-3 space-y-2">
              <div className="flex items-center gap-2 text-[12px] font-medium text-textMain/90">
                <Globe size={14} className="text-sky-400" />
                {zh ? '浏览器选择' : 'Browser'}
              </div>
              <p className="text-[11px] text-textMuted leading-relaxed">
                {zh
                  ? '选择用于 Bing 搜索的浏览器。所有浏览器均支持持久化 Cookie（登录后重启服务即生效）。'
                  : 'Pick the browser used for Bing search. All options persist cookies (login once, restart to apply).'}
              </p>
              <select
                value={browserSel}
                onChange={(e) => setBrowserSel(e.target.value)}
                className="w-full px-2.5 py-1.5 rounded-lg bg-bgDark border border-border text-[12px] text-textMain focus:outline-none focus:border-primary/50"
              >
                <option value="chrome">{zh ? 'Google Chrome' : 'Google Chrome'}</option>
                <option value="msedge">{zh ? 'Microsoft Edge' : 'Microsoft Edge'}</option>
                <option value="chromium">{zh ? '自带的 Chromium' : 'Bundled Chromium'}</option>
                <option value="firefox">{zh ? 'Mozilla Firefox' : 'Mozilla Firefox'}</option>
                <option value="custom">{zh ? '自定义可执行文件路径…' : 'Custom executable path…'}</option>
              </select>
              {browserSel === 'custom' && (
                <input
                  type="text"
                  value={browserPath}
                  onChange={(e) => setBrowserPath(e.target.value)}
                  placeholder={zh ? '例如 C:\\Program Files\\BraveSoftware\\Brave-Browser\\Application\\brave.exe' : 'e.g. C:\\Program Files\\...\\brave.exe'}
                  className="w-full px-2.5 py-1.5 rounded-lg bg-bgDark border border-border text-[12px] text-textMain focus:outline-none focus:border-primary/50"
                />
              )}
              <button
                type="button"
                disabled={browserSaving}
                onClick={() => void saveBrowser()}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[12px] font-medium bg-primary/20 text-primary hover:bg-primary/30 disabled:opacity-50"
              >
                {browserSaving ? <OpenSquadLoader size={14} /> : <Globe size={13} />}
                {zh ? '保存浏览器' : 'Save browser'}
              </button>
            </div>

            {!ready && steps.length > 0 && (
              <ol className="list-decimal list-inside space-y-1.5 text-[12px] text-textMain/90">
                {steps.map((s) => (
                  <li key={s}>{s}</li>
                ))}
              </ol>
            )}

            <div className="text-[11px] text-textMuted font-mono break-all bg-bgLight/60 border border-border/50 rounded-lg px-2.5 py-2">
              {zh ? '档案目录：' : 'Profile: '}
              {data.profile_dir || '—'}
              <div className="mt-1">
                {zh ? '服务状态：' : 'Service: '}
                {serviceAlive == null ? '—' : serviceAlive ? (zh ? '运行中' : 'running') : (zh ? '已停止' : 'stopped')}
              </div>
            </div>

            {data.setup_command && (
              <div className="space-y-1.5">
                <div className="text-[11px] text-textMuted">{zh ? '或手动在终端运行：' : 'Or run in a terminal:'}</div>
                <div className="flex gap-2 items-start">
                  <code className="flex-1 text-[11px] font-mono bg-black/40 border border-border/50 rounded-lg px-2 py-2 break-all">
                    {data.setup_command}
                  </code>
                  <button
                    type="button"
                    onClick={() => void copyCmd()}
                    className="shrink-0 inline-flex items-center gap-1 px-2 py-1.5 rounded-lg text-[11px] bg-bgLight border border-border hover:border-primary/40"
                  >
                    {copied ? <Check size={12} /> : <Copy size={12} />}
                    {copied ? (zh ? '已复制' : 'Copied') : (zh ? '复制' : 'Copy')}
                  </button>
                </div>
              </div>
            )}

            <div className="flex flex-wrap gap-2 pt-1">
              {!ready && (
                <>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => void stopService()}
                    className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[12px] font-medium bg-red-500/15 text-red-300 hover:bg-red-500/25 disabled:opacity-50"
                  >
                    {busy ? <OpenSquadLoader size={14} /> : null}
                    {zh ? '停止 WebSearch' : 'Stop WebSearch'}
                  </button>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => void openLogin()}
                    className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[12px] font-medium bg-primary/20 text-primary hover:bg-primary/30 disabled:opacity-50"
                  >
                    <LogIn size={13} />
                    {zh ? '打开登录窗口' : 'Open login window'}
                  </button>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => void markDone()}
                    className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[12px] font-medium bg-emerald-500/15 text-emerald-300 hover:bg-emerald-500/25 disabled:opacity-50"
                  >
                    <CheckCircle2 size={13} />
                    {zh ? '我已完成登录' : 'I finished login'}
                  </button>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => void dismiss()}
                    className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[12px] font-medium bg-bgLight text-textMuted hover:text-textMain disabled:opacity-50"
                  >
                    <Ban size={13} />
                    {zh ? '暂时跳过' : 'Dismiss'}
                  </button>
                </>
              )}
              {ready && (
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void markDone()}
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[12px] font-medium bg-emerald-500/15 text-emerald-300 hover:bg-emerald-500/25 disabled:opacity-50"
                >
                  {zh ? '刷新并启动服务' : 'Refresh & start service'}
                </button>
              )}
            </div>

            {info && <div className="text-[12px] text-emerald-300/90">{info}</div>}
            {error && <div className="text-[12px] text-red-400">{error}</div>}

            {/* ── Reranker model card ─────────────────────────────── */}
            <div className="pt-2 border-t border-border/40 space-y-2">
              <div className="flex items-center gap-2 text-[12px] font-medium text-textMain/90">
                <Brain size={14} className="text-cyan-400" />
                {zh ? 'Qwen3-Reranker 权重模型（约 1.2GB）' : 'Qwen3-Reranker model (~1.2GB)'}
              </div>
              <p className="text-[11px] text-textMuted leading-relaxed">
                {zh
                  ? '用于搜索结果的相关性重排；未下载时搜索仍可用，但会按 Bing 默认顺序返回。'
                  : 'Used to re-order search results by relevance. Search still works without it, falling back to Bing order.'}
              </p>
              <RerankerCard
                locale={zh ? 'zh' : 'en'}
                initial={data.reranker}
                onChanged={refresh}
              />
            </div>
          </>
        )}
      </div>
    </div>
  );
};

let root: ReturnType<typeof createRoot> | null = null;

export const mount = (el: HTMLElement, props: PluginViewProps) => {
  root = createRoot(el);
  root.render(<WebSearchSetupPanel {...props} />);
};

export const unmount = (_el: HTMLElement) => {
  root?.unmount();
  root = null;
};

/**
 * RerankerCard
 *
 * Thin wrapper around the shared ModelDownloadCard. Uses a
 * plugin-specific selector chain so it reads/writes the `reranker`
 * sub-object returned by ``/api/plugins/websearch/data`` and the
 * ``download_reranker`` action.
 */
const RerankerCard: React.FC<{
  locale: 'zh' | 'en';
  initial?: SetupData['reranker'];
  onChanged?: () => void;
}> = ({ locale, initial, onChanged }) => {
  const isZh = locale !== 'en';
  // Lift the initial payload into ModelDownloadCard by pre-seeding
  // its first refresh; we still always re-fetch on mount so the data
  // is current.
  void initial;

  return (
    <ModelDownloadCard
      pluginName="websearch"
      actionName="download_reranker"
      uninstallActionName="uninstall_reranker"
      title={isZh ? 'Reranker 模型' : 'Reranker model'}
      titleEn="Reranker model"
      description={
        isZh
          ? '默认走 hf-mirror.com（国内友好），若返回 401/403/超时自动切换到 huggingface.co；最后尝试 ModelScope 镜像。'
          : 'Downloads from hf-mirror.com first (CN-friendly); on 401/403/timeout it falls back to huggingface.co and finally ModelScope.'
      }
      iconClass="text-cyan-400"
      locale={locale}
      readySelector={(s) => !!(s.reranker && s.reranker.ready)}
      modelDirSelector={(s) => (s.reranker && s.reranker.snapshot_dir) || (s.reranker && s.reranker.model_dir) || ''}
      downloadStateSelector={(s) => (s.reranker && s.reranker.download) || {}}
      renderExtras={(s) => {
        const r = s.reranker;
        if (!r) return null;
        // Only show the missing-file list once a download has been attempted
        // (state becomes non-empty). Before the first download every file is
        // "missing", so listing them is noise.
        if (r.download && r.download.state && r.missing && r.missing.length > 0) {
          return (
            <p className="text-[11px] text-amber-500">
              {isZh ? '缺失文件：' : 'Missing: '}
              {r.missing.join(', ')}
            </p>
          );
        }
        if (r.files && Object.keys(r.files).length > 0) {
          const items = Object.entries(r.files).filter(([, n]) => typeof n === 'number' && n > 0);
          if (items.length === 0) return null;
          return (
            <ul className="text-[11px] text-textMuted space-y-0.5">
              {items.slice(0, 6).map(([name, size]) => (
                <li key={name}>
                  {name} — {(Number(size) / (1024 * 1024)).toFixed(1)} MB
                </li>
              ))}
              {items.length > 6 ? <li>… +{items.length - 6} more</li> : null}
            </ul>
          );
        }
        return null;
      }}
      onDownloaded={() => {
        if (onChanged) onChanged();
      }}
    />
  );
};
