/**
 * SoloContextFooter — Cursor-style status row under the Solo composer.
 * One line: project cwd | Mode (children) | … | Model/Effort (trailing) | token ring %.
 */
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Check, ChevronDown, FileDown, Folder, FolderOpen, List, Scissors, X } from 'lucide-react';
import {
  folderLabel,
  loadCwdRecents,
  pickFolder,
  pushCwdRecent,
} from '../../utils/cwdRecents';
import { POPOVER_SURFACE_CLASS } from './popoverSurface';
import type { SessionExportResult } from '../../utils/sessionExport';

export interface SoloTokenBreakdown {
  system?: number;
  user?: number;
  thought?: number;
  tool?: number;
  tool_defs?: number;
  response?: number;
  overhead?: number;
}

export interface SoloTokenStats {
  used: number;
  max: number;
  breakdown?: SoloTokenBreakdown;
  session?: {
    total_tokens?: number;
    total_requests?: number;
    /** Prompt tokens for this session — cache hit + cache miss. */
    input_tokens?: number;
    total_input_tokens?: number;
    output_tokens?: number;
    total_output_tokens?: number;
    /** Prompt tokens served from the provider's cache (subset of input). */
    cache_read_tokens?: number;
    /** Input − cache hit, precomputed server-side; derived when absent. */
    cache_miss_tokens?: number;
    /**
     * True when a turn ran without provider usage (the endpoint sent no usage
     * chunk), so the counters above are tokenizer estimates and cache read is
     * structurally 0. The panel must not present that as a real 0% hit rate.
     */
    usage_estimated?: boolean;
  } | null;
}

/** Handler the panel calls to export the session it belongs to. */
export type SoloExportContextHandler = () => Promise<SessionExportResult> | void;

interface SoloContextFooterProps {
  cwd: string | null;
  tokenStats: SoloTokenStats | null;
  /** After first message, path is view-only */
  locked?: boolean;
  /** Apply a chosen working directory path */
  onSelectCwd?: (path: string) => void | Promise<void>;
  onViewReport?: () => void;
  /** Manual context compression (same as former L1 scissors) */
  onCompressContext?: () => void;
  compressing?: boolean;
  compressDisabled?: boolean;
  /**
   * Export this session's transcript as a Markdown download. Resolves with the
   * outcome so the panel can say why nothing was written (empty session, fetch
   * failed) instead of closing on a click that produced no file.
   */
  onExportContext?: SoloExportContextHandler;
  /** Controls after folder (e.g. Mode) */
  children?: React.ReactNode;
  /** Controls immediately before the token ring (e.g. Model / Effort) */
  trailing?: React.ReactNode;
}

function fmtTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(Math.round(n));
}

function shortPath(path: string, max = 48): string {
  const p = path.replace(/\\/g, '/');
  if (p.length <= max) return p;
  return `…${p.slice(-(max - 1))}`;
}

function samePath(a: string, b: string): boolean {
  return a.replace(/\\/g, '/').toLowerCase() === b.replace(/\\/g, '/').toLowerCase();
}

const SEGMENTS: Array<{
  key: keyof SoloTokenBreakdown;
  labelKey: string;
  fallback: string;
  color: string;
  bar: string;
}> = [
  { key: 'system', labelKey: 'contextViewer.kindPrompt', fallback: 'System prompt', color: '#94a3b8', bar: 'bg-slate-400' },
  { key: 'tool_defs', labelKey: 'contextViewer.kindToolDefs', fallback: 'Tool definitions', color: '#a78bfa', bar: 'bg-violet-400' },
  { key: 'thought', labelKey: 'contextViewer.kindThought', fallback: 'Thought', color: '#34d399', bar: 'bg-emerald-400' },
  { key: 'tool', labelKey: 'contextViewer.kindToolCall', fallback: 'Tool calls', color: '#fbbf24', bar: 'bg-amber-400' },
  { key: 'user', labelKey: 'contextViewer.kindUser', fallback: 'Conversation', color: '#fb923c', bar: 'bg-orange-400' },
  { key: 'response', labelKey: 'contextViewer.kindAssistant', fallback: 'Assistant', color: '#60a5fa', bar: 'bg-blue-400' },
  { key: 'overhead', labelKey: 'contextViewer.kindOverhead', fallback: 'Other', color: '#64748b', bar: 'bg-slate-500' },
];

