/**
 * Behavioural tests for the message-preview search highlighter.
 *
 * The output is injected with `dangerouslySetInnerHTML`, so every case here is
 * about "does hostile input stay inert" as much as "is the match wrapped".
 * Mutation-checked: changing `const safe = escapeHtml(text)` to `const safe =
 * text` fails this file.
 */
import { describe, expect, it } from 'vitest';
import { HIGHLIGHT_TAG_OPEN, highlightText } from './highlightText';

describe('highlightText — escaping', () => {
  it('escapes HTML in the message text', () => {
    const out = highlightText('<script>alert(1)</script>', '');
    expect(out).not.toContain('<script');
    expect(out).toContain('&lt;script&gt;');
  });

  it('neutralizes a tag that would otherwise fire', () => {
    const out = highlightText('<img src=x onerror=alert(1)>', '');
    // No `<` survives, so no tag can be formed; `onerror=` still appears as
    // literal text, which is exactly what the user should see.
    expect(out).not.toContain('<');
    expect(out).toContain('&lt;img');
    expect(out).toContain('onerror=alert(1)&gt;');
  });

  it('escapes the text even when a query is supplied', () => {
    const out = highlightText('<b>bold</b>', 'bold');
    expect(out).not.toContain('<b>');
    expect(out).toContain('&lt;b&gt;');
    expect(out).toContain(HIGHLIGHT_TAG_OPEN);
  });

  it('does not let a hostile query introduce markup', () => {
    const out = highlightText('plain text', '<img src=x onerror=alert(1)>');
    expect(out).not.toContain('<img');
    expect(out.toLowerCase()).not.toContain('onerror=');
  });

  it('keeps ampersands rendering as ampersands', () => {
    expect(highlightText('a & b', '')).toBe('a &amp; b');
  });
});

describe('highlightText — matching', () => {
  it('wraps every occurrence, case-insensitively', () => {
    const out = highlightText('Foo foo FOO', 'foo');
    expect(out.match(/<mark/g)).toHaveLength(3);
  });

  it('returns the escaped text untouched when the query is empty or blank', () => {
    expect(highlightText('hello <b>', '')).toBe('hello &lt;b&gt;');
    expect(highlightText('hello <b>', '   ')).toBe('hello &lt;b&gt;');
    expect(highlightText('hello', '')).not.toContain('<mark');
  });

  it('treats regex metacharacters in the query literally', () => {
    const out = highlightText('a.b and axb', 'a.b');
    expect(out.match(/<mark/g)).toHaveLength(1);
    expect(out).toContain(`${HIGHLIGHT_TAG_OPEN}a.b</mark>`);
  });

  it('handles parentheses and brackets in the query without throwing', () => {
    expect(() => highlightText('f(x) [y] $1', '(x)')).not.toThrow();
    expect(highlightText('f(x) [y]', '(x)')).toContain('<mark');
  });

  it('matches a query containing an ampersand against escaped text', () => {
    const out = highlightText('Tom & Jerry', 'Tom & Jerry');
    expect(out.match(/<mark/g)).toHaveLength(1);
  });

  it('tolerates null-ish arguments', () => {
    expect(highlightText('', 'x')).toBe('');
    expect(highlightText('a', '')).toBe('a');
  });
});
