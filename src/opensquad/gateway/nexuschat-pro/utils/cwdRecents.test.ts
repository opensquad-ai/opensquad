/** Unit tests for cwd path helpers used by Agent Web folder picker. */
import { describe, expect, it } from 'vitest';
import { folderLabel, suggestPathFromFolderHint } from './cwdRecents';

describe('suggestPathFromFolderHint', () => {
  it('builds sibling path from current cwd + folder name', () => {
    expect(suggestPathFromFolderHint('C:/ai_test/t', 'ds')).toBe('C:/ai_test/ds');
    expect(suggestPathFromFolderHint('C:\\ai_test\\t', 'ds')).toBe('C:\\ai_test\\ds');
  });

  it('returns folder name when cwd is missing', () => {
    expect(suggestPathFromFolderHint(null, 'ds')).toBe('ds');
    expect(suggestPathFromFolderHint('', 'ds')).toBe('ds');
  });

  it('returns cwd when folder name is missing', () => {
    expect(suggestPathFromFolderHint('C:/ai_test/t', null)).toBe('C:/ai_test/t');
  });
});

describe('folderLabel', () => {
  it('returns last path segment', () => {
    expect(folderLabel('C:/ai_test/ds')).toBe('ds');
    expect(folderLabel('C:\\ai_test\\ds')).toBe('ds');
  });
});
