/**
 * ScheduledTasksPage — delegated timed tasks manager.
 * Rendered as an L2 "scheduled-tasks" content tab in the middle pane.
 * Three internal sub-views: 新建任务 / 执行列表 / 任务列表.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Clock, Plus, Pencil, Play, Trash2, Zap, CheckCircle2, XCircle, Loader2 } from 'lucide-react';
import {
  scheduledTaskAPI,
  type ScheduledExecution,
  type ScheduledTask,
} from '../../services/api';
import {
  ScheduledTaskForm,
  emptyFormValue,
  taskToFormValue,
  type TaskFormValue,
} from './ScheduledTaskForm';
import { ExecWorkflowView } from './ExecWorkflowView';

type SubTab = 'new' | 'execution' | 'task';

interface Props {
  agentName: string;
  rootPath: string;
}

export const ScheduledTasksPage: React.FC<Props> = ({ agentName, rootPath }) => {
  const { t } = useTranslation();
  const [tasks, setTasks] = useState<ScheduledTask[]>([]);
  const [executions, setExecutions] = useState<ScheduledExecution[]>([]);
  const [sub, setSub] = useState<SubTab>('task');
  const [selTaskId, setSelTaskId] = useState<string | null>(null);
  const [selExecId, setSelExecId] = useState<string | null>(null);
  const [editing, setEditing] = useState<TaskFormValue | null>(null);
  const [loading, setLoading] = useState(false);

  const reload = useCallback(async (opts?: { quiet?: boolean }) => {
    if (!opts?.quiet) setLoading(true);
    try {
      const [tr, er] = await Promise.all([
        scheduledTaskAPI.list(agentName),
        scheduledTaskAPI.executions(agentName),
      ]);
      setTasks(tr.tasks || []);
      setExecutions((er.executions || []).slice().reverse());
    } finally {
      if (!opts?.quiet) setLoading(false);
    }
  }, [agentName]);

  useEffect(() => { reload(); }, [reload]);
  // Quiet poll — do not flip loading (avoids list flicker) and do not remount
  // the workflow pane; ExecWorkflowView soft-polls session history itself.
  useEffect(() => {
    const id = setInterval(() => { void reload({ quiet: true }); }, 8000);
    return () => clearInterval(id);
  }, [reload]);

  const selectedTask = useMemo(
    () => tasks.find(t => t.id === selTaskId) || null,
    [tasks, selTaskId],
  );
  const selectedExec = useMemo(
    () => executions.find(e => e.id === selExecId) || null,
    [executions, selExecId],
  );

  const startNew = () => { setEditing(emptyFormValue(rootPath)); setSub('new'); };
  const startEdit = (task: ScheduledTask) => {
    setEditing(taskToFormValue(task));
    setSelTaskId(task.id);
    setSub('new');
  };
  const onSaved = (task: ScheduledTask) => {
    setEditing(null);
    setSelTaskId(task.id);
    setSub('task');
    reload();
  };

  const handleRun = async (task: ScheduledTask) => {
    try {
      await scheduledTaskAPI.runNow(agentName, task.id);
      reload();
    } catch (e: any) {
      // 409 already_running → apiRequest throws; inform instead of silently failing.
      const msg = e?.message || '';
      if (/already_running|409/i.test(msg)) {
        alert(t('scheduledTasks.alreadyRunning'));
      } else {
        alert(t('scheduledTasks.runFailed') + ': ' + msg);
      }
    }
  };
  const handleToggle = async (task: ScheduledTask, enabled: boolean) => {
    await scheduledTaskAPI.setEnabled(agentName, task.id, enabled);
    reload();
  };
  const handleDelete = async (task: ScheduledTask) => {
    if (!confirm(t('scheduledTasks.confirmDelete'))) return;
    await scheduledTaskAPI.remove(agentName, task.id);
    if (selTaskId === task.id) setSelTaskId(null);
    reload();
  };

  const handleDeleteExec = async (exec: ScheduledExecution) => {
    if (!confirm(t('scheduledTasks.confirmDeleteExec'))) return;
    try {
      await scheduledTaskAPI.removeExecution(agentName, exec.id);
      if (selExecId === exec.id) setSelExecId(null);
      await reload({ quiet: true });
    } catch (e: any) {
      alert(t('scheduledTasks.deleteExecFailed') + ': ' + (e?.message || ''));
    }
  };

  return (
    <div className="flex h-full min-h-0">
      {/* Left: list */}
      <div className="w-64 shrink-0 flex flex-col min-h-0 border-r border-border bg-panel">
        <div className="px-3 py-3 border-b border-border/60 shrink-0">
          <div className="flex items-center gap-1.5 text-sm font-semibold">
            <Clock size={14} className="text-violet-500" />
            {t('scheduledTasks.title')}
          </div>
          <div className="mt-0.5 text-[10px] text-textMuted truncate" title={rootPath}>{rootPath}</div>
          <button
            type="button"
            onClick={startNew}
            className="mt-2 w-full inline-flex items-center justify-center gap-1.5 px-2 py-1.5 rounded-lg text-[12px] font-medium text-sky-600 border border-sky-500/40 hover:bg-sky-500/10"
          >
            <Plus size={13} /> {t('scheduledTasks.newTask')}
          </button>
        </div>

        <div className="px-2 pt-2 shrink-0">
          <div className="inline-flex w-full rounded-lg bg-black/[0.05] dark:bg-white/[0.08] p-[3px]">
            {(['execution', 'task'] as const).map(st => (
              <button
                key={st}
                type="button"
                onClick={() => { setSub(st); setEditing(null); }}
                className={`flex-1 px-2 py-1 rounded-md text-[11px] font-medium transition-colors ${
                  sub === st ? 'bg-white dark:bg-black/40 shadow-sm text-text' : 'text-textMuted hover:text-text'
                }`}
              >
                {t(`scheduledTasks.tab.${st}`)}
              </button>
            ))}
          </div>
        </div>

        <div className="flex-1 min-h-0 overflow-y-auto px-1.5 py-2 space-y-1">
          {loading && tasks.length === 0 && executions.length === 0 ? (
            <div className="px-2 py-3 text-[11px] text-textMuted flex items-center gap-1.5">
              <Loader2 size={12} className="animate-spin" /> {t('scheduledTasks.loading')}
            </div>
          ) : sub === 'execution' ? (
            executions.length === 0 ? (
              <EmptyHint text={t('scheduledTasks.emptyExecution')} />
            ) : (
              executions.map(e => (
                <ExecRow
                  key={e.id}
                  exec={e}
                  active={e.id === selExecId}
                  onClick={() => { setSelExecId(e.id); setEditing(null); }}
                  onDelete={() => handleDeleteExec(e)}
                />
              ))
            )
          ) : tasks.length === 0 ? (
            <EmptyHint text={t('scheduledTasks.emptyTask')} />
          ) : (
            tasks.map(tk => (
              <TaskRow
                key={tk.id}
                task={tk}
                active={tk.id === selTaskId && !editing}
                onClick={() => { setSelTaskId(tk.id); setEditing(null); setSub('task'); }}
                onToggle={(en) => handleToggle(tk, en)}
              />
            ))
          )}
        </div>
      </div>

      {/* Right: detail */}
      <div className="flex-1 min-w-0 min-h-0 flex flex-col">
        {editing ? (
          <ScheduledTaskForm
            agentName={agentName}
            rootPath={rootPath}
            value={editing}
            onCancel={() => { setEditing(null); }}
            onSaved={onSaved}
          />
        ) : sub === 'execution' && selectedExec ? (
          <ExecWorkflowView
            agentName={agentName}
            rootPath={rootPath}
            exec={selectedExec}
            task={tasks.find((t) => t.id === selectedExec.task_id) || null}
            onRunAgain={() => {
              const tk = tasks.find((t) => t.id === selectedExec.task_id);
              if (tk) handleRun(tk);
            }}
            onEdit={() => {
              const tk = tasks.find((t) => t.id === selectedExec.task_id);
              if (tk) startEdit(tk);
            }}
            onStopped={reload}
          />
        ) : sub === 'task' && selectedTask ? (
          <TaskDetail task={selectedTask}
            onEdit={() => startEdit(selectedTask)}
            onRun={() => handleRun(selectedTask)}
            onDelete={() => handleDelete(selectedTask)}
            onToggle={(en) => handleToggle(selectedTask, en)} />
        ) : (
          <div className="flex-1 flex items-center justify-center text-[12px] text-textMuted px-6 text-center">
            {sub === 'execution' ? t('scheduledTasks.selectExecution') : t('scheduledTasks.selectTask')}
          </div>
        )}
      </div>
    </div>
  );
};

