import { useCallback, useEffect, useRef, useState, type RefObject } from 'react';
import { getAiWsService } from '../services/aiWebSocket';
import { adminAPI, agentSessionAPI, SERVER_BASE_URL } from '../services/api';
import {
  clearVoiceCallPersist,
  writeVoiceCallPersist,
} from '../utils/voiceCallPersist';

type WsService = ReturnType<typeof getAiWsService>;

export type VoiceBindings = {
  asr_card: string;
  tts_card: string;
  realtime_card: string;
  realtime_voice: string;
};

/**
 * Auto-TTS pipeline + realtime voice call chrome.
 * Isolated so streaming scroll / timeline updates do not rebuild this state.
 */
export function useAgentWebVoice(opts: {
  agentId: string;
  agentDirName?: string;
  wsServiceRef: RefObject<WsService | null>;
}) {
  const { agentId, agentDirName, wsServiceRef } = opts;

  const [voicePanelOpen, setVoicePanelOpen] = useState(false);
  const [voiceRealtimeStatus, setVoiceRealtimeStatus] = useState('idle');
  const [voiceTranscript, setVoiceTranscript] = useState('');
  const [voiceRealtimeError, setVoiceRealtimeError] = useState('');
  const [autoSpeechEnabled, setAutoSpeechEnabled] = useState(false);
  const [voiceBindings, setVoiceBindings] = useState<VoiceBindings>({
    asr_card: '',
    tts_card: '',
    realtime_card: '',
    realtime_voice: '',
  });

  const autoSpeechEnabledRef = useRef(false);
  const voiceRealtimeStatusRef = useRef('idle');
  const autoTtsAudioRef = useRef<HTMLAudioElement | null>(null);
  const lastAutoSpokenRef = useRef('');
  const autoTtsGenRef = useRef(0);
  const autoTtsStreamOffsetRef = useRef(0);
  const autoTtsTextQueueRef = useRef<string[]>([]);
  const autoTtsUrlQueueRef = useRef<string[]>([]);
  const autoTtsOrderedUrlsRef = useRef<Map<number, string>>(new Map());
  const autoTtsNextSynthSeqRef = useRef(0);
  const autoTtsNextPlaySeqRef = useRef(0);
  const autoTtsSynthActiveRef = useRef(0);
  const autoTtsPlayingRef = useRef(false);
  const enqueueAutoTtsChunksRef = useRef<(chunks: string[]) => void>(() => {});
  const feedAutoTtsFromStreamRef = useRef<(fullText: string) => void>(() => {});
  const voiceCaptionRef = useRef<{ role: string; text: string }[]>([]);
  const voiceConnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const voiceResumeProbeRef = useRef(false);
  const voicePageHideRef = useRef(false);

  const clearVoiceConnectTimer = useCallback(() => {
    if (voiceConnectTimerRef.current) {
      clearTimeout(voiceConnectTimerRef.current);
      voiceConnectTimerRef.current = null;
    }
  }, []);

  const armVoiceConnectTimeout = useCallback(() => {
    clearVoiceConnectTimer();
    voiceConnectTimerRef.current = setTimeout(() => {
      setVoiceRealtimeStatus((prev) => {
        if (prev === 'connecting') {
          setVoiceRealtimeError('Realtime connect timed out (Agent 未在 20s 内返回状态，请重启 Agent 后再试)');
          return 'error';
        }
        return prev;
      });
    }, 20000);
  }, [clearVoiceConnectTimer]);

  useEffect(() => {
    autoSpeechEnabledRef.current = autoSpeechEnabled;
  }, [autoSpeechEnabled]);

  useEffect(() => {
    voiceRealtimeStatusRef.current = voiceRealtimeStatus;
  }, [voiceRealtimeStatus]);

  useEffect(() => {
    if (!agentId) {
      setAutoSpeechEnabled(false);
      return;
    }
    try {
      setAutoSpeechEnabled(localStorage.getItem(`ai_chat_auto_tts:${agentId}`) === 'true');
    } catch {
      setAutoSpeechEnabled(false);
    }
  }, [agentId]);

  const stopAutoTts = useCallback(() => {
    autoTtsGenRef.current += 1;
    autoTtsTextQueueRef.current = [];
    autoTtsUrlQueueRef.current = [];
    autoTtsOrderedUrlsRef.current.clear();
    autoTtsNextSynthSeqRef.current = 0;
    autoTtsNextPlaySeqRef.current = 0;
    autoTtsSynthActiveRef.current = 0;
    autoTtsPlayingRef.current = false;
    autoTtsStreamOffsetRef.current = 0;
    const a = autoTtsAudioRef.current;
    if (a) {
      a.pause();
      a.removeAttribute('src');
      a.load();
    }
  }, []);

  const unlockAutoTtsAudio = useCallback(() => {
    try {
      const silent =
        'data:audio/wav;base64,UklGRigAAABXQVZFZm10IBIAAAABAAEARKwAAIhYAQACABAAAABkYXRhAgAAAAEA';
      const a = new Audio(silent);
      a.volume = 0.01;
      void a.play().then(() => {
        a.pause();
        a.currentTime = 0;
      }).catch(() => { /* ignore */ });
      if (!autoTtsAudioRef.current) {
        autoTtsAudioRef.current = new Audio();
      }
    } catch { /* ignore */ }
  }, []);

  const splitAutoTtsChunks = useCallback((text: string, maxLen = 100): string[] => {
    const cleaned = (text || '').replace(/\s+/g, ' ').trim();
    if (!cleaned) return [];
    const rawParts = cleaned.split(/(?<=[。！？!?；;…\n])/);
    const chunks: string[] = [];
    for (const part of rawParts) {
      const s = part.trim();
      if (!s) continue;
      if (s.length <= maxLen) {
        chunks.push(s);
        continue;
      }
      for (let i = 0; i < s.length; i += maxLen) {
        const piece = s.slice(i, i + maxLen).trim();
        if (piece) chunks.push(piece);
      }
    }
    return chunks;
  }, []);

  const resolveAutoTtsUrl = useCallback((url: string) => {
    if (!url) return '';
    if (url.startsWith('http')) return url;
    return `${SERVER_BASE_URL}${url.startsWith('/') ? url : `/${url}`}`;
  }, []);

  const pumpAutoTtsPlay = useCallback(async (gen: number) => {
    if (autoTtsPlayingRef.current) return;
    autoTtsPlayingRef.current = true;
    try {
      while (gen === autoTtsGenRef.current) {
        const url = autoTtsUrlQueueRef.current.shift();
        if (!url) break;
        if (!autoSpeechEnabledRef.current) break;
        const audio = autoTtsAudioRef.current || new Audio();
        autoTtsAudioRef.current = audio;
        audio.src = url;
        try {
          await audio.play();
        } catch (playErr) {
          console.warn('[AIChatPage] Auto TTS play() blocked:', playErr);
          unlockAutoTtsAudio();
          await new Promise((r) => setTimeout(r, 40));
          try {
            await audio.play();
          } catch {
            continue;
          }
        }
        await new Promise<void>((resolve) => {
          const done = () => {
            audio.removeEventListener('ended', done);
            audio.removeEventListener('error', done);
            resolve();
          };
          audio.addEventListener('ended', done);
          audio.addEventListener('error', done);
        });
      }
    } finally {
      autoTtsPlayingRef.current = false;
      if (
        gen === autoTtsGenRef.current &&
        autoTtsUrlQueueRef.current.length > 0 &&
        autoSpeechEnabledRef.current
      ) {
        void pumpAutoTtsPlay(gen);
      }
    }
  }, [unlockAutoTtsAudio]);

  const pumpAutoTtsSynth = useCallback(async (gen: number) => {
    const CONCURRENCY = 2;
    const flushOrdered = () => {
      while (autoTtsOrderedUrlsRef.current.has(autoTtsNextPlaySeqRef.current)) {
        const url = autoTtsOrderedUrlsRef.current.get(autoTtsNextPlaySeqRef.current)!;
        autoTtsOrderedUrlsRef.current.delete(autoTtsNextPlaySeqRef.current);
        autoTtsNextPlaySeqRef.current += 1;
        if (url) autoTtsUrlQueueRef.current.push(url);
      }
      if (autoTtsUrlQueueRef.current.length > 0) {
        void pumpAutoTtsPlay(gen);
      }
    };
    while (
      gen === autoTtsGenRef.current &&
      autoSpeechEnabledRef.current &&
      agentId &&
      autoTtsTextQueueRef.current.length > 0 &&
      autoTtsSynthActiveRef.current < CONCURRENCY
    ) {
      const chunk = autoTtsTextQueueRef.current.shift();
      if (!chunk) break;
      const seq = autoTtsNextSynthSeqRef.current;
      autoTtsNextSynthSeqRef.current += 1;
      autoTtsSynthActiveRef.current += 1;
      void (async () => {
        try {
          console.log('[AIChatPage] Auto TTS chunk…', chunk.slice(0, 60));
          const t0 = performance.now();
          const res = await agentSessionAPI.synthesize(agentId, chunk);
          console.log('[AIChatPage] Auto TTS chunk ready', Math.round(performance.now() - t0), 'ms');
          if (gen !== autoTtsGenRef.current || !autoSpeechEnabledRef.current) return;
          const url = resolveAutoTtsUrl(res.url || '');
          autoTtsOrderedUrlsRef.current.set(seq, url || '');
          flushOrdered();
        } catch (err) {
          console.warn('[AIChatPage] Auto TTS chunk failed:', err);
          if (gen === autoTtsGenRef.current) {
            autoTtsOrderedUrlsRef.current.set(seq, '');
            flushOrdered();
          }
        } finally {
          autoTtsSynthActiveRef.current = Math.max(0, autoTtsSynthActiveRef.current - 1);
          if (gen === autoTtsGenRef.current) {
            void pumpAutoTtsSynth(gen);
          }
        }
      })();
    }
  }, [agentId, pumpAutoTtsPlay, resolveAutoTtsUrl]);

  const enqueueAutoTtsChunks = useCallback((chunks: string[]) => {
    const cleaned = chunks.map((c) => c.trim()).filter(Boolean);
    if (!cleaned.length || !agentId) return;
    if (!autoSpeechEnabledRef.current) return;
    const vs = voiceRealtimeStatusRef.current;
    if (vs === 'connected' || vs === 'connecting' || vs === 'tool_running') {
      console.log('[AIChatPage] Auto TTS skipped: realtime busy =', vs);
      return;
    }
    autoTtsTextQueueRef.current.push(...cleaned);
    void pumpAutoTtsSynth(autoTtsGenRef.current);
  }, [agentId, pumpAutoTtsSynth]);

  const feedAutoTtsFromStream = useCallback((fullText: string) => {
    if (!autoSpeechEnabledRef.current || !agentId) return;
    const vs = voiceRealtimeStatusRef.current;
    if (vs === 'connected' || vs === 'connecting' || vs === 'tool_running') return;

    const full = fullText || '';
    let offset = autoTtsStreamOffsetRef.current;
    if (offset > full.length) offset = 0;
    const pending = full.slice(offset);
    if (!pending.trim()) return;

    const chunks: string[] = [];
    let consumed = 0;
    const re = /[\s\S]*?[。！？!?\n]/g;
    let m: RegExpExecArray | null;
    while ((m = re.exec(pending)) !== null) {
      const piece = m[0].trim();
      if (piece) chunks.push(...splitAutoTtsChunks(piece));
      consumed = m.index + m[0].length;
    }
    if (!chunks.length && pending.trim().length >= 100) {
      const cut = pending.slice(0, 100);
      chunks.push(...splitAutoTtsChunks(cut));
      consumed = cut.length;
    }
    if (!chunks.length || consumed <= 0) return;
    autoTtsStreamOffsetRef.current = offset + consumed;
    enqueueAutoTtsChunks(chunks);
  }, [agentId, enqueueAutoTtsChunks, splitAutoTtsChunks]);

  const speakFinalReply = useCallback(async (text: string) => {
    const prompt = (text || '').trim();
    if (!agentId || !prompt) return;
    if (!autoSpeechEnabledRef.current) {
      console.log('[AIChatPage] Auto TTS skipped: disabled');
      return;
    }
    const vs = voiceRealtimeStatusRef.current;
    if (vs === 'connected' || vs === 'connecting' || vs === 'tool_running') {
      console.log('[AIChatPage] Auto TTS skipped: realtime busy =', vs);
      return;
    }
    if (lastAutoSpokenRef.current === prompt) {
      console.log('[AIChatPage] Auto TTS skipped: already spoken this text');
      return;
    }
    lastAutoSpokenRef.current = prompt;

    const offset = Math.min(autoTtsStreamOffsetRef.current, prompt.length);
    const rest = prompt.slice(offset).trim();
    autoTtsStreamOffsetRef.current = prompt.length;
    if (rest) {
      enqueueAutoTtsChunks(splitAutoTtsChunks(rest));
    } else if (autoTtsTextQueueRef.current.length === 0 && autoTtsUrlQueueRef.current.length === 0 && !autoTtsPlayingRef.current) {
      enqueueAutoTtsChunks(splitAutoTtsChunks(prompt));
    }
  }, [agentId, enqueueAutoTtsChunks, splitAutoTtsChunks]);

  const toggleAutoSpeech = useCallback((enabled: boolean) => {
    setAutoSpeechEnabled(enabled);
    autoSpeechEnabledRef.current = enabled;
    if (agentId) {
      try {
        localStorage.setItem(`ai_chat_auto_tts:${agentId}`, String(enabled));
      } catch { /* ignore */ }
    }
    if (enabled) {
      unlockAutoTtsAudio();
    } else {
      stopAutoTts();
    }
  }, [agentId, stopAutoTts, unlockAutoTtsAudio]);

  useEffect(() => () => stopAutoTts(), [stopAutoTts]);

  enqueueAutoTtsChunksRef.current = enqueueAutoTtsChunks;
  feedAutoTtsFromStreamRef.current = feedAutoTtsFromStream;

  const speakFinalReplyRef = useRef(speakFinalReply);
  speakFinalReplyRef.current = speakFinalReply;

  const handleVoiceBindingsChange = useCallback(
    async (next: VoiceBindings) => {
      setVoiceBindings(next);
      wsServiceRef.current?.setVoiceConfig(next);
      const dir = agentDirName || agentId;
      if (!dir) return;
      try {
        const cfg = await adminAPI.getConfig(dir);
        const full = { ...(cfg.config || {}) };
        full.voice = { ...(full.voice || {}), ...next };
        await adminAPI.updateConfig(dir, full);
      } catch (e) {
        console.warn('[AIChatPage] persist voice config failed', e);
      }
    },
    [agentId, agentDirName, wsServiceRef],
  );

  const handleVoiceRealtimeStart = useCallback(
    (startOpts?: { forceAskAgent?: boolean }) => {
      setVoiceTranscript('');
      voiceCaptionRef.current = [];
      setVoiceRealtimeError('');
      setVoiceRealtimeStatus('connecting');
      writeVoiceCallPersist(agentId, startOpts?.forceAskAgent !== false);
      armVoiceConnectTimeout();
      wsServiceRef.current?.startVoiceRealtime({
        force_ask_agent: startOpts?.forceAskAgent !== false,
      });
    },
    [agentId, armVoiceConnectTimeout, wsServiceRef],
  );

  const handleVoiceRealtimeStop = useCallback(() => {
    clearVoiceConnectTimer();
    clearVoiceCallPersist(agentId);
    wsServiceRef.current?.stopVoiceRealtime();
    setVoiceRealtimeStatus('idle');
    setVoiceRealtimeError('');
  }, [agentId, clearVoiceConnectTimer, wsServiceRef]);

  const handleVoiceAudioChunk = useCallback((b64: string) => {
    wsServiceRef.current?.sendVoiceAudioIn(b64);
  }, [wsServiceRef]);

  const handleMouthpieceUtterance = useCallback((b64: string, sampleRate: number) => {
    wsServiceRef.current?.sendMouthpieceUtterance(b64, sampleRate);
  }, [wsServiceRef]);

  const handleForceAskAgentChange = useCallback((force: boolean) => {
    wsServiceRef.current?.setVoiceRealtimeOptions({ force_ask_agent: force });
  }, [wsServiceRef]);

  return {
    autoSpeechEnabled,
    autoSpeechEnabledRef,
    toggleAutoSpeech,
    stopAutoTts,
    feedAutoTtsFromStreamRef,
    speakFinalReplyRef,
    lastAutoSpokenRef,
    voicePanelOpen,
    setVoicePanelOpen,
    voiceRealtimeStatus,
    setVoiceRealtimeStatus,
    voiceTranscript,
    setVoiceTranscript,
    voiceRealtimeError,
    setVoiceRealtimeError,
    voiceBindings,
    setVoiceBindings,
    voiceCaptionRef,
    voiceResumeProbeRef,
    voicePageHideRef,
    voiceRealtimeStatusRef,
    clearVoiceConnectTimer,
    armVoiceConnectTimeout,
    handleVoiceBindingsChange,
    handleVoiceRealtimeStart,
    handleVoiceRealtimeStop,
    handleVoiceAudioChunk,
    handleMouthpieceUtterance,
    handleForceAskAgentChange,
    unlockAutoTtsAudio,
  };
}
