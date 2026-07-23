/**
 * ExecWorkflowView — embedded Agent Web chat for a scheduled-task execution.
 *
 * Live WS timeline (dialogue → tool-flow → dialogue) plus token stats, matching
 * the main Agent Web pane. Disk hydrate is a fallback; soft-poll alone is not
 * used while a session is open (it caused clumped logs and delayed follow-ups).
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Clock, Pencil, Zap, Loader2, Square } from 'lucide-react';
import {
  modelCardAPI,
  scheduledTaskAPI,
  skillAPI,
  type ModelCardInfo,
  type ScheduledExecution,
  type ScheduledTask,
  type SkillInfo,
} from '../../services/api';
import { SessionChatPane } from './SessionChatPane';
import { AgentWebComposer, type ComposerSendPayload } from './AgentWebComposer';
import { type AgentMode } from './ModePicker';
import { type ReasoningEffort } from './EffortPicker';
import { useExecSessionLiveTimeline } from '../../hooks/useExecSessionLiveTimeline';

interface Props {
  agentName: string;
  rootPath: string;
  exec: ScheduledExecution;
  task: ScheduledTask | null;
  onRunAgain: () => void;
  onEdit: () => void;
  onStopped?: () => void;
}

const StatusBadge: React.FC<{ status: string }> = ({ status }) => {
  const map: Record<string, { c: string; label: string }> = {
    running: { c: 'bg-sky-500/15 text-sky-600', label: 'running' },
    success: { c: 'bg-emerald-500/15 text-emerald-600', label: 'success' },
    failed: { c: 'bg-rose-500/15 text-rose-600', label: 'failed' },
    missed: { c: 'bg-amber-500/15 text-amber-600', label: 'missed' },
  };
  const m = map[status] || map.success;
  return (
    <span className={`inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[10px] font-medium ${m.c}`}>
      {status === 'running' ? <Loader2 size={10} className="animate-spin" /> : null}
      {m.label}
    </span>
  );
};

const fmtTime = (ts: number | null) =>
  ts ? new Date(ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '--';

export const ExecWorkflowView: React.FC<Props> = ({ agentName, rootPath, exec, task, onRunAgain, onEdit, onStopped }) => {
  const { t } = useTranslation();
  const [cards, setCards] = useState<ModelCardInfo[]>([]);
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [modelCard, setModelCard] = useState<string>(task?.model_card || '');
  const [effort, setEffort] = useState<ReasoningEffort>('medium');
  const [mode, setMode] = useState<AgentMode>('build');
  const [sending, setSending] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [sendErr, setSendErr] = useState<string | null>(null);

  // Session history + live events live on the delegated agent.
  const sessionAgentId = (task?.delegate_agent || agentName || '').trim() || agentName;

  const { timeline, tokenStats, appendOptimisticUser, busy: liveBusy } = useExecSessionLiveTimeline(
    sessionAgentId,
    exec.session_id,
  );

  useEffect(() => {
    modelCardAPI.getCards().then((r) => setCards(r.cards || [])).catch(() => {});
    skillAPI.getSkills().then((r) => setSkills(r.skills || [])).catch(() => {});
  }, []);

  // Keep the composer's model in sync when switching executions.
  useEffect(() => {
    setModelCard(task?.model_card || '');
    setSendErr(null);
  }, [exec.id, task?.model_card]);

  const selectedCard = useMemo(
    () => cards.find((c) => c.name === modelCard) || null,
    [cards, modelCard],
  );

  const onSend = useCallback(
    async (payload: ComposerSendPayload) => {
      if (!exec.session_id) {
        setSendErr(t('scheduledTasks.noSessionYet'));
        return;
      }
      const text = payload.text.trim();
      if (!text) return;
      setSending(true);
      setSendErr(null);
      // Optimistic: show the user bubble immediately under the prior turn,
      // then stream agent tool-flow via WS (not a delayed HTTP poll rebuild).
      appendOptimisticUser(text);
      try {
        await scheduledTaskAPI.sendFollowup(agentName, exec.id, text, modelCard);
      } catch (e: any) {
        setSendErr(e?.message || t('scheduledTasks.sendFailed'));
      } finally {
        setSending(false);
      }
    },
    [agentName, exec.id, exec.session_id, modelCard, t, appendOptimisticUser],
  );

  const handleStop = useCallback(async () => {
    setStopping(true);
    try {
      await scheduledTaskAPI.stopExecution(agentName, exec.id);
      onStopped?.();
    } catch (e: any) {
      setSendErr(e?.message || t('scheduledTasks.stopFailed'));
    } finally {
      setStopping(false);
    }
  }, [agentName, exec.id, onStopped, t]);

  const hasSession = !!exec.session_id;

  return (
    <div className="flex flex-col h-full min-h-0">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-border shrink-0">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <h3 className="text-sm font-semibold truncate">{exec.task_name}</h3>
            <StatusBadge status={exec.status} />
          </div>
          <div className="mt-0.5 text-[10px] text-textMuted truncate">
            {fmtTime(exec.started_at)}{exec.manual ? ' · manual' : ''}{exec.session_id ? ` · ${exec.session_id.slice(-8)}` : ''}
          </div>
        </div>
        <div className="flex items-center gap-1.5 shrink-0">
          {task && (
            <button type="button" onClick={onRunAgain}
              className="inline-flex items-center gap-1 px-2 py-1 rounded-lg text-[11px] font-medium border border-border hover:bg-black/5 dark:hover:bg-white/10">
              <Zap size={11} className="text-amber-500" /> {t('scheduledTasks.runAgain')}
            </button>
          )}
          {task && (
            <button type="button" onClick={onEdit}
              className="inline-flex items-center gap-1 px-2 py-1 rounded-lg text-[11px] font-medium border border-border hover:bg-black/5 dark:hover:bg-white/10">
              <Pencil size={11} /> {t('scheduledTasks.edit')}
            </button>
          )}
          {exec.status === 'running' && (
            <button type="button" onClick={handleStop} disabled={stopping}
              className="inline-flex items-center gap-1 px-2 py-1 rounded-lg text-[11px] font-medium border border-rose-500/40 text-rose-600 hover:bg-rose-500/10 disabled:opacity-50">
              <Square size={11} /> {stopping ? t('scheduledTasks.stopping') : t('scheduledTasks.stop')}
            </button>
          )}
        </div>
      </div>

      {/* Timeline — live WS when session exists */}
      <div className="flex-1 min-h-0 flex flex-col">
        {hasSession ? (
          <SessionChatPane
            key={`exec-${exec.id}-${exec.session_id}`}
            agentId={sessionAgentId}
            sessionId={exec.session_id!}
            liveTimeline={timeline}
            isSolo
            columnClass="max-w-3xl mx-auto w-full"
            agentName={task?.delegate_agent || agentName}
          />
        ) : (
          <div className="flex-1 flex items-center justify-center text-[12px] text-textMuted px-6 text-center">
            <div>
              <Clock size={20} className="mx-auto mb-2 text-textMuted/50" />
              {t('scheduledTasks.noSessionYet')}
            </div>
          </div>
        )}
      </div>

      {sendErr && (
        <div className="px-4 py-1 text-[11px] text-rose-500 shrink-0">{sendErr}</div>
      )}

      {/* Composer — full Agent Web input bar */}
      <AgentWebComposer
        agentId={agentName}
        columnClass="max-w-3xl mx-auto w-full"
        disabled={!hasSession}
        busy={sending || liveBusy}
        agentMode={mode}
        onModeChange={setMode}
        modelCards={cards}
        currentCardName={modelCard || null}
        modelName={selectedCard?.model_name || ''}
        fallbackLabel={t('scheduledTasks.fModelDefault')}
        onSelectModel={(name) => setModelCard(name)}
        reasoningEffort={effort}
        onEffortChange={setEffort}
        cwd={task?.workspace || rootPath}
        tokenStats={tokenStats}
        availableSkills={skills}
        skillsLoading={false}
        onOpenSkills={() => window.dispatchEvent(new CustomEvent('switchView', { detail: 'skills' }))}
        onSend={onSend}
        onStop={() => { void handleStop(); }}
        onActivate={() => {}}
      />
    </div>
  );
};

export default ExecWorkflowView;
