/**
 * VoicePanel — Agent Web voice UI
 * Modes: record voice message | realtime call (PCM16)
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Mic, Phone, PhoneOff, Square, X } from 'lucide-react';

export type VoiceMode = 'record' | 'realtime';

export interface VoicePanelProps {
  open: boolean;
  onClose: () => void;
  disabled?: boolean;
  realtimeStatus?: string;
  transcript?: string;
  onSendVoiceMessage: (blob: Blob, durationSec: number) => Promise<void> | void;
  onRealtimeStart: () => void;
  onRealtimeStop: () => void;
  onAudioChunk: (pcm16Base64: string) => void;
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
  disabled,
  realtimeStatus = 'idle',
  transcript = '',
  onSendVoiceMessage,
  onRealtimeStart,
  onRealtimeStop,
  onAudioChunk,
}) => {
  const [mode, setMode] = useState<VoiceMode>('record');
  const [isRecording, setIsRecording] = useState(false);
  const [duration, setDuration] = useState(0);
  const [muted, setMuted] = useState(false);
  const [error, setError] = useState('');

  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const durationRef = useRef(0);
  const streamRef = useRef<MediaStream | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const processorRef = useRef<ScriptProcessorNode | null>(null);
  const playCtxRef = useRef<AudioContext | null>(null);
  const playTimeRef = useRef(0);

  const stopTimer = () => {
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
  };

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

  useEffect(() => {
    if (!open) {
      stopTimer();
      setIsRecording(false);
      cleanupRealtimeCapture();
      onRealtimeStop();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  useEffect(() => () => {
    stopTimer();
    cleanupRealtimeCapture();
  }, [cleanupRealtimeCapture]);

  const startRecord = async () => {
    setError('');
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;
      const recorder = new MediaRecorder(stream);
      mediaRecorderRef.current = recorder;
      chunksRef.current = [];
      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data);
      };
      recorder.onstop = () => {
        const blob = new Blob(chunksRef.current, { type: 'audio/webm' });
        const sec = durationRef.current;
        stream.getTracks().forEach((t) => t.stop());
        streamRef.current = null;
        void onSendVoiceMessage(blob, sec);
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
      setError(e?.message || 'Microphone permission denied');
    }
  };

  const stopRecord = () => {
    stopTimer();
    if (mediaRecorderRef.current && isRecording) {
      mediaRecorderRef.current.stop();
      setIsRecording(false);
    }
  };

  const startRealtime = async () => {
    setError('');
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
      });
      streamRef.current = stream;
      const ctx = new AudioContext({ sampleRate: 24000 });
      audioCtxRef.current = ctx;
      const source = ctx.createMediaStreamSource(stream);
      const processor = ctx.createScriptProcessor(4096, 1, 1);
      processorRef.current = processor;
      processor.onaudioprocess = (ev) => {
        if (muted) return;
        const input = ev.inputBuffer.getChannelData(0);
        // Resample roughly to 24k if needed — AudioContext already at 24k when supported
        const pcm = floatTo16BitPCM(input);
        onAudioChunk(arrayBufferToBase64(pcm));
      };
      source.connect(processor);
      processor.connect(ctx.destination);
      onRealtimeStart();
    } catch (e: any) {
      setError(e?.message || 'Microphone permission denied');
    }
  };

  const stopRealtime = () => {
    cleanupRealtimeCapture();
    onRealtimeStop();
  };

  // Expose play helper via custom event from parent (PCM16 base64)
  useEffect(() => {
    const handler = (ev: Event) => {
      const detail = (ev as CustomEvent).detail as { audio?: string };
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
        const src = ctx.createBufferSource();
        src.buffer = buf;
        src.connect(ctx.destination);
        const startAt = Math.max(ctx.currentTime, playTimeRef.current);
        src.start(startAt);
        playTimeRef.current = startAt + buf.duration;
      } catch {
        /* ignore decode errors */
      }
    };
    window.addEventListener('opensquad-voice-audio-out', handler as EventListener);
    return () => window.removeEventListener('opensquad-voice-audio-out', handler as EventListener);
  }, []);

  if (!open) return null;

  const inCall = mode === 'realtime' && (realtimeStatus === 'connected' || realtimeStatus === 'tool_running' || realtimeStatus === 'session.created' || realtimeStatus === 'session.updated');

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
          <button type="button" onClick={onClose} className="p-1 text-textMuted hover:text-textMain">
            <X size={16} />
          </button>
        </div>

        {error ? <p className="text-xs text-red-500 mb-2">{error}</p> : null}

        {mode === 'record' ? (
          <div className="flex items-center gap-3">
            {!isRecording ? (
              <button
                type="button"
                disabled={disabled}
                onClick={() => void startRecord()}
                className="flex items-center gap-2 px-3 py-2 rounded-lg bg-primary text-white text-sm"
              >
                <Mic size={16} /> 按住式录音
              </button>
            ) : (
              <button
                type="button"
                onClick={stopRecord}
                className="flex items-center gap-2 px-3 py-2 rounded-lg bg-red-500 text-white text-sm"
              >
                <Square size={14} /> 停止并发送 ({duration}s)
              </button>
            )}
            <span className="text-xs text-textMuted">最长 60 秒，发送为语音附件</span>
          </div>
        ) : (
          <div className="space-y-2">
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
                </>
              )}
              <span className="text-xs text-textMuted">状态: {realtimeStatus || 'idle'}</span>
            </div>
            {transcript ? (
              <p className="text-xs text-textMain/80 max-h-16 overflow-y-auto whitespace-pre-wrap">{transcript}</p>
            ) : (
              <p className="text-xs text-textMuted">通话中可触发 Agent 工具（filesystem 等）</p>
            )}
          </div>
        )}
      </div>
    </div>
  );
};

export default VoicePanel;
