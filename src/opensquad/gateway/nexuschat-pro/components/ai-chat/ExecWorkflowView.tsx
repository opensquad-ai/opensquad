/**
 * ExecWorkflowView — embedded Agent Web chat for a scheduled-task execution.
 *
 * When sessionBridge is provided, reuses AIChatPage live timeline / token stats /
 * queue / stop / pending panel (stay on scheduled-tasks). Local hook is fallback.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Clock, Pencil, Zap, Loader2, Square } from 'lucide-react';
import {
  adminAPI,
  modelCardAPI,
  scheduledTaskAPI,
  skillAPI,
  type ModelCardInfo,
  type ScheduledExecution,
  type ScheduledTask,
  type SkillInfo,
} from '../../services/api';
import { getAiWsService } from '../../services/aiWebSocket';
import { SessionChatPane } from './SessionChatPane';
import { AgentWebComposer, type ComposerSendPayload } from './AgentWebComposer';
import { type AgentMode } from './ModePicker';
import { type ReasoningEffort } from './EffortPicker';
import { useExecSessionLiveTimeline } from '../../hooks/useExecSessionLiveTimeline';
import type { PaneSessionBridge } from './WorkspacePaneShell';

interface Props {
  agentName: string;
  rootPath: string;
  exec: ScheduledExecution;
  task: ScheduledTask | null;
  sessionBridge?: PaneSessionBridge;
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
    stopped: { c: 'bg-emerald-500/10 text-emerald-700', label: 'stopped' },
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

export const ExecWorkflowView: React.FC<Props> = ({
  agentName,
  rootPath,
  exec,
  task,
  sessionBridge,
  onRunAgain,
  onEdit,
  onStopped,
}) => {
  const { t } = useTranslation();
  const [cards, setCards] = useState<ModelCardInfo[]>([]);
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [modelCard, setModelCard] = useState<string>(task?.model_card || '');
  const [effort, setEffort] = useState<ReasoningEffort>('high');
  const [mode, setMode] = useState<AgentMode>('build');
  const [sending, setSending] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [compressing, setCompressing] = useState(false);
  const [sendErr, setSendErr] = useState<string | null>(null);
  /** Fallback when WS token_stats have not arrived yet for this session. */
  const [seedTokenStats, setSeedTokenStats] = useState<{
    used: number;
    max: number;
    breakdown?: any;
    session?: any;
  } | null>(null);

  // Prefer the workspace agent id (same WS the main Agent Web pane uses).
  const sessionAgentId = (agentName || task?.delegate_agent || '').trim() || agentName;
  const sid = (exec.session_id || '').trim();

  const {
    timeline: localTimeline,
    tokenStats: localTokenStats,
    appendOptimisticUser,
    sealOnStop,
    busy: liveBusy,
  } = useExecSessionLiveTimeline(sessionAgentId, exec.session_id, {
    // Bridge owns live WS; local hook only hydrates once. Competing 1.2s disk
    // polls rewrite the timeline and wipe text selection while running.
    diskPoll: !sessionBridge,
  });

  // Prefer Agent Web live bucket when bridge has one; else local hook (disk hydrate).
  // If bridge is empty/stale but local still has the finished assistant reply
  // (e.g. a follow-up briefly replaced the live bucket from disk), keep local.
  const bridgeTimeline = sid && sessionBridge?.getSessionLiveTimeline
    ? sessionBridge.getSessionLiveTimeline(sid)
    : null;
  const timeline = useMemo(() => {
    if (bridgeTimeline == null) return localTimeline;
    if (!bridgeTimeline.length) return localTimeline;
    if (localTimeline.length === 0) return bridgeTimeline;
    const asstCount = (entries: typeof localTimeline) =>
      entries.reduce((n, e) => {
        if (e.kind === 'message' && e.data.role === 'assistant' && String(e.data.content || '').trim()) {
          return n + 1;
        }
        return n;
      }, 0);
    if (asstCount(localTimeline) > asstCount(bridgeTimeline)) return localTimeline;
    return bridgeTimeline;
  }, [bridgeTimeline, localTimeline]);

  const bridgeTokenStats = sid && sessionBridge?.getSessionTokenStats
    ? sessionBridge.getSessionTokenStats(sid)
    : null;
  const tokenStats = bridgeTokenStats ?? localTokenStats;

  // Merge live WS + agent-file seed. Never let a used=0 seed/WS wipe a richer value.
  const displayTokenStats = useMemo(() => {
    const liveMax = Number(tokenStats?.max) || 0;
    const seedMax = Number(seedTokenStats?.max) || 0;
    const max = Math.max(liveMax, seedMax);
    if (max <= 0) return tokenStats;
    const liveUsed = Number(tokenStats?.used) || 0;
    const seedUsed = Number(seedTokenStats?.used) || 0;
    return {
      used: Math.max(liveUsed, seedUsed),
      max,
      breakdown: (liveUsed >= seedUsed ? tokenStats?.breakdown : undefined)
        ?? seedTokenStats?.breakdown
        ?? tokenStats?.breakdown,
      session: tokenStats?.session ?? seedTokenStats?.session,
    };
  }, [tokenStats, seedTokenStats]);

  const bridgeBusy = !!(sid && sessionBridge?.isSessionBusy?.(sid));
  const composerBusy = sessionBridge
    ? (sending || bridgeBusy)
    : (sending || liveBusy);

  // Seed model cards / skills / token stats; quietly refresh agent token_stats
  // so the ring shows even if a WS chunk was filtered or raced.
  useEffect(() => {
    modelCardAPI.getCards().then((r) => setCards(r.cards || [])).catch(() => {});
    skillAPI.getSkills().then((r) => setSkills(r.skills || [])).catch(() => {});

    let cancelled = false;
    const pullAgentStats = () => {
      adminAPI.getAgents().then((res) => {
        if (cancelled) return;
        const found = res.agents.find((a) => a.agent_id === sessionAgentId || a.dir_name === sessionAgentId);
        const ts = found?.token_stats;
        const card = (found as any)?.model_card;
        if (card && !task?.model_card) setModelCard(String(card));
        if (!ts) return;
        const max = Number(ts.max) || 0;
        if (max <= 0) return;
        const statsSid = String((ts as any).session_id || '').trim();
        const used = Number(ts.used) || 0;
        if (!statsSid || !exec.session_id || statsSid === exec.session_id) {
          setSeedTokenStats((prev) => {
            if (
              prev
              && prev.used === used
              && prev.max === max
              && prev.breakdown === (ts as any).breakdown
              && prev.session === (ts as any).session
            ) {
              return prev;
            }
            return {
              used,
              max,
              breakdown: (ts as any).breakdown,
              session: (ts as any).session,
            };
          });
        } else {
          setSeedTokenStats((prev) => ({
            used: prev?.used ?? 0,
            max: Math.max(prev?.max || 0, max),
            breakdown: prev?.breakdown,
            session: prev?.session,
          }));
        }
      }).catch(() => {});
    };
    pullAgentStats();
    // Idle executions do not need an 8s admin poll — it re-renders the chat
    // tree and clears text selection for no benefit.
    if (exec.status !== 'running') {
      return () => {
        cancelled = true;
      };
    }
    const id = window.setInterval(pullAgentStats, 8000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [sessionAgentId, task?.model_card, exec.session_id, exec.status]);

  useEffect(() => {
    if (task?.model_card) setModelCard(task.model_card);
    setSendErr(null);
    setSeedTokenStats(null);
  }, [exec.id, task?.model_card]);

  // Ensure WS is up for this agent (same singleton as AIChatPage).
  useEffect(() => {
    if (!sessionAgentId) return;
    const ws = getAiWsService(sessionAgentId);
    ws.connect(sessionAgentId);
  }, [sessionAgentId]);

  // When the execution is no longer running, seal any open workflow fold so the
  // 1s "Working for" clock stops (idle refresh that blocks text selection).
  useEffect(() => {
    if (!exec.status || exec.status === 'running') return;
    sealOnStop();
  }, [exec.status, sealOnStop]);

  // Claim session for this browser user so events + token_stats route here.
  useEffect(() => {
    if (!sid || !sessionBridge?.ensureSessionWatched) return;
    sessionBridge.ensureSessionWatched(sid);
  }, [sid, sessionBridge]);

  // Scheduled-task sessions always run in Build (unattended — no Plan approval).
  useEffect(() => {
    if (!sid || !sessionAgentId) return;
    setMode('build');
    try {
      getAiWsService(sessionAgentId).setAgentMode('build', undefined, sid);
    } catch {
      /* ignore */
    }
  }, [sid, sessionAgentId]);

  const selectedCard = useMemo(
    () => cards.find((c) => c.name === modelCard) || null,
    [cards, modelCard],
  );

  useEffect(() => {
    const max = Number(selectedCard?.token_max) || 0;
    if (max > 0) {
      setSeedTokenStats((prev) => prev ?? { used: 0, max });
    }
  }, [selectedCard]);

  const onSend = useCallback(
    async (payload: ComposerSendPayload) => {
      if (!exec.session_id) {
        setSendErr(t('scheduledTasks.noSessionYet'));
        return;
      }
      const text = payload.text.trim();
      if (!text && !(payload.images?.length) && !(payload.attachments?.length)) return;
      setSending(true);
      setSendErr(null);
      try {
        if (sessionBridge?.sendToSessionStay) {
          // Same queue / deliver path as Agent Web — stay on scheduled-tasks tab.
          await sessionBridge.sendToSessionStay(exec.session_id, payload);
          return;
        }
        appendOptimisticUser(text || '[attachment]');
        const ws = getAiWsService(sessionAgentId);
        ws.connect(sessionAgentId);
        if (ws.isConnected) {
          ws.sendMessage(text, payload.images, payload.attachments, {
            session_id: exec.session_id,
            ...(modelCard ? { model_card: modelCard } : {}),
          });
        } else {
          await scheduledTaskAPI.sendFollowup(agentName, exec.id, text, modelCard);
        }
      } catch (e: any) {
        setSendErr(e?.message || t('scheduledTasks.sendFailed'));
      } finally {
        setSending(false);
      }
    },
    [
      agentName,
      exec.id,
      exec.session_id,
      modelCard,
      t,
      appendOptimisticUser,
      sessionAgentId,
      sessionBridge,
    ],
  );

  const handleStop = useCallback(async () => {
    setStopping(true);
    sealOnStop();
    try {
      if (sid) {
        if (sessionBridge?.stopSession) {
          sessionBridge.stopSession(sid);
        } else {
          try {
            getAiWsService(sessionAgentId).stopTask({ session_id: sid });
          } catch {
            /* fall through to admin stop */
          }
        }
      }
      await scheduledTaskAPI.stopExecution(agentName, exec.id);
      onStopped?.();
    } catch (e: any) {
      setSendErr(e?.message || t('scheduledTasks.stopFailed'));
    } finally {
      setStopping(false);
    }
  }, [agentName, exec.id, sid, onStopped, t, sessionAgentId, sealOnStop, sessionBridge]);

  const handleModeChange = useCallback(
    (next: AgentMode) => {
      setMode(next);
      if (exec.session_id) {
        getAiWsService(sessionAgentId).setAgentMode(next, undefined, exec.session_id);
      }
    },
    [exec.session_id, sessionAgentId],
  );

  const handleEffortChange = useCallback(
    (next: ReasoningEffort) => {
      setEffort(next);
      if (exec.session_id) {
        getAiWsService(sessionAgentId).setReasoningEffort(next, exec.session_id);
      }
    },
    [exec.session_id, sessionAgentId],
  );

  const handleSelectModel = useCallback(
    (name: string) => {
      setModelCard(name);
      if (exec.session_id) {
        getAiWsService(sessionAgentId).switchModel(name, exec.session_id);
      }
    },
    [exec.session_id, sessionAgentId],
  );

  const handleCompress = useCallback(() => {
    setCompressing(true);
    try {
      getAiWsService(sessionAgentId).compressContext();
    } finally {
      window.setTimeout(() => setCompressing(false), 1500);
    }
  }, [sessionAgentId]);

  const hasSession = !!exec.session_id;
  /** null → SessionChatPane hydrates from disk; non-empty → live mirror. */
  const paneTimeline = timeline.length > 0 ? timeline : null;
  // Only soft-poll when we have no live timeline yet AND no sessionBridge
  // (bridge owns WS). Polling while live remounts nodes and kills text selection.
  const panePollMs =
    !sessionBridge
    && exec.status === 'running'
    && (!paneTimeline || paneTimeline.length === 0)
      ? 4000
      : undefined;
  const pendingPanel =
    sid && sessionBridge?.renderSessionPendingPanel
      ? sessionBridge.renderSessionPendingPanel(sid)
      : null;

  return (
    <div className="flex flex-col h-full min-h-0">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between px-3 sm:px-4 py-2.5 border-b border-border shrink-0">
        <div className="min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <h3 className="text-sm font-semibold truncate min-w-0 max-w-full">{exec.task_name}</h3>
            <StatusBadge status={exec.status} />
          </div>
          <div className="mt-0.5 text-[10px] text-textMuted truncate">
            {fmtTime(exec.started_at)}{exec.manual ? ' · manual' : ''}{exec.session_id ? ` · ${exec.session_id.slice(-8)}` : ''}
          </div>
        </div>
        <div className="flex items-center gap-1.5 shrink-0 flex-wrap">
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

      <div className="flex-1 min-h-0 flex flex-col">
        {hasSession ? (
          <SessionChatPane
            key={`exec-${exec.id}-${exec.session_id}`}
            agentId={sessionAgentId}
            sessionId={exec.session_id!}
            liveTimeline={paneTimeline}
            pollIntervalMs={panePollMs}
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

      <AgentWebComposer
        agentId={sessionAgentId}
        columnClass="max-w-3xl mx-auto w-full"
        disabled={!hasSession}
        busy={composerBusy}
        agentMode={mode}
        onModeChange={handleModeChange}
        modelCards={cards}
        currentCardName={modelCard || null}
        modelName={selectedCard?.model_name || selectedCard?.title || modelCard || ''}
        fallbackLabel={t('scheduledTasks.fModelDefault')}
        onSelectModel={handleSelectModel}
        onRefreshModelCards={() => {
          modelCardAPI.getCards().then((r) => setCards(r.cards || [])).catch(() => {});
        }}
        reasoningEffort={effort}
        onEffortChange={handleEffortChange}
        cwd={task?.workspace || rootPath}
        tokenStats={displayTokenStats}
        onCompressContext={handleCompress}
        compressing={compressing}
        compressDisabled={!hasSession || compressing}
        availableSkills={skills}
        skillsLoading={false}
        onOpenSkills={() => window.dispatchEvent(new CustomEvent('opensquad-open-skills'))}
        pendingPanel={pendingPanel}
        onSend={onSend}
        onStop={() => { void handleStop(); }}
        onActivate={() => {}}
      />
    </div>
  );
};

export default ExecWorkflowView;
