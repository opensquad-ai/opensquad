/**
 * VoicePanel — Agent Web voice UI
 * Modes: record voice message | realtime call (PCM16)
 *
 * Closing / collapsing the panel does NOT hang up an active realtime call —
 * mic uplink + playback keep running in the background until Hangup.
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { ChevronsDown, Mic, Phone, PhoneOff, Settings2, X } from 'lucide-react';
import { getUserMediaSafe } from '../../utils/mediaDevices';
import type { ModelCardInfo } from '../../services/api';
import { filterCardsForVoiceSlot, withSelectedVoiceCard } from '../../utils/voiceCardRole';
import { VoiceRecordPill } from './VoiceRecordPill';

export type VoiceMode = 'record' | 'realtime';

export interface VoiceCardBindings {
  asr_card: string;
  tts_card: string;
  realtime_card: string;
  realtime_voice: string;
}

export interface VoicePanelProps {
  open: boolean;
  /** Collapse panel UI only (realtime call keeps running if active). */
  onClose: () => void;
  /** Expand a minimized panel (e.g. from the floating call bar). */
  onOpen?: () => void;
  disabled?: boolean;
  realtimeStatus?: string;
  realtimeError?: string;
  transcript?: string;
  onSendVoiceMessage: (blob: Blob, durationSec: number) => Promise<void> | void;
  /** Shown while parent is calling /transcribe. */
  dictating?: boolean;
  /** forceAskAgent: mouthpiece ASR→Agent→TTS (no Realtime listen) */
  onRealtimeStart: (opts?: { forceAskAgent?: boolean }) => void;
  onRealtimeStop: () => void;
  onAudioChunk: (pcm16Base64: string) => void;
  /** Force/mouthpiece: whole utterance PCM16 base64 @ sampleRate */
  onMouthpieceUtterance?: (pcm16Base64: string, sampleRate: number) => void;
  /** Live-update force_ask_agent while a call is active. */
  onForceAskAgentChange?: (force: boolean) => void;
  /** Available model cards for independent ASR / TTS / Realtime binding. */
  modelCards?: ModelCardInfo[];
  voiceBindings?: VoiceCardBindings;
  onVoiceBindingsChange?: (next: VoiceCardBindings) => void | Promise<void>;
  /** Notify composer when record-message capture state changes (for input-bar pill). */
  onCaptureStateChange?: (state: {
    recording: boolean;
    durationSec: number;
    level: number;
  }) => void;
  /** Parent can call stopRecord() from the composer pill. */
  captureApiRef?: React.MutableRefObject<{ stopRecord: () => void } | null>;
}

const FORCE_ASK_STORAGE_KEY = 'opensquad_voice_force_ask_agent';

function readForceAskDefault(): boolean {
  try {
    const v = localStorage.getItem(FORCE_ASK_STORAGE_KEY);
    if (v === '0' || v === 'false') return false;
    if (v === '1' || v === 'true') return true;
  } catch {
    /* ignore */
  }
  return true;
}

function isRealtimeActive(status: string): boolean {
  return (
    status === 'connected' ||
    status === 'tool_running' ||
    status === 'connecting' ||
    status === 'session.created' ||
    status === 'session.updated'
  );
}

