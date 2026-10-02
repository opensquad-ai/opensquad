import React, { useEffect, useMemo, useState, useCallback } from 'react';
import {
  RefreshCw,
  ChevronDown,
  CheckCircle2,
  Circle,
  FileText,
  Target,
  ListChecks,
  History,
  Trash2,
  Menu,
  Plus,
  X,
} from 'lucide-react';
import { marked } from 'marked';
import { sanitizeHtml } from '../utils/safeHtml';
import { useTranslation } from 'react-i18next';
import { OpenSquadLoader } from './OpenSquadLoader';
import { collabBoardAPI, CollabBoardItem, CollabBoardTask, PlanSnapshot } from '../services/api';
import {
  type ParsedStep,
  PlanStatusIcon,
  parseTaskSteps,
  resolveWorkerId,
} from '../utils/taskSteps';
import {
  adminHeaderBar,
  adminHeaderCta,
  adminHeaderGhostBtn,
  adminHeaderIcon,
  adminHeaderIconBox,
  adminHeaderNavBtn,
  adminHeaderTitle,
} from './admin/adminShellStyles';

interface Props {
  onBack: () => void;
}

type TabId = 'requirements' | 'plan' | 'tasks';

const TABS: { id: TabId; label: string; icon: React.ReactNode }[] = [
  { id: 'requirements', label: 'collabBoard.tabRequirements', icon: <Target size={14} /> },
  { id: 'plan', label: 'collabBoard.tabPlan', icon: <FileText size={14} /> },
  { id: 'tasks', label: 'collabBoard.tabTasks', icon: <ListChecks size={14} /> },
];

const formatTime = (iso?: string) => {
  if (!iso) return '-';
  try {
    const d = new Date(iso);
    return d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
  } catch {
    return iso;
  }
};

const sortTaskStatus = (s: string) => {
  const order: Record<string, number> = { doing: 0, pending: 1, blocked: 2, done: 3 };
  return order[s] ?? 9;
};


const buildRequirementFallback = (items: CollabBoardItem[], t: (key: string, opts?: Record<string, any>) => string) => {
  if (items.length === 0) {
    return [
      t('collabBoard.reqDocTitle'),
      '',
      t('collabBoard.reqDocGuide'),
      t('collabBoard.reqDocGoal'),
      t('collabBoard.reqDocScope'),
      t('collabBoard.reqDocConstraints'),
      t('collabBoard.reqDocDoD'),
    ].join('\n');
  }

  const lines: string[] = [t('collabBoard.reqDocTitle'), ''];
  items.forEach((it, idx) => {
    lines.push(`## ${idx + 1}. ${it.title || t('collabBoard.unnamedReq')}`);
    lines.push(t('collabBoard.reqStatus', { status: it.status || t('collabBoard.pendingConfirm') }));
    lines.push(t('collabBoard.reqSubmitter', { agent: it.agent_id || 'unknown' }));
    lines.push(t('collabBoard.reqUpdateTime', { time: formatTime(it.updated_at) }));
    lines.push('');
    lines.push(it.content || t('collabBoard.noDescription'));
    lines.push('');
  });
  return lines.join('\n');
};

interface MarkdownSectionProps {
  title: string;
  subtitle: string;
  value: string;
  placeholder: string;
  onSave: (next: string) => Promise<void>;
  snapshots?: PlanSnapshot[];
  onLoadSnapshots?: () => Promise<void>;
}

