/**
 * ContextViewer — 上下文详情面板
 *
 * 右侧抽屉面板，展示当前 Agent 会话的：
 *   1. 会话级统计（token、模型、时间等）
 *   2. 上下文拆分进度条
 *   3. 原始消息/事件列表（可展开查看完整内容）
 *
 * 通过接收 AIChatPage 的 timeline（实时 React state）和 tokenStats 作为 props
 * 实现随对话实时更新，无需额外轮询。
 */
import React, { useState, useEffect, useCallback, useMemo } from 'react';
import {
  X, ChevronDown, ChevronUp, Copy, Check,
  User, Bot, Wrench, Brain, Info, List, Layers, FileText,
  Moon, Bell, Zap, FolderOpen,
} from 'lucide-react';
import { agentSessionAPI } from '../../services/api';
import { useTranslation } from 'react-i18next';

// ---- 传入的扁平化上下文条目 ----

export interface ContextEntry {
  id: string;
  kind: 'user' | 'assistant' | 'thought' | 'tool_call' | 'tool_result' | 'info' | 'plan' | 'prompt' | 'sleep' | 'wake' | 'state_change';
  content: string | object;
  timestamp?: string;       // ISO 字符串
  elapsed_ms?: number;      // 仅 assistant：整轮耗时（ms）
  result?: any;             // 仅 tool_result：工具返回值
  resultStatus?: 'success' | 'error';
  /** 仅 prompt：动态上下文前缀（注入 user message 的部分），若有则一并展示 */
  dynamicPrefix?: string;
  /** 仅 prompt：本次 prompt 是否有变化（true=变化，false=首次） */
  promptChanged?: boolean;
  /** 仅 prompt：unified diff 行列表（后端生成，is_changed=true 时非空） */
  diff?: string[];
}

// ---- Props ----

interface TokenStatsShape {
  used: number;
  max: number;
  breakdown?: { system?: number; user: number; thought: number; tool: number; tool_defs?: number; response: number; overhead?: number };
  model?: string;
}

export interface ContextViewerProps {
  agentId: string;
  agentName: string;
  sessionId: string | null;
  // provider: vendor / 供应商 名称 (e.g. "DeepSeek", "OpenAI")
  provider?: string | null;
  // api_protocol: API 协议类型 (e.g. "openai_compat", "anthropic")
  apiProtocol?: string | null;
  model?: string | null;
  /** 工作目录（来自 agent config filesystem.workspace_dirs[0]） */
  cwd?: string | null;
  tokenStats: TokenStatsShape | null;
  /** 实时扁平化上下文条目，由 AIChatPage 通过 useMemo 从 timeline 派生 */
  entries: ContextEntry[];
  onClose: () => void;
}

// ---- 工具函数 ----

/** 数值格式化（保留 undefined/null → '—'）*/
function fmtNum(n: number | undefined | null): string {
  if (n == null || isNaN(n as number)) return '—';
  return (n as number).toLocaleString();
}

function fmtDate(iso: string | undefined | null, locale?: string): string {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleString(locale || 'en', {
      year: 'numeric', month: 'long', day: 'numeric',
      hour: '2-digit', minute: '2-digit',
    });
  } catch { return iso; }
}

function getEntrySummary(entry: ContextEntry): string {
  const raw = entry.content as any;
  if (entry.kind === 'info' && raw && typeof raw === 'object') {
    const text = typeof raw.text === 'string' ? raw.text : '';
    const count = typeof raw.count === 'number' ? raw.count : undefined;
    if (text && count !== undefined) return `${text} (${count})`;
    if (text) return text;
    if (Array.isArray(raw.messages)) return `messages: ${raw.messages.length}`;
    return 'info object';
  }

  if (typeof entry.content === 'string') {
    const s = entry.content.replace(/\s+/g, ' ').trim();
    return s || '—';
  }

  if (raw && typeof raw === 'object') {
    if (typeof raw.name === 'string') return raw.name;
    if (typeof raw.text === 'string') return raw.text;
    return 'object';
  }

  return '—';
}

