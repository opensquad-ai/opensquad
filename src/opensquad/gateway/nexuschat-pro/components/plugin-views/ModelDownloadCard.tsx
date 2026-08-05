import React, { useEffect, useState, useCallback } from 'react';
import {
  RefreshCw, Loader2, AlertCircle, Download, CheckCircle2, HardDrive, Search,
} from 'lucide-react';
import { pluginAPI } from '../../services/api';

/**
 * ModelDownloadCard
 *
 * Reusable "download a model from the internet" UI used by the SenseVoice
 * panel, the WebSearch reranker card, and the Whisper model picker.
 *
 * Generic over a plugin name + action: every plugin that exposes a
 * `query_data` returning `{ready, model_dir, download: {...}, ...}` and a
 * `pluginAction(..., 'download_model', {force})` action can be rendered
 * with this card.
 *
 * Props
 * -----
 * - pluginName:      plugin id passed to pluginAPI.getPluginData / pluginAction
 * - title:           header text (zh + en handled inside)
 * - description:     small line under the title
 * - icon:            Lucide icon for the header
 * - iconClass:       tailwind class for the icon color
 * - renderExtras:    optional (status) => ReactNode  – extra rows in the
 *                    "files / size" panel (used to render available
 *                    models in the whisper card)
 * - locale:          'zh' | 'en' (defaults to zh)
 * - readySelector:   (status) => boolean — defaults to `status.ready`
 * - downloadStateSelector: (status) => the persisted download state object
 *                    (defaults to `status.download`)
 */
type AnyStatus = Record<string, any>;

export interface ModelDownloadCardProps {
  pluginName: string;
  title: string;
  titleEn?: string;
  description?: string;
  descriptionEn?: string;
  icon?: React.ComponentType<{ size?: number; className?: string }>;
  iconClass?: string;
  locale?: 'zh' | 'en';
  readySelector?: (s: AnyStatus) => boolean;
  modelDirSelector?: (s: AnyStatus) => string;
  downloadStateSelector?: (s: AnyStatus) => AnyStatus;
  renderExtras?: (status: AnyStatus) => React.ReactNode;
  /** Action name to POST to pluginAction(). Default: 'download_model'. */
  actionName?: string;
  /** Optional callback fired after a successful download completes. */
  onDownloaded?: (status: AnyStatus) => void;
}

