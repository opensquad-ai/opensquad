import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { createRoot, Root } from 'react-dom/client';
import { PluginViewProps } from '@opensquad/plugin-sdk';
import {
  ArrowLeft, RefreshCw, Loader2, AlertCircle, Clock,
  Cpu, Zap, BarChart3, Hash, Bot, DatabaseZap, PieChart, Filter
} from 'lucide-react';

// Simple plugin API proxy
const pluginAPI = {
    getPluginData: async (id: string, params: any) => {
        const query = new URLSearchParams(params).toString();
        const token = localStorage.getItem('chat_token');
        const headers: Record<string, string> = {};
        if (token) {
            headers['Authorization'] = `Bearer ${token}`;
        }

        // Updated to use the correct Gateway proxy path
        const resp = await fetch(`/api/ai-web/admin/plugins/${id}/data?${query}`, {
            headers
        });

        if (!resp.ok) {
            if (resp.status === 401) throw new Error('Unauthorized: Please login');
            const errData = await resp.json().catch(() => ({}));
            throw new Error(errData.error || `HTTP ${resp.status}`);
        }
        return resp.json();
    }
};

interface ByModelRow {
  model: string;
  tokens: number;
  requests: number;
  cache_read_tokens: number;
  cache_creation_tokens: number;
}

interface ByAgentRow {
  agent_id: string;
  tokens: number;
  requests: number;
  cache_read_tokens: number;
  cache_creation_tokens: number;
}

interface TimelineByModelPoint {
  bucket: string;
  by_model: Record<string, number>;
}

interface DashboardData {
  metric: 'total' | 'cache_read' | 'cache_creation';
  summary: {
    total_tokens: number;
    total_requests: number;
    total_input: number;
    total_output: number;
    unique_models: number;
    unique_agents: number;
    total_cache_read: number;
    total_cache_creation: number;
  };
  timeline_by_model: TimelineByModelPoint[];
  by_model: ByModelRow[];
  by_agent: ByAgentRow[];
  meta: { time_range: string; cutoff: string; query_time_ms: number; metric?: string; error?: string };
}

type MetricKey = 'total' | 'cache_read' | 'cache_creation';

const TIME_RANGES = [
  { value: '24h', label: '24H' },
  { value: '7d', label: '7D' },
  { value: '30d', label: '30D' },
  { value: 'all', label: 'All' },
];

const METRICS: { value: MetricKey; label: string; sub: string; icon: React.ReactNode; tone: string }[] = [
  { value: 'total',         label: '总 Token',   sub: 'input + output',     icon: <Zap size={14} />,        tone: 'indigo' },
  { value: 'cache_read',    label: '缓存命中',   sub: 'cache hit (省)',     icon: <DatabaseZap size={14} />, tone: 'emerald' },
  { value: 'cache_creation',label: '缓存创建',   sub: 'cache warm-up',      icon: <DatabaseZap size={14} />, tone: 'violet' },
];

// Stable, color-blind-friendly-ish palette (10 hues). Order = rank by usage.
const MODEL_PALETTE = [
  '#3b82f6', // blue
  '#10b981', // emerald
  '#8b5cf6', // violet
  '#ef4444', // red
  '#f59e0b', // amber
  '#06b6d4', // cyan
  '#84cc16', // lime
  '#ec4899', // pink
  '#6366f1', // indigo
  '#14b8a6', // teal
  '#f97316', // orange
  '#a855f7', // purple
];

function modelColor(model: string, rankedModels: string[]): string {
  const idx = rankedModels.indexOf(model);
  if (idx < 0) return '#94a3b8'; // slate-400 for unknown
  return MODEL_PALETTE[idx % MODEL_PALETTE.length];
}

function formatNumber(n: number): string {
  if (!Number.isFinite(n)) return '0';
  if (Math.abs(n) >= 1_0000_0000) return (n / 1_0000_0000).toFixed(2) + '亿';
  if (Math.abs(n) >= 10_000) return (n / 10_000).toFixed(1) + '万';
  if (Math.abs(n) >= 1_000) return (n / 1_000).toFixed(1) + 'K';
  return String(Math.round(n));
}

function formatNumberFull(n: number): string {
  if (!Number.isFinite(n)) return '0';
  return Math.round(n).toLocaleString('en-US');
}

