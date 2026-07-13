import React, { useCallback, useEffect, useState } from 'react';
import {
  ArrowLeft, RefreshCw, Loader2, AlertCircle, GraduationCap,
  Play, Download, Upload, Settings2, BookOpen, History, ToggleLeft,
} from 'lucide-react';
import { pluginAPI } from '../../../services/api';

interface AgentItem {
  agent_id: string;
  agent_name: string;
  dir_name: string;
}

interface CorpusItem {
  id: string;
  session_id?: string;
  session_title?: string;
  created_at?: string;
  source?: string;
  summary?: string;
  learned_by?: string | null;
}

interface RunWrite {
  target?: string;
  content?: string;
  evidence_refs?: string[];
  at?: string;
  section?: string;
  topic?: string;
  raw?: string;
}

interface CorpusDetail {
  id?: string;
  session_id?: string;
  session_title?: string;
  created_at?: string;
  source?: string;
  summary?: string;
  learned_by?: string | null;
  learned_at?: string;
  missing?: boolean;
}

interface RunItem {
  id: string;
  status?: string;
  trigger?: string;
  created_at?: string;
  finished_at?: string;
  summary?: string;
  error?: string | null;
  corpus_ids?: string[];
  writes?: RunWrite[];
  corpus_items?: CorpusDetail[];
}

type Tab = 'overview' | 'corpus' | 'runs' | 'pipeline' | 'settings';