/** One legend row of the panel: colour chip · label · right-aligned count. */
const TokenRow: React.FC<{ color: string; label: string; value: string }> = ({
  color,
  label,
  value,
}) => (
  <div className="flex items-center gap-2 text-[12px]">
    <span className="w-2 h-2 rounded-[3px] shrink-0" style={{ backgroundColor: color }} />
    <span className="flex-1 min-w-0 truncate text-textMuted">{label}</span>
    <span className="font-mono text-textMain/80 tabular-nums shrink-0">{value}</span>
  </div>
);

const TokenRing: React.FC<{ pct: number; size?: number }> = ({ pct, size = 14 }) => {
  const r = (size - 3) / 2;
  const c = 2 * Math.PI * r;
  const clamped = Math.max(0, Math.min(100, pct));
  const offset = c * (1 - clamped / 100);

  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="shrink-0 -rotate-90">
      <circle
        cx={size / 2}
        cy={size / 2}
        r={r}
        fill="none"
        stroke="currentColor"
        strokeWidth={2}
        className="text-black/15 dark:text-white/20"
      />
      <circle
        cx={size / 2}
        cy={size / 2}
        r={r}
        fill="none"
        stroke="currentColor"
        strokeWidth={2}
        strokeLinecap="round"
        strokeDasharray={c}
        strokeDashoffset={offset}
        className="text-black/45 dark:text-white/55"
      />
    </svg>
  );
};