// Pick a "nice" step for axis ticks (1, 2, 5 * 10^k).
function niceStep(max: number): number {
  if (max <= 0) return 1;
  const exp = Math.floor(Math.log10(max));
  const base = Math.pow(10, exp);
  const norm = max / base;
  let step: number;
  if (norm <= 1) step = 0.2;
  else if (norm <= 2) step = 0.5;
  else if (norm <= 5) step = 1;
  else step = 2;
  return step * base;
}

// ─── Stacked bar chart (SVG) ────────────────────────────────────────────────
interface StackedBarProps {
  points: TimelineByModelPoint[];
  rankedModels: string[];
  colors: Record<string, string>;
  metric: MetricKey;
  metricLabel: string;
}

const StackedBarChart: React.FC<StackedBarProps> = ({ points, rankedModels, colors, metricLabel }) => {
  const W = 800;
  const H = 260;
  const PAD_L = 56;
  const PAD_R = 16;
  const PAD_T = 20;
  const PAD_B = 36;

  // Per-bucket totals and overall max
  const { bucketTotals, maxTotal } = useMemo(() => {
    const totals = points.map(p => Object.values(p.by_model).reduce((s, v) => s + v, 0));
    return { bucketTotals: totals, maxTotal: Math.max(1, ...totals) };
  }, [points]);

  const ticks = useMemo(() => {
    const step = niceStep(maxTotal);
    const arr: number[] = [];
    for (let v = 0; v <= maxTotal * 1.05; v += step) arr.push(v);
    return arr;
  }, [maxTotal]);

  const n = Math.max(1, points.length);
  const slotW = (W - PAD_L - PAD_R) / n;
  const barW = Math.max(4, slotW * 0.7);

  const [hover, setHover] = useState<{ idx: number; x: number; y: number } | null>(null);
  const svgRef = useRef<SVGSVGElement | null>(null);

  const onBarEnter = (idx: number, e: React.MouseEvent) => {
    const rect = svgRef.current?.getBoundingClientRect();
    if (!rect) return;
    setHover({ idx, x: e.clientX - rect.left, y: e.clientY - rect.top });
  };

  return (
    <div className="relative">
      <svg
        ref={svgRef}
        viewBox={`0 0 ${W} ${H}`}
        className="w-full h-64"
        preserveAspectRatio="none"
        onMouseLeave={() => setHover(null)}
      >
        {/* Y axis grid + labels */}
        {ticks.map((t, i) => {
          const y = PAD_T + (H - PAD_T - PAD_B) * (1 - t / Math.max(maxTotal, niceStep(maxTotal)));
          return (
            <g key={i}>
              <line
                x1={PAD_L} y1={y} x2={W - PAD_R} y2={y}
                stroke="#e2e8f0" strokeWidth="1" strokeDasharray="2 3"
              />
              <text
                x={PAD_L - 6} y={y + 3}
                textAnchor="end" fontSize="10" fill="#94a3b8"
              >
                {formatNumber(t)}
              </text>
            </g>
          );
        })}

        {/* X axis line */}
        <line
          x1={PAD_L} y1={H - PAD_B}
          x2={W - PAD_R} y2={H - PAD_B}
          stroke="#cbd5e1" strokeWidth="1"
        />

        {/* Stacked bars */}
        {points.map((p, i) => {
          const slotX = PAD_L + i * slotW;
          const x = slotX + (slotW - barW) / 2;
          const innerH = H - PAD_T - PAD_B;
          const total = bucketTotals[i] || 0;
          if (total === 0) {
            return (
              <g key={p.bucket}>
                <rect
                  x={x} y={H - PAD_B - 1} width={barW} height={1}
                  fill="#cbd5e1"
                />
                <text
                  x={x + barW / 2} y={H - PAD_B + 14}
                  textAnchor="middle" fontSize="10" fill="#94a3b8"
                >
                  {p.bucket.slice(5)}
                </text>
              </g>
            );
          }

          // Build segments in ranked order so colors stack consistently
          const segments: { model: string; v: number; cum: number }[] = [];
          let cum = 0;
          for (const m of rankedModels) {
            const v = p.by_model[m] || 0;
            if (v > 0) {
              segments.push({ model: m, v, cum });
              cum += v;
            }
          }

          return (
            <g
              key={p.bucket}
              onMouseEnter={(e) => onBarEnter(i, e)}
              onMouseMove={(e) => onBarEnter(i, e)}
              style={{ cursor: 'pointer' }}
            >
              {segments.map((seg) => {
                const y0 = H - PAD_B - (seg.cum / maxTotal) * innerH;
                const y1 = H - PAD_B - ((seg.cum + seg.v) / maxTotal) * innerH;
                return (
                  <rect
                    key={seg.model}
                    x={x}
                    y={y1}
                    width={barW}
                    height={Math.max(0.5, y0 - y1)}
                    fill={colors[seg.model] || '#94a3b8'}
                  />
                );
              })}
              <text
                x={x + barW / 2} y={H - PAD_B + 14}
                textAnchor="middle" fontSize="10" fill="#64748b"
              >
                {p.bucket.slice(5)}
              </text>
            </g>
          );
        })}
      </svg>

      {hover && points[hover.idx] && (() => {
        const p = points[hover.idx];
        const total = bucketTotals[hover.idx] || 0;
        const segments = rankedModels
          .map(m => ({ model: m, v: p.by_model[m] || 0 }))
          .filter(s => s.v > 0)
          .sort((a, b) => b.v - a.v);
        return (
          <div
            className="absolute z-20 pointer-events-none bg-slate-900 text-white text-[11px] rounded-lg shadow-xl px-3 py-2 -translate-x-1/2 -translate-y-full"
            style={{ left: hover.x, top: hover.y - 8 }}
          >
            <div className="font-bold mb-1">{p.bucket} · {formatNumberFull(total)} {metricLabel}</div>
            {segments.slice(0, 6).map(s => (
              <div key={s.model} className="flex items-center gap-2 whitespace-nowrap">
                <span
                  className="inline-block w-2 h-2 rounded-sm"
                  style={{ background: colors[s.model] || '#94a3b8' }}
                />
                <span className="text-slate-300">{s.model}</span>
                <span className="ml-auto font-mono">{formatNumber(s.v)}</span>
              </div>
            ))}
            {segments.length > 6 && (
              <div className="text-slate-400 mt-1">+{segments.length - 6} more</div>
            )}
          </div>
        );
      })()}
    </div>
  );
};

