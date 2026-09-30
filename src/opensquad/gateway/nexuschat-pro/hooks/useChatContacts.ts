/**
 * Contacts for chat mode: every agent (with its live process status) plus the
 * user's group chats.
 *
 * Kept as one hook so the chat rail, the detail drawer and the contacts
 * variant of the agent manager never disagree about who exists or what state
 * an agent is in.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { adminAPI, groupAPI, type AdminAgent, type GroupListItem } from '../services/api';

export interface ChatContacts {
  agents: AdminAgent[];
  groups: GroupListItem[];
  loading: boolean;
  error: string | null;
  reload: () => Promise<void>;
}

/** Idle poll — the rail also reloads on the session-list push event. */
const POLL_MS = 15000;

export function useChatContacts(opts?: { enabled?: boolean; pollMs?: number }): ChatContacts {
  const enabled = opts?.enabled ?? true;
  const pollMs = opts?.pollMs ?? POLL_MS;
  const [agents, setAgents] = useState<AdminAgent[]>([]);
  const [groups, setGroups] = useState<GroupListItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const reload = useCallback(async () => {
    setLoading(true);
    const [agentsRes, groupsRes] = await Promise.allSettled([
      adminAPI.getAgents(),
      groupAPI.getGroups(),
    ]);
    if (!mountedRef.current) return;
    if (agentsRes.status === 'fulfilled') {
      setAgents(Array.isArray(agentsRes.value?.agents) ? agentsRes.value.agents : []);
    }
    if (groupsRes.status === 'fulfilled') {
      setGroups(Array.isArray(groupsRes.value) ? groupsRes.value : []);
    }
    // Only the agent list gates the rail — a missing /groups (older gateway)
    // must not blank the contacts that do exist.
    setError(agentsRes.status === 'rejected' ? String(agentsRes.reason?.message || agentsRes.reason) : null);
    setLoading(false);
  }, []);

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    void reload();
    const timer = window.setInterval(() => {
      if (!cancelled) void reload();
    }, pollMs);
    const onRefresh = () => {
      if (!cancelled) void reload();
    };
    window.addEventListener('agent-nav-changed', onRefresh);
    window.addEventListener('plugin-nav-changed', onRefresh);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
      window.removeEventListener('agent-nav-changed', onRefresh);
      window.removeEventListener('plugin-nav-changed', onRefresh);
    };
  }, [enabled, pollMs, reload]);

  return { agents, groups, loading, error, reload };
}
