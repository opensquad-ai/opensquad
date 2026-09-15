import React from 'react';

/**
 * Timeline row wrapper.
 * Do not use content-visibility here: tool-flow / markdown heights vary from
 * tens to thousands of pixels, and the 88px intrinsic size made the main
 * chat scrollbar jump every time a row entered the viewport.
 */
export const TimelineRow: React.FC<{
  children: React.ReactNode;
  className?: string;
  /** Kept for callers; layout is always native so the scrollbar stays stable. */
  lockLayout?: boolean;
}> = ({ children, className }) => (
  <div className={['timeline-row', className].filter(Boolean).join(' ')}>
    {children}
  </div>
);