// ─── Donut chart (SVG) ──────────────────────────────────────────────────────
interface DonutProps {
  rows: { label: string; value: number; pct: number; color: string }[];
  total: number;
  metricLabel: string;
}

const Donut: React.FC<DonutProps> = ({ rows, total, metricLabel }) => {
  const R = 78;
  const r = 52;
  const mid = (R + r) / 2;
  const C = 2 * Math.PI * R;
  const [hover, setHover] = useState<number | null>(null);

  if (total <= 0) {
    return (
      <svg viewBox="-100 -100 200 200" className="w-48 h-48">
        <circle cx={0} cy={0} r={R} fill="none" stroke="#e2e8f0" strokeWidth={R - r} />
        <text x={0} y={-2} textAnchor="middle" fontSize="14" fill="#94a3b8">暂无数据</text>
        <text x={0} y={14} textAnchor="middle" fontSize="9" fill="#cbd5e1">no data</text>
      </svg>
    );
  }

  let offset = 0;
  return (
    <svg viewBox="-100 -100 200 200" className="w-48 h-48" style={{ transform: 'rotate(-90deg)' }}>
      <circle cx={0} cy={0} r={R} fill="none" stroke="#f1f5f9" strokeWidth={R - r} />
      {rows.map((seg, i) => {
        const len = (seg.value / total) * C;
        const el = (
          <circle
            key={seg.label}
            cx={0} cy={0} r={mid}
            fill="none"
            stroke={seg.color}
            strokeWidth={R - r}
            strokeDasharray={`${len} ${C - len}`}
            strokeDashoffset={-offset}
            style={{
              cursor: 'pointer',
              opacity: hover === null || hover === i ? 1 : 0.35,
              transition: 'opacity 0.15s',
            }}
            onMouseEnter={() => setHover(i)}
            onMouseLeave={() => setHover(null)}
          />
        );
        offset += len;
        return el;
      })}
    </svg>
  );
};

