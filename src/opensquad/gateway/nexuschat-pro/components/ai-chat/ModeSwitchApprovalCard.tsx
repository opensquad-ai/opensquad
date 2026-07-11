/**
 * ModeSwitchApprovalCard — Approve / Deny agent-requested Plan↔Build switches.
 */
import React from 'react';
import { Check, X } from 'lucide-react';
import type { AgentMode } from './ModePicker';

export interface ModeSwitchApproval {
  id: string;
  from_mode: AgentMode;
  to_mode: AgentMode;
  reason: string;
  status: 'pending' | 'approved' | 'denied';
}

interface ModeSwitchApprovalCardProps {
  request: ModeSwitchApproval;
  onApprove: (id: string, toMode: AgentMode) => void;
  onDeny: (id: string) => void;
}

export const ModeSwitchApprovalCard: React.FC<ModeSwitchApprovalCardProps> = ({
  request,
  onApprove,
  onDeny,
}) => {
  const pending = request.status === 'pending';
  const labelFrom = request.from_mode === 'plan' ? 'Plan' : 'Build';
  const labelTo = request.to_mode === 'plan' ? 'Plan' : 'Build';

  return (
    <div className="my-3 mx-1 rounded-xl border border-border bg-panel px-3.5 py-3 shadow-sm">
      <div className="text-[12px] font-semibold text-textMain mb-1">
        Switch mode: {labelFrom} → {labelTo}
      </div>
      <div className="text-[12px] text-textMuted mb-3 leading-relaxed">
        {request.reason || 'The agent requested a mode change.'}
      </div>
      {pending ? (
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => onApprove(request.id, request.to_mode)}
            className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg text-[12px] font-medium bg-primary text-white hover:opacity-90 border-0 cursor-pointer"
          >
            <Check size={13} />
            Approve
          </button>
          <button
            type="button"
            onClick={() => onDeny(request.id)}
            className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg text-[12px] font-medium text-textMuted hover:bg-black/[0.05] dark:hover:bg-white/[0.06] border border-border cursor-pointer bg-transparent"
          >
            <X size={13} />
            Deny
          </button>
        </div>
      ) : (
        <div className="text-[11px] text-textMuted">
          {request.status === 'approved' ? 'Approved' : 'Denied'}
        </div>
      )}
    </div>
  );
};