export const ModelDownloadCard: React.FC<ModelDownloadCardProps> = ({
  pluginName,
  title,
  titleEn,
  description,
  descriptionEn,
  icon: Icon,
  iconClass = 'text-primary',
  locale,
  readySelector,
  modelDirSelector,
  downloadStateSelector,
  renderExtras,
  actionName = 'download_model',
  onDownloaded,
}) => {
  const isZh = (locale || 'zh') !== 'en';
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [status, setStatus] = useState<AnyStatus>({});

  const ready = readySelector ? readySelector(status) : !!status.ready;
  const modelDir = modelDirSelector ? modelDirSelector(status) : status.model_dir || '';
  const downloadState = downloadStateSelector ? downloadStateSelector(status) : status.download || {};

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await pluginAPI.getPluginData(pluginName);
      setStatus(data || {});
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setLoading(false);
    }
  }, [pluginName]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Poll while downloading
  useEffect(() => {
    if (downloadState.state !== 'downloading') return;
    const t = window.setInterval(() => {
      void (async () => {
        try {
          const data = await pluginAPI.getPluginData(pluginName);
          setStatus(data || {});
        } catch {
          /* ignore poll errors */
        }
      })();
    }, 1500);
    return () => window.clearInterval(t);
  }, [downloadState.state, pluginName]);

  const onDownload = async (force = false) => {
    setBusy(true);
    setError(null);
    setInfo(null);
    try {
      const res = await pluginAPI.pluginAction(pluginName, actionName, { force });
      // The action result may not contain the same shape as getPluginData,
      // so just trigger a refresh and surface a message.
      await refresh();
      setInfo(
        res?.message ||
          (force
            ? isZh ? '已开始重新下载' : 'Re-download started'
            : isZh ? '已开始下载模型' : 'Download started'),
      );
      if (res?.ready && onDownloaded) {
        onDownloaded(res);
      }
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setBusy(false);
    }
  };

  const progress = Number(downloadState.progress || 0);
  const downloading = downloadState.state === 'downloading';
  const displayTitle = isZh ? title : titleEn || title;
  const displayDesc = isZh ? description : descriptionEn || description;

  return (
    <div className="rounded-lg border border-border bg-bgLight/40 p-3 space-y-2">
      <div className="flex items-center gap-2 text-sm">
        {ready ? (
          <CheckCircle2 size={16} className="text-emerald-500" />
        ) : (
          <HardDrive size={16} className="text-amber-500" />
        )}
        {Icon ? <Icon size={14} className={iconClass} /> : null}
        <span className="font-medium">{displayTitle}</span>
        <button
          type="button"
          onClick={() => void refresh()}
          disabled={loading || busy}
          className="ml-auto p-1 rounded hover:bg-bgLight text-textMuted disabled:opacity-50"
          title={isZh ? '刷新' : 'Refresh'}
        >
          <RefreshCw size={12} className={loading ? 'animate-spin' : ''} />
        </button>
      </div>

      {displayDesc ? (
        <p className="text-[11px] text-textMuted leading-relaxed">{displayDesc}</p>
      ) : null}

      <p className="text-[11px] text-textMuted break-all">
        {isZh ? '目录：' : 'Path: '}
        {modelDir || '—'}
      </p>

      {(status.missing && status.missing.length > 0) ? (
        <p className="text-[11px] text-amber-500">
          {isZh ? '缺失文件：' : 'Missing: '}
          {status.missing.join(', ')}
        </p>
      ) : null}

      {renderExtras ? renderExtras(status) : null}

      {error ? (
        <div className="flex items-start gap-1.5 rounded-md border border-red-500/40 bg-red-500/10 px-2 py-1.5 text-[11px] text-red-400">
          <AlertCircle size={12} className="mt-0.5 shrink-0" />
          <span className="break-all">{error}</span>
        </div>
      ) : null}
      {info ? (
        <div className="rounded-md border border-border bg-bgLight/60 px-2 py-1.5 text-[11px] text-textMuted">
          {info}
        </div>
      ) : null}

      {(downloading || downloadState.state === 'error') && (
        <div className="space-y-1.5">
          <div className="flex justify-between text-[11px] text-textMuted">
            <span className="truncate">
              {downloadState.message || downloadState.state}
              {downloadState.source ? (
                <span className="text-textMuted/70">
                  {' '}({downloadState.mirror_index || 1}/{downloadState.mirror_total || 1} {isZh ? '镜像' : 'mirror'}: {downloadState.source})
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
          {downloadState.file ? (
            <p className="text-[11px] text-textMuted">
              {isZh ? '当前文件：' : 'Current file: '}
              {downloadState.file}
            </p>
          ) : null}
        </div>
      )}

      <div className="flex flex-wrap gap-2 pt-1">
        <button
          type="button"
          disabled={busy || downloading || ready}
          onClick={() => void onDownload(false)}
          className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-primary text-white text-xs disabled:opacity-50"
        >
          {busy || downloading ? (
            <Loader2 size={13} className="animate-spin" />
          ) : (
            <Download size={13} />
          )}
          {ready
            ? (isZh ? '模型已下载' : 'Downloaded')
            : (isZh ? '下载模型' : 'Download model')}
        </button>
        {ready ? (
          <button
            type="button"
            disabled={busy || downloading}
            onClick={() => void onDownload(true)}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md border border-border text-xs disabled:opacity-50"
          >
            <RefreshCw size={12} />
            {isZh ? '重新下载' : 'Re-download'}
          </button>
        ) : null}
      </div>
    </div>
  );
};

export default ModelDownloadCard;
