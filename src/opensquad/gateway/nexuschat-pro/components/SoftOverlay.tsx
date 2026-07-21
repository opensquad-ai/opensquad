/**
 * Soft overlay shell — backdrop + panel with enter/exit ease (Agent Web modals).
 */
import React from 'react';
import { SOFT_PRESENCE_MS, useSoftPresence } from '../utils/useSoftPresence';

export interface SoftOverlayProps {
  open: boolean;
  onBackdrop?: () => void;
  children: React.ReactNode;
  /** Extra classes on the fixed backdrop */
  className?: string;
  /** Extra classes on the dialog panel */
  panelClassName?: string;
  zClass?: string;
  durationMs?: number;
  /** Stop backdrop click when busy */
  dismissDisabled?: boolean;
}

export const SoftOverlay: React.FC<SoftOverlayProps> = ({
  open,
  onBackdrop,
  children,
  className = '',
  panelClassName = '',
  zClass = 'z-[200]',
  durationMs = SOFT_PRESENCE_MS,
  dismissDisabled = false,
}) => {
  const { mounted, visible } = useSoftPresence(open, durationMs);
  if (!mounted) return null;

  return (
    <div
      className={`fixed inset-0 ${zClass} flex items-center justify-center bg-black/40 backdrop-blur-[1px] p-4 os-soft-overlay ${
        visible ? 'is-open' : ''
      } ${className}`.trim()}
      role="presentation"
      onMouseDown={(e) => {
        if (dismissDisabled || !onBackdrop) return;
        if (e.target === e.currentTarget) onBackdrop();
      }}
    >
      <div className={`os-soft-overlay-panel ${panelClassName}`.trim()}>{children}</div>
    </div>
  );
};

export { useSoftPresence, SOFT_PRESENCE_MS };
