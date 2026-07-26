/**
 * Agents with `config.ui.nav_shortcut` enabled — shown as quick-open avatars
 * in the chat list / session rail footer.
 */
import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { adminAPI, AdminAgent } from '../services/api';
import { resolveChatAvatar, resolveChatName } from '../utils/image';

export type AgentNavShortcut = {
  agent_id: string;
  label: string;
  avatar: string | null;
};

const AGENT_SHORTCUTS_CACHE_KEY = 'agent_nav_shortcuts_cache_v1';

function loadAgentShortcutsCache(): AgentNavShortcut[] {
  try {
    const raw = localStorage.getItem(AGENT_SHORTCUTS_CACHE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter((x: unknown) => x && typeof (x as AgentNavShortcut).agent_id === 'string')
      .map((x: AgentNavShortcut) => ({
        agent_id: x.agent_id,
        label: typeof x.label === 'string' ? x.label : x.agent_id,
        avatar: typeof x.avatar === 'string' ? x.avatar : null,
      }));
  } catch {
    return [];
  }
}

export function useAgentNavShortcuts() {
  const { t } = useTranslation();
  const [shortcuts, setShortcuts] = useState<AgentNavShortcut[]>(() => loadAgentShortcutsCache());
  const [checkingAgentId, setCheckingAgentId] = useState<string | null>(null);

  useEffect(() => {
    const loadAgentShortcuts = async () => {
      try {
        const { agents } = await adminAPI.getAgents();
        const withCfg = await Promise.all(
          (agents || []).map(async (a: AdminAgent) => {
            try {
              const key = a.dir_name || a.agent_id || a.agent_name;
              const cfgRes = await adminAPI.getConfig(key);
              return { agent: a, cfg: cfgRes?.config || {} };
            } catch {
              return { agent: a, cfg: {} as Record<string, unknown> };
            }
          }),
        );

        const next = withCfg
          .filter((item) => !!item.agent?.agent_id)
          .filter((item) => !!(item.cfg as { ui?: { nav_shortcut?: boolean } })?.ui?.nav_shortcut)
          .map((item) => ({
            agent_id: item.agent.agent_id,
            label:
              item.agent.agent_name ||
              resolveChatName(item.agent.chat_profile) ||
              item.agent.dir_name ||
              item.agent.agent_id,
            avatar: resolveChatAvatar(item.agent.chat_profile),
          }));
        setShortcuts(next);
        try {
          localStorage.setItem(AGENT_SHORTCUTS_CACHE_KEY, JSON.stringify(next));
        } catch {
          /* ignore quota */
        }
      } catch (error) {
        console.error('[useAgentNavShortcuts] Failed to load agent shortcuts:', error);
      }
    };

    loadAgentShortcuts();
    const handleNavChange = () => {
      void loadAgentShortcuts();
    };
    window.addEventListener('agent-nav-changed', handleNavChange);
    window.addEventListener('plugin-nav-changed', handleNavChange);
    return () => {
      window.removeEventListener('agent-nav-changed', handleNavChange);
      window.removeEventListener('plugin-nav-changed', handleNavChange);
    };
  }, []);

  const openAgentChat = useCallback(
    async (agentId: string) => {
      if (checkingAgentId) return;
      setCheckingAgentId(agentId);
      try {
        const { agents } = await adminAPI.getAgents();
        const agent = (agents || []).find((a: AdminAgent) => a.agent_id === agentId);
        if (!agent) {
          alert(t('agentManager.agentNotFound') || 'Agent not found');
          return;
        }
        if (!agent.ready) {
          alert(t('agentManager.agentNotReady') || 'Agent is starting, please wait.');
          return;
        }
        window.dispatchEvent(new CustomEvent('openAgentChat', { detail: { agentId } }));
        window.dispatchEvent(new CustomEvent('switchView', { detail: 'ai-chat' }));
      } catch (error) {
        console.error('[useAgentNavShortcuts] Failed to open agent chat:', error);
        alert(t('agentManager.loadFailed') || 'Failed to check agent status');
      } finally {
        setCheckingAgentId(null);
      }
    },
    [checkingAgentId, t],
  );

  return { shortcuts, checkingAgentId, openAgentChat };
}