const MarkdownSection: React.FC<MarkdownSectionProps> = ({
  title,
  subtitle,
  value,
  placeholder,
  onSave,
  snapshots,
  onLoadSnapshots,
}) => {
  const { t } = useTranslation();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');
  const [saving, setSaving] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [selectedSnapshot, setSelectedSnapshot] = useState<PlanSnapshot | null>(null);

  useEffect(() => {
    if (!editing) setDraft(value || '');
  }, [value, editing]);

  const renderedHtml = useMemo(() => {
    const src = selectedSnapshot?.content ?? value;
    if (!src?.trim()) return '';
    return sanitizeHtml(marked.parse(src, { breaks: true }) as string);
  }, [value, selectedSnapshot]);

  const handleSave = async () => {
    setSaving(true);
    try {
      await onSave(draft);
      setEditing(false);
      setSelectedSnapshot(null);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="rounded-xl border border-border bg-panel p-4">
      <div className="flex items-center justify-between gap-3 mb-3">
        <div>
          <div className="text-sm font-semibold text-textMain">{title}</div>
          <div className="text-[11px] text-textMuted mt-0.5">{subtitle}</div>
        </div>
        {!editing ? (
          <button
            onClick={() => { setEditing(true); setDraft(value || ''); setSelectedSnapshot(null); }}
            className="px-2 py-1 rounded text-[10px] font-medium bg-primary/15 text-primary border border-primary/30 hover:bg-primary/25"
          >
            {t('collabBoard.editMarkdown')}
          </button>
        ) : (
          <div className="flex gap-1">
            <button
              onClick={handleSave}
              disabled={saving}
              className="px-2 py-1 rounded text-[10px] font-medium bg-emerald-500/15 text-emerald-400 border border-emerald-500/30 disabled:opacity-60"
            >
              {saving ? t('collabBoard.saving') : t('collabBoard.save')}
            </button>
            <button
              onClick={() => { setEditing(false); setDraft(value || ''); setSelectedSnapshot(null); }}
              className="px-2 py-1 rounded text-[10px] font-medium bg-slate-500/15 text-slate-400 border border-slate-500/30"
            >
              {t('collabBoard.cancel')}
            </button>
          </div>
        )}
      </div>

      {editing ? (
        <textarea
          value={draft}
          onChange={e => setDraft(e.target.value)}
          rows={16}
          placeholder={placeholder}
          className="w-full px-3 py-2 rounded bg-bgPage border border-border text-sm text-textMain font-mono"
        />
      ) : renderedHtml ? (
        <div
          className="prose prose-sm prose-invert max-w-none text-xs text-textMain leading-relaxed"
          dangerouslySetInnerHTML={{ __html: renderedHtml }}
        />
      ) : (
        <div className="text-xs text-textMuted py-8 text-center">{t('collabBoard.noContent')}</div>
      )}

      {snapshots && onLoadSnapshots && (
        <>
          <button
            onClick={async () => {
              const next = !historyOpen;
              setHistoryOpen(next);
              if (next) await onLoadSnapshots();
            }}
            className="mt-3 w-full flex items-center justify-between px-3 py-2 rounded-lg bg-bgPage border border-border text-xs text-textMuted hover:bg-primary/5 transition-colors"
          >
            <span className="flex items-center gap-1.5">
              <History size={12} />
              {t('collabBoard.planHistory', { count: snapshots.length })}
            </span>
            <ChevronDown size={12} className={`transition-transform ${historyOpen ? '' : '-rotate-90'}`} />
          </button>

          {historyOpen && (
            <div className="mt-2 space-y-1 max-h-48 overflow-auto">
              {snapshots.map(s => (
                <button
                  key={s.filename}
                  onClick={() => setSelectedSnapshot(selectedSnapshot?.filename === s.filename ? null : s)}
                  className={`w-full text-left px-3 py-2 rounded text-[11px] transition-colors ${
                    selectedSnapshot?.filename === s.filename
                      ? 'bg-primary/10 text-primary'
                      : 'bg-bgPage text-textMuted hover:bg-primary/5'
                  }`}
                >
                  <div className="flex items-center justify-between">
                    <span>{s.title}</span>
                    <span className="text-[10px] text-textMuted/60">{formatTime(s.saved_at)}</span>
                  </div>
                </button>
              ))}
              {snapshots.length === 0 && (
                <div className="text-[11px] text-textMuted py-2 text-center">{t('collabBoard.noHistory')}</div>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
};

interface AgentAssignmentBoardProps {
  taskItems: CollabBoardItem[];
}

const AgentAssignmentBoard: React.FC<AgentAssignmentBoardProps> = ({ taskItems }) => {
  const { t } = useTranslation();
  const [expandedKeys, setExpandedKeys] = useState<Record<string, boolean>>({});

  const grouped = useMemo(() => {
    const map = new Map<string, CollabBoardItem[]>();
    taskItems.forEach((t) => {
      const aid = resolveWorkerId(t);
      if (!map.has(aid)) map.set(aid, []);
      map.get(aid)!.push(t);
    });
    return Array.from(map.entries()).map(([agentId, tasks]) => ({
      agentId,
      tasks: tasks.sort((a, b) => (b.updated_at || '').localeCompare(a.updated_at || '')),
    }));
  }, [taskItems]);

  if (grouped.length === 0) {
    return (
      <div className="rounded-xl border border-border bg-panel p-4">
        <div className="text-sm font-semibold text-textMain">{t('collabBoard.taskAssignArea')}</div>
        <div className="text-xs text-textMuted py-8 text-center">{t('collabBoard.noTaskAssign')}</div>
      </div>
    );
  }

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
      {grouped.map(({ agentId, tasks }) => (
        <div key={agentId} className="rounded-xl border border-border bg-panel p-4">
          <div className="flex items-center justify-between mb-3">
            <div className="text-sm font-semibold text-primary">@{agentId}</div>
            <div className="text-[10px] text-textMuted">{t('collabBoard.taskCount', { count: tasks.length })}</div>
          </div>

          <div className="space-y-3">
            {tasks.map((taskItem) => {
              const steps = parseTaskSteps(taskItem, t);
              const hasHierarchy = steps.some(s => s.children && s.children.length > 0);

              // Render a single step row (handles both flat and nested)
              const renderStep = (step: ParsedStep, stepKey: string, depth: number = 0) => {
                const expanded = !!expandedKeys[stepKey];
                const hasChildren = step.children && step.children.length > 0;
                const indentClass = depth > 0 ? 'ml-4' : '';

                return (
                  <div key={stepKey} className={`${indentClass}`}>
                    <div className="rounded border border-border/50 bg-panel/40 overflow-hidden">
                      <button
                        type="button"
                        onClick={() => setExpandedKeys(prev => ({ ...prev, [stepKey]: !expanded }))}
                        className="w-full flex items-center justify-between gap-2 px-2 py-2 text-left hover:bg-primary/5"
                      >
                        <div className="flex items-center gap-1.5 min-w-0">
                          {depth === 0 && hasChildren ? (
                            <Target size={12} className="text-primary flex-shrink-0" />
                          ) : (
                            <PlanStatusIcon status={step.status} />
                          )}
                          <div className={`text-xs truncate ${
                            step.status === 'done'
                              ? 'text-textMuted line-through'
                              : step.status === 'doing'
                                ? 'text-textMain font-medium'
                                : depth === 0 && hasChildren
                                  ? 'text-primary font-semibold'
                                  : 'text-textMuted'
                          }`}>
                            {step.title}
                          </div>
                        </div>
                        <div className="flex items-center gap-2 flex-shrink-0">
                          {hasChildren && (
                            <span className="text-[10px] text-textMuted">{t('collabBoard.subtaskCount', { count: step.children!.length })}</span>
                          )}
                          {(hasChildren || step.detail) && (
                            <ChevronDown size={12} className={`text-textMuted transition-transform ${expanded ? '' : '-rotate-90'}`} />
                          )}
                        </div>
                      </button>
                      {expanded && (
                        <div className="px-2 pb-2 border-t border-border/40">
                          {/* Show detail text */}
                          {step.detail && !hasChildren && (
                            <div className="text-[11px] text-textMuted whitespace-pre-wrap py-1.5">
                              {step.detail}
                            </div>
                          )}
                          {/* Render children */}
                          {hasChildren && (
                            <div className="space-y-2 pt-2">
                              {step.children!.map((child, ci) =>
                                renderStep(child, `${stepKey}-child-${ci}`, depth + 1)
                              )}
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  </div>
                );
              };

              return (
                <div key={taskItem.id} className="rounded-lg border border-border/70 bg-bgPage p-3">
                  {!hasHierarchy && (
                    <div className="text-xs font-semibold text-textMain mb-2">{taskItem.title || taskItem.item_key || t('collabBoard.unnamedTask')}</div>
                  )}
                  <div className="space-y-2">
                    {steps.map((s, idx) => renderStep(s, `${taskItem.id}-step-${idx}`))}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
};


export const CollabBoardPage: React.FC<Props> = ({ onBack }) => {
  const { t } = useTranslation();
  const [items, setItems] = useState<CollabBoardItem[]>([]);
  const [tasks, setTasks] = useState<CollabBoardTask[]>([]);
  const [loading, setLoading] = useState(false);
  const [taskFilter, setTaskFilter] = useState('');
  const [activeTab, setActiveTab] = useState<TabId>('requirements');
  const [planSnapshots, setPlanSnapshots] = useState<PlanSnapshot[]>([]);

  const loadTasks = useCallback(async () => {
    const tr = await collabBoardAPI.listTasks();
    const ts = tr.tasks || [];
    setTasks(ts);
    setTaskFilter(prev => prev || (ts.length > 0 ? ts[0].task_id : ''));
  }, []);

  const loadPlanSnapshots = useCallback(async () => {
    if (!taskFilter) {
      setPlanSnapshots([]);
      return;
    }
    try {
      const res = await collabBoardAPI.listPlanSnapshots(taskFilter);
      setPlanSnapshots(res.snapshots || []);
    } catch (e) {
      console.error('Failed to load plan snapshots:', e);
      setPlanSnapshots([]);
    }
  }, [taskFilter]);

  const load = useCallback(async (silent = false) => {
    if (!taskFilter) return;
    if (!silent) setLoading(true);
    try {
      const res = await collabBoardAPI.listItems(taskFilter, '', 'all');
      setItems(res.items || []);
      await loadPlanSnapshots();
    } finally {
      if (!silent) setLoading(false);
    }
  }, [taskFilter, loadPlanSnapshots]);

  useEffect(() => { loadTasks(); }, [loadTasks]);
  useEffect(() => { load(); }, [load]);

  // Auto-refresh board data and task list every 5 seconds (silent, no loading spinner)
  useEffect(() => {
    let cancelled = false;
    const interval = setInterval(async () => {
      try {
        // 1. Refresh task list dropdown (so new collab appears automatically)
        const tr = await collabBoardAPI.listTasks();
        if (!cancelled) {
          setTasks(tr.tasks || []);
        }
        // 2. Refresh board data for current filter
        if (taskFilter) {
          const res = await collabBoardAPI.listItems(taskFilter, '', 'all');
          if (!cancelled) {
            setItems(res.items || []);
            const snapRes = await collabBoardAPI.listPlanSnapshots(taskFilter);
            if (!cancelled) setPlanSnapshots(snapRes.snapshots || []);
          }
        }
      } catch {
        // Silently ignore polling errors
      }
    }, 5000);
    return () => { cancelled = true; clearInterval(interval); };
  }, [taskFilter]);

  const requirementItems = useMemo(() => items.filter(i => i.item_type === 'requirement'), [items]);
  const taskItems = useMemo(() => items.filter(i => i.item_type === 'task'), [items]);

  const requirementDocItem = useMemo(
    () => items.find(i => i.item_type === 'requirement_doc') || items.find(i => i.item_type === 'requirement'),
    [items]
  );
  const planItem = useMemo(() => items.find(i => i.item_type === 'plan'), [items]);

  const requirementDocValue = useMemo(
    () => requirementDocItem?.content || buildRequirementFallback(requirementItems, t),
    [requirementDocItem?.content, requirementItems]
  );

  const upsertDoc = async (itemType: string, title: string, content: string) => {
    await collabBoardAPI.upsertItem({
      collab_id: taskFilter,
      task_name: tasks.find(t => t.task_id === taskFilter)?.task_name || taskFilter,
      agent_id: 'web_user',
      item_type: itemType,
      item_key: itemType,
      title,
      content,
      status: 'doing',
      progress: 0,
      visibility: 'public',
    });
    await load();
  };

  const saveRequirementDoc = async (content: string) => {
    await upsertDoc('requirement_doc', t('collabBoard.reqDocName'), content);
  };

  const savePlanDoc = async (content: string) => {
    if (planItem?.content && planItem.content !== content) {
      try {
        await collabBoardAPI.savePlanSnapshot({
          collab_id: taskFilter,
          content: planItem.content,
          title: planItem.title || 'Plan snapshot',
          agent_id: planItem.agent_id || 'web_user',
        });
      } catch (e) {
        console.error('Failed to save plan snapshot:', e);
      }
    }
    await upsertDoc('plan', t('collabBoard.planDocName'), content);
    await loadPlanSnapshots();
  };

  const createTask = async () => {
    const name = prompt(t('collabBoard.enterTaskName'));
    if (!name || !name.trim()) return;
    const r = await collabBoardAPI.createTask({ task_name: name.trim(), created_by: 'web_user' });
    await loadTasks();
    if (r.task?.task_id) setTaskFilter(r.task.task_id);
  };

  const deleteTask = async () => {
    if (!taskFilter) return;
    const task = tasks.find(t => t.task_id === taskFilter);
    const label = task?.task_name || taskFilter;
    if (!confirm(t('collabBoard.confirmDeleteTask', { name: label }))) return;
    try {
      await collabBoardAPI.deleteTask(taskFilter);
      setTaskFilter('');
      await loadTasks();
    } catch (e: any) {
      alert(t('collabBoard.deleteTaskFailed', { error: e?.message || String(e) }));
    }
  };

  return (
    <div className="flex-1 h-full bg-bgLight flex flex-col overflow-hidden">
      {/* 头部栏 */}
      <div className={`${adminHeaderBar} justify-between`}>
        <div className="flex items-center gap-2 md:gap-2.5 flex-1 min-w-0">
          <button
            onClick={() => window.dispatchEvent(new CustomEvent('openMobileNav'))}
            className={`${adminHeaderNavBtn} md:hidden`}
            aria-label="Navigation menu"
          >
            <Menu size={16} />
          </button>
          <div className={`hidden md:flex ${adminHeaderIconBox}`}>
            <Target size={14} className={adminHeaderIcon} />
          </div>
          <div className="flex items-baseline gap-1.5 shrink-0 min-w-0">
            <h2 className={adminHeaderTitle}>{t('collabBoard.title')}</h2>
          </div>
        </div>

        <div className="flex items-center gap-1.5 shrink-0">
          <select
            value={taskFilter}
            onChange={e => setTaskFilter(e.target.value)}
            className="px-2 py-1 rounded-md bg-bgPage border border-border text-xs text-textMain focus:outline-none focus:border-primary/50 max-w-[120px] md:max-w-[200px] truncate"
          >
            <option value="">{t('collabBoard.selectTask')}</option>
            {tasks.map(t => <option key={t.task_id} value={t.task_id}>{t.task_name} ({t.task_id})</option>)}
          </select>
          <button onClick={createTask} className={adminHeaderCta}>
            <Plus size={13} /> {t('collabBoard.newTask')}
          </button>
          {taskFilter && (
            <button onClick={deleteTask} className={`${adminHeaderGhostBtn} text-red-400 hover:text-red-500 hover:bg-red-500/10 hover:border-red-500/20`} title={t('collabBoard.deleteTask')}>
              <Trash2 size={14} />
            </button>
          )}
          <button onClick={() => { loadTasks(); load(); }} className={adminHeaderGhostBtn}>
            {loading ? <OpenSquadLoader size={14} /> : <RefreshCw size={14} />}
          </button>
          <button
            onClick={onBack}
            className={`${adminHeaderGhostBtn} px-2`}
            title={t('common.close')}
            aria-label={t('common.close')}
          >
            <X size={16} />
          </button>
        </div>
      </div>

      <div className="flex border-b border-border bg-panel/50 shrink-0 px-4 md:px-6">
        {TABS.map(tab => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={`flex items-center gap-1.5 px-3 py-2.5 text-xs font-medium border-b-2 transition-colors ${
              activeTab === tab.id
                ? 'border-primary text-primary'
                : 'border-transparent text-textMuted hover:text-textMain'
            }`}
          >
            {tab.icon}
            {t(tab.label)}
          </button>
        ))}
      </div>

      <div className="flex-1 overflow-auto p-4 md:p-6 space-y-4">
        {!taskFilter ? (
          <div className="flex flex-col items-center justify-center h-64 gap-3">
            <Target className="text-textMuted" size={32} />
            <p className="text-textMuted text-sm">{t('collabBoard.selectTaskFirst')}</p>
          </div>
        ) : loading ? (
          <div className="flex flex-col items-center justify-center h-64 gap-3">
            <OpenSquadLoader size={40} />
            <p className="text-textMuted text-sm">{t('collabBoard.loading')}</p>
          </div>
        ) : (
          <>
            {activeTab === 'requirements' && (
              <MarkdownSection
                title={t('collabBoard.reqAreaTitle')}
                subtitle={t('collabBoard.reqAreaSubtitle')}
                value={requirementDocValue}
                placeholder={t('collabBoard.reqAreaPlaceholder')}
                onSave={saveRequirementDoc}
              />
            )}

            {activeTab === 'plan' && (
              <MarkdownSection
                title={t('collabBoard.planAreaTitle')}
                subtitle={t('collabBoard.planAreaSubtitle')}
                value={planItem?.content || ''}
                placeholder={t('collabBoard.planAreaPlaceholder')}
                onSave={savePlanDoc}
                snapshots={planSnapshots}
                onLoadSnapshots={loadPlanSnapshots}
              />
            )}

            {activeTab === 'tasks' && (
              <AgentAssignmentBoard taskItems={taskItems} />
            )}
          </>
        )}
      </div>
    </div>
  );
};
