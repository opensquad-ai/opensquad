/**
 * Persist "user wants an active voice call" across page refresh.
 * sessionStorage survives reload in the same tab; cleared when the tab closes.
 */

const PREFIX = 'opensquad_voice_call_v1:';

export type VoiceCallPersist = {
  forceAskAgent: boolean;
};

export function voiceCallStorageKey(agentId: string): string {
  return `${PREFIX}${agentId}`;
}

export function readVoiceCallPersist(agentId: string): VoiceCallPersist | null {
  if (!agentId) return null;
  try {
    const raw = sessionStorage.getItem(voiceCallStorageKey(agentId));
    if (!raw) return null;
    const parsed = JSON.parse(raw) as VoiceCallPersist;
    if (!parsed || typeof parsed !== 'object') return null;
    return { forceAskAgent: parsed.forceAskAgent !== false };
  } catch {
    return null;
  }
}

export function writeVoiceCallPersist(agentId: string, forceAskAgent: boolean): void {
  if (!agentId) return;
  try {
    sessionStorage.setItem(
      voiceCallStorageKey(agentId),
      JSON.stringify({ forceAskAgent: forceAskAgent !== false } satisfies VoiceCallPersist),
    );
  } catch {
    /* ignore quota / private mode */
  }
}

export function clearVoiceCallPersist(agentId: string): void {
  if (!agentId) return;
  try {
    sessionStorage.removeItem(voiceCallStorageKey(agentId));
  } catch {
    /* ignore */
  }
}

/** Delayed hangup so React StrictMode remount does not kill a live call. */
const pendingHangups = new Map<string, ReturnType<typeof setTimeout>>();

export function cancelPendingVoiceHangup(agentId: string): void {
  const t = pendingHangups.get(agentId);
  if (t != null) {
    clearTimeout(t);
    pendingHangups.delete(agentId);
  }
}

export function schedulePendingVoiceHangup(agentId: string, fn: () => void, delayMs = 800): void {
  cancelPendingVoiceHangup(agentId);
  const t = setTimeout(() => {
    pendingHangups.delete(agentId);
    fn();
  }, delayMs);
  pendingHangups.set(agentId, t);
}
