import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { AlertCircle, ArrowLeft, BarChart3, RefreshCw } from 'lucide-react';
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import type { TooltipContentProps } from 'recharts';
import { OpenSquadLoader } from '../OpenSquadLoader';
import { tokenStatsAPI, type AgentTokenStats, type AgentTokenTimelinePoint } from '../../services/api';
import { formatTokenCount, formatTokenExact } from '../../utils/usageFormat';
import {
  adminHeaderBar,
  adminHeaderGhostBtn,
  adminHeaderIcon,
  adminHeaderIconBox,
  adminHeaderSubtitle,
  adminHeaderTitle,
} from '../admin/adminShellStyles';

interface AgentTokenStatsPageProps {
  /** Canonical agent id (config `agent_id`) — matches the analytics DB column. */
  agentId: string;
  onBack: () => void;
}

const RANGES = ['1h', '6h', '24h', '7d', '30d', 'all'] as const;
type RangeValue = (typeof RANGES)[number];

/** Three stacked series — order and hues match the reference design. */
const SERIES = [
  { key: 'input_hit', color: '#93c5fd', labelKey: 'agentTokenStats.seriesHit' },
  { key: 'input_miss', color: '#3b82f6', labelKey: 'agentTokenStats.seriesMiss' },
  { key: 'output', color: '#1d4ed8', labelKey: 'agentTokenStats.seriesOutput' },
] as const;

/** `2026-09-30T15` → `15:00`, `2026-09-30` → `09-30`. */
function formatBucket(bucket: string): string {
  if (!bucket) return '';
  if (bucket.length === 16) return bucket.slice(11, 16);
  if (bucket.length === 13) return `${bucket.slice(11, 13)}:00`;
  if (bucket.length === 10) return bucket.slice(5);
  return bucket;
}

interface StatsTooltipProps extends Partial<Pick<TooltipContentProps<number, string>, 'active' | 'label' | 'payload'>> {
  labels: Record<string, string>;
}

const StatsTooltip: React.FC<StatsTooltipProps> = ({ active, label, payload, labels }) => {
  if (!active || !payload || payload.length === 0) return null;
  const byKey = new Map(payload.map((entry) => [String(entry.dataKey), entry]));
  const total = SERIES.reduce((sum, s) => sum + Number(byKey.get(s.key)?.value ?? 0), 0);

  return (
    <div className="rounded-lg border border-border bg-panel px-3 py-2 shadow-lg text-[11px]">
      <div className="flex items-center justify-between gap-6 mb-1">
        <span className="font-semibold text-textMain">{formatBucket(String(label ?? ''))}</span>
        <span className="font-mono tabular-nums text-textMain">{formatTokenExact(total)}</span>
      </div>
      {SERIES.map((s) => (
        <div key={s.key} className="flex items-center gap-2 whitespace-nowrap">
          <span className="inline-block w-2 h-2 rounded-sm shrink-0" style={{ background: s.color }} />
          <span className="text-textMuted">{labels[s.key] ?? s.key}</span>
          <span className="ml-auto font-mono tabular-nums text-textMain">
            {formatTokenExact(Number(byKey.get(s.key)?.value ?? 0))}
          </span>
        </div>
      ))}
    </div>
  );
};

const StatCard: React.FC<{ label: string; value: string; hint?: string; accent?: string }> = ({
  label,
  value,
  hint,
  accent,
}) => (
  <div className="bg-panel border border-border rounded-xl px-4 py-3">
    <div className="flex items-center gap-1.5 mb-1.5">
      {accent ? <span className="inline-block w-2.5 h-2.5 rounded-sm shrink-0" style={{ background: accent }} /> : null}
      <span className="text-[10px] font-semibold uppercase tracking-wide text-textMuted/70">{label}</span>
    </div>
    <div className="text-lg font-bold text-textMain tabular-nums">{value}</div>
    {hint ? <div className="text-[10px] text-textMuted/60 mt-0.5">{hint}</div> : null}
  </div>
);

