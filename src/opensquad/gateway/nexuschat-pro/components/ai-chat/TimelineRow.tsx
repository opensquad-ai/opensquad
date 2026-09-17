import React, { type CSSProperties } from 'react';

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
  /** Only set while the pane is revealing its content — carries the entrance
   *  animation delay (see `.os-revealing` in index.css). Leave undefined
   *  otherwise so virtual remounts during scroll do not animate. */
  style?: CSSProperties;
}> = ({ children, className, style }) => (
  <div className={['timeline-row', className].filter(Boolean).join(' ')} style={style}>
    {children}
  </div>
);
