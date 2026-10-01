/**
 * Invite strings for a group hosted on another machine.
 *
 * Mirrors `opensquad/invite.py`: `<host>[:port]#<group_id>[?code=XXXXXX]`. The
 * group page shows it, the other machine pastes it into `pair_with_node` /
 * `join_by_invite`. Keeping the builder here means the string is produced by the
 * same shape the agent side parses.
 */

export const DEFAULT_PORT = 9555;

export interface ParsedInvite {
  host: string;
  port: number;
  groupId: string;
  code: string;
  secure: boolean;
}

const HOST_RE = /^[A-Za-z0-9._-]+$/;
const GROUP_RE = /^[A-Za-z0-9._-]+$/;

export function buildInviteString(
  host: string,
  groupId: string,
  options: { port?: number; code?: string; secure?: boolean } = {},
): string {
  const port = options.port ?? DEFAULT_PORT;
  const scheme = options.secure ? 'https://' : '';
  const query = options.code ? `?code=${options.code}` : '';
  return `${scheme}${host}:${port}#${groupId}${query}`;
}

/** Parse the same shape; null when it is not an invite (mirrors the agent side). */
export function parseInviteString(text: string): ParsedInvite | null {
  let raw = (text || '').trim();
  if (!raw || !raw.includes('#')) return null;

  let secure = false;
  for (const [prefix, isSecure] of [
    ['https://', true],
    ['http://', false],
    ['wss://', true],
    ['ws://', false],
  ] as const) {
    if (raw.toLowerCase().startsWith(prefix)) {
      secure = isSecure;
      raw = raw.slice(prefix.length);
      break;
    }
  }

  const [authority, ...restParts] = raw.split('#');
  const rest = restParts.join('#');
  const [groupPart, query = ''] = rest.split('?');
  const [hostPart, portPart = ''] = authority.split(':');
  const host = hostPart.trim();
  const groupId = (groupPart || '').trim();
  if (!host || !HOST_RE.test(host) || !groupId || !GROUP_RE.test(groupId)) return null;

  let port = DEFAULT_PORT;
  if (portPart.trim()) {
    port = Number(portPart.trim());
    if (!Number.isInteger(port) || port < 1 || port > 65535) return null;
  }

  let code = '';
  for (const pair of query.split('&')) {
    const [key, value = ''] = pair.split('=');
    if (key.trim().toLowerCase() === 'code') code = value.trim();
  }

  return { host, port, groupId, code, secure };
}
