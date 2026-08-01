import React, { useCallback, useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import {
  ArrowLeft, RefreshCw, Loader2, AlertCircle, CheckCircle2, LogIn, Copy, Check, Ban,
} from 'lucide-react';
import { pluginAPI, pluginServiceAPI } from '../../../services/api';
import type { PluginViewProps } from '../registry';

type SetupData = {
  needs_bing_login?: boolean;
  bing_login_ready?: boolean;
  setup_command?: string;
  message_zh?: string;
  message_en?: string;
  steps_zh?: string[];
  profile_dir?: string;
  description?: string;
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

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const payload = await pluginAPI.getPluginData('websearch');
      setData(payload || {});
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
          <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {loading ? (
          <div className="flex items-center gap-2 text-textMuted text-sm">
            <Loader2 size={16} className="animate-spin" />
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
                    {busy ? <Loader2 size={13} className="animate-spin" /> : null}
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
