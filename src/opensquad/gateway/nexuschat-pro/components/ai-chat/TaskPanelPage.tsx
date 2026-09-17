/**
 * TaskPanelPage — M2 parallel task scheduler panel.
 * Rendered as an L2 "tasks" content tab in the middle pane.
 *
 * Desktop: list | detail side-by-side.
 * Mobile (≤767px): master–detail — list OR detail fills the viewport.
 *
 * Lists tasks from the in-agent TaskScheduler (`/api/tasks`), submits new
 * parallel tasks (optionally isolated in an M1 git worktree), and streams
 * live status via `task_update` / `task_removed` websocket events. Tasks
 * that ran in a worktree expose a change report with merge / discard
 * decisions (M1 surface).
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  ArrowLeft, Check, CheckCircle2, ChevronRight, CircleDashed, GitMerge,
  ListTodo, ListTree, Loader2, PauseCircle, Plus, RotateCcw, Target, Trash2, X, XCircle,
} from 'lucide-react';
import {
  taskAPI,
  type ParallelTask,
  type TaskMilestone,
  type WorktreeReport,
} from '../../services/api';
import { OpenSquadLoader } from '../OpenSquadLoader';
import { getAiWsService, type AIWSMessage } from '../../services/aiWebSocket';
import { useIsMobileViewport } from '../../hooks/useMatchMedia';
import { normalizeTaskPlan } from '../../utils/taskPlan';
import { openSessionTab } from '../../utils/uiEvents';

interface Props {
  agentName: string;
  rootPath: string;
}

// A task that holds no running turn. 'blocked' belongs here: it is parked
// (resumable), not running — so it must not keep the fast poll cadence alive.
const TERMINAL = new Set(['done', 'failed', 'aborted', 'interrupted', 'blocked']);

/** An M3 goal is parked on its budget when it can be continued. */
export const isResumableGoal = (task: Pick<ParallelTask, 'kind' | 'status'>): boolean =>
  task.kind === 'goal' && (task.status === 'blocked' || task.status === 'interrupted');

export interface TaskFormState {
  title: string;
  prompt: string;
  use_worktree: boolean;
  kind: 'task' | 'goal';
  /** One milestone per line; blank lines are ignored. */
  milestones: string;
  maxTokens: string;
  maxSeconds: string;
  maxAttempts: string;
}

export const EMPTY_TASK_FORM: TaskFormState = {
  title: '', prompt: '', use_worktree: true, kind: 'task',
  milestones: '', maxTokens: '', maxSeconds: '', maxAttempts: '',
};

/**
 * The request body `buildSubmitPayload` produces. The goal-only fields are
 * optional so a plain task and a goal share one type — callers must treat their
 * absence as meaningful, not as "unset".
 */
export interface TaskSubmitPayload {
  title: string;
  prompt: string;
  use_worktree: boolean;
  kind?: 'goal';
  goal?: string;
  milestones?: string[];
  budget?: { max_tokens: number; max_seconds: number; max_attempts: number };
}

/**
 * Turn the form into a submit payload.
 *
 * Budget fields are strings in the UI (so the user can clear them); a blank or
 * unparseable value means "no limit" — 0 — rather than NaN, which the backend
 * would reject. `max_attempts` is the exception: 0 would mean "never retry",
 * so it falls back to the backend default of 2.
 */
export const buildSubmitPayload = (form: TaskFormState): TaskSubmitPayload => {
  const num = (raw: string): number => {
    const n = Number.parseInt(raw, 10);
    return Number.isFinite(n) && n > 0 ? n : 0;
  };
  const payload = {
    title: form.title.trim(),
    prompt: form.prompt.trim(),
    use_worktree: form.use_worktree,
  };
  if (form.kind !== 'goal') return payload;
  const milestones = form.milestones
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean);
  return {
    ...payload,
    kind: 'goal',
    goal: payload.prompt,
    milestones,
    budget: {
      max_tokens: num(form.maxTokens),
      max_seconds: num(form.maxSeconds),
      max_attempts: num(form.maxAttempts) || 2,
    },
  };
};

