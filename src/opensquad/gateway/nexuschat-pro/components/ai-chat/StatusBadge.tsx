/**
 * StatusBadge — compact agent connection light (no label text).
 * Green breathing when online; amber when disconnected / connecting.
 */
import React from 'react';

export type AgentStatus =
  | 'connected'
  | 'disconnected'
  | 'connecting'
  | 'agent-starting'
  | 'thinking'
  | 'working'
  | 'sleeping'
  | 'error'
  | 'idle'
  | 'awaiting_reply';

interface StatusBadgeProps {
  status: AgentStatus;
  /** Optional accessible / hover label */
  title?: string;
  className?: string;
}

const STATUS_TITLE: Record<AgentStatus, string> = {
  connected: 'Connected',
  idle: 'Ready',
  working: 'Working',
  sleeping: 'Sleeping',
  disconnected: 'Disconnected',
  connecting: 'Connecting…',
  'agent-starting': 'Agent starting…',
  thinking: 'Thinking…',
  error: 'Error',
  awaiting_reply: 'Awaiting reply',
};

function isOfflineStatus(status: AgentStatus): boolean {
  return (
    status === 'disconnected' ||
    status === 'connecting' ||
    status === 'agent-starting' ||
    status === 'error'
  );
}

export const StatusBadge: React.FC<StatusBadgeProps> = ({
  status,
  title,
  className = '',
}) => {
  const offline = isOfflineStatus(status);
  return (
    <span
      className={`inline-block h-2 w-2 shrink-0 rounded-full ${
        offline
          ? 'bg-amber-400 animate-pulse'
          : 'bg-emerald-500 animate-breathe-idle'
      } ${className}`}
      title={title || STATUS_TITLE[status] || STATUS_TITLE.disconnected}
      aria-label={title || STATUS_TITLE[status] || STATUS_TITLE.disconnected}
      role="status"
    />
  );
};
