/**
 * CollabTaskWindow — the single collaboration-task window opened from a
 * [[COLLAB_TASK]] card.
 *
 * Everything comes from one call (GET /ai-web/collab-board/tasks/{id}/summary):
 * requirements, plan, assignment, progress, files, skills, the collab card and
 * the agent discussion list. The discussion list is the board's record, not
 * group-chat history, so it stays complete no matter how chat pages or what the
 * group announcement throttled.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Check, CheckCircle2, ChevronDown, Circle, Copy, Loader2, Target, X } from 'lucide-react';

import { AI_MARKDOWN_CLASS, renderFencedMarkdown } from '../utils/fencedMarkdown';
import { type ParsedStep, PlanStatusIcon, groupByWorker, parseTaskSteps } from '../utils/taskSteps';
import { CollabTaskComposer } from './CollabTaskComposer';
import {
  SERVER_BASE_URL,
  collabBoardAPI,
  messageAPI,
  type CollabBoardItem,
  type CollabBoardSummary,
} from '../services/api';
import { OpenSquadLoader } from './OpenSquadLoader';

/**
 * What a gate should say.
 *
 * The approval item is the user's verdict, but a gate with **no** approval card is not
 * "not started": a task can be fully assigned while nobody posted a card for 任务分配,
 * and the window used to claim 未开始 next to the assignments it was showing. With no
 * card, read the board instead.
 */
export function gateDisplayFor(
  gate: string,
  ctx: { approval?: CollabBoardItem | null; counts: Record<string, number>; taskDone?: boolean },
): { key: 'gateApproved' | 'gateRejected' | 'gatePending' | 'gateDoing' | 'gateNone'; count?: number } {
  const status = String(ctx.approval?.status || '').toLowerCase();
  if (status === 'approved') return { key: 'gateApproved' };
  if (status === 'rejected') return { key: 'gateRejected' };
  if (status === 'pending') return { key: 'gatePending' };
  const n = Number(ctx.counts?.[gate] || 0);
  if (n > 0) return { key: 'gateDoing', count: n };
  if (ctx.taskDone && (gate === '任务验收' || gate === '确定需求')) return { key: 'gateApproved' };
  return { key: 'gateNone' };
}

const POLL_MS = 5000;

/** Local time for a board timestamp (message bubbles and gate rows both show it). */
const fmtTime = (iso?: string) => {
  if (!iso) return '';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
};

export interface CollabTaskWindowProps {
  collabId: string;
  onClose: () => void;
  /** The signed-in user's display name — their own messages bubble on the right. */
  viewerName?: string;
}

/**
 * The board's text is written by agents, so it is Markdown (headings, bold, lists,
 * tables). Rendered plain it showed its own syntax — `## 主任务`, `**负责人**: pm` —
 * where the group-chat board renders it. Same renderer and class as the chat surfaces.
 */
