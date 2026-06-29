import React, { useState, useEffect, useCallback, useRef } from 'react';
import { createRoot, Root } from 'react-dom/client';
import { PluginViewProps } from '@opensquad/plugin-sdk';
import {
  ArrowLeft, RefreshCw, Loader2, AlertCircle, Clock,
  CheckCircle2, XCircle, AlertTriangle, Activity, Zap,
  ClipboardList, Hash, Bot, Wrench, Play, Pause, Timer
} from 'lucide-react';

// ── API helper ──
const pluginAPI = {
  getPluginData: async (id: string, params: any) => {
    const query = new URLSearchParams(params).toString();
    const token = localStorage.getItem('chat_token');
    const headers: Record<string, string> = {};
    if (token) headers['Authorization'] = `Bearer ${token}`;
    const resp = await fetch(`/api/ai-web/admin/plugins/${id}/data?${query}`, { headers });
    if (!resp.ok) {
      if (resp.status === 401) throw new Error('Unauthorized: Please login');
      const errData = await resp.json().catch(() => ({}));
      throw new Error(errData.error || `HTTP ${resp.status}`);
    }
    return resp.json();
  }
};

// ── Types ──
interface DashboardData {
  summary: {
    total_tasks: number;
    completed_tasks: number;
    abandoned_tasks: number;
    total_stalls: number;
    total_updates: number;
    avg_duration_sec: number;
    total_tool_calls: number;
    tool_error_count: number;
  };
  live_task: {
    active?: boolean;
    task_id?: string;
    description?: string;
    status?: string;
    elapsed_seconds?: number;
    since_last_activity?: number;
    stall_count?: number;
    progress_updates?: number;
    check_interval?: number;
    history_count?: number;
  };
  live_progress: Array<{ time: string; text: string; elapsed: number }>;
  task_history: Array<{
    task_id: string;
    description: string;
    status: string;
    started_at?: string;
    ended_at?: string;
    created_at?: string;
    elapsed_seconds: number;
    stall_count: number;
    progress_updates: number;
  }>;
  task_events: Array<{
    timestamp: string;
    event_type: string;
    task_id: string;
    agent_id: string;
    description: string;
    detail: string;
    stall_count: number;
    elapsed_sec: number;
  }>;
  tool_timeline: Array<{ bucket: string; calls: number; errors: number; label: string }>;
  tool_stats: Array<{ tool_name: string; call_count: number; error_count: number }>;
  meta: { time_range: string; cutoff: string; query_time_ms: number };
}

const TIME_RANGES = [
  { value: '1h', label: '1H' },
  { value: '6h', label: '6H' },
  { value: '24h', label: '24H' },
  { value: '7d', label: '7D' },
  { value: '30d', label: '30D' },
  { value: 'all', label: 'All' },
];

// ── Helpers ──
function fmtNum(n: number): string {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M';
  if (n >= 1_000) return (n / 1_000).toFixed(1) + 'K';
  return String(n);
}

function fmtDuration(sec: number): string {
  if (sec < 60) return `${Math.round(sec)}s`;
  if (sec < 3600) return `${Math.floor(sec / 60)}m ${Math.round(sec % 60)}s`;
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  return `${h}h ${m}m`;
}

function fmtTime(iso: string): string {
  if (!iso) return '-';
  try {
    const d = new Date(iso);
    return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  } catch { return iso.slice(11, 19); }
}

function fmtBucket(bucket: string, label: string): string {
  if (!bucket) return '';
  if (label === 'day') return bucket.slice(5);
  if (label === 'hour') return bucket.slice(11) + ':00';
  return bucket.length > 11 ? bucket.slice(11) : bucket;
}

function urgencyColor(stallCount: number, maxStalls: number = 5): string {
  if (stallCount === 0) return 'text-emerald-500';
  if (stallCount <= 2) return 'text-amber-500';
  if (stallCount <= 4) return 'text-orange-500';
  return 'text-red-500';
}

