/**
 * The offset a conversation comes back to, and the guards around it.
 *
 * Reported: switching to another area and back always landed at the top of the history. The window
 * remembered a message id and an offset, but only restored them when the message was already
 * rendered — never true on the frame you switch back — so nothing moved the fresh container off
 * scrollTop 0. These tests cover the memory itself and that the window uses it.
 */
import fs from 'fs';
import path from 'path';
import { beforeEach, describe, expect, it } from 'vitest';

import {
  __resetScrollMemory,
  claimRestore,
  forgetScroll,
  recallScroll,
  releaseRestore,
  rememberScroll,
} from './chatScrollMemory';

const read = (rel: string) => fs.readFileSync(path.resolve(__dirname, rel), 'utf8');

describe('chat scroll memory', () => {
  beforeEach(() => __resetScrollMemory());

  it('remembers and recalls an offset', () => {
    rememberScroll('g1', 420);

    expect(recallScroll('g1')).toBe(420);
  });

  it('a conversation nobody has read has no offset', () => {
    expect(recallScroll('nope')).toBeNull();
    expect(recallScroll('')).toBeNull();
  });

  it('ignores values that cannot be a position', () => {
    rememberScroll('g1', Number.NaN);
    rememberScroll('g1', -1);
    rememberScroll('', 10);

    expect(recallScroll('g1')).toBeNull();
  });

  it('restores once per visit, then steps out of the way', () => {
    rememberScroll('g1', 100);

    expect(claimRestore('g1')).toBe(true);
    expect(claimRestore('g1')).toBe(false);

    releaseRestore('g1');

    expect(claimRestore('g1')).toBe(true);
  });

  it('keeps conversations apart', () => {
    rememberScroll('g1', 100);
    rememberScroll('g2', 200);

    expect(recallScroll('g1')).toBe(100);
    expect(recallScroll('g2')).toBe(200);
  });

  it('forgets a conversation completely', () => {
    rememberScroll('g1', 100);
    claimRestore('g1');

    forgetScroll('g1');

    expect(recallScroll('g1')).toBeNull();
    expect(claimRestore('g1')).toBe(true);
  });
});

describe('the chat window uses it', () => {
  it('restores once the conversation has rendered, not only when the message is present', () => {
    const src = read('../components/ChatWindow.tsx');

    expect(src).toContain('claimRestore(group.id)');
    expect(src).toContain('container.scrollTop = remembered');
    expect(src).toMatch(/if \(messages\.length === 0\) return;/);
  });

  it('records an offset only after the view has settled', () => {
    const src = read('../components/ChatWindow.tsx');

    expect(src).toContain('rememberScroll(group.id, container.scrollTop)');
    expect(src).toContain('if (isRestoringPositionRef.current || !hasRestoredPositionRef.current) return;');
    expect(src).toContain("container.addEventListener('scroll', onScroll, { passive: true })");
    expect(src).toContain('releaseRestore(group.id)');
  });
});