export const TaskPanelPage: React.FC<Props> = ({ agentName, rootPath }) => {
  const { t } = useTranslation();
  const isMobile = useIsMobileViewport();
  const [tasks, setTasks] = useState<ParallelTask[]>([]);
  const [selTaskId, setSelTaskId] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [form, setForm] = useState<TaskFormState>(EMPTY_TASK_FORM);
  const [mobileDetail, setMobileDetail] = useState(false);

  const reload = useCallback(async (opts?: { quiet?: boolean }) => {
    if (!opts?.quiet) setLoading(true);
    try {
      const tr = await taskAPI.list(agentName);
      const next = tr.tasks || [];
      // Keep identity when nothing changed so the list does not re-render every poll.
      setTasks((prev) => {
        if (prev.length !== next.length) return next;
        for (let i = 0; i < prev.length; i++) {
          const a = prev[i];
          const b = next[i];
          if (
            a.task_id !== b.task_id
            || a.status !== b.status
            || a.plan_done !== b.plan_done
            || a.plan_total !== b.plan_total
            || a.finished_at !== b.finished_at
            || a.error !== b.error
          ) {
            return next;
          }
        }
        return prev;
      });
    } catch {
      /* scheduler may be unavailable — keep last snapshot */
    } finally {
      if (!opts?.quiet) setLoading(false);
    }
  }, [agentName]);

  useEffect(() => { reload(); }, [reload]);

  // Live task lifecycle updates from the agent process (relayed by Gateway).
  useEffect(() => {
    if (!agentName) return;
    const ws = getAiWsService(agentName);
    ws.connect(agentName);
    const apply = (msg: AIWSMessage) => {
      const raw: any = msg.content ?? msg.data ?? msg;
      if (!raw || typeof raw !== 'object') return;
      const task: ParallelTask | undefined = raw.task;
      if (!task || !task.task_id) return;
      if (raw.type === 'task_removed') {
        setTasks((prev) => prev.filter((x) => x.task_id !== task.task_id));
        setSelTaskId((cur) => (cur === task.task_id ? null : cur));
        return;
      }
      setTasks((prev) => {
        const idx = prev.findIndex((x) => x.task_id === task.task_id);
        if (idx < 0) return [task, ...prev];
        const copy = prev.slice();
        copy[idx] = { ...copy[idx], ...task };
        return copy;
      });
    };
    const un1 = ws.on('task_update', apply);
    const un2 = ws.on('task_removed', apply);
    return () => { un1(); un2(); };
  }, [agentName]);

  // Slow fallback poll — WS task_update is the primary path.
  const watchingActive = tasks.some((x) => !TERMINAL.has(x.status));
  useEffect(() => {
    const ms = watchingActive ? 4000 : 15000;
    const id = setInterval(() => { void reload({ quiet: true }); }, ms);
    return () => clearInterval(id);
  }, [reload, watchingActive]);

  // Default to the newest task so detail is never an empty placeholder.
  useEffect(() => {
    if (tasks.length === 0) {
      if (selTaskId) setSelTaskId(null);
      return;
    }
    const stillValid = !!selTaskId && tasks.some((x) => x.task_id === selTaskId);
    if (!stillValid) setSelTaskId(tasks[0].task_id);
  }, [tasks, selTaskId]);

  const selected = useMemo(
    () => tasks.find((x) => x.task_id === selTaskId) || null,
    [tasks, selTaskId],
  );

  const startNew = () => {
    setForm(EMPTY_TASK_FORM);
    setShowForm(true);
    setMobileDetail(true);
  };

  const handleSubmit = async () => {
    if (!form.title.trim() || !form.prompt.trim() || submitting) return;
    setSubmitting(true);
    try {
      const resp = await taskAPI.submit({
        ...buildSubmitPayload(form),
        agent_id: agentName,
        base_dir: rootPath,
      });
      setShowForm(false);
      if (resp.task?.task_id) setSelTaskId(resp.task.task_id);
      await reload({ quiet: true });
    } catch (e: any) {
      alert(t('taskPanel.submitFailed') + ': ' + (e?.message || ''));
    } finally {
      setSubmitting(false);
    }
  };

  const handleAbort = async (task: ParallelTask) => {
    try {
      await taskAPI.abort(task.task_id, agentName);
      await reload({ quiet: true });
    } catch (e: any) {
      alert(t('taskPanel.actionFailed') + ': ' + (e?.message || ''));
    }
  };

  const handleApprove = async (task: ParallelTask, approved: boolean) => {
    try {
      await taskAPI.approve(task.task_id, approved, agentName);
      await reload({ quiet: true });
    } catch (e: any) {
      alert(t('taskPanel.actionFailed') + ': ' + (e?.message || ''));
    }
  };

  const handleRemove = async (task: ParallelTask) => {
    try {
      await taskAPI.remove(task.task_id, agentName);
      if (selTaskId === task.task_id) setSelTaskId(null);
      await reload({ quiet: true });
    } catch (e: any) {
      alert(t('taskPanel.actionFailed') + ': ' + (e?.message || ''));
    }
  };

  const handleResume = async (task: ParallelTask) => {
    try {
      await taskAPI.resume(task.task_id, agentName);
      await reload({ quiet: true });
    } catch (e: any) {
      alert(t('taskPanel.resumeFailed') + ': ' + (e?.message || ''));
    }
  };

  const showList = !isMobile || !mobileDetail;
  const showDetail = !isMobile || mobileDetail;

  return (
    <div className="flex h-full min-h-0">
      {/* Left: task list */}
      <div
        className={`${showList ? 'flex' : 'hidden'} ${
          isMobile ? 'w-full flex-1' : 'w-64 shrink-0'
        } flex-col min-h-0 border-r border-border bg-panel`}
      >
        <div className="px-3 py-3 border-b border-border/60 shrink-0">
          <div className="flex items-center gap-1.5 text-sm font-semibold">
            <ListTodo size={14} className="text-primary" />
            {t('taskPanel.title')}
          </div>
          <div className="mt-0.5 text-[10px] text-textMuted truncate" title={rootPath}>{rootPath}</div>
          <button
            type="button"
            onClick={startNew}
            className="mt-2 w-full inline-flex items-center justify-center gap-1.5 px-2 py-1.5 rounded-lg text-[12px] font-medium text-primary border border-primary/40 hover:bg-primary/10"
          >
            <Plus size={13} /> {t('taskPanel.newTask')}
          </button>
        </div>

        <div className="flex-1 min-h-0 overflow-y-auto px-1.5 py-2 space-y-1">
          {loading && tasks.length === 0 ? (
            <div className="px-2 py-3 text-[11px] text-textMuted flex items-center gap-1.5">
              <OpenSquadLoader size={16} /> {t('taskPanel.loading')}
            </div>
          ) : tasks.length === 0 ? (
            <EmptyHint text={t('taskPanel.empty')} />
          ) : (
            tasks.map((task) => (
              <TaskRow
                key={task.task_id}
                task={task}
                active={task.task_id === selTaskId && !showForm}
                onClick={() => { setSelTaskId(task.task_id); setShowForm(false); setMobileDetail(true); }}
              />
            ))
          )}
        </div>
      </div>

      {/* Right: detail / new-task form */}
      <div className={`${showDetail ? 'flex' : 'hidden'} flex-1 min-w-0 min-h-0 flex-col`}>
        {isMobile && (
          <div className="shrink-0 flex items-center gap-2 px-3 py-2 border-b border-border bg-panel">
            <button
              type="button"
              onClick={() => { setMobileDetail(false); setShowForm(false); }}
              className="inline-flex items-center gap-1 px-2 py-1.5 rounded-lg text-[12px] font-medium text-textMuted hover:text-text hover:bg-black/[0.04] dark:hover:bg-white/[0.06]"
            >
              <ArrowLeft size={14} />
              {t('taskPanel.backToList')}
            </button>
          </div>
        )}
        {showForm ? (
          <TaskSubmitForm
            form={form}
            submitting={submitting}
            onChange={setForm}
            onCancel={() => { setShowForm(false); if (isMobile) setMobileDetail(false); }}
            onSubmit={handleSubmit}
          />
        ) : selected ? (
          <TaskDetail
            task={selected}
            repo={rootPath}
            onAbort={() => handleAbort(selected)}
            onApprove={(ok) => handleApprove(selected, ok)}
            onRemove={() => handleRemove(selected)}
            onResume={() => handleResume(selected)}
          />
        ) : (
          <div className="flex-1 flex items-center justify-center text-[12px] text-textMuted px-6 text-center">
            {t('taskPanel.selectTask')}
          </div>
        )}
      </div>
    </div>
  );
};