// ---- 条目类型配置 ----

const KIND_CFG: Record<string, {
  label: string; color: string; bg: string;
  barColor: string; legendLabel: string;
  Icon: React.FC<{ size?: number; className?: string }>;
}> = {
  user:        { label: 'contextViewer.kindUser',       color: 'text-blue-400',    bg: 'bg-blue-400/15',    barColor: 'bg-blue-500',    legendLabel: 'contextViewer.kindUser',       Icon: User },
  assistant:   { label: 'contextViewer.kindAssistant',   color: 'text-emerald-400', bg: 'bg-emerald-400/15', barColor: 'bg-emerald-500', legendLabel: 'contextViewer.kindAssistant',   Icon: Bot },
  thought:     { label: 'contextViewer.kindThought',    color: 'text-violet-400',  bg: 'bg-violet-400/15',  barColor: 'bg-violet-500',  legendLabel: 'contextViewer.kindThought',    Icon: Brain },
  tool_call:   { label: 'contextViewer.kindToolCall',   color: 'text-amber-400',   bg: 'bg-amber-400/15',   barColor: 'bg-amber-500',   legendLabel: 'contextViewer.kindToolCall',   Icon: Wrench },
  tool_result: { label: 'contextViewer.kindToolResult', color: 'text-orange-400',  bg: 'bg-orange-400/15',  barColor: 'bg-orange-500',  legendLabel: 'contextViewer.kindToolResult', Icon: Wrench },
  info:        { label: 'contextViewer.kindInfo',       color: 'text-sky-400',     bg: 'bg-sky-400/15',     barColor: 'bg-sky-500',     legendLabel: 'contextViewer.kindInfo',       Icon: Info },
  plan:        { label: 'contextViewer.kindPlan',       color: 'text-pink-400',    bg: 'bg-pink-400/15',    barColor: 'bg-pink-500',    legendLabel: 'contextViewer.kindPlan',       Icon: Layers },
  prompt:      { label: 'contextViewer.kindPrompt',     color: 'text-teal-400',    bg: 'bg-teal-400/15',    barColor: 'bg-teal-500',    legendLabel: 'contextViewer.kindPrompt',     Icon: FileText },
  sleep:       { label: 'contextViewer.kindSleep',      color: 'text-indigo-400',  bg: 'bg-indigo-400/15',  barColor: 'bg-indigo-500',  legendLabel: 'contextViewer.kindSleep',      Icon: Moon },
  wake:        { label: 'contextViewer.kindWake',       color: 'text-emerald-300', bg: 'bg-emerald-300/15', barColor: 'bg-emerald-400', legendLabel: 'contextViewer.kindWake',       Icon: Bell },
  state_change:{ label: 'contextViewer.kindStateChange', color: 'text-amber-400',  bg: 'bg-amber-400/15',   barColor: 'bg-amber-500',   legendLabel: 'contextViewer.kindStateChange', Icon: Zap },
};

// ---- 统计网格单元 ----

const StatCell: React.FC<{
  label: string;
  value: React.ReactNode;
  highlight?: boolean;
  mono?: boolean;
  span2?: boolean;
}> = ({ label, value, highlight, mono, span2 }) => (
  <div className={span2 ? 'col-span-2' : ''}>
    <div className="text-[10px] text-textMuted mb-0.5">{label}</div>
    <div className={`text-sm font-semibold leading-snug break-all ${highlight ? 'text-primary' : 'text-textMain'} ${mono ? 'font-mono text-xs' : ''}`}>
      {value}
    </div>
  </div>
);

// ---- 主组件 ----

