import React, { useCallback, useSyncExternalStore } from 'react';
import { aiWsService } from '../../services/aiWebSocket';
import { ModElement } from './ModElement';
import { getModSlot, getModSlotVersion, subscribe } from './modSlotStore';

/**
 * Hosts one mod slot for one session.
 *
 * Renders **nothing** when the session has no frame — that is the regression
 * guard for every user who has no mods enabled: the band must not add DOM, not
 * even an empty container.
 */
export const ModSlotHost: React.FC<{
  /** Required: a slot without a session cannot know which frame is its own.
   *  (An earlier draft had a context fallback, but nothing ever mounted the
   *  provider — it only turned "forgot the sid" into a silent empty render.) */
  sid: string;
  slot?: string;
  /** Panes are per-instance: the id the mod passed to `$.ui.open`. */
  paneId?: string;
  className?: string;
}> = ({ sid, slot = 'AbovePrompt', paneId, className }) => {
  useSyncExternalStore(subscribe, getModSlotVersion, getModSlotVersion);
  const frame = sid ? getModSlot(sid, slot, paneId) : undefined;

  // The gateway forwards any command name verbatim, so no new endpoint is
  // needed; the agent turns this into a host `action.invoke`.
  const onPress = useCallback(
    (action: string) => {
      if (!sid || !action) return;
      aiWsService.sendCommand('mod_action', { action, session_id: sid });
    },
    [sid],
  );

  if (!frame || frame.nodes.length === 0) return null;

  return (
    <div className={className} data-mod-slot={slot}>
      {frame.nodes.map((node, i) => (
        <ModElement key={`${slot}-${i}`} node={node} onPress={onPress} />
      ))}
      {frame.dropped > 0 ? (
        <div className="text-[10px] text-textMuted" title="被元素/属性白名单拦下的内容">
          {`（${frame.dropped} 项被白名单拦下）`}
        </div>
      ) : null}
    </div>
  );
};
