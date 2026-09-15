/**
 * Sidebar session buckets: 置顶 / 通讯 / 最近 / 归档.
 * The unique external-reply session lives only in 通讯.
 */
export type SidebarSession = {
  id: string;
  primary?: boolean;
  last_updated?: string | null;
  created_at?: string | null;
};

export type SidebarSessionMeta = {
  pinned?: boolean;
  archived?: boolean;
};

export function resolveCommsSessionId<T extends SidebarSession>(
  sessions: T[],
  primarySessionId: string | null | undefined,
  pendingPrimarySessionId: string | null | undefined,
): string | null {
  const pending = (pendingPrimarySessionId || '').trim();
  if (pending) return pending;
  const primary = (primarySessionId || '').trim();
  if (primary) return primary;
  const flagged = sessions.find((s) => s.primary);
  return flagged?.id ?? null;
}

export function ensureCommsSessionVisible<T extends SidebarSession>(
  workspaceSessions: T[],
  allSessions: T[],
  commsId: string | null,
): T[] {
  if (!commsId) return workspaceSessions;
  if (workspaceSessions.some((s) => s.id === commsId)) return workspaceSessions;
  const fromAll = allSessions.find((s) => s.id === commsId);
  if (fromAll) return [fromAll, ...workspaceSessions];
  // Placeholder row for the comms session before the list has caught up.
  // The generic T only guarantees SidebarSession fields, so the literal has to
  // cross the type boundary explicitly — a direct `as T` is rejected by
  // TS2352 ("neither type sufficiently overlaps").
  const stub = {
    id: commsId,
    title: commsId,
    preview: '',
    current: false,
    primary: true,
  } as unknown as T;
  return [stub, ...workspaceSessions];
}

/** Alias used by SessionSidebar. */
export const withCommsSession = ensureCommsSessionVisible;

export function groupSessionsForSidebar<T extends SidebarSession>(
  sessions: T[],
  metaMap: Record<string, SidebarSessionMeta | undefined>,
  commsId: string | null,
): { pinned: T[]; comms: T[]; recent: T[]; archive: T[] } {
  const pinned: T[] = [];
  const comms: T[] = [];
  const recent: T[] = [];
  const archive: T[] = [];
  for (const s of sessions) {
    if (commsId && s.id === commsId) {
      comms.push(s);
      continue;
    }
    const m = metaMap[s.id];
    if (m?.pinned) pinned.push(s);
    if (!m?.archived) recent.push(s);
    if (m?.archived) archive.push(s);
  }
  const byUpdated = (a: T, b: T) =>
    String(b.last_updated || '').localeCompare(String(a.last_updated || ''));
  pinned.sort(byUpdated);
  recent.sort(byUpdated);
  archive.sort(byUpdated);
  return { pinned, comms, recent, archive };
}
