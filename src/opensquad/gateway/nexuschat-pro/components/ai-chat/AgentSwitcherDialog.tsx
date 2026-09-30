/**
 * AgentSwitcherDialog — 左侧会话菜单「智能体」入口的弹窗。
 *
 * 列出全部 agent + 运行状态，支持 开启 / 停止 / 重启，并可一键切换到该
 * agent 的对话。切换复用 App 的 `openAgentChat` 事件桥（与导航栏快捷头像
 * 同一条路径），因此这里的 agentId 必须用 `agent_id` 而不是目录名。
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Bot, Play, RefreshCw, RotateCw, Square, X } from 'lucide-react';
import { adminAPI, type AdminAgent } from '../../services/api';
import { getLocalAvatarFallback, resolveChatAvatar } from '../../utils/image';
import { SoftOverlay } from '../SoftOverlay';
import { OpenSquadLoader } from '../OpenSquadLoader';
import { POPOVER_SURFACE_CLASS } from './popoverSurface';

interface AgentSwitcherDialogProps {
  open: boolean;
  onClose: () => void;
  /** 当前正在对话的 agent_id：列表内高亮，且不再提供「切换」。 */
  currentAgentId?: string | null;
}

const POLL_MS = 15000;
/** 开启 / 重启有 starting→ready 过渡，用短轮询把它抓出来。 */
const FAST_POLL_MS = 2000;
const FAST_POLL_WINDOW_MS = 30000;

const STATUS_DOT: Record<string, string> = {
  running: 'bg-green-500',
  stopped: 'bg-gray-400',
  crashed: 'bg-red-500',
  starting: 'bg-yellow-400',
  external: 'bg-blue-400',
};

const STATUS_LABEL_KEY: Record<string, string> = {
  running: 'agentManager.statusRunning',
  stopped: 'agentManager.statusStopped',
  crashed: 'agentManager.statusCrashed',
  starting: 'agentManager.statusStarting',
  external: 'agentManager.statusExternal',
};

function agentKey(a: AdminAgent): string {
  return a.dir_name || a.agent_id || a.agent_name;
}

/** 进程在跑但还没注册到 Gateway → 对用户就是「启动中」。 */
function statusOf(a: AdminAgent): string {
  if (a.process_status === 'running' && !a.ready) return 'starting';
  return a.process_status || 'stopped';
}