const EmptyHint: React.FC<{ text: string }> = ({ text }) => (
  <div className="px-2 py-3 text-[11px] text-textMuted/70">{text}</div>
);

const STATUS_META: Record<string, { c: string; Icon: React.FC<any> }> = {
  queued: { c: 'bg-black/10 text-textMuted', Icon: CircleDashed },
  running: { c: 'bg-primary/15 text-primary', Icon: Loader2 },
  waiting_approval: { c: 'bg-amber-500/15 text-amber-600', Icon: CircleDashed },
  // Parked on budget / an unverified milestone — resumable, unlike 'failed'.
  blocked: { c: 'bg-violet-500/15 text-violet-600', Icon: PauseCircle },
  done: { c: 'bg-emerald-500/15 text-emerald-600', Icon: CheckCircle2 },
  failed: { c: 'bg-rose-500/15 text-rose-600', Icon: XCircle },
  aborted: { c: 'bg-black/10 text-textMuted', Icon: XCircle },
  interrupted: { c: 'bg-amber-500/15 text-amber-600', Icon: CircleDashed },
};

const MILESTONE_META: Record<string, { c: string }> = {
  pending: { c: 'text-textMuted border-border' },
  running: { c: 'text-primary border-primary/40' },
  done: { c: 'text-emerald-600 border-emerald-500/40' },
  blocked: { c: 'text-rose-600 border-rose-500/40' },
};

const TaskStatusBadge: React.FC<{ status: string }> = ({ status }) => {
  const m = STATUS_META[status] || STATUS_META.queued;
  const Icon = m.Icon;
  return (
    <span className={`inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[9px] font-medium ${m.c}`}>
      <Icon size={10} className={status === 'running' ? 'animate-spin' : ''} />
      {status}
    </span>
  );
};

const fmtDateTime = (ts: number | null) => {
  if (!ts) return '--';
  const d = new Date(ts * 1000);
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
};