export const ContextViewer: React.FC<ContextViewerProps> = ({
  agentId, agentName, sessionId, provider, apiProtocol, model, cwd,
  tokenStats, entries, onClose,
}) => {
  const { t, i18n } = useTranslation();
  const locale = i18n.language === 'zh' ? 'zh-CN' : 'en';
  const [sessionMeta, setSessionMeta] = useState<{ created_at?: string; last_updated?: string }>({});
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set());
  const [copiedId, setCopiedId] = useState<string | null>(null);

  // 加载会话元数据（created_at / last_updated）
  useEffect(() => {
    if (!agentId || !sessionId) return;
    agentSessionAPI.getSessionHistory(agentId, sessionId)
      .then(res => {
        if (res.session) {
          setSessionMeta({
            created_at: res.session.created_at,
            last_updated: res.session.last_updated,
          });
        }
      })
      .catch(() => {});
  }, [agentId, sessionId]);

  const toggleExpanded = useCallback((id: string) => {
    setExpandedIds(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }, []);

  const copyContent = useCallback((id: string, content: any) => {
    const text = typeof content === 'string' ? content : JSON.stringify(content, null, 2);
    navigator.clipboard.writeText(text).catch(() => {});
    setCopiedId(id);
    setTimeout(() => setCopiedId(null), 2000);
  }, []);

  // ---- 统计计算 ----
  const userCount      = useMemo(() => entries.filter(e => e.kind === 'user').length, [entries]);
  const assistantCount = useMemo(() => entries.filter(e => e.kind === 'assistant').length, [entries]);
  const totalMsgCount  = userCount + assistantCount;

  const usageRate = tokenStats && tokenStats.max > 0
    ? Math.round((tokenStats.used / tokenStats.max) * 100) : 0;

  // 最后活动时间：取 entries 最后一条的时间戳 vs sessionMeta.last_updated，取较新者
  const lastActivity = useMemo(() => {
    const lastEntry = entries.length > 0 ? entries[entries.length - 1] : null;
    const lastEntryTime = lastEntry?.timestamp;
    if (!lastEntryTime && !sessionMeta.last_updated) return undefined;
    if (!lastEntryTime) return sessionMeta.last_updated;
    if (!sessionMeta.last_updated) return lastEntryTime;
    return new Date(lastEntryTime) > new Date(sessionMeta.last_updated)
      ? lastEntryTime : sessionMeta.last_updated;
  }, [entries, sessionMeta.last_updated]);

  // ---- 上下文拆分条 ----
  const breakdown = tokenStats?.breakdown;
  const bdSystem   = breakdown?.system   ?? 0;
  const bdUser     = breakdown?.user     ?? 0;
  const bdResponse = breakdown?.response ?? 0;
  const bdThought  = breakdown?.thought  ?? 0;
  const bdTool     = breakdown?.tool     ?? 0;
  const bdToolDefs = breakdown?.tool_defs ?? 0;
  const bdOverhead = breakdown?.overhead ?? 0;
  const bdTotal    = bdSystem + bdUser + bdResponse + bdThought + bdTool + bdToolDefs + bdOverhead;

  const bdSegments: Array<{ key: string; val: number; color: string; label: string }> = bdTotal > 0
    ? [
        { key: 'system',    val: bdSystem,   color: 'bg-teal-500',    label: 'contextViewer.kindPrompt' },
        { key: 'user',      val: bdUser,     color: 'bg-blue-500',    label: 'contextViewer.kindUser' },
        { key: 'response',  val: bdResponse, color: 'bg-emerald-500', label: 'contextViewer.kindAssistant' },
        { key: 'thought',   val: bdThought,  color: 'bg-violet-500',  label: 'contextViewer.kindThought' },
        { key: 'tool',      val: bdTool,     color: 'bg-amber-500',   label: 'contextViewer.kindToolCall' },
        { key: 'tool_defs', val: bdToolDefs, color: 'bg-orange-500',  label: 'contextViewer.kindToolDefs' },
        { key: 'overhead',  val: bdOverhead, color: 'bg-slate-500',   label: 'contextViewer.kindOverhead' },
      ].filter(s => s.key !== 'overhead' || s.val > 0)
    : [];

  // ---- 子智能体检测 ----
  const delegateEvents = useMemo(() =>
    entries.filter(e =>
      e.kind === 'tool_call' && (
        (typeof e.content === 'object' && typeof (e.content as any)?.name === 'string' &&
          (e.content as any).name.toLowerCase().includes('delegate')) ||
        (typeof e.content === 'string' && e.content.toLowerCase().includes('delegate'))
      )
    ), [entries]);

  // ---- 渲染 ----
  return (
    <div className="absolute inset-0 z-40 flex justify-end">
      {/* 半透明遮罩 */}
      <div className="absolute inset-0 bg-black/35" onClick={onClose} />

      {/* 面板主体 */}
      <div className="relative w-full max-w-[500px] h-full bg-panel border-l border-border flex flex-col shadow-2xl">

        {/* 顶部栏 */}
        <div className="flex items-start justify-between px-4 py-3 border-b border-border flex-shrink-0 bg-panel">
          <div className="min-w-0">
            <div className="flex items-center gap-2 min-w-0">
              <List size={15} className="text-primary flex-shrink-0" />
              <span className="font-semibold text-sm text-textMain">{t('contextViewer.title')}</span>
              <span className="text-xs text-textMuted truncate ml-1">{agentName}</span>
            </div>
            {cwd && (
              <div className="mt-1.5 flex items-center gap-1.5 text-[10px] text-textMuted">
                <FolderOpen size={11} className="text-primary/80 flex-shrink-0" />
                <span className="font-semibold">{t('contextViewer.cwd')}</span>
                <span className="font-mono truncate" title={cwd}>{cwd}</span>
              </div>
            )}
          </div>
          <button
            onClick={onClose}
            className="p-1.5 hover:bg-primary/10 rounded transition-colors flex-shrink-0"
            title={t('common.close')}
          >
            <X size={15} className="text-textMuted" />
          </button>
        </div>

        {/* 可滚动内容区 */}
        <div className="flex-1 overflow-y-auto">
          <div className="p-4 space-y-3">

            {/* ── 会话统计网格 ── */}
            <div className="bg-bgLight rounded-xl p-4">
              <div className="grid grid-cols-2 gap-x-8 gap-y-4">
                <StatCell label={t('contextViewer.session')} value={
                  <span className="font-mono text-xs" title={sessionId || '—'}>
                    {sessionId ? sessionId.slice(-20) : '—'}
                  </span>
                } />
                <StatCell label={t('contextViewer.msgCount')} value={fmtNum(totalMsgCount)} />
                <StatCell label={t('contextViewer.vendor')} value={provider || '—'} />
                <StatCell label={t('contextViewer.protocol')} value={apiProtocol || '—'} />
                <StatCell label={t('contextViewer.model')} value={
                  <span className="truncate block" title={model || ''}>{model || '—'}</span>
                } />
                <StatCell label={t('contextViewer.ctxLimit')} value={fmtNum(tokenStats?.max)} />
                <StatCell label={t('contextViewer.ctxUsed')} value={fmtNum(tokenStats?.used)} />
                <StatCell
                  label={t('contextViewer.usageRate')}
                  value={`${usageRate}%`}
                  highlight
                />
                <StatCell label={t('contextViewer.userMsgs')} value={fmtNum(userCount)} />
                <StatCell label={t('contextViewer.assistantMsgs')} value={fmtNum(assistantCount)} />
                <StatCell label={t('contextViewer.createdAt')} value={<span className="text-xs">{fmtDate(sessionMeta.created_at, locale)}</span>} />
                <StatCell label={t('contextViewer.lastActivity')} value={<span className="text-xs">{fmtDate(lastActivity, locale)}</span>} />
              </div>
            </div>

            {/* ── 上下文拆分 ── */}
            {bdSegments.length > 0 ? (
              <div className="bg-bgLight rounded-xl p-4">
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs font-semibold text-textMuted">{t('contextViewer.ctxBreakdown')}</span>
                  <span className="text-[11px] text-textMuted font-mono">
                    {fmtNum(tokenStats?.used)}{tokenStats?.max ? ` / ${fmtNum(tokenStats.max)}` : ''} token
                  </span>
                </div>
                {/* 进度条 */}
                <div className="flex h-2.5 rounded-full overflow-hidden gap-[1px] bg-bgPage">
                  {bdSegments.map(s => (
                    <div
                      key={s.key}
                      className={`${s.color} transition-all duration-500`}
                      style={{ width: `${(s.val / bdTotal) * 100}%` }}
                    />
                  ))}
                </div>
                {/* 各类别 token 数 + 占比 */}
                <div className="mt-3 space-y-1.5">
                  {bdSegments.map(s => (
                    <div key={s.key} className="flex items-center gap-2">
                      <span className={`w-2 h-2 rounded-full flex-shrink-0 ${s.color}`} />
                      <span className="text-[11px] text-textMuted w-14 flex-shrink-0">{t(s.label)}</span>
                      <div className="flex-1 h-1 bg-bgPage rounded-full overflow-hidden">
                        <div
                          className={`h-full ${s.color} transition-all duration-500`}
                          style={{ width: `${(s.val / bdTotal) * 100}%` }}
                        />
                      </div>
                      <span className="text-[11px] font-mono text-textMuted w-20 text-right flex-shrink-0">
                        {fmtNum(s.val)} <span className="text-textMuted/50">({Math.round((s.val / bdTotal) * 100)}%)</span>
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            ) : tokenStats && (
              <div className="bg-bgLight rounded-xl p-4 text-center text-[11px] text-textMuted">
                {t('contextViewer.ctxLoading')}
              </div>
            )}

            {/* ── 原始消息列表（固定窗口，内部滚动） ── */}
            <div className="bg-bgLight rounded-xl overflow-hidden">
              <div className="px-4 py-2.5 border-b border-border flex items-center justify-between">
                <span className="text-xs font-semibold text-textMuted">
                  {t('contextViewer.rawMessages')}
                </span>
                <span className="text-[11px] text-textMuted">{entries.length} {t('contextViewer.entries')}</span>
              </div>

              {entries.length === 0 ? (
                <div className="px-4 py-8 text-center text-xs text-textMuted">{t('contextViewer.noMessages')}</div>
              ) : (
                <div className="max-h-[52vh] overflow-y-auto divide-y divide-border/50">
                  {entries.map((entry, idx) => {
                    const cfg = KIND_CFG[entry.kind] || KIND_CFG.info;
                    const Icon = cfg.Icon;
                    const isExpanded = expandedIds.has(entry.id);
                    // 生成伪 ID（会话尾段 + 序号）
                    const pseudoId = `${sessionId ? sessionId.slice(-8) : 'sess'}_${String(idx + 1).padStart(3, '0')}`;

                    return (
                      <div key={entry.id}>
                        {/* 折叠行 */}
                        <button
                          className="w-full flex items-center gap-2 px-3 py-2 hover:bg-primary/5 transition-colors text-left"
                          onClick={() => toggleExpanded(entry.id)}
                        >
                          <div className={`w-5 h-5 rounded flex items-center justify-center flex-shrink-0 ${cfg.bg}`}>
                            <Icon size={11} className={cfg.color} />
                          </div>
                          <span className={`text-[11px] font-semibold flex-shrink-0 w-[52px] ${cfg.color}`}>
                            {t(cfg.label)}
                          </span>
                          <span className="text-[11px] text-textMuted font-mono w-[108px] flex-shrink-0 truncate" title={pseudoId}>
                            {pseudoId}
                          </span>
                          <span className="text-[11px] text-textMuted flex-1 truncate" title={getEntrySummary(entry)}>
                            {getEntrySummary(entry)}
                          </span>
                          <span className="text-[11px] text-textMuted flex-shrink-0 whitespace-nowrap">
                            {entry.timestamp
                              ? new Date(entry.timestamp).toLocaleString(locale, {
                                  month: 'numeric', day: 'numeric',
                                  hour: '2-digit', minute: '2-digit',
                                })
                              : '—'}
                          </span>
                          <span className="text-textMuted flex-shrink-0 ml-1">
                            {isExpanded ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
                          </span>
                        </button>

                        {/* 展开内容 */}
                        {isExpanded && (
                          <div className="bg-bgPage border-t border-border/40 px-3 py-3">
                            {/* 元数据区 */}
                            <div className="text-[10px] text-textMuted font-mono space-y-0.5 mb-2.5 leading-relaxed">
                              <div><span className="text-textMuted/60">id:</span> {entry.id}</div>
                              <div><span className="text-textMuted/60">session:</span> {sessionId || '—'}</div>
                              <div><span className="text-textMuted/60">role:</span> {entry.kind}</div>
                              {entry.timestamp && (
                                <div><span className="text-textMuted/60">time:</span> {entry.timestamp}</div>
                              )}
                              {entry.elapsed_ms != null && (
                                <div>
                                  <span className="text-textMuted/60">elapsed:</span>{' '}
                                  <span className="text-emerald-400">{(entry.elapsed_ms / 1000).toFixed(2)}s</span>
                                </div>
                              )}
                              {(entry.kind === 'assistant' || entry.kind === 'user') && (
                                <div><span className="text-textMuted/60">cwd:</span> {cwd || '—'}</div>
                              )}
                              {entry.kind === 'prompt' && (
                                <div>
                                  <span className="text-textMuted/60">changed:</span>{' '}
                                  <span className={entry.promptChanged ? 'text-amber-400' : 'text-sky-400'}>
                                    {entry.promptChanged ? t('contextViewer.changed') : t('contextViewer.firstLoad')}
                                  </span>
                                </div>
                              )}
                              {entry.result != null && (
                                <div>
                                  <span className="text-textMuted/60">status:</span>{' '}
                                  <span className={entry.resultStatus === 'error' ? 'text-red-400' : 'text-emerald-400'}>
                                    {entry.resultStatus || 'success'}
                                  </span>
                                </div>
                              )}
                            </div>

                            {/* 系统提示词主体 */}
                            {entry.kind === 'prompt' ? (
                              <>
                                {/* 1. 动态上下文前缀 */}
                                {entry.dynamicPrefix && (
                                  <>
                                    <div className="text-[10px] font-semibold text-amber-400 mb-1">
                                      {t('contextViewer.dynamicPrefix')}
                                    </div>
                                    <ContentBlock
                                      id={`${entry.id}-dyn`}
                                      content={entry.dynamicPrefix}
                                      copiedId={copiedId}
                                      onCopy={copyContent}
                                      maxH="max-h-48"
                                    />
                                  </>
                                )}
                                {/* 2. unified diff */}
                                {entry.diff && entry.diff.length > 0 && (
                                  <>
                                    <div className="text-[10px] font-semibold text-purple-400 mt-3 mb-1">
                                      {t('contextViewer.diffTitle')}
                                    </div>
                                    <div className="font-mono text-[10px] leading-[1.45] bg-bgDark rounded p-2 overflow-x-auto max-h-72 overflow-y-auto">
                                      {entry.diff.map((line, i) => {
                                        let cls = 'text-textMuted/60';
                                        if (line.startsWith('+') && !line.startsWith('+++')) cls = 'text-emerald-400';
                                        else if (line.startsWith('-') && !line.startsWith('---')) cls = 'text-red-400';
                                        else if (line.startsWith('@@')) cls = 'text-blue-400';
                                        else if (line.startsWith('---') || line.startsWith('+++')) cls = 'text-textMuted/40';
                                        return (
                                          <div key={i} className={cls} style={{ whiteSpace: 'pre' }}>
                                            {line}
                                          </div>
                                        );
                                      })}
                                    </div>
                                  </>
                                )}
                                {/* 3. 完整系统提示词 */}
                                <div className="text-[10px] font-semibold text-teal-400 mt-3 mb-1">{t('contextViewer.fullPrompt')}</div>
                                <ContentBlock
                                  id={entry.id}
                                  content={entry.content}
                                  copiedId={copiedId}
                                  onCopy={copyContent}
                                  maxH="max-h-96"
                                />
                              </>
                            ) : (
                              /* 内容区（非 prompt 类型） */
                              <ContentBlock
                                id={entry.id}
                                content={entry.content}
                                copiedId={copiedId}
                                onCopy={copyContent}
                              />
                            )}

                            {/* 工具结果区 */}
                            {entry.result != null && (
                              <>
                                <div className={`text-[10px] font-semibold mt-2 mb-1 ${
                                  entry.resultStatus === 'error' ? 'text-red-400' : 'text-emerald-400'
                                }`}>
                                  {entry.resultStatus === 'error' ? t('contextViewer.toolError') : t('contextViewer.toolResult')}
                                </div>
                                <ContentBlock
                                  id={`${entry.id}-result`}
                                  content={entry.result}
                                  copiedId={copiedId}
                                  onCopy={copyContent}
                                  maxH="max-h-36"
                                />
                              </>
                            )}
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}
            </div>

            {/* ── 子智能体委托 ── */}
            {delegateEvents.length > 0 && (
              <div className="bg-bgLight rounded-xl p-4">
                <div className="text-xs font-semibold text-textMuted mb-2">
                  {t('contextViewer.delegateTitle', { count: delegateEvents.length })}
                </div>
                <div className="space-y-2">
                  {delegateEvents.map((e, i) => {
                    const data = typeof e.content === 'object' ? (e.content as any) : {};
                    const toolName = data.name || t('contextViewer.delegateTask');
                    let targetAgent = '—';
                    try {
                      const args = typeof data.args === 'string' ? JSON.parse(data.args) : (data.args || {});
                      targetAgent = args.agent_id || args.agent || args.agent_name || targetAgent;
                    } catch {}
                    return (
                      <div key={i} className="flex items-center gap-2 text-[11px] text-textMuted bg-bgPage rounded-lg px-3 py-2">
                        <Bot size={11} className="text-primary flex-shrink-0" />
                        <span className="font-mono">{toolName}</span>
                        <span className="text-primary font-medium">→ {targetAgent}</span>
                        {e.timestamp && (
                          <span className="ml-auto text-textMuted/60">
                            {new Date(e.timestamp).toLocaleString('zh-CN', {
                              month: 'numeric', day: 'numeric',
                              hour: '2-digit', minute: '2-digit',
                            })}
                          </span>
                        )}
                      </div>
                    );
                  })}
                </div>
                <p className="text-[10px] text-textMuted/60 mt-2">
                  {t('contextViewer.delegateHint')}
                </p>
              </div>
            )}

          </div>
        </div>
      </div>
    </div>
  );
};

// ---- 内容块（带复制按钮） ----

const ContentBlock: React.FC<{
  id: string;
  content: any;
  copiedId: string | null;
  onCopy: (id: string, content: any) => void;
  maxH?: string;
}> = ({ id, content, copiedId, onCopy, maxH = 'max-h-64' }) => {
  const { t } = useTranslation();
  const text = typeof content === 'string' ? content : JSON.stringify(content, null, 2);
  return (
    <div className="relative group">
      <button
        className="absolute right-1.5 top-1.5 p-1 text-textMuted hover:text-textMain transition-colors opacity-0 group-hover:opacity-100 z-10"
        onClick={(e) => { e.stopPropagation(); onCopy(id, content); }}
        title={t('contextViewer.copyContent')}
      >
        {copiedId === id ? <Check size={11} className="text-emerald-400" /> : <Copy size={11} />}
      </button>
      <pre className={`text-[11px] text-textMain bg-bgLight border border-border/40 rounded-lg p-2.5 overflow-x-auto whitespace-pre-wrap break-words pr-7 ${maxH} overflow-y-auto font-mono leading-relaxed`}>
        {text}
      </pre>
    </div>
  );
};