const EmptyHint: React.FC<{ text: string }> = ({ text }) => (
  <div className="px-2 py-3 text-[11px] text-textMuted/70">{text}</div>
);

const StatusBadge: React.FC<{ status: string }> = ({ status }) => {
  const map: Record<string, { c: string; Icon: React.FC<any> }> = {
    running: { c: 'bg-sky-500/15 text-sky-600', Icon: Loader2 },
    success: { c: 'bg-emerald-500/15 text-emerald-600', Icon: CheckCircle2 },
    failed: { c: 'bg-rose-500/15 text-rose-600', Icon: XCircle },
    missed: { c: 'bg-amber-500/15 text-amber-600', Icon: Clock },
  };
  const m = map[status] || map.success;
  const Icon = m.Icon;
  return (
    <span className={`inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[9px] font-medium ${m.c}`}>
      <Icon size={9} className={status === 'running' ? 'animate-spin' : ''} />
      {status}
    </span>
  );
};

const fmtTime = (ts: number | null) => (ts ? new Date(ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '--');
const fmtDate = (ts: number | null) => (ts ? new Date(ts * 1000).toLocaleDateString() : '--');

const scheduleSummary = (task: ScheduledTask): string => {
  const s = task.schedule || ({} as any);
  if (s.type === 'once') return 'once';
  if (s.type === 'daily') return `daily ${s.time || '09:00'}`;
  if (s.type === 'weekly') return `weekly ${s.time || '09:00'} (${s.weekdays || ''})`;
  if (s.type === 'interval') return `every ${s.total_seconds || 0}s`;
  return s.type || '';
};

const relTime = (ts: number | null): string => {
  if (!ts) return '--';
  const diff = ts - Date.now() / 1000;
  if (diff <= 0) return '—';
  const h = Math.floor(diff / 3600);
  const m = Math.floor((diff % 3600) / 60);
  if (h >= 24) return `${Math.floor(h / 24)}d ${h % 24}h`;
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m`;
  return 'soon';
};

const ExecRow: React.FC<{
  exec: ScheduledExecution;
  active: boolean;
  onClick: () => void;
  onDelete: () => void;
}> = ({ exec, active, onClick, onDelete }) => {
  const { t } = useTranslation();
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onClick}
      onKeyDown={(ev) => { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); onClick(); } }}
      className={`group w-full text-left px-2 py-1.5 rounded-lg transition-colors cursor-pointer ${active ? 'bg-sky-500/10' : 'hover:bg-black/[0.04] dark:hover:bg-white/[0.05]'}`}
    >
      <div className="flex items-center justify-between gap-1">
        <span className="text-[11px] font-medium truncate">{exec.task_name}</span>
        <div className="flex items-center gap-1 shrink-0">
          <span className="text-[10px] text-textMuted">{fmtTime(exec.started_at)}</span>
          <button
            type="button"
            title={t('scheduledTasks.deleteExec')}
            onClick={(e) => { e.stopPropagation(); onDelete(); }}
            className="opacity-0 group-hover:opacity-100 p-0.5 rounded text-textMuted hover:text-rose-600 hover:bg-rose-500/10 transition-opacity"
          >
            <Trash2 size={11} />
          </button>
        </div>
      </div>
      <div className="mt-0.5 flex items-center gap-1.5">
        <StatusBadge status={exec.status} />
        {exec.manual && <span className="text-[9px] text-textMuted">manual</span>}
      </div>
    </div>
  );
};

const TaskRow: React.FC<{ task: ScheduledTask; active: boolean; onClick: () => void; onToggle: (en: boolean) => void }> = ({ task, active, onClick, onToggle }) => (
  <div
    onClick={onClick}
    className={`group px-2 py-1.5 rounded-lg cursor-pointer transition-colors ${active ? 'bg-sky-500/10' : 'hover:bg-black/[0.04] dark:hover:bg-white/[0.05]'}`}
  >
    <div className="flex items-center justify-between gap-1">
      <span className="text-[11px] font-medium truncate">{task.name}</span>
      <button
        type="button"
        onClick={(e) => { e.stopPropagation(); onToggle(!task.enabled); }}
        className={`relative w-7 h-4 rounded-full transition-colors shrink-0 ${task.enabled ? 'bg-sky-500' : 'bg-black/15 dark:bg-white/20'}`}
        title={task.enabled ? 'enabled' : 'disabled'}
      >
        <span className={`absolute top-0.5 left-0.5 w-3 h-3 rounded-full bg-white shadow transition-transform ${task.enabled ? 'translate-x-3' : ''}`} />
      </button>
    </div>
    <div className="mt-0.5 text-[10px] text-textMuted truncate">{scheduleSummary(task)} · {relTime(task.next_run_ts)}</div>
  </div>
);

const InfoCell: React.FC<{ label: string; value: React.ReactNode }> = ({ label, value }) => (
  <div className="space-y-0.5">
    <div className="text-[10px] text-textMuted">{label}</div>
    <div className="text-[11px] font-medium">{value}</div>
  </div>
);

const TaskDetail: React.FC<{ task: ScheduledTask; onEdit: () => void; onRun: () => void; onDelete: () => void; onToggle: (en: boolean) => void }> = ({ task, onEdit, onRun, onDelete, onToggle }) => (
  <div className="flex flex-col h-full min-h-0">
    <div className="flex items-center justify-between px-4 py-3 border-b border-border shrink-0">
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <h3 className="text-sm font-semibold truncate">{task.name}</h3>
          <span className={`px-1.5 py-0.5 rounded text-[9px] font-medium ${task.enabled ? 'bg-emerald-500/15 text-emerald-600' : 'bg-black/10 text-textMuted'}`}>
            {task.enabled ? 'enabled' : 'disabled'}
          </span>
        </div>
        <div className="mt-0.5 text-[10px] text-textMuted">{scheduleSummary(task)} · {task.workspace || '--'}</div>
      </div>
      <div className="flex items-center gap-1.5 shrink-0">
        <button type="button" onClick={onRun} title="Run now"
          className="inline-flex items-center gap-1 px-2 py-1 rounded-lg text-[11px] font-medium border border-border hover:bg-black/5 dark:hover:bg-white/10">
          <Zap size={11} className="text-amber-500" /> Run now
        </button>
        <button type="button" onClick={onEdit} title="Edit"
          className="inline-flex items-center gap-1 px-2 py-1 rounded-lg text-[11px] font-medium border border-border hover:bg-black/5 dark:hover:bg-white/10">
          <Pencil size={11} /> Edit
        </button>
        <button type="button" onClick={onDelete} title="Delete"
          className="inline-flex items-center gap-1 px-2 py-1 rounded-lg text-[11px] font-medium border border-rose-500/40 text-rose-600 hover:bg-rose-500/10">
          <Trash2 size={11} />
        </button>
        <button type="button" onClick={() => onToggle(!task.enabled)}
          className={`relative w-9 h-5 rounded-full transition-colors ${task.enabled ? 'bg-sky-500' : 'bg-black/15 dark:bg-white/20'}`}>
          <span className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform ${task.enabled ? 'translate-x-4' : ''}`} />
        </button>
      </div>
    </div>
    <div className="flex-1 min-h-0 overflow-y-auto p-4 space-y-4 text-[12px]">
      <div className="grid grid-cols-2 gap-3 rounded-lg bg-black/[0.02] dark:bg-white/[0.03] p-3">
        <InfoCell label="Schedule" value={scheduleSummary(task)} />
        <InfoCell label="Next run" value={relTime(task.next_run_ts)} />
        <InfoCell label="Last run" value={task.last_run_ts ? `${fmtDate(task.last_run_ts)} ${fmtTime(task.last_run_ts)}` : '--'} />
        <InfoCell label="Run count" value={task.run_count} />
        <InfoCell label="Delegate agent" value={task.delegate_agent || '--'} />
        <InfoCell label="Model" value={task.model_card || 'default'} />
      </div>
      {task.skills && task.skills.length > 0 && (
        <div>
          <div className="text-[10px] text-textMuted mb-1">Enabled Skills</div>
          <div className="flex flex-wrap gap-1">
            {task.skills.map(s => (
              <span key={s} className="px-1.5 py-0.5 rounded bg-violet-500/10 text-violet-600 text-[10px] font-medium">{s}</span>
            ))}
          </div>
        </div>
      )}
      <div>
        <div className="text-[10px] text-textMuted mb-1">Execution Prompt</div>
        <div className="rounded-lg bg-black/[0.02] dark:bg-white/[0.03] p-3 text-[11px] whitespace-pre-wrap">{task.prompt}</div>
      </div>
    </div>
  </div>
);

export default ScheduledTasksPage;
