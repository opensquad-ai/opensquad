import { afterEach, describe, expect, it } from 'vitest';
import {
  clearComposerDraft,
  getComposerDraft,
  resetComposerDrafts,
  setComposerDraft,
} from './composerDraftStore';

describe('composerDraftStore', () => {
  afterEach(() => {
    resetComposerDrafts();
  });

  it('stores and clears per session without empty keys', () => {
    expect(getComposerDraft('s1')).toBe('');
    setComposerDraft('s1', 'hello');
    setComposerDraft('s2', 'other');
    expect(getComposerDraft('s1')).toBe('hello');
    setComposerDraft('s1', '');
    expect(getComposerDraft('s1')).toBe('');
    expect(getComposerDraft('s2')).toBe('other');
    clearComposerDraft('s2');
    expect(getComposerDraft('s2')).toBe('');
  });

  it('ignores empty session ids', () => {
    setComposerDraft('', 'nope');
    expect(getComposerDraft('')).toBe('');
  });
});