export const MarkdownText: React.FC<{ text?: string; className?: string; tone?: 'muted' | 'main' }> = ({
  text,
  className = '',
  tone = 'muted',
}) => {
  const html = useMemo(() => renderFencedMarkdown(String(text || '')), [text]);
  if (!text) return null;
  return (
    <div
      className={`${AI_MARKDOWN_CLASS} break-words text-[12px] leading-relaxed ${
        tone === 'main' ? 'text-textMain' : 'text-textMuted'
      } ${className}`}
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
};

/** An attachment as the chat draws it: pictures inline, everything else a chip. */
const AttachmentRow: React.FC<{ attachments: any[] }> = ({ attachments }) => {
  if (!attachments.length) return null;
  const abs = (url: string) => (url.startsWith('http') ? url : `${SERVER_BASE_URL}${url}`);
  return (
    <div className="mt-1 flex flex-wrap gap-1.5">
      {attachments.map((a, i) => {
        const url = String(a?.url || '');
        const name = String(a?.name || url || 'file');
        const isImage = String(a?.type || '') === 'image';
        return (
          <a
            key={`${url}:${i}`}
            href={abs(url)}
            target="_blank"
            rel="noopener noreferrer"
            download
            data-testid="collab-task-bubble-attachment"
            className="flex min-w-0 flex-col gap-0.5 rounded-lg border border-border/60 bg-panel/60 p-1 hover:border-primary/40"
          >
            {isImage ? (
              <img src={abs(url)} alt={name} loading="lazy" className="max-h-40 w-auto rounded object-cover" />
            ) : null}
            <span className="max-w-[12rem] truncate text-[11px] text-textMain">{name}</span>
          </a>
        );
      })}
    </div>
  );
};

/**
 * One message in the task thread.
 *
 * Same bubble as the group chat (own on the right, everyone else on the left, the
 * chat bubble colours), with the attachments inside it: a picture sent with a sentence
 * belongs beside that sentence, not in a separate section at the bottom.
 */
export const DiscussionBubble: React.FC<{ item: CollabBoardItem; self?: boolean }> = ({ item, self = false }) => {
  const attachments = Array.isArray(item.extra?.attachments) ? (item.extra?.attachments as any[]) : [];
  return (
    <div
      className={`flex gap-2 ${self ? 'flex-row-reverse' : ''}`}
      data-testid="collab-task-bubble"
      data-self={self ? '1' : '0'}
    >
      <div
        className={`min-w-0 max-w-[min(85%,36rem)] rounded-2xl border px-2.5 py-1.5 ${
          self
            ? 'rounded-tr-sm border-border bg-chatBubbleSelf'
            : 'rounded-tl-sm border-border bg-chatBubbleOther'
        }`}
      >
        <div className={`flex items-center gap-2 text-[10px] text-textMuted ${self ? 'flex-row-reverse' : ''}`}>
          <span className="truncate font-semibold text-textMain">{item.agent_id}</span>
          <span className="shrink-0">{fmtTime(item.created_at)}</span>
        </div>
        <MarkdownText text={item.content} tone="main" />
        <AttachmentRow attachments={attachments} />
      </div>
    </div>
  );
};

/**
 * One board entry as a document card: agent-written Markdown, folded when long, with the
 * item's status as a chip.
 *
 * No progress bar and no second list of subtasks. A bar said "40%" for a task whose steps
 * the window was already listing underneath it, and the same subtasks were rendered twice
 * — once inside the pasted plan text and once as rows. Steps are rows now (see StepRow),
 * and a percentage nobody can act on is gone.
 */
export const ItemBlock: React.FC<{ item: CollabBoardItem; showAssignee?: boolean }> = ({
  item,
  showAssignee,
}) => {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const long = String(item.content || '').length > 180 || String(item.content || '').split('\n').length > 4;
  return (
    <div className="rounded-lg border border-border bg-bgLight px-2.5 py-2">
      <div className="flex items-center gap-2">
        <span className="min-w-0 flex-1 truncate text-[12px] font-semibold text-textMain">{item.title || item.item_key}</span>
        {showAssignee ? <span className="shrink-0 text-[11px] text-textMuted">@{item.agent_id}</span> : null}
        <span className="shrink-0 rounded bg-panel px-1.5 py-0.5 text-[10px] text-textMuted">{item.status}</span>
      </div>
      {item.content ? (
        <div className={long && !open ? 'max-h-24 overflow-hidden' : undefined} data-testid="collab-item-body">
          <MarkdownText text={item.content} className="mt-1" />
        </div>
      ) : null}
      {long ? (
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          data-testid="collab-item-expand"
          className="mt-1 rounded border border-border px-1.5 py-0.5 text-[10px] text-textMuted hover:bg-primary/10 hover:text-textMain"
        >
          {open ? t('collabTask.collapse', { defaultValue: '收起' }) : t('collabTask.expand', { defaultValue: '展开' })}
        </button>
      ) : null}
    </div>
  );
};

/**
 * One step of a task, drawn the way the collaboration board draws it: status icon, title,
 * and a chevron that opens that step's own detail.
 */
export const StepRow: React.FC<{ step: ParsedStep; testId?: string }> = ({
  step,
  testId = 'collab-task-step',
}) => {
  const [open, setOpen] = useState(false);
  const detail = String(step.detail || '').trim();
  return (
    <div
      className="overflow-hidden rounded border border-border/50 bg-panel/40"
      data-testid={testId}
      data-status={step.status}
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between gap-2 px-2 py-1.5 text-left hover:bg-primary/5"
      >
        <span className="flex min-w-0 items-center gap-1.5">
          <PlanStatusIcon status={step.status} />
          <span
            className={`truncate text-[12px] ${
              step.status === 'done'
                ? 'text-textMuted line-through'
                : step.status === 'doing'
                  ? 'font-medium text-textMain'
                  : 'text-textMuted'
            }`}
          >
            {step.title}
          </span>
        </span>
        {detail ? (
          <ChevronDown
            size={12}
            className={`shrink-0 text-textMuted transition-transform ${open ? '' : '-rotate-90'}`}
          />
        ) : null}
      </button>
      {open && detail ? <MarkdownText text={detail} className="px-2 pb-1.5" /> : null}
    </div>
  );
};

/** The worker's plan text (文件范围, 依赖, 验收标准 …), kept but folded away. */
const PlanFold: React.FC<{ item: CollabBoardItem }> = ({ item }) => {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const text = String(item.content || '');
  if (!text) return null;
  return (
    <>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        data-testid="collab-task-plan-toggle"
        className="rounded border border-border px-1.5 py-0.5 text-[10px] text-textMuted hover:bg-primary/10 hover:text-textMain"
      >
        {open
          ? t('collabTask.collapse', { defaultValue: '收起' })
          : t('collabTask.planDetails', { defaultValue: '分配详情' })}
      </button>
      {open ? (
        <div className="rounded border border-border/50 bg-bgLight px-2 py-1.5">
          <MarkdownText text={text} />
        </div>
      ) : null}
    </>
  );
};

/** How many items / steps a group holds, the way the board counts them. */
const Count: React.FC<{ n: number; suffix?: string }> = ({ n, suffix }) => {
  const { t } = useTranslation();
  return (
    <span className="shrink-0 text-[10px] text-textMuted">
      {t('collabTask.itemCount', { count: n, defaultValue: '{{count}} 项' })}
      {suffix ? ` · ${suffix}` : ''}
    </span>
  );
};

/**
 * One worker's assignments, grouped and titled like the board's 任务分配区: the worker,
 * how many items, then per task a header with its steps underneath.
 */
export const AssignmentGroup: React.FC<{ agentId: string; tasks: CollabBoardItem[] }> = ({
  agentId,
  tasks,
}) => {
  const { t } = useTranslation();
  return (
    <div className="rounded-xl border border-border bg-panel p-3" data-testid="collab-task-group" data-agent={agentId}>
      <div className="mb-2 flex items-center justify-between gap-2">
        <span className="min-w-0 truncate text-[12px] font-semibold text-primary">@{agentId}</span>
        <Count n={tasks.length} />
      </div>
      <div className="space-y-2">
        {tasks.map((task) => {
          const steps = parseTaskSteps(task, t);
          return (
            <div key={task.id} className="space-y-1" data-testid="collab-task-subgroup">
              <div className="flex items-center gap-1.5">
                <Target size={12} className="shrink-0 text-primary" />
                <span className="min-w-0 flex-1 truncate text-[12px] font-semibold text-primary">
                  {task.title || task.item_key}
                </span>
                <Count n={steps.length} />
              </div>
              {steps.map((step, i) => (
                <StepRow key={`${task.id}:${i}`} step={step} />
              ))}
              <PlanFold item={task} />
            </div>
          );
        })}
      </div>
    </div>
  );
};

/** A worker's progress notes, grouped the same way the assignments are. */
export const ProgressGroup: React.FC<{ agentId: string; items: CollabBoardItem[] }> = ({
  agentId,
  items,
}) => (
  <div className="rounded-xl border border-border bg-panel p-3" data-testid="collab-progress-group" data-agent={agentId}>
    <div className="mb-2 flex items-center justify-between gap-2">
      <span className="min-w-0 truncate text-[12px] font-semibold text-primary">@{agentId}</span>
      <Count n={items.length} />
    </div>
    <div className="space-y-1">
      {items.map((it) => (
        <StepRow
          key={it.id}
          testId="collab-progress-step"
          step={{
            title: String(it.title || it.item_key || ''),
            detail: String(it.content || ''),
            status: it.status || 'doing',
          }}
        />
      ))}
    </div>
  </div>
);

const Section: React.FC<{ title: string; count?: number; children: React.ReactNode }> = ({ title, count, children }) => (
  <div className="mt-3">
    <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-textMuted">
      {title}
      {typeof count === 'number' ? <span className="ml-1 font-normal">({count})</span> : null}
    </div>
    {children}
  </div>
);

const Empty: React.FC = () => {
  const { t } = useTranslation();
  return <div className="text-[12px] text-textMuted">{t('collabTask.empty')}</div>;
};

export const CollabTaskWindow: React.FC<CollabTaskWindowProps> = ({ collabId, onClose, viewerName = '' }) => {
  const { t } = useTranslation();
  const [summary, setSummary] = useState<CollabBoardSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  const load = useCallback(
    async (silent = false) => {
      if (!collabId) return;
      if (!silent) setLoading(true);
      try {
        const data = await collabBoardAPI.taskSummary(collabId);
        setSummary(data);
        setError(false);
      } catch {
        setError(true);
      } finally {
        if (!silent) setLoading(false);
      }
    },
    [collabId],
  );

  useEffect(() => {
    setSummary(null);
    void load();
  }, [load]);

  useEffect(() => {
    const timer = window.setInterval(() => void load(true), POLL_MS);
    return () => window.clearInterval(timer);
  }, [load]);

  const items = summary?.items || {};
  const requirements = [...(items.requirement || []), ...(items.requirement_doc || [])];
  const plans = items.plan || [];
  const tasks = items.task || [];
  const statuses = items.status || [];
  const discussions = [...(items.discussion || [])].sort((a, b) =>
    String(a.created_at || '').localeCompare(String(b.created_at || '')),
  );

  // Approval gates (四门闸) live on the board as item_type="approval"; each item
  // carries extra.approval.step and the group message it was posted as, so the
  // window can both show the gate flow and resolve a pending one in place.
  const approvals = items.approval || [];
  const groupId = String((summary?.task?.extra as Record<string, any> | undefined)?.group_id || '');
  // Where the project lives on disk — filled in by the PM, shown beside the task's files.
  const projectDir = String(summary?.project_dir || '');
  const [copiedPath, setCopiedPath] = useState(false);
  const copyProjectDir = () => {
    void navigator.clipboard?.writeText(projectDir).then(() => {
      setCopiedPath(true);
      window.setTimeout(() => setCopiedPath(false), 1500);
    });
  };
  const GATES = ['确定需求', '讨论方案', '任务分配', '任务验收'];

  const gateOf = (item: CollabBoardItem) => String((item.extra?.approval as any)?.step || '');
  const latestForGate = (gate: string) =>
    approvals
      .filter((a) => gateOf(a) === gate)
      .sort((a, b) => String(a.updated_at || '').localeCompare(String(b.updated_at || '')))
      .pop() || null;
  const otherApprovals = approvals.filter((a) => !GATES.includes(gateOf(a)));
  // Content on the board per gate, for the gates nobody posted a card for.
  const gateCounts: Record<string, number> = {
    确定需求: requirements.length,
    讨论方案: plans.length,
    任务分配: tasks.length,
  };
  const gateDisplay = (gate: string) =>
    gateDisplayFor(gate, {
      approval: latestForGate(gate),
      counts: gateCounts,
      taskDone: ['done', 'archived'].includes(String(summary?.status || '')),
    });
  // The "nothing has started" notice is only true while the board is empty: with
  // requirements, a plan or assignments on it, a missing gate card is not a blocker.
  const boardStarted = requirements.length + plans.length + tasks.length + statuses.length > 0;
  const blockingGate = boardStarted
    ? ''
    : GATES.find((gate) => gateDisplay(gate).key !== 'gateApproved') || '';

  const [resolving, setResolving] = useState<string | null>(null);
  const [resolveError, setResolveError] = useState(false);

  const resolveApproval = async (item: CollabBoardItem, action: 'approve' | 'reject') => {
    const approvalId = String(item.item_key || '');
    const messageId = String((item.extra as any)?.message_id || '');
    if (!groupId || !approvalId || !messageId) return;
    setResolving(approvalId);
    setResolveError(false);
    try {
      await messageAPI.resolveCollabApproval(groupId, approvalId, action, { messageId });
      await load(true);
    } catch {
      setResolveError(true);
    } finally {
      setResolving(null);
    }
  };

  const statusKey = (status?: string) => {
    if (status === 'approved') return 'gateApproved';
    if (status === 'rejected') return 'gateRejected';
    if (status === 'pending') return 'gatePending';
    return 'gateNone';
  };

  return (
    <div className="h-full min-h-0 flex flex-col">
      <div className="shrink-0 border-b border-border px-4 py-3 flex items-center gap-2">
        <span className="min-w-0 flex-1 truncate text-sm font-bold text-textMain">
          {summary?.title || t('collabTask.title')}
        </span>
        {summary ? (
          <span className="shrink-0 rounded-md bg-bgLight px-1.5 py-0.5 text-[11px] text-textMuted">
            {summary.status || 'active'}
          </span>
        ) : null}
        <span className="shrink-0 font-mono text-[11px] text-textMuted">{collabId}</span>
        <button
          type="button"
          onClick={onClose}
          className="shrink-0 rounded-lg p-1 text-textMuted hover:bg-primary/10 hover:text-textMain"
          aria-label={t('common.close')}
        >
          <X size={14} />
        </button>
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto px-4 py-3">
        {loading && !summary ? (
          <div className="h-40 flex items-center justify-center">
            <OpenSquadLoader size={26} />
          </div>
        ) : error && !summary ? (
          <div className="py-3 text-[12px] text-rose-500">{t('collabTask.loadFailed')}</div>
        ) : (
          <>
            <Section title={t('collabTask.card')} count={summary?.card ? 1 : 0}>
              {summary?.card ? (
                <div className="rounded-lg border border-border bg-bgLight px-2.5 py-2 text-[12px] text-textMain">
                  {summary.card}
                </div>
              ) : (
                <Empty />
              )}
            </Section>

            <Section title={t('collabTask.skills')} count={summary?.skills?.length || 0}>
              {(summary?.skills || []).length ? (
                <div className="flex flex-wrap gap-1.5">
                  {(summary?.skills || []).map((s) => (
                    <span key={s} className="rounded-lg border border-border px-2 py-0.5 text-[11px] text-textMuted">
                      {s}
                    </span>
                  ))}
                </div>
              ) : (
                <Empty />
              )}
            </Section>

            {(summary?.participants || []).length ? (
              <Section title={t('collabTask.participants')} count={summary?.participants?.length}>
                <div className="flex flex-wrap gap-1.5">
                  {(summary?.participants || []).map((p) => (
                    <span
                      key={p.agent_id}
                      className="rounded-lg border border-border px-2 py-0.5 text-[11px] text-textMain"
                    >
                      {p.name || p.agent_id} · {t(`collabTask.state.${p.state}`, { defaultValue: p.state })}
                    </span>
                  ))}
                </div>
              </Section>
            ) : null}

            <Section title={t('collabTask.approvals')} count={approvals.length}>
              <div className="flex flex-wrap gap-1.5" data-testid="collab-task-gates">
                {GATES.map((gate) => {
                  const display = gateDisplay(gate);
                  const key = display.key;
                  const cls =
                    key === 'gateApproved'
                      ? 'border-emerald-500/40 text-emerald-600'
                      : key === 'gateRejected'
                        ? 'border-rose-500/40 text-rose-500'
                        : key === 'gatePending'
                          ? 'border-amber-500/40 text-amber-600'
                          : key === 'gateDoing'
                            ? 'border-sky-500/40 text-sky-600'
                            : 'border-border text-textMuted';
                  const label =
                    key === 'gateDoing'
                      ? gate === '任务分配'
                        ? t('collabTask.gateAssigned', { defaultValue: '已分配 {{n}} 项', n: display.count })
                        : t('collabTask.gateDoing', { defaultValue: '已有内容' })
                      : t(`collabTask.${key}`);
                  const Icon = key === 'gateApproved' ? CheckCircle2 : key === 'gateNone' ? Circle : Loader2;
                  return (
                    <span
                      key={gate}
                      className={`inline-flex items-center gap-1 rounded-lg border px-2 py-0.5 text-[11px] ${cls}`}
                    >
                      <Icon size={11} />
                      {gate}
                      <span className="text-[10px] opacity-80">· {label}</span>
                    </span>
                  );
                })}
              </div>

              {approvals.length ? (
                <div
                  className="mt-2 space-y-1.5"
                  data-testid="collab-task-approvals"
                  data-others={otherApprovals.length}
                >
                  {[...approvals]
                    .sort((a, b) => {
                      const ga = GATES.includes(gateOf(a)) ? 0 : 1;
                      const gb = GATES.includes(gateOf(b)) ? 0 : 1;
                      return ga - gb || String(a.updated_at || '').localeCompare(String(b.updated_at || ''));
                    })
                    .map((item) => {
                      const meta = (item.extra?.approval || {}) as Record<string, any>;
                      const approvalId = String(item.item_key || '');
                      const messageId = String((item.extra as any)?.message_id || '');
                      // Only a gate posted into a group can be resolved from here.
                      const canResolve = item.status === 'pending' && !!groupId && !!messageId;
                      return (
                        <div key={item.id} className="rounded-lg border border-border bg-bgLight px-2.5 py-2">
                          <div className="flex items-center gap-2">
                            <span className="min-w-0 flex-1 truncate text-[12px] font-semibold text-textMain">
                              {String(meta.step || item.title || '')}
                            </span>
                            <span className="shrink-0 rounded bg-panel px-1.5 py-0.5 text-[10px] text-textMuted">
                              {t(`collabTask.${statusKey(item.status)}`)}
                            </span>
                          </div>
                          {item.content ? <MarkdownText text={item.content} className="mt-1" /> : null}
                          <div className="mt-1 flex flex-wrap items-center gap-2 text-[10px] text-textMuted">
                            {meta.agent_name || meta.agent_id || item.agent_id ? (
                              <span>
                                {t('collabTask.requestedBy')}:{' '}
                                {String(meta.agent_name || meta.agent_id || item.agent_id)}
                              </span>
                            ) : null}
                            {meta.resolved_by_name ? (
                              <span>
                                {t('collabTask.resolvedBy')}: {String(meta.resolved_by_name)}
                              </span>
                            ) : null}
                            {meta.resolve_note ? <span>· {String(meta.resolve_note)}</span> : null}
                          </div>
                          {item.status === 'pending' ? (
                            canResolve ? (
                              <div className="mt-1.5 flex gap-2">
                                <button
                                  type="button"
                                  disabled={resolving === approvalId}
                                  onClick={() => void resolveApproval(item, 'approve')}
                                  className="rounded-lg bg-primary px-2.5 py-1 text-[12px] text-white hover:opacity-90 disabled:opacity-50"
                                >
                                  {t('collabTask.approve')}
                                </button>
                                <button
                                  type="button"
                                  disabled={resolving === approvalId}
                                  onClick={() => void resolveApproval(item, 'reject')}
                                  className="rounded-lg border border-border px-2.5 py-1 text-[12px] text-textMain hover:bg-primary/10 disabled:opacity-50"
                                >
                                  {t('collabTask.reject')}
                                </button>
                              </div>
                            ) : (
                              <div className="mt-1 text-[10px] text-textMuted">{t('collabTask.approvalUnavailable')}</div>
                            )
                          ) : null}
                        </div>
                      );
                    })}
                </div>
              ) : null}
              {resolveError ? (
                <div className="mt-1 text-[11px] text-rose-500">{t('collabTask.resolveFailed')}</div>
              ) : null}
            </Section>

            <Section title={t('collabTask.requirement')} count={requirements.length}>
              {requirements.length ? (
                <div className="space-y-1.5">
                  {requirements.map((it) => (
                    <ItemBlock key={it.id} item={it} />
                  ))}
                </div>
              ) : (
                <Empty />
              )}
            </Section>

            <Section title={t('collabTask.plan')} count={plans.length}>
              {plans.length ? (
                <div className="space-y-1.5">
                  {plans.map((it) => (
                    <ItemBlock key={it.id} item={it} />
                  ))}
                </div>
              ) : (
                <Empty />
              )}
            </Section>

            <Section title={t('collabTask.assign')} count={tasks.length}>
              {tasks.length ? (
                <div className="space-y-2" data-testid="collab-task-assignments">
                  {groupByWorker(tasks).map(({ agentId, items }) => (
                    <AssignmentGroup key={agentId} agentId={agentId} tasks={items} />
                  ))}
                </div>
              ) : (
                <Empty />
              )}
            </Section>

            <Section title={t('collabTask.progress')} count={statuses.length}>
              {blockingGate ? (
                <div
                  className="mb-1.5 rounded-lg border border-amber-500/40 bg-amber-500/5 px-2.5 py-1 text-[11px] text-amber-600"
                  data-testid="collab-task-blocking-gate"
                >
                  {t('collabTask.waitingGate', {
                    defaultValue: '任务尚未开始：等待「{{gate}}」获批',
                    gate: blockingGate,
                  })}
                </div>
              ) : null}
              {statuses.length ? (
                <div className="space-y-2" data-testid="collab-task-progress-groups">
                  {groupByWorker(statuses).map(({ agentId, items }) => (
                    <ProgressGroup key={agentId} agentId={agentId} items={items} />
                  ))}
                </div>
              ) : (
                <Empty />
              )}
            </Section>

            <Section title={t('collabTask.projectDir')} count={projectDir ? 1 : 0}>
              {projectDir ? (
                <div className="flex items-center gap-2">
                  <code
                    className="min-w-0 flex-1 truncate rounded-lg border border-border bg-bgLight px-2 py-1 font-mono text-[11px] text-textMain"
                    data-testid="collab-project-dir"
                    title={projectDir}
                  >
                    {projectDir}
                  </code>
                  <button
                    type="button"
                    onClick={copyProjectDir}
                    className="shrink-0 rounded-lg border border-border p-1 text-textMuted hover:bg-primary/10 hover:text-textMain"
                    aria-label={t('collabTask.copyProjectDir')}
                  >
                    {copiedPath ? <Check size={13} className="text-emerald-600" /> : <Copy size={13} />}
                  </button>
                </div>
              ) : (
                <div className="text-[12px] text-textMuted" data-testid="collab-project-dir-missing">
                  {t('collabTask.projectDirMissing')}
                </div>
              )}
            </Section>
            <Section title={t('collabTask.attachments')} count={summary?.attachments?.length || 0}>
              {(summary?.attachments || []).length ? (
                <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                  {(summary?.attachments || []).map((a) => {
                    const href = a.url.startsWith('http') ? a.url : `${SERVER_BASE_URL}${a.url}`;
                    return (
                      <a
                        key={a.id || a.url}
                        href={href}
                        target="_blank"
                        rel="noopener noreferrer"
                        download
                        title={t('collabTask.openAttachment')}
                        data-testid="collab-attachment"
                        className="flex min-w-0 flex-col gap-1 rounded-lg border border-border bg-bgLight p-2 hover:border-primary/40"
                      >
                        {a.kind === 'image' ? (
                          <img
                            src={href}
                            alt={a.name}
                            loading="lazy"
                            className="h-24 w-full rounded object-cover"
                          />
                        ) : null}
                        <span className="truncate text-[11px] text-textMain">{a.name}</span>
                        <span className="truncate text-[10px] text-textMuted">
                          {[a.size, a.uploader ? `@${a.uploader}` : ''].filter(Boolean).join(' · ')}
                        </span>
                      </a>
                    );
                  })}
                </div>
              ) : (
                <Empty />
              )}
            </Section>

            <Section title={t('collabTask.files')} count={summary?.files?.length || 0}>
              {(summary?.files || []).length ? (
                <div className="space-y-0.5">
                  {(summary?.files || []).map((f) => (
                    <div key={f} className="truncate font-mono text-[11px] text-textMain">
                      {f}
                    </div>
                  ))}
                </div>
              ) : (
                <Empty />
              )}
            </Section>

            <Section title={t('collabTask.discussion')} count={discussions.length}>
              {discussions.length ? (
                <div className="space-y-2" data-testid="collab-task-thread">
                  {discussions.map((it) => (
                    <DiscussionBubble key={it.id} item={it} self={!!viewerName && it.agent_id === viewerName} />
                  ))}
                </div>
              ) : (
                <Empty />
              )}
            </Section>
          </>
        )}
      </div>
      <CollabTaskComposer collabId={collabId} onSent={() => void load(true)} />
    </div>
  );
};

export default CollabTaskWindow;
