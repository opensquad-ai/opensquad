/**
 * Identity for an agent-web terminal slot.
 *
 * The launcher keys shells by id and keeps each one's scrollback (`read(since)`
 * replays it), so a *stable* id is what lets a terminal survive a tab switch, a
 * panel close or a page reload: the panel reattaches to the same shell and pulls
 * its buffer from offset 0. An id minted per mount cannot — the panel comes back
 * as a fresh shell with a blank screen, which is exactly what "open the terminal,
 * open files, come back" looked like.
 *
 * The id only has to be stable and unique per slot; it is a dict key on the
 * launcher, never shown to the user.
 */

/** FNV-1a over the seed, base36. Short, deterministic, safe as a dict key. */
export function stableTerminalId(agentId: string, slot: string): string {
  const seed = `${agentId || ''}|${slot || ''}`;
  let hash = 0x811c9dc5;
  for (let i = 0; i < seed.length; i += 1) {
    hash ^= seed.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return `wt${hash.toString(36)}`;
}

/**
 * A one-off id for an anonymous terminal — one whose panel is the only thing that
 * knows it exists, and which is therefore closed when that panel unmounts.
 */
export function ephemeralTerminalId(): string {
  return `t${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`;
}
