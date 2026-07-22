import React, { useCallback, useEffect, useState } from 'react';
import {
  ArrowLeft, RefreshCw, Loader2, AlertCircle, Download, Mic, CheckCircle2, HardDrive,
} from 'lucide-react';
import { pluginAPI, pluginServiceAPI } from '../../../services/api';

type DownloadState = {
  state?: string;
  message?: string;
  progress?: number;
  file?: string;
};

const SenseVoicePanel: React.FC<{ onBack: () => void }> = ({ onBack }) => {
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const [modelDir, setModelDir] = useState('');
  const [missing, setMissing] = useState<string[]>([]);
  const [files, setFiles] = useState<Record<string, number>>({});
  const [download, setDownload] = useState<DownloadState>({});
  const [description, setDescription] = useState('');
  const [serviceAlive, setServiceAlive] = useState<boolean | null>(null);

  const applyPayload = (data: any) => {
    setReady(!!data.ready);
    setModelDir(data.model_dir || '');
    setMissing(data.missing || []);
    setFiles(data.files || {});
    setDownload(data.download || {});
    if (data.description) setDescription(data.description);
  };

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await pluginAPI.getPluginData('sensevoice');
      applyPayload(data);
      try {
        const svc = await pluginServiceAPI.list();
        const row = (svc.plugin_services || []).find(
          (s: any) => s.plugin_id === 'sensevoice' || s.plugin_id === 'sensevoice_asr'
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
    if (download.state !== 'downloading') return;
    const t = window.setInterval(() => {
      void (async () => {
        try {
          const data = await pluginAPI.getPluginData('sensevoice');
          applyPayload(data);
        } catch {
          /* ignore poll errors */
        }
      })();
    }, 1500);
    return () => window.clearInterval(t);
  }, [download.state]);

  const onDownload = async (force = false) => {
    setBusy(true);
    setError(null);
    setInfo(null);
    try {
      const res = await pluginAPI.pluginAction('sensevoice', 'download_model', { force });
      applyPayload(res);
      setInfo(res.message || (force ? '已开始重新下载' : '已开始下载模型'));
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
      await pluginServiceAPI.start('sensevoice');
      setInfo('SenseVoice 服务启动请求已发送');
      await refresh();
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setBusy(false);
    }
  };

  const progress = Number(download.progress || 0);
  const downloading = download.state === 'downloading';

  return (
    <div className="h-full flex flex-col bg-bgDark text-textMain">
      <div className="flex items-center gap-2 px-4 py-3 border-b border-border">
        <button
          type="button"
          onClick={onBack}
          className="p-1.5 rounded-md hover:bg-bgLight text-textMuted"
          title="返回"
        >
          <ArrowLeft size={16} />
        </button>
        <Mic size={16} className="text-primary" />
        <div className="flex-1 min-w-0">
          <h2 className="text-sm font-semibold truncate">SenseVoice ASR</h2>
          <p className="text-[11px] text-textMuted truncate">系统内置本地语音转文本</p>
        </div>
        <button
          type="button"
          onClick={() => void refresh()}
          disabled={loading || busy}
          className="p-1.5 rounded-md hover:bg-bgLight text-textMuted disabled:opacity-50"
          title="刷新"
        >
          <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
        </button>
      </div>

      <div className="flex-1 overflow-auto p-4 space-y-3">
        {description ? (
          <p className="text-xs text-textMuted leading-relaxed">{description}</p>
        ) : null}

        {error ? (
          <div className="flex items-start gap-2 rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2 text-xs text-red-400">
            <AlertCircle size={14} className="mt-0.5 shrink-0" />
            <span>{error}</span>
          </div>
        ) : null}
        {info ? (
          <div className="rounded-lg border border-border bg-bgLight/60 px-3 py-2 text-xs text-textMuted">
            {info}
          </div>
        ) : null}

        <div className="rounded-lg border border-border bg-bgLight/40 p-3 space-y-2">
          <div className="flex items-center gap-2 text-sm">
            {ready ? (
              <CheckCircle2 size={16} className="text-emerald-500" />
            ) : (
              <HardDrive size={16} className="text-amber-500" />
            )}
            <span className="font-medium">{ready ? '模型已就绪' : '模型未下载'}</span>
          </div>
          <p className="text-[11px] text-textMuted break-all">目录：{modelDir || '—'}</p>
          {missing.length > 0 ? (
            <p className="text-[11px] text-amber-500">缺失文件：{missing.join(', ')}</p>
          ) : null}
          {Object.keys(files).length > 0 ? (
            <ul className="text-[11px] text-textMuted space-y-0.5">
              {Object.entries(files).map(([name, size]) => (
                <li key={name}>
                  {name} — {(Number(size) / (1024 * 1024)).toFixed(1)} MB
                </li>
              ))}
            </ul>
          ) : null}
        </div>

        {(downloading || download.state === 'error') && (
          <div className="rounded-lg border border-border bg-bgLight/40 p-3 space-y-2">
            <div className="flex justify-between text-[11px] text-textMuted">
              <span>{download.message || download.state}</span>
              <span>{progress.toFixed(0)}%</span>
            </div>
            <div className="h-1.5 rounded-full bg-bgDark overflow-hidden">
              <div
                className="h-full bg-primary transition-all"
                style={{ width: `${Math.min(100, Math.max(0, progress))}%` }}
              />
            </div>
            {download.file ? (
              <p className="text-[11px] text-textMuted">当前文件：{download.file}</p>
            ) : null}
          </div>
        )}

        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            disabled={busy || downloading || ready}
            onClick={() => void onDownload(false)}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-primary text-white text-xs disabled:opacity-50"
          >
            {busy || downloading ? <Loader2 size={14} className="animate-spin" /> : <Download size={14} />}
            {ready ? '模型已下载' : '下载模型'}
          </button>
          {ready ? (
            <button
              type="button"
              disabled={busy || downloading}
              onClick={() => void onDownload(true)}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md border border-border text-xs disabled:opacity-50"
            >
              重新下载
            </button>
          ) : null}
          <button
            type="button"
            disabled={busy || !ready}
            onClick={() => void onStartService()}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md border border-border text-xs disabled:opacity-50"
            title={!ready ? '请先下载模型' : '启动 SenseVoice 服务'}
          >
            {serviceAlive ? '服务运行中（可重启）' : '启动服务'}
          </button>
        </div>

        <div className="rounded-lg border border-border/70 px-3 py-2 text-[11px] text-textMuted space-y-1">
          <p>使用步骤：</p>
          <ol className="list-decimal pl-4 space-y-0.5">
            <li>点击「下载模型」（约 150MB，来自 ModelScope SenseVoiceSmall）</li>
            <li>下载完成后启动 SenseVoice 服务</li>
            <li>在 Agent Web 语音配置 → ASR 输入中选择「系统内置 SenseVoice ASR」</li>
          </ol>
          <p className="pt-1">OpenSquad 首次安装不会自动下载此模型。</p>
        </div>
      </div>
    </div>
  );
};

export default SenseVoicePanel;

import { createRoot } from 'react-dom/client';

let root: ReturnType<typeof createRoot> | null = null;

export function mount(el: HTMLElement, props: { onBack: () => void }) {
  root = createRoot(el);
  root.render(<SenseVoicePanel onBack={props.onBack} />);
}

export function unmount(_el: HTMLElement) {
  if (root) {
    root.unmount();
    root = null;
  }
}