const fmtElapsed = (ms: number) => {
  if (!ms || ms <= 0) return '--';
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${s % 60}s`;
  return `${Math.floor(m / 60)}h ${m % 60}m`;
};

const TaskRow: React.FC<{
  task: ParallelTask;
  active: boolean;
  onClick: () => void;
}> = ({ task, active, onClick }) => (
  <div
    role="button"
    tabIndex={0}
    onClick={onClick}
    onKeyDown={(ev) => { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); onClick(); } }}
    className={`w-full text-left px-2 py-1.5 rounded-lg transition-colors cursor-pointer ${
      active ? 'bg-primary/10' : 'hover:bg-black/[0.04] dark:hover:bg-white/[0.05]'
    }`}
  >
    <div className="flex items-center justify-between gap-1">
      <span className="text-[11px] font-medium truncate">{task.title}</span>
      <TaskStatusBadge status={task.status} />
    </div>
    <div className="mt-0.5 flex items-center gap-1.5 text-[10px] text-textMuted">
      {task.kind === 'goal' && <Target size={10} className="shrink-0 text-violet-500" />}
      <span>{fmtDateTime(task.created_at)}</span>
      {task.plan_total > 0 && (
        <span className="shrink-0">{task.plan_done}/{task.plan_total}</span>
      )}
    </div>
    {task.plan_total > 0 && (
      <div className="mt-1 h-1 rounded-full bg-black/[0.06] dark:bg-white/[0.08] overflow-hidden">
        <div
          className="h-full rounded-full bg-primary transition-all"
          style={{ width: `${Math.min(100, Math.round((task.plan_done / task.plan_total) * 100))}%` }}
        />
      </div>
    )}
  </div>
);

/**
 * One read-only field. Pass `onClick` when the value is actionable (the task's
 * session id is the way into that task's tool flow) — a plain `<button>` in the
 * value slot keeps it keyboard-reachable without changing the grid.
 */
const InfoCell: React.FC<{
  label: string;
  value: React.ReactNode;
  onClick?: () => void;
  title?: string;
}> = ({ label, value, onClick, title }) => (
  <div className="space-y-0.5">
    <div className="text-[10px] text-textMuted">{label}</div>
    {onClick ? (
      <button
        type="button"
        onClick={onClick}
        title={title}
        className="block w-full text-left text-[11px] font-medium break-all text-primary hover:underline"
      >
        {value}
      </button>
    ) : (
      <div className="text-[11px] font-medium break-all">{value}</div>
    )}
  </div>
);

/** One M3 milestone: state, attempt count and whatever it produced. */
const MilestoneRow: React.FC<{ ms: TaskMilestone; index: number }> = ({ ms, index }) => {
  const { t } = useTranslation();
  const meta = MILESTONE_META[ms.status] || MILESTONE_META.pending;
  return (
    <div className="rounded-lg border border-border px-2.5 py-2 space-y-1">
      <div className="flex items-center gap-2">
        <span className="shrink-0 text-[10px] text-textMuted tabular-nums">{index + 1}.</span>
        <span className="flex-1 min-w-0 text-[11px] font-medium truncate" title={ms.title}>{ms.title}</span>
        {ms.attempts > 1 && (
          <span className="shrink-0 text-[10px] text-textMuted">
            {t('taskPanel.attempts')} {ms.attempts}
          </span>
        )}
        <span className={`shrink-0 px-1.5 py-0.5 rounded border text-[9px] font-medium ${meta.c}`}>
          {ms.status}
        </span>
      </div>
      {ms.verify && (
        <div className="text-[10px] text-textMuted truncate" title={ms.verify}>✓ {ms.verify}</div>
      )}
      {ms.error && (
        <div className="text-[10px] text-rose-600 whitespace-pre-wrap">{ms.error}</div>
      )}
    </div>
  );
};

const TaskSubmitForm: React.FC<{
  form: TaskFormState;
  submitting: boolean;
  onChange: (v: TaskFormState) => void;
  onCancel: () => void;
  onSubmit: () => void;
}> = ({ form, submitting, onChange, onCancel, onSubmit }) => {
  const { t } = useTranslation();
  const isGoal = form.kind === 'goal';
  return (
    <div className="flex flex-col h-full min-h-0">
      <div className="px-3 sm:px-4 py-3 border-b border-border shrink-0 flex items-center justify-between">
        <h3 className="text-sm font-semibold">{t('taskPanel.newTask')}</h3>
        <button type="button" onClick={onCancel} className="p-1 rounded-lg text-textMuted hover:text-text hover:bg-black/[0.04] dark:hover:bg-white/[0.06]">
          <X size={14} />
        </button>
      </div>
      <div className="flex-1 min-h-0 overflow-y-auto p-4 space-y-4 text-[12px]">
        <div>
          <label className="block text-[10px] font-bold text-textMuted uppercase mb-1">{t('taskPanel.fTitle')}</label>
          <input
            type="text"
            value={form.title}
            onChange={(e) => onChange({ ...form, title: e.target.value })}
            placeholder={t('taskPanel.fTitlePh')}
            className="w-full px-3 py-2 bg-bgLight border border-border rounded-lg focus:outline-none focus:ring-2 focus:ring-primary/50 text-[12px]"
          />
        </div>
        <div>
          <label className="block text-[10px] font-bold text-textMuted uppercase mb-1">
            {isGoal ? t('taskPanel.fGoal') : t('taskPanel.fPrompt')}
          </label>
          <textarea
            value={form.prompt}
            onChange={(e) => onChange({ ...form, prompt: e.target.value })}
            placeholder={isGoal ? t('taskPanel.fGoalPh') : t('taskPanel.fPromptPh')}
            rows={6}
            className="w-full px-3 py-2 bg-bgLight border border-border rounded-lg focus:outline-none focus:ring-2 focus:ring-primary/50 text-[12px] resize-y"
          />
        </div>

        {/* M3 — a goal runs its milestones one at a time under a budget. */}
        <div className="rounded-lg border border-border p-3 space-y-3">
          <label className="flex items-center gap-2 cursor-pointer select-none">
            <button
              type="button"
              role="switch"
              aria-checked={isGoal}
              onClick={() => onChange({ ...form, kind: isGoal ? 'task' : 'goal' })}
              className={`relative w-9 h-5 rounded-full transition-colors ${isGoal ? 'bg-violet-500' : 'bg-black/15 dark:bg-white/20'}`}
            >
              <span className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform ${isGoal ? 'translate-x-4' : ''}`} />
            </button>
            <span className="inline-flex items-center gap-1 text-[11px]">
              <Target size={12} className="text-violet-500" />
              {t('taskPanel.fGoalMode')}
            </span>
          </label>

          {isGoal && (
            <>
              <div>
                <label className="block text-[10px] font-bold text-textMuted uppercase mb-1">{t('taskPanel.fMilestones')}</label>
                <textarea
                  value={form.milestones}
                  onChange={(e) => onChange({ ...form, milestones: e.target.value })}
                  placeholder={t('taskPanel.fMilestonesPh')}
                  rows={4}
                  className="w-full px-3 py-2 bg-bgLight border border-border rounded-lg focus:outline-none focus:ring-2 focus:ring-primary/50 text-[12px] resize-y font-mono"
                />
                <div className="mt-1 text-[10px] text-textMuted">{t('taskPanel.fMilestonesHint')}</div>
              </div>
              <div className="grid grid-cols-3 gap-2">
                <div>
                  <label className="block text-[10px] font-bold text-textMuted uppercase mb-1">{t('taskPanel.fMaxTokens')}</label>
                  <input
                    type="number" min={0} inputMode="numeric"
                    value={form.maxTokens}
                    onChange={(e) => onChange({ ...form, maxTokens: e.target.value })}
                    placeholder={t('taskPanel.unlimited')}
                    className="w-full px-2 py-1.5 bg-bgLight border border-border rounded-lg focus:outline-none focus:ring-2 focus:ring-primary/50 text-[12px]"
                  />
                </div>
                <div>
                  <label className="block text-[10px] font-bold text-textMuted uppercase mb-1">{t('taskPanel.fMaxSeconds')}</label>
                  <input
                    type="number" min={0} inputMode="numeric"
                    value={form.maxSeconds}
                    onChange={(e) => onChange({ ...form, maxSeconds: e.target.value })}
                    placeholder={t('taskPanel.unlimited')}
                    className="w-full px-2 py-1.5 bg-bgLight border border-border rounded-lg focus:outline-none focus:ring-2 focus:ring-primary/50 text-[12px]"
                  />
                </div>
                <div>
                  <label className="block text-[10px] font-bold text-textMuted uppercase mb-1">{t('taskPanel.fMaxAttempts')}</label>
                  <input
                    type="number" min={1} inputMode="numeric"
                    value={form.maxAttempts}
                    onChange={(e) => onChange({ ...form, maxAttempts: e.target.value })}
                    placeholder="2"
                    className="w-full px-2 py-1.5 bg-bgLight border border-border rounded-lg focus:outline-none focus:ring-2 focus:ring-primary/50 text-[12px]"
                  />
                </div>
              </div>
            </>
          )}
        </div>

        <label className="flex items-center gap-2 cursor-pointer select-none">
          <button
            type="button"
            role="switch"
            aria-checked={form.use_worktree}
            onClick={() => onChange({ ...form, use_worktree: !form.use_worktree })}
            className={`relative w-9 h-5 rounded-full transition-colors ${form.use_worktree ? 'bg-primary' : 'bg-black/15 dark:bg-white/20'}`}
          >
            <span className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform ${form.use_worktree ? 'translate-x-4' : ''}`} />
          </button>
          <span className="text-[11px]">{t('taskPanel.fWorktree')}</span>
        </label>
      </div>
      <div className="px-4 py-3 border-t border-border shrink-0 flex items-center justify-end gap-2">
        <button
          type="button"
          onClick={onCancel}
          className="px-3 py-1.5 rounded-lg text-[11px] font-medium border border-border hover:bg-black/5 dark:hover:bg-white/10"
        >
          {t('common.cancel')}
        </button>
        <button
          type="button"
          disabled={submitting || !form.title.trim() || !form.prompt.trim()}
          onClick={onSubmit}
          className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg text-[11px] font-medium bg-primary text-white hover:bg-primary/90 disabled:opacity-40"
        >
          {submitting ? <OpenSquadLoader size={12} /> : <Check size={12} />}
          {t('taskPanel.submit')}
        </button>
      </div>
    </div>
  );
};

/**
 * Exported for the jsdom regression lock: a plain task ships `plan: {}`, and
 * reading a field off that placeholder is what used to blank the app. The test
 * mounts this component directly because no pure helper can catch a missing
 * null-check on a wire field.
 */
export const TaskDetail: React.FC<{
  task: ParallelTask;
  repo: string;
  onAbort: () => void;
  onApprove: (ok: boolean) => void;
  onRemove: () => void;
  onResume: () => void;
}> = ({ task, repo, onAbort, onApprove, onRemove, onResume }) => {
  const { t } = useTranslation();
  const [report, setReport] = useState<WorktreeReport | null>(null);
  const [reportLoading, setReportLoading] = useState(false);
  const [reportOpen, setReportOpen] = useState(false);
  const [mergeBusy, setMergeBusy] = useState(false);
  const [confirmingRemove, setConfirmingRemove] = useState(false);

  const isTerminal = TERMINAL.has(task.status);
  const hasWorktree = !!task.worktree_path;
  // Never dereference `task.plan` directly: a plain task sends the empty object,
  // and this is the read that produced
  // "Cannot read properties of undefined (reading 'max_tokens')".
  const plan = normalizeTaskPlan(task.plan);
  const resumable = isResumableGoal(task);

  // Load the M1 change report when the user expands the section.
  useEffect(() => {
    if (!reportOpen || !hasWorktree || !repo) return;
    let cancelled = false;
    setReportLoading(true);
    taskAPI.worktreeReport(task.task_id, repo)
      .then((r) => { if (!cancelled) setReport(r); })
      .catch(() => { if (!cancelled) setReport(null); })
      .finally(() => { if (!cancelled) setReportLoading(false); });
    return () => { cancelled = true; };
  }, [reportOpen, hasWorktree, task.task_id, repo]);

  const refreshReport = () => {
    if (!repo) return;
    setReportLoading(true);
    taskAPI.worktreeReport(task.task_id, repo)
      .then(setReport)
      .catch(() => setReport(null))
      .finally(() => setReportLoading(false));
  };

  const handleMerge = async () => {
    if (!repo || mergeBusy) return;
    setMergeBusy(true);
    try {
      await taskAPI.worktreeMerge(task.task_id, repo, 'squash');
      refreshReport();
    } catch (e: any) {
      alert(t('taskPanel.mergeFailed') + ': ' + (e?.message || ''));
    } finally {
      setMergeBusy(false);
    }
  };

  const handleDiscard = async () => {
    if (!repo || mergeBusy) return;
    if (!confirm(t('taskPanel.confirmDiscard'))) return;
    setMergeBusy(true);
    try {
      await taskAPI.worktreeDiscard(task.task_id, repo);
      setReport(null);
      refreshReport();
    } catch (e: any) {
      alert(t('taskPanel.discardFailed') + ': ' + (e?.message || ''));
    } finally {
      setMergeBusy(false);
    }
  };

  return (
    <div className="flex flex-col h-full min-h-0">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between px-3 sm:px-4 py-3 border-b border-border shrink-0">
        <div className="min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <h3 className="text-sm font-semibold truncate max-w-full">{task.title}</h3>
            <TaskStatusBadge status={task.status} />
          </div>
          <div className="mt-0.5 text-[10px] text-textMuted truncate">
            {task.task_id}{task.origin ? ` · ${task.origin}` : ''}
          </div>
        </div>
        <div className="flex items-center gap-1.5 shrink-0 flex-wrap">
          {/* Read-only action, and the only one a finished task offers: without
              it a completed task had no way at all into what it actually did. */}
          {task.session_id && (
            <button
              type="button"
              onClick={() => openSessionTab(task.session_id)}
              className="inline-flex items-center gap-1 px-2 py-1 rounded-lg text-[11px] font-medium border border-primary/40 text-primary hover:bg-primary/10"
            >
              <ListTree size={11} /> {t('taskPanel.viewFlow')}
            </button>
          )}
          {task.status === 'waiting_approval' && (
            <>
              <button type="button" onClick={() => onApprove(true)}
                className="inline-flex items-center gap-1 px-2 py-1 rounded-lg text-[11px] font-medium border border-emerald-500/40 text-emerald-600 hover:bg-emerald-500/10">
                <Check size={11} /> {t('taskPanel.approve')}
              </button>
              <button type="button" onClick={() => onApprove(false)}
                className="inline-flex items-center gap-1 px-2 py-1 rounded-lg text-[11px] font-medium border border-rose-500/40 text-rose-600 hover:bg-rose-500/10">
                <X size={11} /> {t('taskPanel.reject')}
              </button>
            </>
          )}
          {!isTerminal && task.status !== 'waiting_approval' && (
            <button type="button" onClick={onAbort}
              className="inline-flex items-center gap-1 px-2 py-1 rounded-lg text-[11px] font-medium border border-amber-500/40 text-amber-600 hover:bg-amber-500/10">
              <X size={11} /> {t('taskPanel.abort')}
            </button>
          )}
          {resumable && (
            <button type="button" onClick={onResume}
              className="inline-flex items-center gap-1 px-2 py-1 rounded-lg text-[11px] font-medium border border-violet-500/40 text-violet-600 hover:bg-violet-500/10">
              <RotateCcw size={11} /> {t('taskPanel.resume')}
            </button>
          )}
          {isTerminal && (
            confirmingRemove ? (
              <span className="flex items-center gap-0.5">
                <button type="button" title={t('common.confirm')} onClick={onRemove}
                  className="p-1 rounded bg-rose-500 text-white hover:bg-rose-600 transition-colors">
                  <Check size={11} />
                </button>
                <button type="button" title={t('common.cancel')} onClick={() => setConfirmingRemove(false)}
                  className="p-1 rounded text-textMuted hover:bg-black/[0.06] dark:hover:bg-white/[0.10] transition-colors">
                  <X size={11} />
                </button>
              </span>
            ) : (
              <button type="button" title={t('taskPanel.remove')} onClick={() => setConfirmingRemove(true)}
                className="inline-flex items-center gap-1 px-2 py-1 rounded-lg text-[11px] font-medium border border-rose-500/40 text-rose-600 hover:bg-rose-500/10">
                <Trash2 size={11} />
              </button>
            )
          )}
        </div>
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto p-4 space-y-4 text-[12px]">
        <div className="grid grid-cols-2 gap-3 rounded-lg bg-black/[0.02] dark:bg-white/[0.03] p-3">
          <InfoCell label={t('taskPanel.createdAt')} value={fmtDateTime(task.created_at)} />
          <InfoCell label={t('taskPanel.startedAt')} value={fmtDateTime(task.started_at)} />
          <InfoCell label={t('taskPanel.finishedAt')} value={fmtDateTime(task.finished_at)} />
          <InfoCell label={t('taskPanel.elapsed')} value={fmtElapsed(task.cost?.elapsed_ms || 0)} />
          {task.plan_total > 0 && (
            <InfoCell label={t('taskPanel.progress')} value={`${task.plan_done} / ${task.plan_total}`} />
          )}
          <InfoCell
            label={t('taskPanel.session')}
            value={task.session_id || '--'}
            // A task with no session yet has no flow to show — leave the cell
            // inert rather than opening an empty tab.
            onClick={task.session_id ? () => openSessionTab(task.session_id) : undefined}
            title={task.session_id ? t('taskPanel.viewFlowHint') : undefined}
          />
        </div>

        {task.plan_total > 0 && (
          <div>
            <div className="text-[10px] text-textMuted mb-1">{t('taskPanel.progress')}</div>
            <div className="h-1.5 rounded-full bg-black/[0.06] dark:bg-white/[0.08] overflow-hidden">
              <div
                className="h-full rounded-full bg-primary transition-all"
                style={{ width: `${Math.min(100, Math.round((task.plan_done / task.plan_total) * 100))}%` }}
              />
            </div>
          </div>
        )}

        {/* M3 — a parked goal is resumable, so say *why* it parked. */}
        {plan?.blocked_reason && (
          <div className="rounded-lg bg-violet-500/10 text-violet-700 dark:text-violet-300 p-3 text-[11px] flex items-start gap-2">
            <PauseCircle size={13} className="shrink-0 mt-0.5" />
            <div className="min-w-0">
              <div className="font-medium">{t('taskPanel.blockedReason')}</div>
              <div className="whitespace-pre-wrap break-words">{plan.blocked_reason}</div>
            </div>
          </div>
        )}

        {plan && (
          <div className="grid grid-cols-2 gap-3 rounded-lg bg-black/[0.02] dark:bg-white/[0.03] p-3">
            <InfoCell
              label={t('taskPanel.budgetTokens')}
              value={plan.budget.max_tokens > 0
                ? `${plan.spent_tokens} / ${plan.budget.max_tokens}`
                : `${plan.spent_tokens}`}
            />
            <InfoCell
              label={t('taskPanel.budgetSeconds')}
              value={plan.budget.max_seconds > 0 ? String(plan.budget.max_seconds) : t('taskPanel.unlimited')}
            />
            <InfoCell label={t('taskPanel.budgetAttempts')} value={String(plan.budget.max_attempts)} />
            <InfoCell label={t('taskPanel.planStatus')} value={plan.status || '--'} />
          </div>
        )}

        {plan && plan.milestones.length > 0 && (
          <div>
            <div className="text-[10px] text-textMuted mb-1">
              {t('taskPanel.milestones')} ({plan.plan_done}/{plan.plan_total})
            </div>
            <div className="space-y-1.5">
              {plan.milestones.map((ms, i) => (
                <MilestoneRow key={ms.id || i} ms={ms} index={i} />
              ))}
            </div>
          </div>
        )}

        {task.error && (
          <div>
            <div className="text-[10px] text-textMuted mb-1">{t('taskPanel.error')}</div>
            <div className="rounded-lg bg-rose-500/10 text-rose-600 p-3 text-[11px] whitespace-pre-wrap">{task.error}</div>
          </div>
        )}

        {task.result_summary && (
          <div>
            <div className="text-[10px] text-textMuted mb-1">{t('taskPanel.result')}</div>
            <div className="rounded-lg bg-black/[0.02] dark:bg-white/[0.03] p-3 text-[11px] whitespace-pre-wrap">{task.result_summary}</div>
          </div>
        )}

        <div>
          <div className="text-[10px] text-textMuted mb-1">{t('taskPanel.prompt')}</div>
          <div className="rounded-lg bg-black/[0.02] dark:bg-white/[0.03] p-3 text-[11px] whitespace-pre-wrap">{task.prompt}</div>
        </div>

        {hasWorktree && (
          <div className="rounded-lg border border-border overflow-hidden">
            <button
              type="button"
              onClick={() => setReportOpen((o) => !o)}
              className="w-full flex items-center justify-between px-3 py-2 text-[11px] font-medium hover:bg-black/[0.03] dark:hover:bg-white/[0.05]"
            >
              <span className="flex items-center gap-1.5">
                <GitMerge size={12} className="text-violet-500" />
                {t('taskPanel.changeReport')}
              </span>
              <ChevronRight size={12} className={`text-textMuted transition-transform ${reportOpen ? 'rotate-90' : ''}`} />
            </button>
            {reportOpen && (
              <div className="px-3 pb-3 space-y-2 border-t border-border/60">
                {reportLoading ? (
                  <div className="pt-3 text-[11px] text-textMuted flex items-center gap-1.5">
                    <OpenSquadLoader size={14} /> {t('taskPanel.reportLoading')}
                  </div>
                ) : report && report.status === 'ok' ? (
                  <>
                    <div className="pt-2 flex items-center gap-3 text-[10px] text-textMuted">
                      <span>+{report.insertions ?? 0}</span>
                      <span>-{report.deletions ?? 0}</span>
                      <span className="truncate">{report.branch}</span>
                    </div>
                    {report.files && report.files.length > 0 && (
                      <div className="max-h-40 overflow-y-auto rounded bg-black/[0.02] dark:bg-white/[0.03] p-2 space-y-0.5">
                        {report.files.map((f) => (
                          <div key={`${f.status}-${f.path}`} className="text-[10px] font-mono truncate">
                            <span className={f.status.startsWith('A') ? 'text-emerald-600' : f.status.startsWith('D') ? 'text-rose-600' : 'text-primary'}>
                              {f.status}
                            </span>{' '}
                            <span className="text-textMain">{f.path}</span>
                          </div>
                        ))}
                      </div>
                    )}
                    {report.commits && report.commits.length > 0 && (
                      <div className="text-[10px] text-textMuted space-y-0.5">
                        {report.commits.map((c) => (
                          <div key={c} className="font-mono truncate">{c}</div>
                        ))}
                      </div>
                    )}
                    <div className="flex items-center gap-1.5 pt-1">
                      <button
                        type="button"
                        disabled={mergeBusy}
                        onClick={handleMerge}
                        className="inline-flex items-center gap-1 px-2 py-1 rounded-lg text-[10px] font-medium bg-emerald-500 text-white hover:bg-emerald-600 disabled:opacity-40"
                      >
                        <GitMerge size={10} /> {t('taskPanel.merge')}
                      </button>
                      <button
                        type="button"
                        disabled={mergeBusy}
                        onClick={handleDiscard}
                        className="inline-flex items-center gap-1 px-2 py-1 rounded-lg text-[10px] font-medium border border-rose-500/40 text-rose-600 hover:bg-rose-500/10 disabled:opacity-40"
                      >
                        <Trash2 size={10} /> {t('taskPanel.discard')}
                      </button>
                    </div>
                  </>
                ) : (
                  <div className="pt-3 text-[11px] text-textMuted">
                    {report?.message || t('taskPanel.reportUnavailable')}
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
};

export default TaskPanelPage;