export const SoloContextFooter: React.FC<SoloContextFooterProps> = ({
  cwd,
  tokenStats,
  locked = false,
  onSelectCwd,
  onViewReport,
  onCompressContext,
  compressing = false,
  compressDisabled = false,
  onExportContext,
  children,
  trailing,
}) => {
  const { t } = useTranslation();
  const [tokenOpen, setTokenOpen] = useState(false);
  const [cwdOpen, setCwdOpen] = useState(false);
  const [recents, setRecents] = useState<string[]>(() => loadCwdRecents());
  const [picking, setPicking] = useState(false);
  const [copied, setCopied] = useState(false);
  const copiedTimer = useRef<number | null>(null);
  const cwdRootRef = useRef<HTMLDivElement>(null);
  /** Export failure text; '' = idle (the row shows its normal hint instead). */
  const [exportNote, setExportNote] = useState('');
  const [exporting, setExporting] = useState(false);

  const runExport = async () => {
    if (!onExportContext || exporting) return;
    setExporting(true);
    setExportNote('');
    try {
      const res = await onExportContext();
      // An older caller may not return a result at all; that is fire-and-forget
      // (the browser download is its own feedback) and must not paint an error.
      if (!res || res.ok) return;
      setExportNote(
        res.reason === 'empty'
          ? t('aiChat.sessionSidebar.exportEmpty', { defaultValue: 'This session has no content yet' })
          : t('aiChat.sessionSidebar.exportFailed', { defaultValue: 'Export failed, please retry' }),
      );
    } finally {
      setExporting(false);
    }
  };

  const used = tokenStats?.used ?? 0;
  const max = tokenStats?.max ?? 0;
  const pct = max > 0 ? Math.round((used / max) * 100) : 0;

  const segments = useMemo(() => {
    const bd = tokenStats?.breakdown;
    if (!bd) return [];
    return SEGMENTS.map((s) => ({
      ...s,
      label: t(s.labelKey, { defaultValue: s.fallback }),
      val: Number(bd[s.key] ?? 0),
    })).filter((s) => s.val > 0);
  }, [tokenStats?.breakdown, t]);

  const barMax = max > 0 ? max : 1;

  // ── Prompt-cache split: input = 命中缓存 + 未命中缓存 ──────────────────
  // `cache_read_tokens` is a *subset* of the session input tokens on every
  // provider (OpenAI/Ark/Gemini `cached_tokens`, DeepSeek `prompt_cache_hit`,
  // Claude `cache_read_input_tokens`), which is what makes a hit rate
  // well-defined as read / (read + miss).  `cache_miss_tokens` is precomputed
  // server-side; deriving it here only keeps older payloads working.
  const cache = useMemo(() => {
    const s: NonNullable<SoloTokenStats['session']> | null = tokenStats?.session ?? null;
    if (!s) return null;
    const input = Number(s.input_tokens ?? s.total_input_tokens ?? 0) || 0;
    const output = Number(s.output_tokens ?? s.total_output_tokens ?? 0) || 0;
    const hit = Math.max(0, Number(s.cache_read_tokens ?? 0) || 0);
    const miss = typeof s.cache_miss_tokens === 'number'
      ? Math.max(0, s.cache_miss_tokens)
      : Math.max(0, input - hit);
    const billed = hit + miss;
    if (billed <= 0 && output <= 0) return null;
    return {
      hit,
      miss,
      /** The three rows sum to this, so they always reconcile on screen. */
      input: billed,
      output,
      hitPct: billed > 0 ? (hit / billed) * 100 : 0,
      hitShare: billed > 0 ? (hit / billed) * 100 : 0,
      /** Provider never reported usage → hit/miss split is not trustworthy. */
      estimated: s.usage_estimated === true,
    };
  }, [tokenStats?.session]);

  useEffect(() => {
    if (cwd) setRecents(pushCwdRecent(cwd));
  }, [cwd]);

  useEffect(() => {
    if (locked) setCwdOpen(false);
  }, [locked]);

  useEffect(() => {
    if (!cwdOpen || locked) return;
    const onDoc = (e: MouseEvent) => {
      if (!cwdRootRef.current?.contains(e.target as Node)) {
        setCwdOpen(false);
      }
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setCwdOpen(false);
    };
    document.addEventListener('mousedown', onDoc);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDoc);
      document.removeEventListener('keydown', onKey);
    };
  }, [cwdOpen, locked]);

  const applyPath = async (path: string) => {
    const trimmed = path.trim();
    if (!trimmed || !onSelectCwd) return;
    setRecents(pushCwdRecent(trimmed));
    setCwdOpen(false);
    await onSelectCwd(trimmed);
  };

  // 会话进行中（locked）路径为只读，点击复制绝对路径并短暂提示
  const copyCwd = async () => {
    if (!cwd) return;
    try {
      await navigator.clipboard.writeText(cwd);
    } catch {
      const ta = document.createElement('textarea');
      ta.value = cwd;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      try {
        document.execCommand('copy');
      } finally {
        document.body.removeChild(ta);
      }
    }
    setCopied(true);
    if (copiedTimer.current) window.clearTimeout(copiedTimer.current);
    copiedTimer.current = window.setTimeout(() => setCopied(false), 1500);
  };

  const handleOpenFolder = async () => {
    setPicking(true);
    try {
      // Close popover first so the native dialog is clearly in front
      setCwdOpen(false);
      const result = await pickFolder(cwd);
      if (result.cancelled) return;
      if (result.path) {
        await applyPath(result.path);
        return;
      }
      alert(
        result.error
          ? `无法打开本机目录选择器：${result.error}`
          : '未能获取所选文件夹的绝对路径，请确认 Launcher 正在本机运行。',
      );
    } finally {
      setPicking(false);
    }
  };

  const displayName = cwd ? folderLabel(cwd) : 'Select folder';
  const recentList = useMemo(() => {
    const list = [...recents];
    if (cwd && !list.some((p) => samePath(p, cwd))) list.unshift(cwd);
    return list;
  }, [recents, cwd]);

  const canPick = !locked && !!onSelectCwd;
  const canOpenTokenPanel = max > 0 || !!onViewReport || !!onCompressContext || !!onExportContext;

  return (
    <div className="relative mt-1.5 w-full">
      {tokenOpen && canOpenTokenPanel && (
        <div className="absolute bottom-[calc(100%+6px)] left-0 right-0 z-40 rounded-xl border border-border bg-panel shadow-[0_8px_30px_rgba(0,0,0,0.12)] overflow-hidden">
          <div className="flex items-center justify-between px-3.5 py-2.5 border-b border-border/70">
            <span className="text-[13px] font-semibold text-textMain">
              {t('aiChat.contextUsage', { defaultValue: 'Context Usage' })}
            </span>
            <button
              type="button"
              onClick={() => setTokenOpen(false)}
              className="p-1 rounded-md text-textMuted hover:text-textMain hover:bg-primary/10 border-0 bg-transparent cursor-pointer"
              title={t('common.close')}
            >
              <X size={14} />
            </button>
          </div>

          {max > 0 ? (
            <>
              <div className="px-3.5 pt-2.5 pb-1 flex items-baseline justify-between gap-3">
                <span className="text-[13px] font-medium text-textMain">{pct}% Full</span>
                <span className="text-[12px] font-mono text-textMuted">
                  ~{fmtTokens(used)} / {fmtTokens(max)} Tokens
                </span>
              </div>

              <div className="px-3.5 pb-2">
                <div className="flex h-1.5 rounded-full overflow-hidden bg-black/[0.06] dark:bg-white/[0.08]">
                  {segments.map((s) => (
                    <div
                      key={s.key}
                      className={`${s.bar} transition-all`}
                      style={{ width: `${(s.val / barMax) * 100}%` }}
                      title={`${s.label}: ${fmtTokens(s.val)}`}
                    />
                  ))}
                </div>
              </div>

              {/* Breakdown + session row + cache hit/miss split; scrolls once
                  the cache block is present. */}
              <div className="px-3.5 pb-3 space-y-1.5 max-h-[300px] overflow-y-auto">
                {segments.length === 0 ? (
                  <div className="text-[12px] text-textMuted py-2">No breakdown yet</div>
                ) : (
                  segments.map((s) => (
                    <div key={s.key} className="flex items-center gap-2 text-[12px]">
                      <span
                        className="w-2 h-2 rounded-[3px] shrink-0"
                        style={{ backgroundColor: s.color }}
                      />
                      <span className="flex-1 min-w-0 truncate text-textMuted">{s.label}</span>
                      <span className="font-mono text-textMain/80 tabular-nums shrink-0">{fmtTokens(s.val)}</span>
                    </div>
                  ))
                )}
                {tokenStats?.session && (
                  <div className="flex items-center gap-2 text-[12px]">
                    <span className="w-2 h-2 rounded-[3px] shrink-0 bg-slate-500" />
                    <span className="flex-1 min-w-0 truncate text-textMuted">
                      {t('contextViewer.sessionUsage', { defaultValue: 'This session' })}
                    </span>
                    <span className="font-mono text-textMain/80 tabular-nums shrink-0">
                      {fmtTokens(tokenStats.session.total_tokens ?? 0)}
                    </span>
                    <span className="font-mono text-textMuted tabular-nums shrink-0 text-[11px]">
                      · {tokenStats.session.total_requests ?? 0} req
                    </span>
                  </div>
                )}
                {cache && (
                  <div className="pt-2 mt-0.5 border-t border-border/50 space-y-1.5">
                    <div className="flex items-baseline justify-between gap-2">
                      <span className="text-[12px] text-textMuted">
                        {t('contextViewer.cacheHitRate', { defaultValue: 'Cache hit rate' })}
                      </span>
                      <span className="font-mono text-[12px] font-semibold text-textMain tabular-nums">
                        {cache.estimated
                          ? t('contextViewer.cacheHitUnknown', { defaultValue: '—' })
                          : `${cache.hitPct.toFixed(1)}%`}
                      </span>
                    </div>
                    {cache.estimated ? (
                      // No provider usage: the split would be a fabricated
                      // 0 / everything-uncached pair, so it is not rendered.
                      <>
                        <p className="text-[11px] text-textMuted leading-snug">
                          {t('contextViewer.cacheHitUnavailable', {
                            defaultValue:
                              'This model returned no usage data, so the cache hit rate cannot be measured.',
                          })}
                        </p>
                        <TokenRow
                          color="#94a3b8"
                          label={t('contextViewer.inputTokens', { defaultValue: 'Input' })}
                          value={`~${fmtTokens(cache.input)}`}
                        />
                        <TokenRow
                          color="#60a5fa"
                          label={t('contextViewer.outputTokens', { defaultValue: 'Output' })}
                          value={`~${fmtTokens(cache.output)}`}
                        />
                      </>
                    ) : (
                      <>
                        <div className="flex h-1.5 rounded-full overflow-hidden bg-black/[0.06] dark:bg-white/[0.08]">
                          <div
                            className="bg-emerald-500 transition-all"
                            style={{ width: `${cache.hitShare}%` }}
                            title={`${t('contextViewer.cacheHit', { defaultValue: 'Input · cached' })}: ${fmtTokens(cache.hit)}`}
                          />
                          <div
                            className="bg-amber-500 transition-all"
                            style={{ width: `${100 - cache.hitShare}%` }}
                            title={`${t('contextViewer.cacheMiss', { defaultValue: 'Input · uncached' })}: ${fmtTokens(cache.miss)}`}
                          />
                        </div>
                        <TokenRow
                          color="#10b981"
                          label={t('contextViewer.cacheHit', { defaultValue: 'Input · cached' })}
                          value={fmtTokens(cache.hit)}
                        />
                        <TokenRow
                          color="#f59e0b"
                          label={t('contextViewer.cacheMiss', { defaultValue: 'Input · uncached' })}
                          value={fmtTokens(cache.miss)}
                        />
                        <TokenRow
                          color="#94a3b8"
                          label={t('contextViewer.inputTokens', { defaultValue: 'Input' })}
                          value={fmtTokens(cache.input)}
                        />
                        <TokenRow
                          color="#60a5fa"
                          label={t('contextViewer.outputTokens', { defaultValue: 'Output' })}
                          value={fmtTokens(cache.output)}
                        />
                      </>
                    )}
                  </div>
                )}
              </div>
            </>
          ) : (
            <div className="px-3.5 py-3 text-[12px] text-textMuted">
              {t('aiChat.tokenStatsPending', { defaultValue: 'Waiting for token stats…' })}
            </div>
          )}

          {/* Outside token breakdown: context details + compress + export */}
          {(onViewReport || onCompressContext || onExportContext) && (
            <div className="px-3.5 py-2.5 border-t border-border/70 bg-black/[0.015] dark:bg-white/[0.03] space-y-2">
              {onViewReport ? (
                <button
                  type="button"
                  onClick={() => {
                    setTokenOpen(false);
                    onViewReport();
                  }}
                  className="w-full flex items-center justify-center gap-2 px-3 py-2 rounded-lg text-[12px] font-medium
                    border border-border/60 bg-panel text-textMain
                    hover:bg-primary/10
                    transition-colors cursor-pointer"
                  title={t('aiChat.contextDetails')}
                >
                  <List size={14} className="text-textMuted" />
                  <span>{t('aiChat.contextDetails')}</span>
                </button>
              ) : null}
              {onCompressContext ? (
                <>
                  <button
                    type="button"
                    onClick={() => {
                      if (compressDisabled || compressing) return;
                      onCompressContext();
                    }}
                    disabled={compressDisabled || compressing}
                    className="w-full flex items-center justify-center gap-2 px-3 py-2 rounded-lg text-[12px] font-medium
                      border border-border/60 bg-panel text-textMain
                      hover:bg-primary/10
                      disabled:opacity-50 disabled:cursor-not-allowed
                      transition-colors cursor-pointer"
                    title={
                      compressing
                        ? '正在压缩上下文…'
                        : '总结并压缩当前会话上下文'
                    }
                  >
                    <Scissors
                      size={14}
                      className={compressing ? 'text-primary animate-pulse' : 'text-textMuted'}
                    />
                    <span>{compressing ? '正在压缩…' : '压缩上下文'}</span>
                  </button>
                  <p className="text-[10px] leading-relaxed text-textMuted/75 text-center">
                    将较早对话归档摘要，释放上下文空间
                  </p>
                </>
              ) : null}
              {/* Sits under compress: same session, same "act on the context"
                  family. Moved here from the sidebar, where a hover-only row
                  icon was the only affordance for the session being read. */}
              {onExportContext ? (
                <>
                  <button
                    type="button"
                    onClick={() => void runExport()}
                    disabled={exporting}
                    className="w-full flex items-center justify-center gap-2 px-3 py-2 rounded-lg text-[12px] font-medium
                      border border-border/60 bg-panel text-textMain
                      hover:bg-primary/10
                      disabled:opacity-50 disabled:cursor-not-allowed
                      transition-colors cursor-pointer"
                    title={t('aiChat.exportContextHint', { defaultValue: 'Export this session as a Markdown file' })}
                  >
                    <FileDown
                      size={14}
                      className={exporting ? 'text-primary animate-pulse' : 'text-textMuted'}
                    />
                    <span>{t('aiChat.exportContext', { defaultValue: 'Export context' })}</span>
                  </button>
                  <p
                    className={`text-[10px] leading-relaxed text-center ${
                      exportNote ? 'text-rose-500' : 'text-textMuted/75'
                    }`}
                  >
                    {exportNote || t('aiChat.exportContextHint', { defaultValue: 'Export this session as a Markdown file' })}
                  </p>
                </>
              ) : null}
            </div>
          )}
        </div>
      )}

      {/* One row: Folder | Mode | … | Model/Effort | Token ring */}
      <div className="flex items-center gap-1.5 min-h-[28px] px-0.5">
        <div ref={cwdRootRef} className="relative min-w-0 max-w-[min(28%,180px)] shrink">
          <button
            type="button"
            onClick={() => {
              if (canPick) {
                // 新会话（未锁定）：点击仍打开目录选择弹层
                setTokenOpen(false);
                setCwdOpen((v) => !v);
                return;
              }
              void copyCwd();
            }}
            className={`flex items-center gap-1 min-w-0 text-left border-0 bg-transparent p-0 group ${
              canPick || cwd ? 'cursor-pointer' : 'cursor-default'
            }`}
            title={cwd || 'Select project folder'}
          >
            <FolderOpen size={12} className="text-textMuted/70 shrink-0 group-hover:text-textMuted" />
            <span className="text-[11px] text-textMuted/70 truncate group-hover:text-textMuted">
              {cwd ? displayName : 'No project directory'}
            </span>
            {canPick && (
              <ChevronDown
                size={11}
                className={`text-textMuted/45 shrink-0 transition-transform ${cwdOpen ? 'rotate-180' : ''}`}
              />
            )}
          </button>

          {/* 复制成功提示：短暂显示后自动消失。
              w-max + whitespace-nowrap 是必需的：父级被 max-w 限宽（chip 只有几十 px），
              而这个浮层没写宽度，absolute 的 shrink-to-fit 会取 min-content ——
              中文于是被压成一字一行，变成竖排。 */}
          {copied && (
            <div
              className="absolute bottom-[calc(100%+6px)] left-0 z-50 flex w-max items-center gap-1 whitespace-nowrap rounded-md px-2 py-1 text-[11px] font-medium
                bg-black/80 text-white dark:bg-white/90 dark:text-black shadow-lg pointer-events-none"
            >
              <Check size={11} />
              <span>{t('common.copied', { defaultValue: '已复制' })}</span>
            </div>
          )}

          {cwdOpen && canPick && (
            <div className={`absolute bottom-[calc(100%+8px)] left-0 z-50 w-[min(420px,calc(100vw-2rem))] rounded-xl border border-border ${POPOVER_SURFACE_CLASS} overflow-hidden`}>
              <div className="px-3 py-2.5 border-b border-border/60">
                <div className="text-[12px] text-textMain font-medium truncate">{displayName}</div>
                {cwd ? (
                  <div className="text-[11px] text-textMuted/55 truncate mt-0.5" title={cwd}>
                    {shortPath(cwd, 56)}
                  </div>
                ) : null}
              </div>

              <div className="px-3 pt-2 pb-1 text-[10px] font-medium uppercase tracking-wide text-textMuted/50">
                Recents
              </div>
              <div className="max-h-[220px] overflow-y-auto pb-1">
                {recentList.length === 0 ? (
                  <div className="px-3 py-3 text-[12px] text-textMuted/60">No recent folders</div>
                ) : (
                  recentList.map((path) => {
                    const active = cwd ? samePath(path, cwd) : false;
                    return (
                      <button
                        key={path}
                        type="button"
                        onClick={() => void applyPath(path)}
                        className="w-full flex items-center gap-2.5 px-3 py-2 text-left border-0 bg-transparent cursor-pointer hover:bg-primary/10 transition-colors"
                        title={path}
                      >
                        <Folder size={14} className="text-textMuted/70 shrink-0" />
                        <span className="flex-1 min-w-0 text-[12px] text-textMain truncate">
                          {folderLabel(path)}
                        </span>
                        {active ? <Check size={14} className="text-textMuted shrink-0" /> : null}
                      </button>
                    );
                  })
                )}
              </div>

              <div className="border-t border-border/60">
                <button
                  type="button"
                  onClick={() => void handleOpenFolder()}
                  disabled={picking || !onSelectCwd}
                  className="w-full flex items-center gap-2.5 px-3 py-2.5 text-left border-0 bg-transparent cursor-pointer hover:bg-primary/10 transition-colors disabled:opacity-50"
                >
                  <FolderOpen size={14} className="text-textMuted/70 shrink-0" />
                  <span className="text-[12px] text-textMain font-medium">
                    {picking ? 'Opening…' : 'Open Folder'}
                  </span>
                </button>
              </div>
            </div>
          )}
        </div>

        {children ? (
          <div className="flex items-center gap-1.5 min-w-0 shrink-0">{children}</div>
        ) : null}

        <div className="flex-1 min-w-0" />

        {trailing ? (
          <div className="flex items-center gap-1.5 min-w-0 shrink-0">{trailing}</div>
        ) : null}

        <button
          type="button"
          onClick={() => {
            setCwdOpen(false);
            setTokenOpen((v) => !v);
          }}
          disabled={!canOpenTokenPanel}
          className="flex items-center gap-1.5 shrink-0 border-0 bg-transparent p-0 cursor-pointer disabled:opacity-40 disabled:cursor-default hover:opacity-90"
          title={canOpenTokenPanel ? t('aiChat.contextUsage', { defaultValue: 'Context usage' }) : 'Waiting for token stats'}
        >
          <TokenRing pct={pct} />
          <span className="text-[11px] font-medium text-textMuted tabular-nums">
            {max ? `${pct}%` : '—'}
          </span>
          {max > 0 ? (
            <span className="text-[10px] text-textMuted/65 tabular-nums">
              {fmtTokens(used)}
            </span>
          ) : null}
        </button>
      </div>
    </div>
  );
};
