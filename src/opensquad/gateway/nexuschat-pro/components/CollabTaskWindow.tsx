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
import React, { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { CheckCircle2, Circle, Loader2, X } from 'lucide-react';
import { collabBoardAPI, type CollabBoardItem, type CollabBoardSummary } from '../services/api';
import { OpenSquadLoader } from './OpenSquadLoader';

const POLL_MS = 5000;

export interface CollabTaskWindowProps {
  collabId: string;
  onClose: () => void;
}

const SubTaskRow: React.FC<{ id: string; title: string; status: string }> = ({ title, status }) => (
  <div className="flex items-center gap-1.5 text-[12px] text-textMain">
    {status === 'done' ? (
      <CheckCircle2 size={12} className="shrink-0 text-emerald-600" />
    ) : status === 'doing' ? (
      <Loader2 size={12} className="shrink-0 text-amber-500" />
    ) : (
      <Circle size={12} className="shrink-0 text-textMuted" />
    )}
    <span className="min-w-0 truncate">{title}</span>
  </div>
);

const ItemBlock: React.FC<{ item: CollabBoardItem; showAssignee?: boolean }> = ({ item, showAssignee }) => {
  const { t } = useTranslation();
  const subtasks = Array.isArray(item.extra?.subtasks) ? item.extra?.subtasks : [];
  return (
    <div className="rounded-lg border border-border bg-bgLight px-2.5 py-2">
      <div className="flex items-center gap-2">
        <span className="min-w-0 flex-1 truncate text-[12px] font-semibold text-textMain">{item.title || item.item_key}</span>
        {showAssignee ? <span className="shrink-0 text-[11px] text-textMuted">@{item.agent_id}</span> : null}
        <span className="shrink-0 rounded bg-panel px-1.5 py-0.5 text-[10px] text-textMuted">{item.status}</span>
      </div>
      {item.content ? (
        <div className="mt-1 whitespace-pre-wrap break-words text-[12px] leading-relaxed text-textMuted">{item.content}</div>
      ) : null}
      {subtasks.length ? (
        <div className="mt-1.5 space-y-0.5">
          {subtasks.map((st: any) => (
            <SubTaskRow key={st.id} id={st.id} title={st.title} status={st.status} />
          ))}
        </div>
      ) : null}
      {item.latest_tool_name ? (
        <div className="mt-1 truncate font-mono text-[10px] text-textMuted">
          {t('collabTask.latestTool')}: {item.latest_tool_name}
        </div>
      ) : null}
    </div>
  );
};

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

export const CollabTaskWindow: React.FC<CollabTaskWindowProps> = ({ collabId, onClose }) => {
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

  const fmtTime = (iso?: string) => {
    if (!iso) return '';
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? '' : d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  };

  return (
    <div className="h-full min-h-0 flex flex-col">
      <div className="shrink-0 border-b border-border px-4 py-3 flex items-center gap-2">
        <span className="min-w-0 flex-1 truncate text-sm font-bold text-textMain">
          {summary?.title || t('collabTask.title')}
        </span>
        {summary ? (
          <span className="shrink-0 rounded-md bg-bgLight px-1.5 py-0.5 text-[11px] text-textMuted">
            {summary.status || 'active'} · {summary.progress}%
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
                <div className="space-y-1.5">
                  {tasks.map((it) => (
                    <ItemBlock key={it.id} item={it} showAssignee />
                  ))}
                </div>
              ) : (
                <Empty />
              )}
            </Section>

            <Section title={t('collabTask.progress')} count={statuses.length}>
              {statuses.length ? (
                <div className="space-y-1.5">
                  {statuses.map((it) => (
                    <ItemBlock key={it.id} item={it} showAssignee />
                  ))}
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
                <div className="space-y-1.5">
                  {discussions.map((it) => (
                    <div key={it.id} className="rounded-lg border border-border bg-bgLight px-2.5 py-2">
                      <div className="flex items-center gap-2 text-[11px] text-textMuted">
                        <span className="truncate font-semibold text-textMain">{it.agent_id}</span>
                        <span className="shrink-0">{fmtTime(it.created_at)}</span>
                      </div>
                      <div className="mt-0.5 whitespace-pre-wrap break-words text-[12px] leading-relaxed text-textMain">
                        {it.content}
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <Empty />
              )}
            </Section>
          </>
        )}
      </div>
    </div>
  );
};

export default CollabTaskWindow;
