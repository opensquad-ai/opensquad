/**
 * DelegateFold — main-stream entry for delegate_task.
 * Stays "running" until the parent tool returns; click opens SubAgentPanel.
 */
import React, { useState, useCallback } from 'react';
import { CheckCircle, XCircle, GitBranch } from 'lucide-react';
import type { DelegateBundle } from '../../utils/delegateGrouping';
import { SubAgentPanel } from './SubAgentPanel';
import { OpenSquadLoader } from '../OpenSquadLoader';

export interface DelegateFoldProps {
  bundle: DelegateBundle;
  /** Solo = faint text chevron; classic = bordered card */
  variant?: 'solo' | 'classic';
}

export const DelegateFold: React.FC<DelegateFoldProps> = ({
  bundle,
  variant = 'classic',
}) => {
  const [panelOpen, setPanelOpen] = useState(false);
  const openPanel = useCallback(() => setPanelOpen(true), []);
  const closePanel = useCallback(() => setPanelOpen(false), []);

  const status: 'running' | 'success' | 'error' = bundle.running
    ? 'running'
    : bundle.parent.resultStatus === 'error'
      ? 'error'
      : 'success';

  const toolHint =
    bundle.toolCount > 0
      ? `${bundle.toolCount} nested tool${bundle.toolCount === 1 ? '' : 's'}`
      : bundle.running
        ? 'sub-agent'
        : '';

  const panel = (
    <SubAgentPanel
      open={panelOpen}
      onClose={closePanel}
      title={bundle.label}
      prompt={bundle.prompt}
      events={bundle.children}
      finalResult={bundle.finalResult}
      running={bundle.running}
    />
  );

  if (variant === 'solo') {
    const faint = 'color-mix(in srgb, rgb(var(--color-text-muted)) 55%, transparent)';
    return (
      <>
        <button
          type="button"
          onClick={openPanel}
          style={{ color: faint }}
          className="group inline-flex items-baseline gap-1.5 py-0.5 text-left max-w-full bg-transparent border-0 p-0 cursor-pointer"
          title="Open delegate window"
        >
          <span className="text-[13px] leading-relaxed min-w-0" style={{ color: faint }}>
            <span className="font-normal">{bundle.running ? 'Exploring' : 'Explored'}</span>
            {toolHint ? <span>{' '}{toolHint}</span> : null}
            {bundle.running ? <span style={{ opacity: 0.85 }}> …</span> : null}
          </span>
          <span className="text-[13px] shrink-0" style={{ color: faint }}>↗</span>
        </button>
        {panel}
      </>
    );
  }

  const statusIcon =
    status === 'success' ? (
      <CheckCircle size={12} className="text-emerald-500 flex-shrink-0" />
    ) : status === 'error' ? (
      <XCircle size={12} className="text-red-500 flex-shrink-0" />
    ) : (
      <OpenSquadLoader size={12} className="flex-shrink-0" />
    );

  return (
    <>
      <button
        type="button"
        onClick={openPanel}
        data-tool-expanded={panelOpen || undefined}
        className="w-full text-left rounded-md border border-violet-500/25 bg-violet-500/5 hover:bg-violet-500/10 overflow-hidden transition-colors cursor-pointer"
        title="Open delegate window"
      >
        <div className="flex items-center gap-1.5 px-2 py-1.5 select-none">
          {statusIcon}
          <GitBranch size={11} className="text-violet-400 flex-shrink-0" />
          <span className="text-[11px] text-violet-400 font-semibold leading-none flex-shrink-0">
            Delegate
          </span>
          <span className="text-[11px] text-gray-800 dark:text-gray-200 font-mono font-medium truncate flex-1">
            {bundle.label}
          </span>
          {toolHint ? (
            <span className="text-[9px] text-violet-400/70 flex-shrink-0">{toolHint}</span>
          ) : null}
          <span className="text-[10px] text-textMuted flex-shrink-0">↗</span>
        </div>
      </button>
      {panel}
    </>
  );
};