const SelfLearnPanel: React.FC<{ onBack: () => void }> = ({ onBack }) => {
  const [agents, setAgents] = useState<AgentItem[]>([]);
  const [agentId, setAgentId] = useState('');
  const [tab, setTab] = useState<Tab>('overview');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [meta, setMeta] = useState<Record<string, any>>({});
  const [stats, setStats] = useState<Record<string, number>>({});
  const [corpus, setCorpus] = useState<CorpusItem[]>([]);
  const [runs, setRuns] = useState<RunItem[]>([]);
  const [pipeline, setPipeline] = useState<Record<string, any>>({});
  const [unlearnedOnly, setUnlearnedOnly] = useState(true);
  const [busy, setBusy] = useState(false);
  const [allowAgentMd, setAllowAgentMd] = useState(false);
  const [allowReminder, setAllowReminder] = useState(false);
  const [expandedCorpus, setExpandedCorpus] = useState<string | null>(null);
  const [expandedRun, setExpandedRun] = useState<string | null>(null);
  const [runDetail, setRunDetail] = useState<RunItem | null>(null);
  const [runDetailLoading, setRunDetailLoading] = useState(false);

  const load = useCallback(async (aid?: string, t?: Tab) => {
    setLoading(true);
    setError(null);
    try {
      const params: Record<string, string> = {
        tab: t || tab,
        unlearned_only: unlearnedOnly ? 'true' : 'false',
      };
      if (aid || agentId) params.agent_id = aid || agentId;
      const data = await pluginAPI.getPluginData('self_learn', params);
      setAgents(data.agents || []);
      const nextId = data.agent_id || aid || agentId || data.agents?.[0]?.agent_id || '';
      setAgentId(nextId);
      setMeta(data.meta || {});
      setStats(data.stats || {});
      setCorpus(data.corpus?.items || []);
      setRuns(data.runs?.items || []);
      setPipeline(data.pipeline || {});
      if (data.pipeline?.gates) {
        setAllowAgentMd(!!data.pipeline.gates.allow_agent_md);
        setAllowReminder(!!data.pipeline.gates.allow_reminder);
      }
      if (data.error) setError(String(data.error));
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setLoading(false);
    }
  }, [agentId, tab, unlearnedOnly]);

  useEffect(() => {
    void load();
  }, []);

  useEffect(() => {
    if (agentId) void load(agentId, tab);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, unlearnedOnly]);

  const startLearn = async (force = false) => {
    if (!agentId) return;
    setBusy(true);
    setError(null);
    setInfo(null);
    try {
      const res = await pluginAPI.pluginAction('self_learn', 'start_learn', {
        agent_id: agentId,
        force,
        allow_agent_md: allowAgentMd,
        allow_reminder: allowReminder,
      });
      if (!res?.ok) setError(res?.error || 'start_failed');
      else if (res?.queued) {
        setInfo(`Queued (#${res.request_id || '?'}). Keep the agent running — it picks up within ~15s. Then refresh Runs.`);
      }
      await load(agentId, 'runs');
      setTab('runs');
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setBusy(false);
    }
  };

  const saveMeta = async (patch: Record<string, any>) => {
    if (!agentId) return;
    setBusy(true);
    try {
      const res = await pluginAPI.pluginAction('self_learn', 'update_meta', {
        agent_id: agentId,
        ...patch,
      });
      if (res?.meta) setMeta(res.meta);
      else await load(agentId, 'settings');
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setBusy(false);
    }
  };

  const savePipelineGates = async () => {
    if (!agentId) return;
    setBusy(true);
    try {
      const next = {
        ...pipeline,
        gates: {
          ...(pipeline.gates || {}),
          allow_agent_md: allowAgentMd,
          allow_reminder: allowReminder,
        },
      };
      const res = await pluginAPI.pluginAction('self_learn', 'save_pipeline', {
        agent_id: agentId,
        pipeline: next,
      });
      if (res?.pipeline) setPipeline(res.pipeline);
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setBusy(false);
    }
  };

  const openRunDetail = async (runId: string) => {
    if (expandedRun === runId) {
      setExpandedRun(null);
      setRunDetail(null);
      return;
    }
    setExpandedRun(runId);
    setRunDetailLoading(true);
    setRunDetail(null);
    try {
      const res = await pluginAPI.pluginAction('self_learn', 'get_run', {
        agent_id: agentId,
        run_id: runId,
      });
      if (res?.ok && res.run) setRunDetail(res.run as RunItem);
      else setError(res?.error || 'get_run_failed');
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setRunDetailLoading(false);
    }
  };

  const targetLabel = (t?: string) => {
    const v = (t || 'other').toLowerCase();
    if (v === 'memory') return '长期记忆 (memory)';
    if (v === 'agent.md' || v === 'agent_md') return 'Agent 档案 (agent.md)';
    if (v === 'reminder') return '提醒 (reminder)';
    return t || 'other';
  };

  const doExport = async () => {
    if (!agentId) return;
    setBusy(true);
    try {
      const res = await pluginAPI.pluginAction('self_learn', 'export', {
        agent_id: agentId,
        include_agent_md: true,
      });
      if (!res?.ok || !res.content_base64) {
        setError(res?.error || 'export_failed');
        return;
      }
      const bin = atob(res.content_base64);
      const bytes = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
      const blob = new Blob([bytes], { type: 'application/zip' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = res.filename || 'self_learn_export.zip';
      a.click();
      URL.revokeObjectURL(url);
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setBusy(false);
    }
  };

  const doImport = async (file: File) => {
    if (!agentId) return;
    setBusy(true);
    try {
      const buf = await file.arrayBuffer();
      const bytes = new Uint8Array(buf);
      let binary = '';
      for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
      const b64 = btoa(binary);
      const res = await pluginAPI.pluginAction('self_learn', 'import', {
        agent_id: agentId,
        content_base64: b64,
        dry_run: false,
      });
      if (!res?.ok) setError(res?.error || 'import_failed');
      await load(agentId, tab);
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setBusy(false);
    }
  };

  const tabs: { id: Tab; label: string; icon: React.ReactNode }[] = [
    { id: 'overview', label: 'Overview', icon: <GraduationCap size={14} /> },
    { id: 'corpus', label: 'Corpus', icon: <BookOpen size={14} /> },
    { id: 'runs', label: 'Runs', icon: <History size={14} /> },
    { id: 'pipeline', label: 'Pipeline', icon: <Settings2 size={14} /> },
    { id: 'settings', label: 'Triggers', icon: <ToggleLeft size={14} /> },
  ];

  return (
    <div className="h-full flex flex-col bg-bg text-textMain">
      <div className="flex items-center gap-2 px-4 py-3 border-b border-border">
        <button type="button" onClick={onBack} className="p-1.5 rounded-lg hover:bg-black/5 dark:hover:bg-white/10">
          <ArrowLeft size={16} />
        </button>
        <GraduationCap size={18} className="text-primary" />
        <div className="font-semibold text-sm flex-1">Self Learn</div>
        <select
          className="text-xs bg-panel border border-border rounded-lg px-2 py-1"
          value={agentId}
          onChange={(e) => {
            setAgentId(e.target.value);
            void load(e.target.value, tab);
          }}
        >
          {agents.map((a) => (
            <option key={a.agent_id} value={a.agent_id}>{a.agent_name || a.agent_id}</option>
          ))}
        </select>
        <button type="button" onClick={() => load(agentId, tab)} className="p-1.5 rounded-lg hover:bg-black/5 dark:hover:bg-white/10" title="Refresh">
          {loading ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
        </button>
      </div>

      <div className="flex gap-1 px-3 pt-2 border-b border-border overflow-x-auto">
        {tabs.map((t) => (
          <button
            key={t.id}
            type="button"
            onClick={() => setTab(t.id)}
            className={`flex items-center gap-1.5 px-3 py-2 text-xs rounded-t-lg border-b-2 ${
              tab === t.id ? 'border-primary text-primary' : 'border-transparent text-textMuted hover:text-textMain'
            }`}
          >
            {t.icon}
            {t.label}
          </button>
        ))}
      </div>

      {error && (
        <div className="mx-4 mt-3 flex items-center gap-2 text-xs text-red-500 bg-red-500/10 rounded-lg px-3 py-2">
          <AlertCircle size={14} />
          <span className="flex-1">{error}</span>
          <button type="button" onClick={() => setError(null)} className="underline">dismiss</button>
        </div>
      )}
      {info && (
        <div className="mx-4 mt-3 flex items-center gap-2 text-xs text-emerald-600 bg-emerald-500/10 rounded-lg px-3 py-2">
          <span className="flex-1">{info}</span>
          <button type="button" onClick={() => setInfo(null)} className="underline">dismiss</button>
        </div>
      )}

      <div className="flex-1 overflow-auto p-4 space-y-4">
        {tab === 'overview' && (
          <>
            <div className="grid grid-cols-3 gap-3">
              <div className="rounded-xl border border-border bg-panel p-3">
                <div className="text-[10px] uppercase text-textMuted">Corpus</div>
                <div className="text-xl font-semibold">{stats.corpus_total ?? 0}</div>
              </div>
              <div className="rounded-xl border border-border bg-panel p-3">
                <div className="text-[10px] uppercase text-textMuted">Unlearned</div>
                <div className="text-xl font-semibold text-amber-500">{stats.corpus_unlearned ?? 0}</div>
              </div>
              <div className="rounded-xl border border-border bg-panel p-3">
                <div className="text-[10px] uppercase text-textMuted">Runs</div>
                <div className="text-xl font-semibold">{stats.runs_total ?? 0}</div>
              </div>
            </div>
            <div className="rounded-xl border border-border bg-panel p-4 space-y-3">
              <div className="text-sm font-medium">Start learning</div>
              <div className="flex flex-wrap gap-3 text-xs text-textMuted">
                <label className="flex items-center gap-1.5">
                  <input type="checkbox" checked={allowAgentMd} onChange={(e) => setAllowAgentMd(e.target.checked)} />
                  Allow agent.md
                </label>
                <label className="flex items-center gap-1.5">
                  <input type="checkbox" checked={allowReminder} onChange={(e) => setAllowReminder(e.target.checked)} />
                  Allow reminder
                </label>
              </div>
              <div className="flex gap-2">
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => startLearn(false)}
                  className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg bg-primary text-white text-xs font-medium disabled:opacity-50"
                >
                  {busy ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />}
                  Learn now
                </button>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => startLearn(true)}
                  className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg border border-border text-xs disabled:opacity-50"
                >
                  Force (ignore busy/cooldown)
                </button>
              </div>
              <div className="text-[11px] text-textMuted">
                Last learn: {meta.last_learn_at || 'never'} · Last user activity: {meta.last_user_activity_at || '—'}
              </div>
              <div className="text-[11px] text-textMuted rounded-lg bg-black/[0.03] dark:bg-white/[0.04] px-3 py-2">
                While a run is active, open the agent chat: a <b>Self-Learn</b> fold appears in the workflow
                (same as sub-agent / delegate window). Click it to see thoughts, tools, and the final summary.
              </div>
            </div>
            <div className="flex gap-2">
              <button type="button" onClick={doExport} disabled={busy} className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg border border-border text-xs">
                <Download size={14} /> Export zip
              </button>
              <label className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg border border-border text-xs cursor-pointer">
                <Upload size={14} /> Import zip
                <input
                  type="file"
                  accept=".zip,application/zip"
                  className="hidden"
                  onChange={(e) => {
                    const f = e.target.files?.[0];
                    if (f) void doImport(f);
                    e.target.value = '';
                  }}
                />
              </label>
            </div>
          </>
        )}

        {tab === 'corpus' && (
          <>
            <label className="flex items-center gap-2 text-xs text-textMuted">
              <input type="checkbox" checked={unlearnedOnly} onChange={(e) => setUnlearnedOnly(e.target.checked)} />
              Unlearned only
            </label>
            <div className="space-y-2">
              {corpus.length === 0 && <div className="text-sm text-textMuted">No corpus entries.</div>}
              {corpus.map((c) => (
                <button
                  key={c.id}
                  type="button"
                  onClick={() => setExpandedCorpus(expandedCorpus === c.id ? null : c.id)}
                  className="w-full text-left rounded-xl border border-border bg-panel p-3 hover:border-primary/40"
                >
                  <div className="flex items-center gap-2 text-xs text-textMuted mb-1">
                    <span className="font-mono">{c.id}</span>
                    <span>·</span>
                    <span>{c.source || 'compress'}</span>
                    <span>·</span>
                    <span>{c.created_at}</span>
                    {c.learned_by ? <span className="text-emerald-500">learned</span> : <span className="text-amber-500">pending</span>}
                  </div>
                  <div className="text-sm font-medium truncate">{c.session_title || c.session_id || 'session'}</div>
                  {expandedCorpus === c.id && (
                    <pre className="mt-2 text-[11px] whitespace-pre-wrap text-textMuted max-h-64 overflow-auto">{c.summary}</pre>
                  )}
                </button>
              ))}
            </div>
          </>
        )}

        {tab === 'runs' && (
          <div className="space-y-2">
            <div className="text-[11px] text-textMuted rounded-lg bg-black/[0.03] dark:bg-white/[0.04] px-3 py-2">
              点击某次 Run 展开学习详情：来源语料 → 学到了什么 → 写入到哪里（memory / agent.md / reminder）。
            </div>
            {runs.length === 0 && <div className="text-sm text-textMuted">No learn runs yet.</div>}
            {runs.map((r) => {
              const open = expandedRun === r.id;
              const detail = open ? runDetail : null;
              const writes = detail?.writes || r.writes || [];
              const corpusItems = detail?.corpus_items || [];
              const corpusIds = detail?.corpus_ids || r.corpus_ids || [];
              return (
                <div key={r.id} className="rounded-xl border border-border bg-panel overflow-hidden">
                  <button
                    type="button"
                    onClick={() => void openRunDetail(r.id)}
                    className="w-full text-left p-3 hover:bg-black/[0.02] dark:hover:bg-white/[0.03]"
                  >
                    <div className="flex items-center gap-2 text-xs mb-1">
                      <span className="font-mono text-textMuted">{r.id}</span>
                      <span className={`px-1.5 py-0.5 rounded ${
                        r.status === 'done' ? 'bg-emerald-500/10 text-emerald-500'
                          : r.status === 'running' ? 'bg-blue-500/10 text-blue-500'
                            : r.status === 'error' ? 'bg-red-500/10 text-red-500'
                              : 'bg-gray-500/10 text-textMuted'
                      }`}>{r.status}</span>
                      <span className="text-textMuted">{r.trigger}</span>
                      {(r.corpus_ids?.length || 0) > 0 && (
                        <span className="text-textMuted">{r.corpus_ids!.length} corpus</span>
                      )}
                      {(r.writes?.length || 0) > 0 && (
                        <span className="text-textMuted">{r.writes!.length} writes</span>
                      )}
                      <span className="text-textMuted ml-auto">{r.created_at}</span>
                    </div>
                    {r.summary && <div className="text-xs text-textMuted whitespace-pre-wrap line-clamp-3">{r.summary}</div>}
                    {r.error && <div className="text-xs text-red-500 mt-1">{r.error}</div>}
                  </button>
                  {open && (
                    <div className="border-t border-border px-3 py-3 space-y-3 bg-black/[0.015] dark:bg-white/[0.02]">
                      {runDetailLoading && (
                        <div className="flex items-center gap-2 text-xs text-textMuted">
                          <Loader2 size={12} className="animate-spin" /> Loading detail…
                        </div>
                      )}
                      {!runDetailLoading && (
                        <>
                          <div>
                            <div className="text-[11px] font-semibold uppercase tracking-wide text-textMuted mb-1">Summary</div>
                            <div className="text-xs whitespace-pre-wrap">{detail?.summary || r.summary || '—'}</div>
                          </div>

                          <div>
                            <div className="text-[11px] font-semibold uppercase tracking-wide text-textMuted mb-1">
                              来源语料 ({corpusItems.length || corpusIds.length})
                            </div>
                            {corpusItems.length === 0 && corpusIds.length === 0 && (
                              <div className="text-xs text-textMuted">本次未记录 corpus_ids（子智能体可能未调用 mark_learned）。</div>
                            )}
                            {corpusItems.length === 0 && corpusIds.length > 0 && (
                              <div className="text-xs font-mono text-textMuted">{corpusIds.join(', ')}</div>
                            )}
                            <div className="space-y-2">
                                  {corpusItems.map((c) => (
                                <div key={c.id || 'missing'} className="rounded-lg border border-border/70 p-2">
                                  <div className="flex flex-wrap gap-2 text-[11px] text-textMuted mb-1">
                                    <span className="font-mono">{c.id}</span>
                                    {c.source && <span>· {c.source}</span>}
                                    {c.session_title && <span>· {c.session_title}</span>}
                                    {c.missing && <span className="text-amber-500">missing</span>}
                                  </div>
                                  {c.summary && (
                                    <pre className="text-[11px] whitespace-pre-wrap text-textMuted max-h-40 overflow-auto">{c.summary}</pre>
                                  )}
                                </div>
                              ))}
                            </div>
                          </div>

                          <div>
                            <div className="text-[11px] font-semibold uppercase tracking-wide text-textMuted mb-1">
                              写入去向 ({writes.length})
                            </div>
                            {writes.length === 0 ? (
                              <div className="text-xs text-amber-600 dark:text-amber-400">
                                暂无结构化写入记录。旧 Run 或只 mark 语料未调用 memory_write / finish_run(writes_json) 时会出现空列表。
                                新一次 Learn now 后这里会显示 memory / agent.md / reminder。
                              </div>
                            ) : (
                              <div className="space-y-2">
                                {writes.map((w, idx) => (
                                  <div key={`${w.target}-${idx}`} className="rounded-lg border border-border/70 p-2">
                                    <div className="text-xs font-medium mb-1">{targetLabel(w.target)}</div>
                                    <div className="text-xs whitespace-pre-wrap">{w.content || w.raw || '—'}</div>
                                    {(w.evidence_refs?.length || 0) > 0 && (
                                      <div className="mt-1 text-[11px] text-textMuted font-mono">
                                        evidence: {w.evidence_refs!.join(', ')}
                                      </div>
                                    )}
                                  </div>
                                ))}
                              </div>
                            )}
                          </div>
                        </>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}

        {tab === 'pipeline' && (
          <div className="space-y-3">
            <div className="text-sm font-medium">{pipeline.name || 'default'} pipeline</div>
            <div className="space-y-2">
              {(pipeline.steps || []).map((s: any, idx: number) => (
                <div key={s.id || idx} className="rounded-xl border border-border bg-panel p-3">
                  <div className="text-xs font-semibold mb-1">{idx + 1}. {s.title || s.id}</div>
                  <div className="text-[11px] text-textMuted whitespace-pre-wrap">{s.instruction}</div>
                </div>
              ))}
            </div>
            <div className="rounded-xl border border-border bg-panel p-3 space-y-2">
              <div className="text-xs font-semibold">Gates</div>
              <label className="flex items-center gap-2 text-xs">
                <input type="checkbox" checked={allowAgentMd} onChange={(e) => setAllowAgentMd(e.target.checked)} />
                allow_agent_md
              </label>
              <label className="flex items-center gap-2 text-xs">
                <input type="checkbox" checked={allowReminder} onChange={(e) => setAllowReminder(e.target.checked)} />
                allow_reminder
              </label>
              <button type="button" disabled={busy} onClick={savePipelineGates} className="px-3 py-1.5 rounded-lg bg-primary text-white text-xs disabled:opacity-50">
                Save gates
              </button>
            </div>
          </div>
        )}

        {tab === 'settings' && (
          <div className="rounded-xl border border-border bg-panel p-4 space-y-3 max-w-md">
            <label className="flex items-center justify-between text-sm">
              <span>Idle auto-learn</span>
              <input
                type="checkbox"
                checked={!!meta.idle_auto_enabled}
                onChange={(e) => saveMeta({ idle_auto_enabled: e.target.checked })}
              />
            </label>
            <label className="block text-sm">
              <span className="text-textMuted text-xs">Idle minutes</span>
              <input
                type="number"
                className="mt-1 w-full rounded-lg border border-border bg-transparent px-2 py-1.5 text-sm"
                value={Number(meta.idle_minutes ?? 30)}
                onChange={(e) => setMeta((m) => ({ ...m, idle_minutes: Number(e.target.value) }))}
                onBlur={() => saveMeta({ idle_minutes: Number(meta.idle_minutes ?? 30) })}
              />
            </label>
            <label className="block text-sm">
              <span className="text-textMuted text-xs">Cooldown hours</span>
              <input
                type="number"
                className="mt-1 w-full rounded-lg border border-border bg-transparent px-2 py-1.5 text-sm"
                value={Number(meta.cooldown_hours ?? 24)}
                onChange={(e) => setMeta((m) => ({ ...m, cooldown_hours: Number(e.target.value) }))}
                onBlur={() => saveMeta({ cooldown_hours: Number(meta.cooldown_hours ?? 24) })}
              />
            </label>
            <label className="block text-sm">
              <span className="text-textMuted text-xs">Interval hours (0=off)</span>
              <input
                type="number"
                className="mt-1 w-full rounded-lg border border-border bg-transparent px-2 py-1.5 text-sm"
                value={Number(meta.interval_hours ?? 0)}
                onChange={(e) => setMeta((m) => ({ ...m, interval_hours: Number(e.target.value) }))}
                onBlur={() => saveMeta({ interval_hours: Number(meta.interval_hours ?? 0) })}
              />
            </label>
          </div>
        )}
      </div>
    </div>
  );
};

export default SelfLearnPanel;

import { createRoot } from 'react-dom/client';

let root: ReturnType<typeof createRoot> | null = null;

export function mount(el: HTMLElement, props: { onBack: () => void }) {
  root = createRoot(el);
  root.render(<SelfLearnPanel onBack={props.onBack} />);
}

export function unmount(_el: HTMLElement) {
  if (root) {
    root.unmount();
    root = null;
  }
}
