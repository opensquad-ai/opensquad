import React from 'react';

/**
 * Off-screen timeline rows skip layout/paint via CSS content-visibility.
 * Live / tail rows lock native layout so stick-to-bottom streaming does not jitter.
 */
const ROW_STYLE: React.CSSProperties = {
  contentVisibility: 'auto',
  containIntrinsicSize: 'auto 88px',
};

export const TimelineRow: React.FC<{
  children: React.ReactNode;
  className?: string;
  lockLayout?: boolean;
}> = ({ children, className, lockLayout }) => (
  <div
    className={[
      'timeline-row',
      lockLayout ? 'timeline-row-live' : '',
      className,
    ].filter(Boolean).join(' ')}
    style={lockLayout ? undefined : ROW_STYLE}
  >
    {children}
  </div>
);
