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
});