// ─── Main view ──────────────────────────────────────────────────────────────
export const TokenDashboard: React.FC<PluginViewProps> = ({ onBack }) => {
  const [data, setData] = useState<DashboardData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [range, setRange] = useState('30d');
  const [metric, setMetric] = useState<MetricKey>('total');

  const fetchData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params: Record<string, string> = { range, metric };
      const result = await pluginAPI.getPluginData('token_analytics', params);
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
  }, [range, metric]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  // Models ranked by selected metric (descending)
  const rankedModels = useMemo(() => {
    if (!data) return [];
    return [...data.by_model]
      .filter(m => m.tokens > 0)
      .sort((a, b) => b.tokens - a.tokens)
      .map(m => m.model);
  }, [data]);

  // Stable color map keyed by model name (order = ranking)
  const colors = useMemo(() => {
    const map: Record<string, string> = {};
    rankedModels.forEach((m, i) => { map[m] = MODEL_PALETTE[i % MODEL_PALETTE.length]; });
    return map;
  }, [rankedModels]);

  // Donut rows (filter zero, sort desc)
  const donutRows = useMemo(() => {
    if (!data) return [];
    return rankedModels
      .map(m => {
        const row = data.by_model.find(r => r.model === m)!;
        return {
          label: m,
          value: row.tokens,
          pct: row.tokens / Math.max(1, rankedModels.reduce((s, mm) => {
            const r = data.by_model.find(x => x.model === mm);
            return s + (r?.tokens || 0);
          }, 0)),
          color: colors[m],
        };
      });
  }, [data, rankedModels, colors]);

  // Summary cards (active metric highlighted)
  const metricValue = useMemo(() => {
    if (!data) return 0;
    if (metric === 'total') return data.summary.total_tokens;
    if (metric === 'cache_read') return data.summary.total_cache_read;
    return data.summary.total_cache_creation;
  }, [data, metric]);
  const metricSub = useMemo(() => {
    if (metric === 'total') return '总消耗';
    if (metric === 'cache_read') return '节省的 token';
    return '建立缓存';
  }, [metric]);

  const isEmpty = data && rankedModels.length === 0;

  return (
    <div className="flex-1 h-full bg-slate-50 flex flex-col overflow-hidden rounded-2xl border border-slate-200">
      {/* Header */}
      <div className="px-6 py-4 border-b border-slate-200 bg-white flex items-center gap-4 shrink-0">
        <button onClick={onBack} className="p-2 rounded-lg text-slate-400 hover:bg-slate-100 hover:text-indigo-600 transition-colors">
          <ArrowLeft size={20} />
        </button>
        <div className="flex items-center gap-3 flex-1">
          <div className="w-10 h-10 bg-indigo-50 rounded-xl flex items-center justify-center text-indigo-600">
            <BarChart3 size={22} />
          </div>
          <div>
            <h1 className="text-lg font-bold text-slate-800">Token 消耗统计</h1>
            <p className="text-xs text-slate-500">
              {data?.summary
                ? `${formatNumber(metricValue)} ${metricSub} / ${data.summary.total_requests.toLocaleString()} 请求`
                : '加载中...'}
            </p>
          </div>
        </div>

        <div className="flex gap-1 bg-slate-100 rounded-lg p-1">
          {TIME_RANGES.map(tr => (
            <button
              key={tr.value}
              onClick={() => setRange(tr.value)}
              className={`px-2 py-1 rounded text-[10px] font-bold transition-colors ${
                range === tr.value ? 'bg-white text-indigo-600 shadow-sm' : 'text-slate-500 hover:text-slate-700'
              }`}
            >
              {tr.label}
            </button>
          ))}
        </div>

        <button onClick={fetchData} className="p-2 rounded-lg text-slate-400 hover:bg-slate-100 transition-colors">
          <RefreshCw size={18} className={loading ? 'animate-spin' : ''} />
        </button>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-6 space-y-6">
        {loading && !data ? (
          <div className="flex flex-col items-center justify-center h-64 gap-3">
            <Loader2 className="animate-spin text-indigo-600" size={32} />
            <p className="text-slate-500 text-sm">正在计算统计数据...</p>
          </div>
        ) : error && !data ? (
          <div className="bg-rose-50 p-6 rounded-2xl border border-rose-100 flex flex-col items-center text-center">
            <AlertCircle className="text-rose-500 mb-2" size={32} />
            <p className="text-rose-800 font-medium">{error}</p>
            <button onClick={fetchData} className="mt-4 text-sm font-bold text-rose-600 hover:underline">重试</button>
          </div>
        ) : data ? (
          <>
            {/* Metric filter */}
            <div className="bg-white p-3 rounded-2xl border border-slate-100 shadow-sm flex items-center gap-2">
              <div className="flex items-center gap-2 px-2 text-slate-400">
                <Filter size={14} />
                <span className="text-xs font-bold uppercase tracking-wider">指标</span>
              </div>
              <div className="flex gap-1 bg-slate-100 rounded-lg p-1">
                {METRICS.map(m => (
                  <button
                    key={m.value}
                    onClick={() => setMetric(m.value)}
                    className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-bold transition-colors ${
                      metric === m.value
                        ? 'bg-white text-indigo-600 shadow-sm'
                        : 'text-slate-500 hover:text-slate-700'
                    }`}
                  >
                    {m.icon}
                    {m.label}
                  </button>
                ))}
              </div>
              <div className="ml-auto text-xs text-slate-400">{METRICS.find(x => x.value === metric)?.sub}</div>
            </div>

            {/* Summary Cards */}
            <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-4 gap-4">
              <StatCard
                label={METRICS.find(x => x.value === metric)?.label || '总 Token'}
                value={formatNumber(metricValue)}
                icon={<Zap size={18} />}
                color="bg-amber-500"
                subtitle={metricSub}
                active
              />
              <StatCard label="请求数" value={data.summary.total_requests.toLocaleString()} icon={<Hash size={18} />} color="bg-blue-500" />
              <StatCard label="输入 Token" value={formatNumber(data.summary.total_input)} icon={<ChevronDown size={18} />} color="bg-emerald-500" />
              <StatCard label="输出 Token" value={formatNumber(data.summary.total_output)} icon={<Cpu size={18} />} color="bg-indigo-500" />
              <StatCard label="模型数" value={String(data.summary.unique_models)} icon={<Cpu size={18} />} color="bg-cyan-500" />
              <StatCard label="Agent 数" value={String(data.summary.unique_agents)} icon={<Bot size={18} />} color="bg-rose-500" />
              <StatCard label="缓存命中" value={formatNumber(data.summary.total_cache_read)} icon={<DatabaseZap size={18} />} color="bg-teal-500" subtitle="节省的 token" />
              {data.summary.total_cache_creation > 0 && (
                <StatCard label="缓存创建" value={formatNumber(data.summary.total_cache_creation)} icon={<DatabaseZap size={18} />} color="bg-violet-500" subtitle="建立缓存" />
              )}
            </div>

            {/* Stacked bar chart */}
            <Section title="每天 Token 趋势" icon={<BarChart3 size={16} />}>
              {isEmpty ? (
                <EmptyHint metric={metric} />
              ) : (
                <>
                  <StackedBarChart
                    points={data.timeline_by_model}
                    rankedModels={rankedModels}
                    colors={colors}
                    metric={metric}
                    metricLabel={METRICS.find(x => x.value === metric)?.label || 'tokens'}
                  />
                  {rankedModels.length > 0 && (
                    <div className="flex flex-wrap gap-x-4 gap-y-2 mt-4 pt-3 border-t border-slate-100">
                      {rankedModels.map(m => (
                        <div key={m} className="flex items-center gap-1.5 text-xs">
                          <span
                            className="inline-block w-2.5 h-2.5 rounded-sm"
                            style={{ background: colors[m] }}
                          />
                          <span className="text-slate-600 font-medium">{m}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </>
              )}
            </Section>

            {/* Donut + legend */}
            <Section title="模型用量" icon={<PieChart size={16} />}>
              {isEmpty ? (
                <EmptyHint metric={metric} />
              ) : (
                <div className="flex flex-col md:flex-row items-center gap-6">
                  <div className="relative">
                    <Donut
                      rows={donutRows}
                      total={donutRows.reduce((s, r) => s + r.value, 0)}
                      metricLabel={METRICS.find(x => x.value === metric)?.label || 'tokens'}
                    />
                    <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none">
                      <div className="text-xl font-black text-slate-800">
                        {formatNumber(donutRows.reduce((s, r) => s + r.value, 0))}
                      </div>
                      <div className="text-[10px] text-slate-400 mt-0.5">
                        {METRICS.find(x => x.value === metric)?.label}
                      </div>
                    </div>
                  </div>

                  <div className="flex-1 w-full space-y-2">
                    {donutRows.map(r => (
                      <div
                        key={r.label}
                        className="flex items-center gap-3 px-2 py-1.5 rounded-lg hover:bg-slate-50"
                      >
                        <span
                          className="inline-block w-2.5 h-2.5 rounded-sm shrink-0"
                          style={{ background: r.color }}
                        />
                        <span className="text-sm text-slate-700 font-medium flex-1 truncate">
                          {r.label}
                        </span>
                        <span className="text-sm font-mono text-slate-600 tabular-nums">
                          {formatNumber(r.value)}
                        </span>
                        <span className="text-xs text-slate-400 tabular-nums w-12 text-right">
                          {(r.pct * 100).toFixed(1)}%
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </Section>

            {/* By Agent */}
            {data.by_agent.length > 0 && (
              <Section title="按 Agent 统计" icon={<Bot size={16} />}>
                <BarList
                  items={data.by_agent.map(a => ({
                    label: a.agent_id,
                    value: a.tokens,
                    sub: `${a.requests.toLocaleString()} req`,
                  }))}
                  color="bg-emerald-500"
                />
              </Section>
            )}
          </>
        ) : null}
      </div>
    </div>
  );
};

const ChevronDown = (props: any) => (
  <svg viewBox="0 0 24 24" width={18} height={18} fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" {...props}>
    <polyline points="6 9 12 15 18 9" />
  </svg>
);

const EmptyHint: React.FC<{ metric: MetricKey }> = ({ metric }) => (
  <div className="h-48 flex flex-col items-center justify-center text-slate-400 gap-2">
    <BarChart3 size={28} className="opacity-40" />
    <p className="text-sm">
      {metric === 'cache_read' || metric === 'cache_creation'
        ? '当前模型未使用 prompt cache,此指标为 0'
        : '所选时间范围内暂无数据'}
    </p>
  </div>
);

const StatCard: React.FC<{ label: string; value: string; icon: React.ReactNode; color: string; subtitle?: string; active?: boolean }> = ({ label, value, icon, color, subtitle, active }) => (
    <div className={`bg-white p-4 rounded-2xl border ${active ? 'border-indigo-200 shadow-md ring-1 ring-indigo-100' : 'border-slate-100'} shadow-sm`}>
        <div className={`w-8 h-8 rounded-lg ${color} text-white flex items-center justify-center mb-3`}>
            {icon}
        </div>
        <div className="text-xl font-black text-slate-800">{value}</div>
        <div className="text-[10px] font-bold text-slate-400 uppercase tracking-tight">{label}</div>
        {subtitle && <div className="text-[9px] text-slate-300 mt-0.5">{subtitle}</div>}
    </div>
);

const Section: React.FC<{ title: string; icon: React.ReactNode; children: React.ReactNode }> = ({ title, icon, children }) => (
    <div className="bg-white p-5 rounded-2xl border border-slate-100 shadow-sm">
        <div className="flex items-center gap-2 mb-4">
            <div className="text-slate-400">{icon}</div>
            <h2 className="text-sm font-bold text-slate-800 uppercase tracking-wider">{title}</h2>
        </div>
        {children}
    </div>
);

const BarList: React.FC<{ items: { label: string; value: number; sub: string }[]; color: string }> = ({ items, color }) => {
    const maxVal = Math.max(1, ...items.map(i => i.value));
    return (
        <div className="space-y-3">
            {items.map((item, i) => (
                <div key={i} className="space-y-1">
                    <div className="flex justify-between text-xs font-medium">
                        <span className="text-slate-700 truncate">{item.label}</span>
                        <span className="text-slate-400">{formatNumber(item.value)} <span className="text-[10px]">({item.sub})</span></span>
                    </div>
                    <div className="h-1.5 w-full bg-slate-100 rounded-full overflow-hidden">
                        <div className={`h-full ${color}`} style={{ width: `${(item.value / maxVal) * 100}%` }} />
                    </div>
                </div>
            ))}
        </div>
    );
};

// Plugin System Interface
const _roots = new WeakMap<HTMLElement, Root>();
export function mount(container: HTMLElement, props: PluginViewProps): void {
  const root = createRoot(container);
  _roots.set(container, root);
  root.render(<TokenDashboard {...props} />);
}
export function unmount(container: HTMLElement): void {
  const root = _roots.get(container);
  if (root) {
    root.unmount();
    _roots.delete(container);
  }
}
export default TokenDashboard;
