/**
 * Classify model cards for voice slot pickers (ASR / TTS / Realtime).
 * Mirrors ModelsPage tagging: dual audio ⇒ realtime; out-only ⇒ tts;
 * in-only only when the model id looks like a voice model (not multimodal chat).
 */
export type VoiceRole = 'asr' | 'tts' | 'realtime';

export type VoiceCardLike = {
  name?: string;
  is_audio?: boolean;
  is_audio_output?: boolean;
  is_builtin?: boolean;
  model_name?: string;
};

export function voiceRoleOf(card: VoiceCardLike): VoiceRole | null {
  const audioIn = !!card.is_audio;
  const audioOut = !!card.is_audio_output;
  const mn = (card.model_name || '').toLowerCase();
  const looksVoice = /realtime|tts|asr|stepaudio|whisper|speech|audio-|sensevoice/.test(mn);

  if (audioIn && audioOut) return 'realtime';
  if (audioOut && !audioIn) return 'tts';
  if (audioIn && !audioOut) return looksVoice ? 'asr' : null;
  if (looksVoice) {
    if (mn.includes('realtime')) return 'realtime';
    if (mn.includes('tts')) return 'tts';
    if (mn.includes('asr') || mn.includes('whisper') || mn.includes('sensevoice')) return 'asr';
  }
  return null;
}

/** Cards eligible for a voice binding dropdown. */
export function filterCardsForVoiceSlot<T extends VoiceCardLike>(
  cards: T[],
  slot: 'asr_card' | 'tts_card' | 'realtime_card',
): T[] {
  if (slot === 'asr_card') {
    // Voice-input ASR cards + system builtins (Whisper / SenseVoice).
    return cards.filter((c) => !!c.is_builtin || voiceRoleOf(c) === 'asr');
  }
  if (slot === 'tts_card') {
    return cards.filter((c) => voiceRoleOf(c) === 'tts');
  }
  return cards.filter((c) => voiceRoleOf(c) === 'realtime');
}

/** Keep a previously saved selection visible even if flags no longer match. */
export function withSelectedVoiceCard<T extends VoiceCardLike & { name: string }>(
  filtered: T[],
  all: T[],
  selectedName: string | null | undefined,
): T[] {
  const selected = (selectedName || '').trim();
  if (!selected || filtered.some((c) => c.name === selected)) return filtered;
  const card = all.find((c) => c.name === selected);
  return card ? [card, ...filtered] : filtered;
}