export const AgentTokenStatsPage: React.FC<AgentTokenStatsPageProps> = ({ agentId, onBack }) => {
  const { t } = useTranslation();
  const [range, setRange] = useState<RangeValue>('24h');
  const [model, setModel] = useState('');
  const [data, setData] = useState<AgentTokenStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await tokenStatsAPI.getAgentTokens({ agentId, range, model: model || undefined });
      setData(result);
    } catch (e) {
      setError(e instanceof Error ? e.message : t('agentTokenStats.error'));
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [agentId, range, model, t]);

  useEffect(() => {
    void fetchData();
  }, [fetchData]);

  // Reset the model filter whenever the agent changes — a model from the
  // previous agent almost certainly does not exist for the new one.
  useEffect(() => {
    setModel('');
  }, [agentId]);

  const seriesLabels = useMemo<Record<string, string>>(
    () => ({
      input_hit: t('agentTokenStats.seriesHit'),
      input_miss: t('agentTokenStats.seriesMiss'),
      output: t('agentTokenStats.seriesOutput'),
    }),
    [t],
  );

  const summary = data?.summary;
  const timeline: AgentTokenTimelinePoint[] = data?.timeline ?? [];
  const byModel = data?.by_model ?? [];
  const hasData = Boolean(summary && summary.requests > 0 && timeline.length > 0);

  return (
    <div className="flex-1 min-w-0 min-h-0 flex flex-col bg-background">
      <div className={adminHeaderBar}>
        <button type="button" onClick={onBack} className={`${adminHeaderGhostBtn} mr-2`} title={t('agentTokenStats.back')}>
          <ArrowLeft size={16} />
        </button>
        <div className={`${adminHeaderIconBox} mr-2`}>
          <BarChart3 size={16} className={adminHeaderIcon} />
        </div>
        <div className="min-w-0">
          <div className="flex items-baseline gap-2">
            <span className={adminHeaderTitle}>{t('agentTokenStats.title')}</span>
            {summary ? (
              <span className="text-xs font-mono tabular-nums text-textMuted">
                {formatTokenCount(summary.total)}
              </span>
            ) : null}
          </div>
          <div className={adminHeaderSubtitle}>{agentId}</div>
        </div>

        <div className="ml-auto flex items-center gap-2">
          <div className="flex gap-0.5 bg-panel border border-border rounded-md p-0.5">
            {RANGES.map((r) => (
              <button
                key={r}
                type="button"
                onClick={() => setRange(r)}
                className={`px-2 py-1 rounded text-[11px] font-medium transition-colors ${
                  range === r ? 'bg-primary/10 text-primary' : 'text-textMuted hover:text-textMain'
                }`}
              >
                {t(`agentTokenStats.range_${r}`)}
              </button>
            ))}
          </div>
          <button
            type="button"
            onClick={() => void fetchData()}
            className={adminHeaderGhostBtn}
            title={t('agentTokenStats.refresh')}
          >
            <RefreshCw size={15} className={loading ? 'animate-spin' : ''} />
          </button>
        </div>
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto p-4 space-y-4">
        {loading && !data ? (
          <div className="h-64 flex items-center justify-center">
            <OpenSquadLoader size={32} />
          </div>
        ) : error ? (
          <div className="bg-rose-500/5 border border-rose-500/20 rounded-xl p-6 flex flex-col items-center text-center gap-2">
            <AlertCircle className="text-rose-500" size={26} />
            <p className="text-sm text-textMain">{error}</p>
            <button type="button" onClick={() => void fetchData()} className="text-xs font-semibold text-primary hover:underline">
              {t('agentTokenStats.retry')}
            </button>
          </div>
        ) : (
          <>
            <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-3">
              <StatCard label={t('agentTokenStats.cardInput')} value={formatTokenCount(summary?.input ?? 0)} />
              <StatCard
                label={t('agentTokenStats.cardCacheHit')}
                value={formatTokenCount(summary?.cache_read ?? 0)}
                accent={SERIES[0].color}
              />
              <StatCard
                label={t('agentTokenStats.cardCacheMiss')}
                value={formatTokenCount(summary?.cache_miss ?? 0)}
                accent={SERIES[1].color}
              />
              <StatCard
                label={t('agentTokenStats.cardOutput')}
                value={formatTokenCount(summary?.output ?? 0)}
                accent={SERIES[2].color}
              />
              <StatCard label={t('agentTokenStats.cardTotal')} value={formatTokenCount(summary?.total ?? 0)} />
              <StatCard label={t('agentTokenStats.cardRequests')} value={(summary?.requests ?? 0).toLocaleString('en-US')} />
            </div>

            <div className="bg-panel border border-border rounded-xl p-4">
              <div className="flex items-center justify-between gap-3 mb-3">
                <span className="text-sm font-semibold text-textMain">{t('agentTokenStats.sectionTimeline')}</span>
                <select
                  value={model}
                  onChange={(e) => setModel(e.target.value)}
                  className="bg-background border border-border text-textMain text-xs rounded-md px-2 py-1 max-w-[220px]"
                  title={t('agentTokenStats.modelFilter')}
                >
                  <option value="">{t('agentTokenStats.modelFilterAll')}</option>
                  {(data?.models ?? []).map((m) => (
                    <option key={m} value={m}>
                      {m}
                    </option>
                  ))}
                </select>
              </div>

              {!hasData ? (
                <div className="h-56 flex flex-col items-center justify-center gap-2 text-textMuted">
                  <BarChart3 size={26} className="opacity-40" />
                  <p className="text-sm">{t('agentTokenStats.empty')}</p>
                </div>
              ) : (
                <>
                  <div className="h-72 w-full">
                    <ResponsiveContainer width="100%" height="100%">
                      <BarChart data={timeline} margin={{ top: 8, right: 8, bottom: 0, left: 8 }}>
                        <CartesianGrid strokeDasharray="3 3" stroke="currentColor" className="text-border" vertical={false} />
                        <XAxis
                          dataKey="bucket"
                          tickFormatter={formatBucket}
                          tick={{ fontSize: 10 }}
                          tickLine={false}
                          axisLine={false}
                          minTickGap={24}
                        />
                        <YAxis
                          tickFormatter={formatTokenCount}
                          tick={{ fontSize: 10 }}
                          tickLine={false}
                          axisLine={false}
                          width={44}
                        />
                        <Tooltip cursor={{ fill: 'currentColor', fillOpacity: 0.06 }} content={<StatsTooltip labels={seriesLabels} />} />
                        {SERIES.map((s) => (
                          <Bar key={s.key} dataKey={s.key} stackId="tokens" fill={s.color} name={seriesLabels[s.key]} />
                        ))}
                      </BarChart>
                    </ResponsiveContainer>
                  </div>

                  <div className="flex flex-wrap items-center gap-x-4 gap-y-2 mt-3 pt-3 border-t border-border/60">
                    {SERIES.map((s) => (
                      <div key={s.key} className="flex items-center gap-1.5 text-xs">
                        <span className="inline-block w-2.5 h-2.5 rounded-sm" style={{ background: s.color }} />
                        <span className="text-textMuted">{seriesLabels[s.key]}</span>
                      </div>
                    ))}
                  </div>
                </>
              )}
            </div>

            <div className="bg-panel border border-border rounded-xl p-4">
              <div className="text-sm font-semibold text-textMain mb-3">{t('agentTokenStats.sectionByModel')}</div>
              {byModel.length === 0 ? (
                <p className="text-sm text-textMuted py-6 text-center">{t('agentTokenStats.empty')}</p>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-xs tabular-nums">
                    <thead>
                      <tr className="text-textMuted/70 text-left">
                        <th className="font-medium py-1.5 pr-3">{t('agentTokenStats.colModel')}</th>
                        <th className="font-medium py-1.5 px-3 text-right">{t('agentTokenStats.cardInput')}</th>
                        <th className="font-medium py-1.5 px-3 text-right">{t('agentTokenStats.cardCacheHit')}</th>
                        <th className="font-medium py-1.5 px-3 text-right">{t('agentTokenStats.cardCacheMiss')}</th>
                        <th className="font-medium py-1.5 px-3 text-right">{t('agentTokenStats.cardOutput')}</th>
                        <th className="font-medium py-1.5 px-3 text-right">{t('agentTokenStats.cardTotal')}</th>
                        <th className="font-medium py-1.5 pl-3 text-right">{t('agentTokenStats.cardRequests')}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {byModel.map((row) => (
                        <tr key={row.model} className="border-t border-border/50 text-textMain">
                          <td className="py-1.5 pr-3 max-w-[260px] truncate" title={row.model}>
                            {row.model}
                          </td>
                          <td className="py-1.5 px-3 text-right">{formatTokenExact(row.input)}</td>
                          <td className="py-1.5 px-3 text-right">{formatTokenExact(row.cache_read)}</td>
                          <td className="py-1.5 px-3 text-right">{formatTokenExact(row.cache_miss)}</td>
                          <td className="py-1.5 px-3 text-right">{formatTokenExact(row.output)}</td>
                          <td className="py-1.5 px-3 text-right font-medium">{formatTokenExact(row.total)}</td>
                          <td className="py-1.5 pl-3 text-right text-textMuted">{row.requests.toLocaleString('en-US')}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
};

export default AgentTokenStatsPage;
