/**
 * Compact agent avatar buttons for the account rail footer
 * (agents with ui.nav_shortcut enabled).
 */
import React from 'react';
import { getLocalAvatarFallback } from '../utils/image';
import { useAgentNavShortcuts } from '../hooks/useAgentNavShortcuts';

const AVATAR_COLORS = [
  'bg-violet-500',
  'bg-blue-500',
  'bg-emerald-500',
  'bg-rose-500',
  'bg-amber-500',
  'bg-cyan-500',
  'bg-pink-500',
  'bg-indigo-500',
];

function avatarColor(id: string): string {
  let hash = 0;
  for (let i = 0; i < id.length; i++) hash = (hash * 31 + id.charCodeAt(i)) >>> 0;
  return AVATAR_COLORS[hash % AVATAR_COLORS.length];
}

export const AgentNavShortcutAvatars: React.FC = () => {
  const { shortcuts, checkingAgentId, openAgentChat } = useAgentNavShortcuts();

  if (shortcuts.length === 0) return null;

  return (
    <div
      className="flex max-w-[40%] shrink-0 items-center gap-1 overflow-x-auto [&::-webkit-scrollbar]:hidden"
      style={{ scrollbarWidth: 'none' }}
      role="group"
      aria-label="Agent shortcuts"
    >
      {shortcuts.map((agent) => {
        const busy = checkingAgentId === agent.agent_id;
        return (
          <button
            key={`agent-nav-${agent.agent_id}`}
            type="button"
            onClick={() => void openAgentChat(agent.agent_id)}
            disabled={busy}
            className={`shrink-0 rounded-full p-0.5 ring-1 ring-border/50 transition-opacity hover:ring-primary/40 ${
              busy ? 'cursor-wait opacity-60' : ''
            }`}
            title={agent.label}
            aria-label={agent.label}
          >
            {agent.avatar ? (
              <img
                src={agent.avatar}
                alt=""
                className="h-7 w-7 rounded-full object-cover bg-border"
                loading="lazy"
                onError={(e) => {
                  const img = e.currentTarget;
                  if (img.dataset.fallbackApplied) return;
                  img.dataset.fallbackApplied = '1';
                  img.src = getLocalAvatarFallback(agent.agent_id, agent.label);
                }}
              />
            ) : (
              <span
                className={`flex h-7 w-7 items-center justify-center rounded-full text-[11px] font-semibold text-white ${avatarColor(agent.agent_id)}`}
              >
                {agent.label.charAt(0).toUpperCase()}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
};
