import React from 'react';

/**
 * Off-screen timeline rows skip layout/paint via CSS content-visibility.
 * Avoids pulling in a virtualization library while long sessions stay scrollable.
 */
const ROW_STYLE: React.CSSProperties = {
  contentVisibility: 'auto',
  containIntrinsicSize: 'auto 88px',
};

export const TimelineRow: React.FC<{
  children: React.ReactNode;
  className?: string;
}> = ({ children, className }) => (
  <div className={className ? `timeline-row ${className}` : 'timeline-row'} style={ROW_STYLE}>
    {children}
  </div>
);
