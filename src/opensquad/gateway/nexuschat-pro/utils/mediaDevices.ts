/**
 * Safe getUserMedia for Safari / iOS.
 *
 * iOS Safari often throws:
 *   undefined is not an object (evaluating 'navigator.mediaDevices.getUserMedia')
 * when `navigator.mediaDevices` is missing (non-HTTPS, or older WebKit).
 */

export function isSecureMediaContext(): boolean {
  if (typeof window === 'undefined') return false;
  // HTTPS, localhost, and file:// (rare) — Safari requires secure context for mediaDevices.
  if (window.isSecureContext) return true;
  const host = window.location?.hostname || '';
  return host === 'localhost' || host === '127.0.0.1' || host === '[::1]';
}

export function getUserMediaSupported(): boolean {
  if (typeof navigator === 'undefined') return false;
  if (navigator.mediaDevices && typeof navigator.mediaDevices.getUserMedia === 'function') {
    return true;
  }
  const legacy = (navigator as any).getUserMedia
    || (navigator as any).webkitGetUserMedia
    || (navigator as any).mozGetUserMedia;
  return typeof legacy === 'function';
}

/**
 * Request mic/camera with modern + legacy fallbacks.
 * Always check support before calling, or catch the thrown Error.
 */
export async function getUserMediaSafe(
  constraints: MediaStreamConstraints = { audio: true },
): Promise<MediaStream> {
  if (typeof navigator === 'undefined') {
    throw new Error('当前环境不支持麦克风（无 navigator）');
  }

  // Prefer modern API when present.
  if (navigator.mediaDevices && typeof navigator.mediaDevices.getUserMedia === 'function') {
    return navigator.mediaDevices.getUserMedia(constraints);
  }

  // Legacy callback APIs (older Safari / WebView).
  const legacy: ((
    c: MediaStreamConstraints,
    success: (s: MediaStream) => void,
    fail: (e: Error) => void,
  ) => void) | undefined =
    (navigator as any).getUserMedia
    || (navigator as any).webkitGetUserMedia
    || (navigator as any).mozGetUserMedia;

  if (typeof legacy === 'function') {
    return new Promise<MediaStream>((resolve, reject) => {
      try {
        legacy.call(navigator, constraints, resolve, reject);
      } catch (e) {
        reject(e instanceof Error ? e : new Error(String(e)));
      }
    });
  }

  if (!isSecureMediaContext()) {
    throw new Error(
      '当前页面不是安全上下文（需 HTTPS 或 localhost），Safari/iOS 无法访问麦克风。请用 https:// 打开本站。',
    );
  }

  throw new Error('当前环境不支持摄像头/麦克风（navigator.mediaDevices 不可用）');
}

/** Mix AudioBuffer channels to mono Float32 samples. */
function mixToMono(buf: AudioBuffer): Float32Array {
  const len = buf.length;
  const out = new Float32Array(len);
  const n = buf.numberOfChannels;
  for (let c = 0; c < n; c++) {
    const ch = buf.getChannelData(c);
    for (let i = 0; i < len; i++) out[i] += ch[i] / n;
  }
  return out;
}

/** Encode mono Float32 PCM as 16-bit little-endian WAV. */
function encodeWavMono(samples: Float32Array, sampleRate: number): ArrayBuffer {
  const dataSize = samples.length * 2;
  const buffer = new ArrayBuffer(44 + dataSize);
  const view = new DataView(buffer);
  const writeStr = (offset: number, s: string) => {
    for (let i = 0; i < s.length; i++) view.setUint8(offset + i, s.charCodeAt(i));
  };
  writeStr(0, 'RIFF');
  view.setUint32(4, 36 + dataSize, true);
  writeStr(8, 'WAVE');
  writeStr(12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true); // PCM
  view.setUint16(22, 1, true); // mono
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeStr(36, 'data');
  view.setUint32(40, dataSize, true);
  let off = 44;
  for (let i = 0; i < samples.length; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(off, s < 0 ? s * 0x8000 : s * 0x7fff, true);
    off += 2;
  }
  return buffer;
}

/**
 * Decode browser MediaRecorder blobs (often webm/opus) to a WAV File.
 * StepFun ASR is unreliable with some webm containers; WAV is stable.
 */
export async function blobToWavFile(
  blob: Blob,
  filename = `voice_${Date.now()}.wav`,
): Promise<File> {
  if (!blob || blob.size < 64) {
    throw new Error('录音太短或为空，请按住麦克风多说几秒再松手');
  }
  // Already wav — pass through only when the blob itself is WAV.
  // Callers often pass a desired output filename ending in ``.wav`` while the
  // blob is still webm/opus from MediaRecorder — do NOT treat that as WAV.
  const type = (blob.type || '').toLowerCase();
  if (type.includes('wav') || type.includes('wave')) {
    return blob instanceof File
      ? blob
      : new File([blob], filename, { type: 'audio/wav' });
  }

  const AudioCtx =
    window.AudioContext ||
    (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
  if (!AudioCtx) {
    // Fallback: send original blob
    const ext = type.includes('webm') ? 'webm' : type.includes('ogg') ? 'ogg' : 'webm';
    return new File([blob], filename.replace(/\.wav$/i, `.${ext}`), {
      type: blob.type || `audio/${ext}`,
    });
  }

  const ctx = new AudioCtx();
  try {
    const ab = await blob.arrayBuffer();
    const audioBuf = await ctx.decodeAudioData(ab.slice(0));
    const mono = mixToMono(audioBuf);
    const wav = encodeWavMono(mono, audioBuf.sampleRate);
    return new File([wav], filename, { type: 'audio/wav' });
  } catch (e) {
    console.warn('[blobToWavFile] decode failed, falling back to original blob', e);
    const ext = type.includes('webm') ? 'webm' : 'webm';
    return new File([blob], filename.replace(/\.wav$/i, `.${ext}`), {
      type: blob.type || 'audio/webm',
    });
  } finally {
    try {
      await ctx.close();
    } catch {
      /* ignore */
    }
  }
}
