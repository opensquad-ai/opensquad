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
