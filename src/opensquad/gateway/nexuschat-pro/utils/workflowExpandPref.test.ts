import { describe, it, expect, beforeEach, vi } from 'vitest';
import {
  isWorkflowExpandLevel,
  readWorkflowExpandLevel,
  workflowExpandFlags,
  writeWorkflowExpandLevel,
  WORKFLOW_EXPAND_LEVEL_KEY,
} from './workflowExpandPref';

function mockLocalStorage() {
  const store = new Map<string, string>();
  vi.stubGlobal('localStorage', {
    getItem: (k: string) => (store.has(k) ? store.get(k)! : null),
    setItem: (k: string, v: string) => {
      store.set(k, String(v));
    },
    removeItem: (k: string) => {
      store.delete(k);
    },
    clear: () => store.clear(),
  });
  return store;
}

describe('workflowExpandPref', () => {
  beforeEach(() => {
    mockLocalStorage();
    vi.stubGlobal('window', {
      dispatchEvent: () => true,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
    });
  });

  it('maps progressive flags', () => {
    expect(workflowExpandFlags('collapsed')).toEqual({
      thoughts: false,
      plan: false,
      tools: false,
    });
    expect(workflowExpandFlags('thoughts')).toEqual({
      thoughts: true,
      plan: true,
      tools: false,
    });
    expect(workflowExpandFlags('full')).toEqual({
      thoughts: true,
      plan: true,
      tools: true,
    });
  });

  it('migrates legacy hide-workflow to collapsed', () => {
    localStorage.setItem('ai_chat_show_workflow', 'false');
    expect(readWorkflowExpandLevel()).toBe('collapsed');
  });

  it('defaults to thoughts when no keys', () => {
    expect(readWorkflowExpandLevel()).toBe('thoughts');
  });

  it('persists explicit level', () => {
    writeWorkflowExpandLevel('full');
    expect(localStorage.getItem(WORKFLOW_EXPAND_LEVEL_KEY)).toBe('full');
    expect(readWorkflowExpandLevel()).toBe('full');
    expect(isWorkflowExpandLevel('full')).toBe(true);
    expect(isWorkflowExpandLevel('nope')).toBe(false);
  });
});
