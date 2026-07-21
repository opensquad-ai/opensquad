import { useEffect, useState } from 'react';

/** Progressive auto-expand for workflow folds (thought / plan / tool). */
export type WorkflowExpandLevel = 'collapsed' | 'thoughts' | 'full';

export const WORKFLOW_EXPAND_LEVEL_KEY = 'ai_chat_workflow_expand_level';
export const WORKFLOW_EXPAND_LEVEL_EVENT = 'opensquad-workflow-expand-level';

const LEGACY_SHOW_WORKFLOW_KEY = 'ai_chat_show_workflow';

const LEVELS: readonly WorkflowExpandLevel[] = ['collapsed', 'thoughts', 'full'];

export function isWorkflowExpandLevel(v: unknown): v is WorkflowExpandLevel {
  return typeof v === 'string' && (LEVELS as readonly string[]).includes(v);
}

/** Map level → which fold kinds auto-open (user toggles always win). */
export function workflowExpandFlags(level: WorkflowExpandLevel): {
  thoughts: boolean;
  plan: boolean;
  tools: boolean;
} {
  switch (level) {
    case 'full':
      return { thoughts: true, plan: true, tools: true };
    case 'thoughts':
      return { thoughts: true, plan: true, tools: false };
    default:
      return { thoughts: false, plan: false, tools: false };
  }
}

function migrateFromLegacy(): WorkflowExpandLevel {
  try {
    const legacy = localStorage.getItem(LEGACY_SHOW_WORKFLOW_KEY);
    // Classic "hide workflow" → fully collapsed defaults.
    if (legacy === 'false') return 'collapsed';
  } catch {
    /* ignore */
  }
  // Former lightbulb-on expanded thoughts but not tools.
  return 'thoughts';
}

export function readWorkflowExpandLevel(): WorkflowExpandLevel {
  try {
    const raw = localStorage.getItem(WORKFLOW_EXPAND_LEVEL_KEY);
    if (isWorkflowExpandLevel(raw)) return raw;
  } catch {
    /* ignore */
  }
  return migrateFromLegacy();
}

export function writeWorkflowExpandLevel(level: WorkflowExpandLevel): void {
  try {
    localStorage.setItem(WORKFLOW_EXPAND_LEVEL_KEY, level);
  } catch {
    /* ignore */
  }
  try {
    window.dispatchEvent(
      new CustomEvent(WORKFLOW_EXPAND_LEVEL_EVENT, { detail: level }),
    );
  } catch {
    /* ignore */
  }
}

/** Live preference for chat UI + settings panel. */
export function useWorkflowExpandLevel(): [
  WorkflowExpandLevel,
  (level: WorkflowExpandLevel) => void,
] {
  const [level, setLevel] = useState<WorkflowExpandLevel>(() => readWorkflowExpandLevel());

  useEffect(() => {
    const sync = () => setLevel(readWorkflowExpandLevel());
    const onCustom = (e: Event) => {
      const detail = (e as CustomEvent).detail;
      if (isWorkflowExpandLevel(detail)) setLevel(detail);
      else sync();
    };
    const onStorage = (e: StorageEvent) => {
      if (e.key === WORKFLOW_EXPAND_LEVEL_KEY || e.key === LEGACY_SHOW_WORKFLOW_KEY) sync();
    };
    window.addEventListener(WORKFLOW_EXPAND_LEVEL_EVENT, onCustom);
    window.addEventListener('storage', onStorage);
    return () => {
      window.removeEventListener(WORKFLOW_EXPAND_LEVEL_EVENT, onCustom);
      window.removeEventListener('storage', onStorage);
    };
  }, []);

  const setPersisted = (next: WorkflowExpandLevel) => {
    setLevel(next);
    writeWorkflowExpandLevel(next);
  };

  return [level, setPersisted];
}
