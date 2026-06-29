/**
 * StatusBadge - displays agent connection/activity status.
 *
 * Shows: connected, disconnected, connecting, thinking, error.
 */
import React from 'react';
import { Wifi, WifiOff, Loader2, AlertCircle } from 'lucide-react';

export type AgentStatus = 'connected' | 'disconnected' | 'connecting' | 'agent-starting' | 'thinking' | 'working' | 'sleeping' | 'error' | 'idle' | 'awaiting_reply';

interface StatusBadgeProps {
  status: AgentStatus;
  agentName?: string;
}

const STATUS_CONFIG: Record<AgentStatus, {
  label: string;
  dotClass: string;
  icon: React.ReactNode;
}> = {
  connected: {
    label: 'Connected',
    dotClass: 'bg-emerald-500',
    icon: <Wifi size={12} />,
  },
  idle: {
    label: 'Ready',
    dotClass: 'bg-emerald-500',
    icon: <Wifi size={12} />,
  },
  working: {
    label: 'Working',
    dotClass: 'bg-blue-500 animate-pulse',
    icon: <Loader2 size={12} className="animate-spin" />,
  },
  sleeping: {
    label: 'Sleeping',
    dotClass: 'bg-gray-400',
    icon: <WifiOff size={12} />,
  },
  disconnected: {
    label: 'Disconnected',
    dotClass: 'bg-gray-400',
    icon: <WifiOff size={12} />,
  },
  connecting: {
    label: 'Connecting...',
    dotClass: 'bg-amber-500 animate-pulse',
    icon: <Loader2 size={12} className="animate-spin" />,
  },
  'agent-starting': {
    label: 'Agent starting...',
    dotClass: 'bg-yellow-400 animate-pulse',
    icon: <Loader2 size={12} className="animate-spin" />,
  },
  thinking: {
    label: 'Thinking...',
    dotClass: 'bg-primary animate-pulse',
    icon: <Loader2 size={12} className="animate-spin" />,
  },
  error: {
    label: 'Error',
    dotClass: 'bg-red-500',
    icon: <AlertCircle size={12} />,
  },
  awaiting_reply: {
    label: 'Awaiting reply',
    dotClass: 'bg-violet-500 animate-pulse',
    icon: <Loader2 size={12} className="animate-spin" />,
  },
};

export const StatusBadge: React.FC<StatusBadgeProps> = ({ status, agentName }) => {
  const config = STATUS_CONFIG[status] || STATUS_CONFIG.disconnected;

  return (
    <div className="flex items-center gap-1.5">
      <span className={`w-1.5 h-1.5 rounded-full ${config.dotClass}`} />
      <span className="text-[10px] text-textMuted">{config.label}</span>
    </div>
  );
};
