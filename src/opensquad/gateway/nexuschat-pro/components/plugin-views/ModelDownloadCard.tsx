import React, { useEffect, useState, useCallback } from 'react';
import {
  RefreshCw, AlertCircle, Download, CheckCircle2, HardDrive, Search,
  Trash2, Folder, Cpu,
} from 'lucide-react';
import { pluginAPI } from '../../services/api';
import { HoverTooltip } from '../HoverTooltip';
import { OpenSquadLoader } from '../OpenSquadLoader';

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
  /** Action name to uninstall / delete the downloaded model. Default: 'uninstall_model'. */
  uninstallActionName?: string;
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
  uninstallActionName = 'uninstall_model',
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

  const onUninstall = async () => {
    if (!window.confirm(
      isZh
        ? '确定要卸载此模型吗？\n\n模型文件将从磁盘删除并释放空间。'
        : 'Uninstall this model?\n\nThe model files will be removed from disk to free space.',
    )) {
      return;
    }
    setBusy(true);
    setError(null);
    setInfo(null);
    try {
      const res = await pluginAPI.pluginAction(pluginName, uninstallActionName, {});
      await refresh();
      setInfo(res?.message || (isZh ? '模型已卸载' : 'Model uninstalled'));
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
    <div className={`rounded-lg border ${
      ready ? 'border-emerald-500/25 bg-emerald-500/[0.04]' : 'border-border bg-bgLight/30'
    } p-3 space-y-2`}>
      {/* Title row: model name + status */}
      <div className="flex items-center gap-2 min-w-0">
        <Cpu size={14} className={ready ? 'text-emerald-400 shrink-0' : 'text-textMuted shrink-0'} />
        {Icon ? <Icon size={13} className={`${iconClass} shrink-0`} /> : null}
        <span className="text-[12px] font-semibold text-textMain truncate">{displayTitle}</span>
        <span className={`inline-flex items-center px-1.5 py-0.5 rounded text-[9px] font-medium shrink-0 ${
          ready
            ? 'bg-emerald-500/15 text-emerald-400 border border-emerald-500/25'
            : 'bg-bgDark/60 text-textMuted border border-border'
        }`}>
          {ready ? (isZh ? '已部署' : 'Installed') : (isZh ? '未安装' : 'Not installed')}
        </span>
        <button
          type="button"
          onClick={() => void refresh()}
          disabled={loading || busy}
          className="ml-auto p-1 rounded hover:bg-bgLight text-textMuted disabled:opacity-50 shrink-0"
          title={isZh ? '刷新' : 'Refresh'}
        >
          {loading ? <OpenSquadLoader size={12} /> : <RefreshCw size={12} />}
        </button>
      </div>

      {displayDesc ? (
        <p className="text-[11px] text-textMuted leading-relaxed line-clamp-2">
          {displayDesc}
        </p>
      ) : null}

      {/* Status / path row */}
      <div className="flex items-center gap-1.5 text-[10.5px] text-textMuted min-w-0">
        {ready
          ? <CheckCircle2 size={11} className="text-emerald-400 shrink-0" />
          : <Download size={11} className="text-textMuted shrink-0" />}
        <span className="truncate min-w-0">
          {ready
            ? (isZh ? '模型已就绪' : 'Model ready')
            : (isZh ? '未下载' : 'Not downloaded')}
        </span>
        {ready && modelDir ? (
          <HoverTooltip text={modelDir} maxWidth="24rem">
            <button
              type="button"
              tabIndex={0}
              className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[10px] text-textMuted/80 hover:text-primary hover:bg-bgLight/60 border border-dashed border-border transition-colors shrink-0"
            >
              <Folder size={9} className="shrink-0" />
              <span className="whitespace-nowrap">{isZh ? '地址' : 'Path'}</span>
            </button>
          </HoverTooltip>
        ) : null}
      </div>

      {(status.missing && status.missing.length > 0) ? (
        <p className="text-[11px] text-amber-500 break-all">
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
        <div className="rounded-md border border-border bg-bgLight/60 px-2 py-1.5 text-[11px] text-textMuted break-all">
          {info}
        </div>
      ) : null}

      {(downloading || downloadState.state === 'error') && (
        <div className="space-y-1.5">
          <div className="flex justify-between text-[11px] text-textMuted min-w-0">
            <span className="truncate min-w-0">
              {downloadState.message || downloadState.state}
              {downloadState.source ? (
                <span className="text-textMuted/70">
                  {' '}({downloadState.mirror_index || 1}/{downloadState.mirror_total || 1} {isZh ? '镜像' : 'mirror'}: {downloadState.source})
                </span>
              ) : null}
            </span>
            <span className="shrink-0">{progress.toFixed(0)}%</span>
          </div>
          <div className="h-1.5 rounded-full bg-bgDark overflow-hidden">
            <div
              className="h-full bg-primary transition-all"
              style={{ width: `${Math.min(100, Math.max(0, progress))}%` }}
            />
          </div>
          {downloadState.file ? (
            <p className="text-[11px] text-textMuted break-all">
              {isZh ? '当前文件：' : 'Current file: '}
              {downloadState.file}
            </p>
          ) : null}
        </div>
      )}

      <div className="flex flex-wrap gap-2 pt-1">
        {!ready ? (
          <button
            type="button"
            disabled={busy || downloading || loading}
            onClick={() => void onDownload(false)}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-primary text-white text-xs disabled:opacity-50 hover:bg-primary/90"
          >
            {busy || downloading ? (
              <OpenSquadLoader size={14} />
            ) : (
              <Download size={13} />
            )}
            {isZh ? '下载模型' : 'Download model'}
          </button>
        ) : (
          <button
            type="button"
            disabled={busy || downloading}
            onClick={() => void onUninstall()}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-red-500/10 text-red-400 hover:bg-red-500/20 text-xs disabled:opacity-50"
            title={isZh ? '卸载模型文件并释放磁盘空间' : 'Remove model files from disk'}
          >
            {busy ? <OpenSquadLoader size={12} /> : <Trash2 size={12} />}
            {isZh ? '卸载模型' : 'Uninstall'}
          </button>
        )}
        {ready ? (
          <button
            type="button"
            disabled={busy || downloading}
            onClick={() => void onDownload(true)}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md border border-border text-xs disabled:opacity-50 hover:border-primary/40"
            title={isZh ? '强制重新下载' : 'Force re-download'}
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
