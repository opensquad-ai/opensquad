/**
 * Folding a long message is only allowed where folding is safe.
 *
 * The message bodies that carry structure — collaboration cards, approval cards, fenced code,
 * tables — are rendered from markup, so cutting them at an arbitrary character cuts the markup with
 * them. Everything else long enough gets folded to its first stretch.
 */
import { describe, expect, it } from 'vitest';

import { LONG_TEXT_LIMIT, foldedText, shouldFold } from './longText';

const long = (n: number) => '字'.repeat(n);

describe('shouldFold', () => {
  it('folds a message longer than the limit', () => {
    expect(shouldFold(long(LONG_TEXT_LIMIT + 1))).toBe(true);
    expect(shouldFold(long(LONG_TEXT_LIMIT * 6))).toBe(true);
  });

  it('leaves a message that fits alone, including one exactly at the limit', () => {
    expect(shouldFold('短消息')).toBe(false);
    expect(shouldFold(long(LONG_TEXT_LIMIT))).toBe(false);
  });

  it('never folds a collaboration or approval card', () => {
    const card = `[[COLLAB_TASK]]{"collab_id":"A1"}`.padEnd(LONG_TEXT_LIMIT + 50, 'x');
    expect(shouldFold(card)).toBe(false);
    expect(shouldFold('[[COLLAB_APPROVAL]]' + long(LONG_TEXT_LIMIT))).toBe(false);
  });

  it('never folds code or a table', () => {
    expect(shouldFold('前言\n```\n' + long(LONG_TEXT_LIMIT) + '\n```')).toBe(false);
    expect(shouldFold('| a | b |\n| - | - |\n' + long(LONG_TEXT_LIMIT))).toBe(false);
  });

  it('survives nothing at all', () => {
    expect(shouldFold(null)).toBe(false);
    expect(shouldFold(undefined)).toBe(false);
    expect(shouldFold('')).toBe(false);
  });
});

describe('foldedText', () => {
  it('keeps exactly the visible stretch', () => {
    expect(foldedText(long(LONG_TEXT_LIMIT + 500))).toHaveLength(LONG_TEXT_LIMIT);
    expect(foldedText('短消息')).toBe('短消息');
    expect(foldedText(null)).toBe('');
  });

  it('takes the beginning of the message, not the end', () => {
    expect(foldedText('开头' + long(1000))).toMatch(/^开头/);
  });
});
