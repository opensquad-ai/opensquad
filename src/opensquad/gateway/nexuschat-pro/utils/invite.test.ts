import fs from 'node:fs';
import path from 'node:path';

import { describe, expect, it } from 'vitest';

import { buildInviteString, DEFAULT_PORT, parseInviteString } from './invite';

describe('invite strings', () => {
  it('builds the shape the agent side parses', () => {
    expect(buildInviteString('192.168.5.4', 'g-7f3a')).toBe(`192.168.5.4:${DEFAULT_PORT}#g-7f3a`);
    expect(buildInviteString('192.168.5.4', 'g-7f3a', { port: 9600 })).toBe('192.168.5.4:9600#g-7f3a');
    expect(buildInviteString('chat.example.com', 'g-7f3a', { code: 'AB12CD', secure: true })).toBe(
      'https://chat.example.com:9555#g-7f3a?code=AB12CD',
    );
  });

  it('round-trips through the parser', () => {
    const invite = buildInviteString('192.168.5.4', 'g-7f3a', { port: 9600, code: 'AB12CD' });

    expect(parseInviteString(invite)).toEqual({
      host: '192.168.5.4',
      port: 9600,
      groupId: 'g-7f3a',
      code: 'AB12CD',
      secure: false,
    });
  });

  it('fills in the default port and reports a secure host', () => {
    expect(parseInviteString('https://chat.example.com#g-7f3a')).toMatchObject({
      port: DEFAULT_PORT,
      secure: true,
      code: '',
    });
  });

  it('refuses what is not an invite', () => {
    for (const bad of ['', '192.168.5.4', '#g-7f3a', '192.168.5.4#', '192.168.5.4:abc#g-7f3a', '192.168.5.4#g 7f3a']) {
      expect(parseInviteString(bad)).toBeNull();
    }
  });

  it('the panel builds the invite from the gateway, never from the page address', () => {
    // Regression: under Vite DEV the page is on :5173 while agents talk to the
    // gateway on :9555, so an invite built from window.location read
    // "127.0.0.1:5173#g-default" — unusable for the other machine.
    const panel = fs.readFileSync(
      path.resolve(__dirname, '..', 'components', 'GroupAccessPanel.tsx'),
      'utf8',
    );
    // The port lives in the string the pairing panel builds; what matters here is that the host
    // comes from the gateway endpoint rather than from the page the browser happens to sit on.
    expect(panel).toContain('useState(GATEWAY_ENDPOINT.host)');
    expect(panel).not.toContain('window.location');
    // The host is detected rather than typed: only the paired invite is offered now, and the one
    // thing the panel still says is when detection came back with an address nobody can dial.
    expect(panel).toContain('groupAccess.loopbackWarning');
    expect(panel).not.toContain('data-testid="group-invite-host"');
    // the panel hands host + group to the pairing panel so the code can be merged
    expect(panel).toContain('host={inviteHost}');
    expect(panel).toContain('groupId={group.id}');

    // One string to send: the pairing panel merges the invite with its code.
    const pairing = fs.readFileSync(
      path.resolve(__dirname, '..', 'components', 'NodePairingPanel.tsx'),
      'utf8',
    );
    expect(pairing).toContain('buildInviteString(host.trim(), groupId');
    expect(pairing).toContain('data-testid="node-pairing-coded-invite"');
  });

  it('takes the host from the backend, and prefers an address a peer can dial', () => {
    const panel = fs.readFileSync(
      path.resolve(__dirname, '..', 'components', 'GroupAccessPanel.tsx'),
      'utf8',
    );

    // the browser cannot read this host's interfaces: the backend detects them
    expect(panel).toContain('nodesAPI');
    expect(panel).toContain('.localAddresses()');
    // The client must ask under /ai-web: the route lives on the ai-web router, and asking for
    // /api/node/local-addresses answered 404 — which the panel then swallowed, leaving the loopback
    // address in place and looking for all the world like detection simply did not work.
    const api = fs.readFileSync(path.resolve(__dirname, '..', 'services', 'api.ts'), 'utf8');
    expect(api).toContain("apiRequest<{ ok: boolean; addresses: string[]; hostname?: string }>('/ai-web/node/local-addresses')");
    expect(api).not.toContain("hostname?: string }>('/node/local-addresses')");
    expect(panel).not.toContain('.catch(() => undefined);');
    // always, not only when the page happens to be served from loopback — the page often is, and
    // that is precisely when the browser's own address is useless to the peer
    expect(panel).toContain('const hostIsLoopback = isLoopbackHost(inviteHost);');
    expect(panel).not.toContain('if (!/^(localhost|127\\.');
    // and a loopback answer is never taken from the list
    expect(panel).toContain('.find((address) => address && !isLoopbackHost(address));');
    expect(panel).toContain('const isLoopbackHost = (value: string): boolean =>');
  });
});
