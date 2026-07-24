/**
 * Grid shell that morphs composer from vertical-center (new session) to bottom-docked.
 */
import React from 'react';
import { NewChatLanding } from './NewChatLanding';

export interface ComposerLandingDockProps {
  landing: boolean;
  seedKey?: string;
  children: React.ReactNode;
}

export const ComposerLandingDock: React.FC<ComposerLandingDockProps> = ({
  landing,
  seedKey,
  children,
}) => {
  return (
    <div
      className={`os-composer-landing-dock min-h-0 flex flex-col flex-shrink-0 ${
        landing ? 'is-landing' : 'is-docked'
      }`}
      data-landing={landing ? '1' : '0'}
    >
      <div className="os-composer-landing-hero" aria-hidden={!landing}>
        <NewChatLanding seedKey={seedKey} />
      </div>
      <div className="os-composer-landing-body w-full min-w-0">{children}</div>
    </div>
  );
};
