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
    expect(panel).toContain('GATEWAY_ENDPOINT.port');
    expect(panel).not.toContain('window.location');
    // and the operator can correct the host when it is a loopback address
    expect(panel).toContain('groupAccess.loopbackWarning');
    expect(panel).toContain('data-testid="group-invite-host"');
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
});