function floatTo16BitPCM(input: Float32Array): ArrayBuffer {
  const buffer = new ArrayBuffer(input.length * 2);
  const view = new DataView(buffer);
  for (let i = 0; i < input.length; i++) {
    const s = Math.max(-1, Math.min(1, input[i]));
    view.setInt16(i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return buffer;
}

function arrayBufferToBase64(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  let binary = '';
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
  }
  return btoa(binary);
}

export const VoicePanel: React.FC<VoicePanelProps> = ({
  open,
  onClose,
  onOpen,
  disabled,
  realtimeStatus = 'idle',
  realtimeError = '',
  transcript = '',
  onSendVoiceMessage,
  dictating = false,
  onRealtimeStart,
  onRealtimeStop,
  onAudioChunk,
  onMouthpieceUtterance,
  onForceAskAgentChange,
  modelCards = [],
  voiceBindings,
  onVoiceBindingsChange,
  onCaptureStateChange,
  captureApiRef,
}) => {
  const [mode, setMode] = useState<VoiceMode>('record');
  const [isRecording, setIsRecording] = useState(false);
  const [duration, setDuration] = useState(0);
  const [recordLevel, setRecordLevel] = useState(0);
  const [muted, setMuted] = useState(false);
  const [error, setError] = useState('');
  const [forceAskAgent, setForceAskAgent] = useState(readForceAskDefault);
  const [showVoiceConfig, setShowVoiceConfig] = useState(false);
  const [draftBindings, setDraftBindings] = useState<VoiceCardBindings>({
    asr_card: '',
    tts_card: '',
    realtime_card: '',
    realtime_voice: '',
  });
  const [savingVoice, setSavingVoice] = useState(false);
  const uplinkPausedRef = useRef(false);
  const statusRef = useRef(realtimeStatus);

  useEffect(() => {
    if (!voiceBindings) return;
    setDraftBindings({
      asr_card: voiceBindings.asr_card || '',
      tts_card: voiceBindings.tts_card || '',
      realtime_card: voiceBindings.realtime_card || '',
      realtime_voice: voiceBindings.realtime_voice || '',
    });
  }, [voiceBindings]);
  statusRef.current = realtimeStatus;
  const ttsPlayingRef = useRef(false);
  const modeRef = useRef<VoiceMode>(mode);
  modeRef.current = mode;

  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const durationRef = useRef(0);
  const streamRef = useRef<MediaStream | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const processorRef = useRef<ScriptProcessorNode | null>(null);
  const recordAnalyserRef = useRef<AnalyserNode | null>(null);
  const recordMeterRafRef = useRef<number | null>(null);
  const recordMeterCtxRef = useRef<AudioContext | null>(null);
  const playCtxRef = useRef<AudioContext | null>(null);
  const playTimeRef = useRef(0);
  const forceAskRef = useRef(forceAskAgent);
  forceAskRef.current = forceAskAgent;
  const vadSpeechRef = useRef(false);
  const vadSilentFramesRef = useRef(0);
  const vadChunksRef = useRef<Int16Array[]>([]);
  const mp3AudioRef = useRef<HTMLAudioElement | null>(null);
  const mp3QueueRef = useRef<string[]>([]);
  const mp3PlayingRef = useRef(false);

  const inCall = mode === 'realtime' && isRealtimeActive(realtimeStatus);
  const isError = realtimeStatus === 'error';

  const syncUplinkPause = useCallback(() => {
    // Only mute uplink while local TTS is playing (echo guard).
    // Keep listening during tool_running / agent work so barge-in speech
    // is still ASR'd and pushed into the agent workflow.
    const busy = ttsPlayingRef.current || statusRef.current === 'connecting';
    uplinkPausedRef.current = busy;
    if (!busy) {
      // Ready for next turn — drop any partial VAD buffer from TTS echo window.
      vadSpeechRef.current = false;
      vadSilentFramesRef.current = 0;
      vadChunksRef.current = [];
    }
  }, []);

  useEffect(() => {
    syncUplinkPause();
  }, [realtimeStatus, syncUplinkPause]);

  // PCM realtime playback: briefly pause mic; mouthpiece mp3 uses ttsPlayingRef instead.
  useEffect(() => {
    let speakTimer: ReturnType<typeof setTimeout> | null = null;
    const handler = (ev: Event) => {
      const detail = (ev as CustomEvent).detail as { audio?: string; url?: string };
      if (detail?.url) return; // mp3 mouthpiece path manages pause via ttsPlayingRef
      uplinkPausedRef.current = true;
      if (speakTimer) clearTimeout(speakTimer);
      speakTimer = setTimeout(() => {
        syncUplinkPause();
      }, 220);
    };
    window.addEventListener('opensquad-voice-audio-out', handler as EventListener);
    return () => {
      window.removeEventListener('opensquad-voice-audio-out', handler as EventListener);
      if (speakTimer) clearTimeout(speakTimer);
    };
  }, [syncUplinkPause]);

  const stopTimer = () => {
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
  };

  const stopRecordMeter = useCallback(() => {
    if (recordMeterRafRef.current != null) {
      cancelAnimationFrame(recordMeterRafRef.current);
      recordMeterRafRef.current = null;
    }
    recordAnalyserRef.current = null;
    if (recordMeterCtxRef.current) {
      void recordMeterCtxRef.current.close().catch(() => undefined);
      recordMeterCtxRef.current = null;
    }
    setRecordLevel(0);
  }, []);

  const startRecordMeter = useCallback(
    (stream: MediaStream) => {
      stopRecordMeter();
      try {
        const ctx = new AudioContext();
        recordMeterCtxRef.current = ctx;
        const source = ctx.createMediaStreamSource(stream);
        const analyser = ctx.createAnalyser();
        analyser.fftSize = 256;
        analyser.smoothingTimeConstant = 0.7;
        source.connect(analyser);
        recordAnalyserRef.current = analyser;
        const data = new Uint8Array(analyser.frequencyBinCount);
        let lastEmit = 0;
        const tick = () => {
          const a = recordAnalyserRef.current;
          if (!a) return;
          a.getByteTimeDomainData(data);
          let sum = 0;
          for (let i = 0; i < data.length; i++) {
            const v = (data[i] - 128) / 128;
            sum += v * v;
          }
          const rms = Math.sqrt(sum / data.length);
          const level = Math.min(1, rms * 4.2);
          const now = performance.now();
          // ~12 fps to parent — enough for bars, avoids thrashing React
          if (now - lastEmit > 80) {
            lastEmit = now;
            setRecordLevel(level);
          }
          recordMeterRafRef.current = requestAnimationFrame(tick);
        };
        recordMeterRafRef.current = requestAnimationFrame(tick);
      } catch {
        /* meter is optional */
      }
    },
    [stopRecordMeter],
  );

  useEffect(() => {
    onCaptureStateChange?.({
      recording: isRecording,
      durationSec: duration,
      level: recordLevel,
    });
  }, [isRecording, duration, recordLevel, onCaptureStateChange]);

  const cleanupRealtimeCapture = useCallback(() => {
    processorRef.current?.disconnect();
    processorRef.current = null;
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    if (audioCtxRef.current) {
      void audioCtxRef.current.close().catch(() => undefined);
      audioCtxRef.current = null;
    }
  }, []);

  // Collapse no longer aborts record-message — composer pill keeps control.

  // Unmount: release local audio. Hangup is explicit (挂断) — avoid StrictMode remount killing the call.
  useEffect(
    () => () => {
      stopTimer();
      stopRecordMeter();
      cleanupRealtimeCapture();
    },
    [cleanupRealtimeCapture, stopRecordMeter],
  );

  const stopRecord = useCallback(() => {
    stopTimer();
    stopRecordMeter();
    const rec = mediaRecorderRef.current;
    if (rec && rec.state === 'recording') {
      try {
        rec.stop();
      } catch {
        /* ignore */
      }
    }
    setIsRecording(false);
  }, [stopRecordMeter]);

  const startRecord = async () => {
    setError('');
    try {
      const stream = await getUserMediaSafe({ audio: true });
      streamRef.current = stream;
      startRecordMeter(stream);
      const recorder = new MediaRecorder(stream);
      mediaRecorderRef.current = recorder;
      chunksRef.current = [];
      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data);
      };
      recorder.onstop = () => {
        stopRecordMeter();
        const blob = new Blob(chunksRef.current, { type: 'audio/webm' });
        const sec = durationRef.current;
        stream.getTracks().forEach((t) => t.stop());
        streamRef.current = null;
        mediaRecorderRef.current = null;
        void (async () => {
          try {
            await onSendVoiceMessage(blob, sec);
          } catch (e: any) {
            setError(e?.message || '语音转写失败');
          }
        })();
      };
      recorder.start();
      setIsRecording(true);
      durationRef.current = 0;
      setDuration(0);
      timerRef.current = setInterval(() => {
        durationRef.current += 1;
        setDuration(durationRef.current);
        if (durationRef.current >= 60) stopRecord();
      }, 1000);
    } catch (e: any) {
      stopRecordMeter();
      setError(e?.message || 'Microphone permission denied');
    }
  };

  useEffect(() => {
    if (!captureApiRef) return;
    captureApiRef.current = { stopRecord };
    return () => {
      captureApiRef.current = null;
    };
  }, [captureApiRef, stopRecord]);

  const startRealtime = async (opts?: { notifyStart?: boolean }) => {
    setError('');
    const notifyStart = opts?.notifyStart !== false;
    try {
      if (streamRef.current) {
        if (notifyStart) onRealtimeStart({ forceAskAgent });
        return;
      }
      const stream = await getUserMediaSafe({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
      });
      streamRef.current = stream;
      const ctx = new AudioContext({ sampleRate: 24000 });
      audioCtxRef.current = ctx;
      const source = ctx.createMediaStreamSource(stream);
      // Mouthpiece keeps 4096 (~171ms) for 2s end-silence math; Realtime needs
      // smaller chunks so server VAD reacts in tens of ms, not ~171ms steps.
      const bufferSize = forceAskRef.current ? 4096 : 1024;
      const processor = ctx.createScriptProcessor(bufferSize, 1, 1);
      processorRef.current = processor;
      vadSpeechRef.current = false;
      vadSilentFramesRef.current = 0;
      vadChunksRef.current = [];

      const flushMouthpieceUtterance = () => {
        const parts = vadChunksRef.current;
        vadChunksRef.current = [];
        vadSpeechRef.current = false;
        vadSilentFramesRef.current = 0;
        if (!parts.length || !onMouthpieceUtterance) return;
        let total = 0;
        for (const p of parts) total += p.length;
        if (total < 2400) return; // < ~100ms @24k — ignore clicks
        const merged = new Int16Array(total);
        let off = 0;
        for (const p of parts) {
          merged.set(p, off);
          off += p.length;
        }
        onMouthpieceUtterance(arrayBufferToBase64(merged.buffer.slice(0, merged.byteLength)), 24000);
      };

      processor.onaudioprocess = (ev) => {
        if (muted || uplinkPausedRef.current) return;
        const input = ev.inputBuffer.getChannelData(0);
        const pcmBuf = floatTo16BitPCM(input);
        const pcmView = new Int16Array(pcmBuf);

        if (forceAskRef.current) {
          // Local energy VAD → whole utterance to ASR→Agent→TTS (no Realtime uplink)
          let sum = 0;
          for (let i = 0; i < input.length; i++) sum += input[i] * input[i];
          const rms = Math.sqrt(sum / Math.max(1, input.length));
          const speaking = rms > 0.015;
          if (speaking) {
            vadSpeechRef.current = true;
            vadSilentFramesRef.current = 0;
            vadChunksRef.current.push(pcmView.slice());
          } else if (vadSpeechRef.current) {
            vadChunksRef.current.push(pcmView.slice());
            vadSilentFramesRef.current += 1;
            // End-of-utterance silence ≈ 2s (depends on ScriptProcessor buffer).
            // 4096@24k → ~171ms/frame → 12 frames; 1024@24k → ~43ms → 47 frames.
            const silenceFramesNeeded = bufferSize >= 4096 ? 12 : Math.ceil(2.0 / (bufferSize / 24000));
            if (vadSilentFramesRef.current >= silenceFramesNeeded) {
              flushMouthpieceUtterance();
            }
          }
          return;
        }

        onAudioChunk(arrayBufferToBase64(pcmBuf));
      };
      source.connect(processor);
      // Keep processor alive without speaker loopback (echo would poison next-turn VAD).
      const silent = ctx.createGain();
      silent.gain.value = 0;
      processor.connect(silent);
      silent.connect(ctx.destination);
      setMode('realtime');
      if (notifyStart) onRealtimeStart({ forceAskAgent });
    } catch (e: any) {
      setError(e?.message || 'Microphone permission denied');
    }
  };

  // After page refresh: parent restores connected status; re-attach mic without restarting agent session.
  const resumeCaptureRef = useRef(false);
  useEffect(() => {
    if (!isRealtimeActive(realtimeStatus) || realtimeStatus === 'connecting') {
      if (!isRealtimeActive(realtimeStatus)) resumeCaptureRef.current = false;
      return;
    }
    if (streamRef.current || resumeCaptureRef.current) return;
    resumeCaptureRef.current = true;
    void startRealtime({ notifyStart: false });
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only re-attach when status becomes active
  }, [realtimeStatus]);

  const stopRealtime = () => {
    vadChunksRef.current = [];
    vadSpeechRef.current = false;
    vadSilentFramesRef.current = 0;
    if (mp3AudioRef.current) {
      try {
        mp3AudioRef.current.pause();
      } catch {
        /* ignore */
      }
      mp3AudioRef.current = null;
    }
    mp3QueueRef.current = [];
    mp3PlayingRef.current = false;
    ttsPlayingRef.current = false;
    cleanupRealtimeCapture();
    onRealtimeStop();
  };

  const toggleForceAsk = () => {
    if (inCall) {
      setError('切换嘴替/Realtime 请先挂断再重新开始通话');
      return;
    }
    const next = !forceAskAgent;
    setForceAskAgent(next);
    try {
      localStorage.setItem(FORCE_ASK_STORAGE_KEY, next ? '1' : '0');
    } catch {
      /* ignore */
    }
    // Only sync live options when a session exists. Idle toggle is local —
    // the next「开始通话」passes forceAskAgent into voice_realtime_start.
    if (isRealtimeActive(realtimeStatus)) {
      onForceAskAgentChange?.(next);
    }
  };

  // Play helper: PCM16 base64 (Realtime) or queued mp3 urls (mouthpiece TTS)
  useEffect(() => {
    const resumeAfterTts = () => {
      ttsPlayingRef.current = false;
      mp3PlayingRef.current = false;
      syncUplinkPause();
    };

    const playNextMp3 = () => {
      const next = mp3QueueRef.current.shift();
      if (!next) {
        resumeAfterTts();
        return;
      }
      try {
        if (mp3AudioRef.current) {
          try {
            mp3AudioRef.current.pause();
          } catch {
            /* ignore */
          }
        }
        const el = new Audio(next);
        mp3AudioRef.current = el;
        ttsPlayingRef.current = true;
        mp3PlayingRef.current = true;
        uplinkPausedRef.current = true;
        el.onended = () => playNextMp3();
        el.onerror = () => playNextMp3();
        void el.play().then(undefined, () => playNextMp3());
      } catch {
        playNextMp3();
      }
    };

    const handler = (ev: Event) => {
      const detail = (ev as CustomEvent).detail as {
        audio?: string;
        url?: string;
        format?: string;
        mime?: string;
        queued?: boolean;
      };
      if (detail?.url) {
        try {
          const src = detail.url.startsWith('http')
            ? detail.url
            : `${window.location.origin}${detail.url.startsWith('/') ? '' : '/'}${detail.url}`;
          mp3QueueRef.current.push(src);
          if (!mp3PlayingRef.current) {
            playNextMp3();
          }
        } catch {
          resumeAfterTts();
        }
        return;
      }
      const b64 = detail?.audio;
      if (!b64) return;
      try {
        const binary = atob(b64);
        const bytes = new Uint8Array(binary.length);
        for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
        const samples = new Int16Array(bytes.buffer);
        const floats = new Float32Array(samples.length);
        for (let i = 0; i < samples.length; i++) floats[i] = samples[i] / 0x8000;
        if (!playCtxRef.current) {
          playCtxRef.current = new AudioContext({ sampleRate: 24000 });
          playTimeRef.current = playCtxRef.current.currentTime;
        }
        const ctx = playCtxRef.current;
        const buf = ctx.createBuffer(1, floats.length, 24000);
        buf.copyToChannel(floats, 0);
        const srcNode = ctx.createBufferSource();
        srcNode.buffer = buf;
        srcNode.connect(ctx.destination);
        const startAt = Math.max(ctx.currentTime, playTimeRef.current);
        srcNode.start(startAt);
        playTimeRef.current = startAt + buf.duration;
      } catch {
        /* ignore decode errors */
      }
    };
    window.addEventListener('opensquad-voice-audio-out', handler as EventListener);
    return () => window.removeEventListener('opensquad-voice-audio-out', handler as EventListener);
  }, [syncUplinkPause]);

  // Minimized: no floating bar — green mic icon in composer is the only indicator.
  // Capture / playback keep running via hooks above.
  if (!open) return null;

  return (
    <div className="absolute bottom-full left-0 right-0 mb-2 z-30">
      <div className="mx-2 rounded-xl border border-border bg-bgDark/95 backdrop-blur shadow-lg p-3">
        <div className="flex items-center justify-between mb-2">
          <div className="flex gap-1 text-xs">
            <button
              type="button"
              disabled={disabled || inCall}
              onClick={() => setMode('record')}
              className={`px-2.5 py-1 rounded-md ${mode === 'record' ? 'bg-primary text-white' : 'bg-border/40 text-textMuted'}`}
            >
              录音消息
            </button>
            <button
              type="button"
              disabled={disabled}
              onClick={() => setMode('realtime')}
              className={`px-2.5 py-1 rounded-md ${mode === 'realtime' ? 'bg-primary text-white' : 'bg-border/40 text-textMuted'}`}
            >
              实时通话
            </button>
          </div>
          <div className="flex items-center gap-0.5">
            {onVoiceBindingsChange ? (
              <button
                type="button"
                onClick={() => setShowVoiceConfig((v) => !v)}
                className={`p-1 hover:text-textMain ${showVoiceConfig ? 'text-primary' : 'text-textMuted'}`}
                title="语音模型卡配置"
              >
                <Settings2 size={16} />
              </button>
            ) : null}
            {inCall ? (
              <button
                type="button"
                onClick={onClose}
                className="p-1 text-textMuted hover:text-textMain"
                title="折叠到后台（通话继续）"
              >
                <ChevronsDown size={16} />
              </button>
            ) : null}
            <button
              type="button"
              onClick={onClose}
              className="p-1 text-textMuted hover:text-textMain"
              title={inCall ? '折叠到后台（通话继续）' : '关闭'}
            >
              <X size={16} />
            </button>
          </div>
        </div>

        {showVoiceConfig && onVoiceBindingsChange ? (
          <div className="mb-2 rounded-lg border border-border/70 bg-bgLight/80 p-2 space-y-1.5">
            <p className="text-[10px] text-textMuted">
              三项各自选择模型卡。ASR 可选系统内置 Whisper / SenseVoice，或本地 OpenAI 兼容转写服务；TTS /
              Realtime 在「模型」面板用 url / api_key / model 创建。保存后立即生效。
            </p>
            {([
              { key: 'asr_card' as const, label: 'ASR 输入' },
              { key: 'tts_card' as const, label: 'TTS 输出' },
              { key: 'realtime_card' as const, label: 'Realtime 双向' },
            ]).map(({ key, label }) => {
              const ordered = withSelectedVoiceCard(
                filterCardsForVoiceSlot(modelCards, key),
                modelCards,
                draftBindings[key],
              );
              return (
              <div key={key} className="flex items-center gap-2">
                <span className="text-[10px] text-textMuted w-16 shrink-0">{label}</span>
                <select
                  className="flex-1 text-xs rounded-md border border-border bg-bgDark px-1.5 py-1"
                  value={draftBindings[key]}
                  disabled={disabled || savingVoice || inCall}
                  onChange={(e) => setDraftBindings((prev) => ({ ...prev, [key]: e.target.value }))}
                >
                  <option value="">(none)</option>
                  {ordered.map((c) => (
                    <option key={c.name} value={c.name}>
                      {c.title || c.name}
                    </option>
                  ))}
                </select>
              </div>
              );
            })}
            <div className="flex items-center gap-2">
              <span className="text-[10px] text-textMuted w-16 shrink-0">音色</span>
              <input
                className="flex-1 text-xs rounded-md border border-border bg-bgDark px-1.5 py-1"
                value={draftBindings.realtime_voice}
                disabled={disabled || savingVoice || inCall}
                placeholder="optional"
                onChange={(e) => setDraftBindings((prev) => ({ ...prev, realtime_voice: e.target.value }))}
              />
            </div>
            <div className="flex justify-end">
              <button
                type="button"
                disabled={disabled || savingVoice || inCall}
                className="text-[11px] px-2.5 py-1 rounded-md bg-primary text-white disabled:opacity-50"
                onClick={() => {
                  void (async () => {
                    setSavingVoice(true);
                    try {
                      await onVoiceBindingsChange(draftBindings);
                      setShowVoiceConfig(false);
                    } catch (e: any) {
                      setError(e?.message || '保存语音配置失败');
                    } finally {
                      setSavingVoice(false);
                    }
                  })();
                }}
              >
                {savingVoice ? '保存中…' : '保存'}
              </button>
            </div>
          </div>
        ) : null}

        {error ? <p className="text-xs text-red-500 mb-2">{error}</p> : null}

        {mode === 'record' ? (
          <div className="flex items-center gap-3">
            {!isRecording && !dictating ? (
              <button
                type="button"
                disabled={disabled}
                onClick={() => void startRecord()}
                className="flex items-center gap-2 px-3 py-2 rounded-lg bg-primary text-white text-sm"
              >
                <Mic size={16} /> 录音转文字
              </button>
            ) : (
              <VoiceRecordPill
                durationSec={duration}
                level={recordLevel}
                dictating={dictating}
                onClick={dictating ? undefined : stopRecord}
                title={dictating ? '正在转写…' : '点击停止并转写'}
              />
            )}
            <span className="text-xs text-textMuted">
              最长 60 秒，转写后写入发送框（需点发送）
            </span>
          </div>
        ) : (
          <div className="space-y-2">
            <label
              className="flex items-center gap-2 text-xs text-textMuted cursor-pointer select-none"
              title="开启：ASR→主Agent→TTS（Realtime 不参与听）。关闭：StepFun Realtime 双工，模型自行决定是否委托。"
            >
              <input
                type="checkbox"
                className="rounded border-border"
                checked={forceAskAgent}
                disabled={disabled || inCall}
                onChange={toggleForceAsk}
              />
              <span>嘴替主 Agent</span>
              <span className="text-[10px] opacity-70">
                {forceAskAgent ? '（ASR→主Agent→TTS）' : '（Realtime 双工）'}
              </span>
            </label>
            <div className="flex items-center gap-2">
              {!inCall ? (
                <button
                  type="button"
                  disabled={disabled}
                  onClick={() => void startRealtime()}
                  className="flex items-center gap-2 px-3 py-2 rounded-lg bg-emerald-600 text-white text-sm"
                >
                  <Phone size={16} /> 开始通话
                </button>
              ) : realtimeStatus === 'connecting' ? (
                <button
                  type="button"
                  onClick={stopRealtime}
                  className="flex items-center gap-2 px-3 py-2 rounded-lg bg-amber-500 text-white text-sm"
                >
                  <PhoneOff size={16} /> 连接中…取消
                </button>
              ) : (
                <>
                  <button
                    type="button"
                    onClick={stopRealtime}
                    className="flex items-center gap-2 px-3 py-2 rounded-lg bg-red-500 text-white text-sm"
                  >
                    <PhoneOff size={16} /> 挂断
                  </button>
                  <button
                    type="button"
                    onClick={() => setMuted((m) => !m)}
                    className={`px-3 py-2 rounded-lg text-sm ${muted ? 'bg-amber-500 text-white' : 'bg-border/50'}`}
                  >
                    {muted ? '已静音' : '静音'}
                  </button>
                  <button
                    type="button"
                    onClick={onClose}
                    className="px-3 py-2 rounded-lg text-sm bg-border/50 text-textMuted hover:text-textMain"
                    title="折叠面板，通话继续在后台"
                  >
                    折叠后台
                  </button>
                </>
              )}
              <span className={`text-xs ${isError ? 'text-red-500' : 'text-textMuted'}`}>
                状态: {realtimeStatus || 'idle'}
              </span>
            </div>
            {isError ? (
              <p className="text-xs text-red-500">
                {realtimeError ||
                  '连接失败。请确认 Agent 已配置 voice.realtime_card（模型卡需含有效 base_url / api_key / model），并已重启 Agent。'}
              </p>
            ) : null}
            {transcript ? (
              <p className="text-xs text-textMain/80 max-h-16 overflow-y-auto whitespace-pre-wrap">{transcript}</p>
            ) : (
              <p className="text-xs text-textMuted">
                通话中可折叠面板到后台；挂断才会结束会话。嘴替模式下主 Agent 可回 [VOICE_NO_REPLY] 表示本轮不播报。
              </p>
            )}
          </div>
        )}
      </div>
    </div>
  );
};

export default VoicePanel;