export const AgentSwitcherDialog: React.FC<AgentSwitcherDialogProps> = ({
  open,
  onClose,
  currentAgentId,
}) => {
  const { t } = useTranslation();
  const [agents, setAgents] = useState<AdminAgent[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const fastPollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchAgents = useCallback(async () => {
    try {
      const res = await adminAPI.getAgents();
      setAgents(Array.isArray(res?.agents) ? res.agents : []);
      setError(null);
    } catch (e: any) {
      setError(e?.message || 'load failed');
    }
  }, []);

  const stopFastPoll = useCallback(() => {
    if (fastPollRef.current) {
      clearInterval(fastPollRef.current);
      fastPollRef.current = null;
    }
  }, []);

  const fastPoll = useCallback(() => {
    stopFastPoll();
    let elapsed = 0;
    fastPollRef.current = setInterval(() => {
      elapsed += FAST_POLL_MS;
      void fetchAgents();
      if (elapsed >= FAST_POLL_WINDOW_MS) stopFastPoll();
    }, FAST_POLL_MS);
  }, [fetchAgents, stopFastPoll]);

  useEffect(() => {
    if (!open) {
      stopFastPoll();
      return;
    }
    setLoading(true);
    void fetchAgents().finally(() => setLoading(false));
    const timer = setInterval(() => void fetchAgents(), POLL_MS);
    return () => {
      clearInterval(timer);
      stopFastPoll();
    };
  }, [open, fetchAgents, stopFastPoll]);

  const doAction = useCallback(
    async (a: AdminAgent, action: 'start' | 'stop' | 'restart') => {
      const key = agentKey(a);
      setBusyKey(key);
      try {
        if (action === 'start') await adminAPI.startAgent(key);
        else if (action === 'stop') await adminAPI.stopAgent(key);
        else await adminAPI.restartAgent(key);
        await fetchAgents();
        // stop 立即生效；start/restart 仍需捕捉 starting→ready。
        if (action !== 'stop') fastPoll();
      } catch (e: any) {
        window.alert(`${action} failed: ${e?.message || e}`);
      } finally {
        setBusyKey(null);
      }
    },
    [fetchAgents, fastPoll],
  );

  const switchTo = useCallback(
    (a: AdminAgent) => {
      if (!a.ready) {
        window.alert(t('agentManager.agentStartingHint'));
        return;
      }
      window.dispatchEvent(new CustomEvent('openAgentChat', { detail: { agentId: a.agent_id } }));
      window.dispatchEvent(new CustomEvent('switchView', { detail: 'ai-chat' }));
      onClose();
    },
    [t, onClose],
  );

  return (
    <SoftOverlay
      open={open}
      onBackdrop={onClose}
      panelClassName={`w-[min(520px,94vw)] max-h-[80vh] flex flex-col rounded-2xl ${POPOVER_SURFACE_CLASS} border border-border shadow-2xl overflow-hidden`}
      durationMs={150}
    >
      <div className="flex items-center gap-2 px-4 h-12 border-b border-border/60 shrink-0">
        <Bot size={16} className="text-textMuted shrink-0" />
        <span className="flex-1 min-w-0 truncate text-[14px] font-medium text-textMain">
          {t('aiChat.agentSwitcherTitle')}
        </span>
        {loading ? <OpenSquadLoader size={14} /> : null}
        <button
          type="button"
          onClick={() => void fetchAgents()}
          className="p-1 rounded text-textMuted hover:bg-black/5 dark:hover:bg-white/10 shrink-0"
          title={t('aiChat.agentRefresh')}
          aria-label={t('aiChat.agentRefresh')}
        >
          <RefreshCw size={14} />
        </button>
        <button
          type="button"
          onClick={onClose}
          className="p-1 rounded text-textMuted hover:bg-black/5 dark:hover:bg-white/10 shrink-0"
          title={t('common.close')}
          aria-label={t('common.close')}
        >
          <X size={16} />
        </button>
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto py-1" data-testid="agent-switcher-list">
        {error && agents.length === 0 ? (
          <div className="px-4 py-6 text-[12px] text-textMuted">{t('aiChat.agentLoadFailed')}</div>
        ) : agents.length === 0 ? (
          <div className="px-4 py-6 text-[12px] text-textMuted">
            {loading ? t('common.loading') : t('aiChat.agentEmpty')}
          </div>
        ) : (
          agents.map((a) => {
            const key = agentKey(a);
            const st = statusOf(a);
            const busy = busyKey === key;
            const isCurrent = !!(currentAgentId && a.agent_id === currentAgentId);
            const running = a.process_status === 'running';
            const avatar = resolveChatAvatar(a.chat_profile);
            const label = a.agent_name || a.dir_name || a.agent_id;
            return (
              <div
                key={`agent-switch-${a.agent_id || key}`}
                className={`flex items-center gap-2.5 px-3 py-2 transition-colors hover:bg-black/[0.03] dark:hover:bg-white/[0.05] ${
                  isCurrent ? 'bg-primary/[0.07]' : ''
                }`}
                data-testid={`agent-row-${a.agent_id || key}`}
              >
                <img
                  src={avatar || getLocalAvatarFallback(key, label)}
                  alt=""
                  className="h-8 w-8 rounded-full object-cover bg-border shrink-0"
                  loading="lazy"
                  onError={(e) => {
                    const img = e.currentTarget;
                    if (img.dataset.fallbackApplied) return;
                    img.dataset.fallbackApplied = '1';
                    img.src = getLocalAvatarFallback(key, label);
                  }}
                />
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-1.5 min-w-0">
                    <span className="truncate text-[13px] text-textMain">{label}</span>
                    {isCurrent ? (
                      <span className="shrink-0 rounded-full bg-primary/10 px-1.5 py-[1px] text-[9px] text-primary">
                        {t('aiChat.agentCurrent')}
                      </span>
                    ) : null}
                  </div>
                  <div className="flex items-center gap-1.5 mt-0.5 min-w-0">
                    <span className={`h-1.5 w-1.5 rounded-full shrink-0 ${STATUS_DOT[st] || 'bg-gray-400'}`} />
                    <span className="shrink-0 text-[10px] text-textMuted">
                      {t(STATUS_LABEL_KEY[st] || 'agentManager.statusStopped')}
                    </span>
                    {a.dir_name ? (
                      <span className="truncate text-[10px] text-textMuted/50 font-mono">{a.dir_name}</span>
                    ) : null}
                  </div>
                </div>
                <div className="flex items-center gap-0.5 shrink-0">
                  <button
                    type="button"
                    disabled={busy || isCurrent || !a.ready}
                    onClick={() => switchTo(a)}
                    className="rounded-md px-2 py-1 text-[11px] text-textMain hover:bg-black/[0.06] dark:hover:bg-white/10 disabled:opacity-35"
                    title={t('aiChat.agentSwitch')}
                  >
                    {t('aiChat.agentSwitch')}
                  </button>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => void doAction(a, running ? 'stop' : 'start')}
                    className="p-1 rounded-md text-textMuted hover:bg-black/[0.06] dark:hover:bg-white/10 disabled:opacity-35"
                    title={running ? t('agentManager.stop') : t('agentManager.start')}
                    aria-label={running ? t('agentManager.stop') : t('agentManager.start')}
                  >
                    {busy ? <OpenSquadLoader size={13} /> : running ? <Square size={13} /> : <Play size={13} />}
                  </button>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => void doAction(a, 'restart')}
                    className="p-1 rounded-md text-textMuted hover:bg-black/[0.06] dark:hover:bg-white/10 disabled:opacity-35"
                    title={t('agentManager.restart')}
                    aria-label={t('agentManager.restart')}
                  >
                    <RotateCw size={13} />
                  </button>
                </div>
              </div>
            );
          })
        )}
      </div>
    </SoftOverlay>
  );
};

export default AgentSwitcherDialog;