function statusBadge(status: string) {
  const map: Record<string, { bg: string; text: string; label: string }> = {
    active:    { bg: 'bg-emerald-100', text: 'text-emerald-700', label: '运行中' },
    completed: { bg: 'bg-blue-100',    text: 'text-blue-700',    label: '已完成' },
    complete:  { bg: 'bg-blue-100',    text: 'text-blue-700',    label: '已完成' },
    abandoned: { bg: 'bg-red-100',     text: 'text-red-700',     label: '已放弃' },
    abandon:   { bg: 'bg-red-100',     text: 'text-red-700',     label: '已放弃' },
  };
  const s = map[status] || { bg: 'bg-slate-100', text: 'text-slate-600', label: status };
  return (
    <span className={`px-2 py-0.5 rounded-full text-[10px] font-bold ${s.bg} ${s.text}`}>
      {s.label}
    </span>
  );
}

function eventIcon(type: string) {
  const size = 14;
  switch (type) {
    case 'start':    return <Play size={size} className="text-emerald-500" />;
    case 'update':   return <Activity size={size} className="text-blue-500" />;
    case 'stall':    return <AlertTriangle size={size} className="text-amber-500" />;
    case 'complete': return <CheckCircle2 size={size} className="text-blue-500" />;
    case 'abandon':  return <XCircle size={size} className="text-red-500" />;
    default:         return <Hash size={size} className="text-slate-400" />;
  }
}

