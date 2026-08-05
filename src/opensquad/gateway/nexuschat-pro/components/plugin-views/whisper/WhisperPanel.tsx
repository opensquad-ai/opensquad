import React, { useCallback, useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import {
  ArrowLeft, RefreshCw, Loader2, AlertCircle, CheckCircle2, Mic, Download,
  HardDrive, Database,
} from 'lucide-react';
import { pluginAPI, pluginServiceAPI } from '../../../services/api';
import type { PluginViewProps } from '../registry';

type WhisperModel = {
  name: string;
  size_mb?: number;
  ready?: boolean;
  selected?: boolean;
};

type WhisperStatus = {
  ok?: boolean;
  plugin?: string;
  title?: string;
  description?: string;
  ready?: boolean;
  model?: string;
  model_dir?: string;
  legacy_cache_dir?: string;
  file_size?: number;
  available_models?: WhisperModel[];
  download?: {
    state?: string;
    message?: string;
    progress?: number;
    file?: string;
    source?: string;
    mirror_index?: number;
    mirror_total?: number;
  };
};

const WhisperPanel: React.FC<PluginViewProps> = ({ onBack, locale }) => {
  const zh = locale !== 'en';
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [status, setStatus] = useState<WhisperStatus>({});
  const [serviceAlive, setServiceAlive] = useState<boolean | null>(null);
  const [selectedModel, setSelectedModel] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await pluginAPI.getPluginData('whisper');
      setStatus(data || {});
      setSelectedModel((data && data.model) || null);
      try {
        const svc = await pluginServiceAPI.list();
        const row = (svc.plugin_services || []).find(
          (s: any) => s.plugin_id === 'whisper' || s.plugin_id === 'whisper_transcribe',
        );
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

  // Poll while downloading
  useEffect(() => {
    if (status.download?.state !== 'downloading') return;
    const t = window.setInterval(() => {
      void (async () => {
        try {
          const data = await pluginAPI.getPluginData('whisper');
          setStatus(data || {});
        } catch {
          /* ignore */
        }
      })();
    }, 1500);
    return () => window.clearInterval(t);
  }, [status.download?.state]);

  const onDownload = async (force = false) => {
    setBusy(true);
    setError(null);
    setInfo(null);
    try {
      const res = await pluginAPI.pluginAction('whisper', 'download_model', {
        force,
        model: selectedModel || undefined,
      });
      await refresh();
      setInfo(
        res?.message ||
          (force
            ? zh ? '已开始重新下载' : 'Re-download started'
            : zh ? '已开始下载模型' : 'Download started'),
      );
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setBusy(false);
    }
  };

  const onStartService = async () => {
    setBusy(true);
    setError(null);
    setInfo(null);
    try {
      await pluginServiceAPI.start('whisper');
      setInfo(zh ? 'Whisper 服务启动请求已发送' : 'Whisper start request sent');
      await refresh();
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setBusy(false);
    }
  };

  const onStopService = async () => {
    setBusy(true);
    setError(null);
    setInfo(null);
    try {
      await pluginServiceAPI.stop('whisper');
      setServiceAlive(false);
      setInfo(zh ? 'Whisper 已停止' : 'Whisper stopped');
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setBusy(false);
    }
  };

  const ready = !!status.ready;
  const downloading = status.download?.state === 'downloading';
  const progress = Number(status.download?.progress || 0);
  const models = status.available_models || [];

  return (
    <div className="h-full flex flex-col bg-bgDark text-textMain">
      <div className="flex items-center gap-2 px-4 py-3 border-b border-border">
        <button
          type="button"
          onClick={onBack}
          className="p-1.5 rounded-md hover:bg-bgLight text-textMuted"
          title={zh ? '返回' : 'Back'}
        >
          <ArrowLeft size={16} />
        </button>
        <Mic size={16} className="text-primary" />
        <div className="flex-1 min-w-0">
          <h2 className="text-sm font-semibold truncate">
            {zh ? 'Whisper 语音转文本' : 'Whisper Speech-to-Text'}
          </h2>
          <p className="text-[11px] text-textMuted truncate">
            {zh
              ? '离线 ASR 服务，可选择不同模型大小'
              : 'Local ASR service, choose model size'}
          </p>
        </div>
        <button
          type="button"
          onClick={() => void refresh()}
          disabled={loading || busy}
          className="p-1.5 rounded-md hover:bg-bgLight text-textMuted disabled:opacity-50"
          title={zh ? '刷新' : 'Refresh'}
        >
          <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
        </button>
      </div>

      <div className="flex-1 overflow-auto p-4 space-y-3">
        {status.description ? (
          <p className="text-xs text-textMuted leading-relaxed">{status.description}</p>
        ) : null}

        {error ? (
          <div className="flex items-start gap-2 rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2 text-xs text-red-400">
            <AlertCircle size={14} className="mt-0.5 shrink-0" />
            <span className="break-all">{error}</span>
          </div>
        ) : null}
        {info ? (
          <div className="rounded-lg border border-border bg-bgLight/60 px-3 py-2 text-xs text-textMuted">
            {info}
          </div>
        ) : null}

        {/* ── Model picker ──────────────────────────────────────── */}
        <div className="rounded-lg border border-border bg-bgLight/40 p-3 space-y-2">
          <div className="flex items-center gap-2 text-sm">
            {ready ? (
              <CheckCircle2 size={16} className="text-emerald-500" />
            ) : (
              <HardDrive size={16} className="text-amber-500" />
            )}
            <span className="font-medium">
              {zh ? '当前模型：' : 'Current model: '}
              {status.model || '—'}
            </span>
            {status.model ? (
              <span className="text-[11px] text-textMuted">
                ({((Number(status.file_size) || 0) / (1024 * 1024)).toFixed(0)} MB)
              </span>
            ) : null}
          </div>

          {ready ? (
            <div className="flex items-center gap-1.5 rounded-md border border-emerald-500/30 bg-emerald-500/10 px-2 py-1.5 text-[11px] text-emerald-300">
              <CheckCircle2 size={12} className="shrink-0" />
              <span>{zh ? '模型已下载' : 'Model downloaded'}</span>
            </div>
          ) : null}

          <div className="text-[11px] text-textMuted">
            {zh
              ? '选择要下载并启用的模型大小。模型越大精度越高，但首次下载与启动更慢。'
              : 'Pick a model size. Larger = better accuracy, slower first download and load.'}
          </div>

          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 pt-1">
            {models.map((m) => {
              const isSelected = m.name === (selectedModel || status.model);
              return (
                <button
                  key={m.name}
                  type="button"
                  onClick={() => setSelectedModel(m.name)}
                  disabled={busy}
                  className={`relative flex flex-col items-start gap-1 rounded-md border px-2.5 py-2 text-left text-[12px] transition-colors ${
                    isSelected
                      ? 'border-primary/60 bg-primary/15 text-textMain'
                      : 'border-border bg-bgLight text-textMuted hover:text-textMain hover:border-primary/30'
                  } disabled:opacity-50`}
                >
                  <div className="flex items-center gap-1.5">
                    <Database size={12} className={isSelected ? 'text-primary' : 'text-textMuted'} />
                    <span className="font-mono font-medium">{m.name}</span>
                    {m.ready ? (
                      <CheckCircle2 size={11} className="text-emerald-500" />
                    ) : null}
                  </div>
                  <div className="text-[10px] text-textMuted">
                    {m.size_mb ? `~${m.size_mb} MB` : ''}
                  </div>
                  {isSelected ? (
                    <span className="absolute right-1.5 top-1.5 text-[9px] text-primary">
                      ●
                    </span>
                  ) : null}
                </button>
              );
            })}
          </div>

          <p className="text-[11px] text-textMuted break-all">
            {zh ? '下载目录：' : 'Download path: '}
            {status.model_dir || '—'}
          </p>
          {status.legacy_cache_dir ? (
            <p className="text-[11px] text-textMuted/70 break-all">
              {zh ? '兼容缓存：' : 'Legacy cache: '}
              {status.legacy_cache_dir}
            </p>
          ) : null}
        </div>

        {/* ── Progress ──────────────────────────────────────────── */}
        {(downloading || status.download?.state === 'error') && (
          <div className="rounded-lg border border-border bg-bgLight/40 p-3 space-y-2">
            <div className="flex justify-between text-[11px] text-textMuted">
              <span className="truncate">
                {status.download?.message || status.download?.state}
                {status.download?.source ? (
                  <span className="text-textMuted/70">
                    {' '}({status.download.mirror_index || 1}/{status.download.mirror_total || 1} {zh ? '镜像' : 'mirror'}: {status.download.source})
                  </span>
                ) : null}
              </span>
              <span>{progress.toFixed(0)}%</span>
            </div>
            <div className="h-1.5 rounded-full bg-bgDark overflow-hidden">
              <div
                className="h-full bg-primary transition-all"
                style={{ width: `${Math.min(100, Math.max(0, progress))}%` }}
              />
            </div>
            {status.download?.file ? (
              <p className="text-[11px] text-textMuted">
                {zh ? '当前文件：' : 'Current file: '}
                {status.download.file}
              </p>
            ) : null}
          </div>
        )}

        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            disabled={busy || downloading || (ready && selectedModel === status.model)}
            onClick={() => void onDownload(false)}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-primary text-white text-xs disabled:opacity-50"
          >
            {busy || downloading ? <Loader2 size={13} className="animate-spin" /> : <Download size={13} />}
            {ready && selectedModel === status.model
              ? (zh ? '模型已下载' : 'Downloaded')
              : (zh ? '下载模型' : 'Download model')}
          </button>
          {ready ? (
            <button
              type="button"
              disabled={busy || downloading}
              onClick={() => void onDownload(true)}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md border border-border text-xs disabled:opacity-50"
            >
              {zh ? '重新下载' : 'Re-download'}
            </button>
          ) : null}

          {serviceAlive ? (
            <button
              type="button"
              disabled={busy}
              onClick={() => void onStopService()}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md border border-red-500/40 text-red-300 text-xs disabled:opacity-50"
            >
              {zh ? '停止服务' : 'Stop service'}
            </button>
          ) : (
            <button
              type="button"
              disabled={busy || !ready}
              onClick={() => void onStartService()}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md border border-border text-xs disabled:opacity-50"
              title={!ready ? (zh ? '请先下载模型' : 'Please download the model first') : ''}
            >
              {zh ? '启动服务' : 'Start service'}
            </button>
          )}
        </div>

        <div className="rounded-lg border border-border/70 px-3 py-2 text-[11px] text-textMuted space-y-1">
          <p>{zh ? '使用步骤：' : 'Usage:'}</p>
          <ol className="list-decimal pl-4 space-y-0.5">
            <li>
              {zh
                ? '选择一个模型大小，点击「下载模型」（首次会从 openai-whisper 官方 Azure CDN 拉取，失败后切换到 hf-mirror.com / huggingface.co）'
                : 'Pick a model size, click "Download model" (fetches from the official Azure CDN first; falls back to hf-mirror.com / huggingface.co)'}
            </li>
            <li>{zh ? '下载完成后启动 Whisper 服务' : 'Start the Whisper service'}</li>
            <li>
              {zh
                ? '在 Agent Web 语音配置 → ASR 输入中选择本地 Whisper'
                : 'Pick local Whisper in Agent Web voice config → ASR input'}
            </li>
          </ol>
          <p className="pt-1">
            {zh
              ? '如果 Azure CDN 出现 401/403/超时，会自动切换到 hf-mirror.com（国内友好），最后尝试 huggingface.co。'
              : 'On 401/403/timeout the downloader falls back from Azure CDN to hf-mirror.com (CN-friendly) and finally to huggingface.co.'}
          </p>
        </div>
      </div>
    </div>
  );
};

let root: ReturnType<typeof createRoot> | null = null;

export const mount = (el: HTMLElement, props: PluginViewProps) => {
  root = createRoot(el);
  root.render(<WhisperPanel {...props} />);
};

export const unmount = (_el: HTMLElement) => {
  root?.unmount();
  root = null;
};
