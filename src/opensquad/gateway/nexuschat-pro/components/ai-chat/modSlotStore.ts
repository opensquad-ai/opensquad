/**
 * Mod slot trees, keyed by session.
 *
 * Deliberately a tiny external store rather than another field in the composer's
 * props chain: the frames arrive on the WebSocket (`useAgentWebSocket`) and are
 * consumed by `ModSlotHost`, and threading new state through AIChatPage would
 * touch far more surface for no gain.
 *
 * A frame whose `sid` is missing is dropped at the source (Python) and ignored
 * here — a live UI event without a session would land in whichever pane is
 * focused (see the routing comment in `useAgentWebSocket`).
 */

/** Mirrors `opensquad.mods_compat.RENDER_ELEMENTS` — the whitelist is the
 *  security boundary (铁律 3), and `Button.action` is a host-owned id. */
export interface ModNode {
  type: 'Text' | 'Box' | 'Button' | 'Markdown' | 'Code';
  props: Record<string, unknown>;
  children?: Array<ModNode | string>;
}

export interface ModSlotFrame {
  slot: string;
  nodes: ModNode[];
  dropped: number;
  /** Present for `Pane`, whose instances are keyed by the id the mod asked for. */
  paneId?: string;
  at: number;
}

/** Panes are workspace-level: a pane tab outlives the session that opened it, and
 *  its id is the mod's own, so their frames live under this fixed key instead of a
 *  session. (Keying them by session meant the pane shell — which has no session —
 *  could never find the content.) */
export const PANE_SCOPE = '@panes';

/** Panes are per-instance; every other slot has one instance per session. */
const frameKey = (slot: string, paneId?: string): string => (paneId ? `${slot}:${paneId}` : slot);

const bySid = new Map<string, Record<string, ModSlotFrame>>();
const listeners = new Set<() => void>();
let version = 0;

function bump(): void {
  version += 1;
  for (const listener of listeners) {
    try {
      listener();
    } catch {
      /* a listener must never break the others */
    }
  }
}

export function setModSlot(
  sid: string,
  slot: string,
  nodes: ModNode[],
  dropped = 0,
  paneId?: string,
): void {
  const key = String(sid || '').trim();
  if (!key) return;
  const forSid = { ...(bySid.get(key) || {}) };
  const slotKey = frameKey(slot, paneId);
  if (!nodes.length) delete forSid[slotKey];
  else forSid[slotKey] = { slot, nodes, dropped, paneId, at: Date.now() };
  bySid.set(key, forSid);
  bump();
}

export function getModSlot(sid: string, slot: string, paneId?: string): ModSlotFrame | undefined {
  return bySid.get(String(sid || '').trim())?.[frameKey(slot, paneId)];
}

/** Pane ids that currently have content, for a given slot. */
export function listModPanes(sid: string, slot = 'Pane'): string[] {
  const frames = bySid.get(String(sid || '').trim());
  if (!frames) return [];
  return Object.values(frames)
    .filter((f) => f.slot === slot && f.paneId)
    .map((f) => String(f.paneId));
}

/** Drop a session's slots — the band must not outlive its session. */
export function clearModSlots(sid: string): void {
  if (bySid.delete(String(sid || '').trim())) bump();
}

export function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** `useSyncExternalStore` needs a value that only changes when the data does. */
export function getModSlotVersion(): number {
  return version;
}
