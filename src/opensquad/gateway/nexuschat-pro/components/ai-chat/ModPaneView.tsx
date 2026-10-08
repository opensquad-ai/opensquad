import React from 'react';
import { ModSlotHost } from './ModSlotHost';
import { PANE_SCOPE } from './modSlotStore';

/**
 * The body of a `mod` content tab.
 *
 * A mod pane is dynamic — its id comes from the mod's own `$.ui.open({id})` — so
 * it cannot live in the static `paneViews` registry. The content itself is the
 * same validated tree everything else uses, so there is exactly one renderer.
 */
export const ModPaneView: React.FC<{ paneId: string }> = ({ paneId }) => (
  <div className="h-full w-full overflow-y-auto p-3 text-xs" data-mod-pane={paneId}>
    <ModSlotHost slot="Pane" paneId={paneId} sid={PANE_SCOPE} className="flex flex-col gap-1" />
  </div>
);