// ── Main Dashboard ──
export const TaskWatchDashboard: React.FC<PluginViewProps> = ({ onBack }) => {
  const [data, setData] = useState<DashboardData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [range, setRange] = useState('24h');
  const [autoRefresh, setAutoRefresh] = useState(true);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await pluginAPI.getPluginData('task_watch', { range });
      if (result.error) {
        setError(result.error);
        setData(null);
      } else {
        setData(result);
      }
    } catch (e: any) {
      setError(e.message || 'Failed to load data');
    } finally {
      setLoading(false);
    }
  }, [range]);

  useEffect(() => { fetchData(); }, [fetchData]);

  // Auto-refresh every 10s
  useEffect(() => {
    if (timerRef.current) clearInterval(timerRef.current);
    if (autoRefresh) {
      timerRef.current = setInterval(fetchData, 10_000);
    }
    return () => { if (timerRef.current) clearInterval(timerRef.current); };
  }, [autoRefresh, fetchData]);

  return (
    <div className="flex-1 h-full bg-slate-50 flex flex-col overflow-hidden rounded-2xl border border-slate-200">
      {/* Header */}
      <div className="px-6 py-4 border-b border-slate-200 bg-white flex items-center gap-4 shrink-0">
        <button onClick={onBack} className="p-2 rounded-lg text-slate-400 hover:bg-slate-100 hover:text-indigo-600 transition-colors">
          <ArrowLeft size={20} />
        </button>
        <div className="flex items-center gap-3 flex-1">
          <div className="w-10 h-10 bg-violet-50 rounded-xl flex items-center justify-center text-violet-600">
            <ClipboardList size={22} />
          </div>
          <div>
            <h1 className="text-lg font-bold text-slate-800">Task Watch</h1>
            <p className="text-xs text-slate-500">
              {data?.live_task?.active
                ? `监控中: ${data.live_task.description?.slice(0, 40) || data.live_task.task_id}`
                : '暂无活跃监控任务'}
            </p>
          </div>
        </div>

        {/* Time range */}
        <div className="flex gap-1 bg-slate-100 rounded-lg p-1">
          {TIME_RANGES.map(tr => (
            <button
              key={tr.value}
              onClick={() => setRange(tr.value)}
              className={`px-2 py-1 rounded text-[10px] font-bold transition-colors ${
                range === tr.value ? 'bg-white text-violet-600 shadow-sm' : 'text-slate-500 hover:text-slate-700'
              }`}
            >
              {tr.label}
            </button>
          ))}
        </div>

        {/* Auto-refresh toggle */}
        <button
          onClick={() => setAutoRefresh(p => !p)}
          className={`p-2 rounded-lg transition-colors ${
            autoRefresh ? 'text-violet-600 bg-violet-50' : 'text-slate-400 hover:bg-slate-100'
          }`}
          title={autoRefresh ? '自动刷新开启 (10s)' : '自动刷新已暂停'}
        >
          {autoRefresh ? <Play size={16} /> : <Pause size={16} />}
        </button>

        <button onClick={fetchData} className="p-2 rounded-lg text-slate-400 hover:bg-slate-100 transition-colors">
          <RefreshCw size={18} className={loading ? 'animate-spin' : ''} />
        </button>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-6 space-y-6">
        {loading && !data ? (
          <div className="flex flex-col items-center justify-center h-64 gap-3">
            <Loader2 className="animate-spin text-violet-600" size={32} />
            <p className="text-slate-500 text-sm">加载数据中...</p>
          </div>
        ) : error && !data ? (
          <div className="bg-rose-50 p-6 rounded-2xl border border-rose-100 flex flex-col items-center text-center">
            <AlertCircle className="text-rose-500 mb-2" size={32} />
            <p className="text-rose-800 font-medium">{error}</p>
            <button onClick={fetchData} className="mt-4 text-sm font-bold text-rose-600 hover:underline">重试</button>
          </div>
        ) : data ? (
          <>
            {/* Live Task Card */}
            {data.live_task?.active && <LiveTaskCard task={data.live_task} progress={data.live_progress} />}

            {/* Summary Cards */}
            <div className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-4 gap-4">
              <StatCard label="总任务" value={fmtNum(data.summary.total_tasks)} icon={<ClipboardList size={18} />} color="bg-violet-500" />
              <StatCard label="已完成" value={fmtNum(data.summary.completed_tasks)} icon={<CheckCircle2 size={18} />} color="bg-emerald-500" />
              <StatCard label="已放弃" value={fmtNum(data.summary.abandoned_tasks)} icon={<XCircle size={18} />} color="bg-red-500" />
              <StatCard label="停滞次数" value={fmtNum(data.summary.total_stalls)} icon={<AlertTriangle size={18} />} color="bg-amber-500" />
              <StatCard label="打卡次数" value={fmtNum(data.summary.total_updates)} icon={<Activity size={18} />} color="bg-blue-500" />
              <StatCard label="平均时长" value={fmtDuration(data.summary.avg_duration_sec)} icon={<Timer size={18} />} color="bg-cyan-500" />
              <StatCard label="工具调用" value={fmtNum(data.summary.total_tool_calls)} icon={<Wrench size={18} />} color="bg-indigo-500" />
              <StatCard label="工具错误" value={fmtNum(data.summary.tool_error_count)} icon={<Zap size={18} />} color="bg-rose-500" />
            </div>

            {/* Tool Activity Timeline */}
            {data.tool_timeline.length > 0 && (
              <Section title="工具调用趋势" icon={<Activity size={16} />}>
                <div className="flex items-end gap-[2px] h-32 pt-4">
                  {data.tool_timeline.slice(-60).map((t, i) => {
                    const maxCalls = Math.max(...data.tool_timeline.map(x => x.calls), 1);
                    const pct = (t.calls / maxCalls) * 100;
                    const errPct = t.errors > 0 ? Math.max((t.errors / t.calls) * 100, 8) : 0;
                    return (
                      <div key={i} className="flex-1 min-w-[3px] relative group" style={{ height: `${Math.max(pct, 2)}%` }}>
                        <div className="absolute inset-0 bg-violet-200 rounded-t-sm" />
                        {errPct > 0 && (
                          <div className="absolute bottom-0 left-0 right-0 bg-red-400 rounded-t-sm" style={{ height: `${errPct}%` }} />
                        )}
                        <div className="absolute inset-0 bg-violet-500 opacity-0 group-hover:opacity-60 transition-opacity rounded-t-sm" />
                        <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 hidden group-hover:block z-10">
                          <div className="bg-slate-900 text-white text-[10px] px-2 py-1 rounded whitespace-nowrap">
                            {fmtBucket(t.bucket, t.label)}: {t.calls} 次{t.errors > 0 ? ` (${t.errors} 错误)` : ''}
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </div>
                <div className="flex gap-4 mt-2 text-[10px] text-slate-400">
                  <span className="flex items-center gap-1"><span className="w-2 h-2 bg-violet-200 rounded-sm" /> 调用</span>
                  <span className="flex items-center gap-1"><span className="w-2 h-2 bg-red-400 rounded-sm" /> 错误</span>
                </div>
              </Section>
            )}

            {/* Two-column: History + Tool Stats */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
              {/* Task History */}
              <Section title="任务历史" icon={<Clock size={16} />}>
                {data.task_history.length === 0 ? (
                  <p className="text-slate-400 text-xs">暂无历史记录</p>
                ) : (
                  <div className="space-y-2 max-h-80 overflow-y-auto">
                    {data.task_history.map((h, i) => (
                      <div key={i} className="flex items-start gap-3 p-3 bg-slate-50 rounded-xl">
                        <div className="shrink-0 mt-0.5">
                          {h.status === 'completed' || h.status === 'complete'
                            ? <CheckCircle2 size={16} className="text-emerald-500" />
                            : h.status === 'abandoned' || h.status === 'abandon'
                              ? <XCircle size={16} className="text-red-500" />
                              : <Activity size={16} className="text-blue-500" />}
                        </div>
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2">
                            <span className="text-xs font-semibold text-slate-700 truncate">{h.description || h.task_id}</span>
                            {statusBadge(h.status)}
                          </div>
                          <div className="flex gap-3 mt-1 text-[10px] text-slate-400">
                            <span>{fmtDuration(h.elapsed_seconds)}</span>
                            <span>{h.progress_updates} 次打卡</span>
                            {h.stall_count > 0 && <span className="text-amber-500">{h.stall_count} 次停滞</span>}
                            <span>{fmtTime(h.started_at || h.created_at || '')}</span>
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </Section>

              {/* Tool Stats */}
              <Section title="工具调用统计" icon={<Wrench size={16} />}>
                {data.tool_stats.length === 0 ? (
                  <p className="text-slate-400 text-xs">暂无数据</p>
                ) : (
                  <BarList
                    items={data.tool_stats.map(t => ({
                      label: t.tool_name,
                      value: t.call_count,
                      sub: t.error_count > 0 ? `${t.error_count} 错误` : '',
                    }))}
                    color="bg-violet-500"
                  />
                )}
              </Section>
            </div>

            {/* Event Log */}
            {data.task_events.length > 0 && (
              <Section title="事件日志" icon={<ClipboardList size={16} />}>
                <div className="space-y-1 max-h-80 overflow-y-auto">
                  {data.task_events.slice(0, 50).map((evt, i) => (
                    <div key={i} className="flex items-center gap-2 px-2 py-1.5 rounded-lg hover:bg-slate-50 text-xs">
                      {eventIcon(evt.event_type)}
                      <span className="text-slate-500 font-mono w-16 shrink-0">{fmtTime(evt.timestamp)}</span>
                      <span className={`font-bold w-14 shrink-0 ${
                        evt.event_type === 'stall' ? 'text-amber-600' :
                        evt.event_type === 'abandon' ? 'text-red-600' :
                        evt.event_type === 'complete' ? 'text-emerald-600' :
                        'text-slate-600'
                      }`}>{evt.event_type}</span>
                      <span className="text-slate-700 truncate flex-1">{evt.description || evt.task_id}</span>
                      {evt.stall_count > 0 && (
                        <span className="text-amber-500 text-[10px] shrink-0">stall #{evt.stall_count}</span>
                      )}
                      {evt.elapsed_sec > 0 && (
                        <span className="text-slate-400 text-[10px] shrink-0">{fmtDuration(evt.elapsed_sec)}</span>
                      )}
                    </div>
                  ))}
                </div>
              </Section>
            )}
          </>
        ) : null}
      </div>
    </div>
  );
};

// ── Live Task Card ──
const LiveTaskCard: React.FC<{
  task: DashboardData['live_task'];
  progress: DashboardData['live_progress'];
}> = ({ task, progress }) => {
  const stallPct = task.stall_count && task.check_interval
    ? Math.min((task.stall_count / 5) * 100, 100) : 0;

  return (
    <div className="bg-white p-5 rounded-2xl border-2 border-violet-200 shadow-sm">
      <div className="flex items-start gap-4">
        {/* Icon with pulse */}
        <div className="relative">
          <div className="w-12 h-12 bg-violet-100 rounded-xl flex items-center justify-center">
            <Activity size={24} className="text-violet-600" />
          </div>
          <div className="absolute -top-1 -right-1 w-3 h-3 bg-emerald-400 rounded-full border-2 border-white animate-pulse" />
        </div>

        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1">
            <h3 className="text-sm font-bold text-slate-800 truncate">{task.description || task.task_id}</h3>
            {statusBadge('active')}
          </div>

          {/* Metrics row */}
          <div className="flex flex-wrap gap-4 text-[11px] text-slate-500 mb-3">
            <span className="flex items-center gap-1">
              <Timer size={12} /> 运行 {fmtDuration(task.elapsed_seconds || 0)}
            </span>
            <span className="flex items-center gap-1">
              <Clock size={12} /> 上次活跃 {Math.round(task.since_last_activity || 0)}s 前
            </span>
            <span className="flex items-center gap-1">
              <Activity size={12} /> {task.progress_updates || 0} 次打卡
            </span>
            <span className={`flex items-center gap-1 ${urgencyColor(task.stall_count || 0)}`}>
              <AlertTriangle size={12} /> 停滞 {task.stall_count || 0}/5
            </span>
          </div>

          {/* Stall progress bar */}
          <div className="h-1.5 w-full bg-slate-100 rounded-full overflow-hidden mb-3">
            <div
              className={`h-full transition-all duration-500 rounded-full ${
                stallPct === 0 ? 'bg-emerald-400' :
                stallPct <= 40 ? 'bg-amber-400' :
                stallPct <= 80 ? 'bg-orange-400' : 'bg-red-500'
              }`}
              style={{ width: `${Math.max(stallPct, 3)}%` }}
            />
          </div>

          {/* Progress log */}
          {progress.length > 0 && (
            <div className="space-y-1 max-h-32 overflow-y-auto">
              {progress.slice(-5).map((p, i) => (
                <div key={i} className="flex gap-2 text-[10px]">
                  <span className="text-slate-400 font-mono shrink-0">{fmtTime(p.time)}</span>
                  <span className="text-slate-600">{p.text}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

// ── Shared Components ──
const StatCard = ({ label, value, icon, color }: any) => (
  <div className="bg-white p-4 rounded-2xl border border-slate-100 shadow-sm">
    <div className={`w-8 h-8 rounded-lg ${color} text-white flex items-center justify-center mb-3`}>
      {icon}
    </div>
    <div className="text-xl font-black text-slate-800">{value}</div>
    <div className="text-[10px] font-bold text-slate-400 uppercase tracking-tight">{label}</div>
  </div>
);

const Section = ({ title, icon, children }: any) => (
  <div className="bg-white p-5 rounded-2xl border border-slate-100 shadow-sm">
    <div className="flex items-center gap-2 mb-4">
      <div className="text-slate-400">{icon}</div>
      <h2 className="text-sm font-bold text-slate-800 uppercase tracking-wider">{title}</h2>
    </div>
    {children}
  </div>
);

const BarList = ({ items, color }: any) => {
  const maxVal = Math.max(...items.map((i: any) => i.value), 1);
  return (
    <div className="space-y-3">
      {items.map((item: any, i: number) => (
        <div key={i} className="space-y-1">
          <div className="flex justify-between text-xs font-medium">
            <span className="text-slate-700 truncate">{item.label}</span>
            <span className="text-slate-400">
              {fmtNum(item.value)}
              {item.sub ? <span className="text-[10px] ml-1 text-red-400">({item.sub})</span> : ''}
            </span>
          </div>
          <div className="h-1.5 w-full bg-slate-100 rounded-full overflow-hidden">
            <div className={`h-full ${color}`} style={{ width: `${(item.value / maxVal) * 100}%` }} />
          </div>
        </div>
      ))}
    </div>
  );
};

// ── Plugin System Interface ──
const _roots = new WeakMap<HTMLElement, Root>();
export function mount(container: HTMLElement, props: PluginViewProps): void {
  const root = createRoot(container);
  _roots.set(container, root);
  root.render(<TaskWatchDashboard {...props} />);
}
export function unmount(container: HTMLElement): void {
  const root = _roots.get(container);
  if (root) {
    root.unmount();
    _roots.delete(container);
  }
}
export default TaskWatchDashboard;
