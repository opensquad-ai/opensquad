import type { ReasoningEffort } from '../components/ai-chat/EffortPicker';

/** Last manually selected model / effort — survives refresh before config loads. */
export function lastModelStorageKey(agentId: string) {
  return `opensquad.agent.${agentId}.lastModel`;
}

export function loadLastModelPick(agentId: string): { card: string | null; effort: ReasoningEffort | null } {
  try {
    const raw = localStorage.getItem(lastModelStorageKey(agentId));
    if (!raw) return { card: null, effort: null };
    const parsed = JSON.parse(raw);
    const card = typeof parsed?.card === 'string' && parsed.card.trim() ? parsed.card.trim() : null;
    const effortRaw = parsed?.effort;
    const effort =
      effortRaw === 'low' || effortRaw === 'medium' || effortRaw === 'high' ? effortRaw : null;
    return { card, effort };
  } catch {
    return { card: null, effort: null };
  }
}

export function saveLastModelPick(
  agentId: string,
  patch: { card?: string | null; effort?: ReasoningEffort | null },
) {
  try {
    const prev = loadLastModelPick(agentId);
    const next = {
      card: patch.card !== undefined ? patch.card : prev.card,
      effort: patch.effort !== undefined ? patch.effort : prev.effort,
    };
    localStorage.setItem(lastModelStorageKey(agentId), JSON.stringify(next));
  } catch {
    /* ignore */
  }
}
