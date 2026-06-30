export type DesktopUpdatePhase =
  | 'idle'
  | 'downloading'
  | 'preparing'
  | 'launching'
  | 'shutting-down';

export interface DesktopUpdateProgress {
  percent: number;
  transferred: number;
  total: number;
}

export interface DesktopUpdateOverlayState {
  phase: DesktopUpdatePhase;
  progress: DesktopUpdateProgress;
  error: string | null;
  version: string | null;
}

const defaultProgress: DesktopUpdateProgress = { percent: 0, transferred: 0, total: 0 };

let state: DesktopUpdateOverlayState = {
  phase: 'idle',
  progress: defaultProgress,
  error: null,
  version: null,
};

const listeners = new Set<(next: DesktopUpdateOverlayState) => void>();

function emit(partial: Partial<DesktopUpdateOverlayState>): void {
  state = { ...state, ...partial };
  listeners.forEach((fn) => fn(state));
}

export function getDesktopUpdateOverlayState(): DesktopUpdateOverlayState {
  return state;
}

export function subscribeDesktopUpdateOverlay(
  listener: (next: DesktopUpdateOverlayState) => void,
): () => void {
  listeners.add(listener);
  listener(state);
  return () => listeners.delete(listener);
}

export function beginDesktopUpdate(version: string | null): void {
  emit({
    phase: 'downloading',
    version,
    error: null,
    progress: { ...defaultProgress },
  });
}

export function setDesktopUpdateProgress(progress: DesktopUpdateProgress): void {
  emit({ phase: 'downloading', progress });
}

export function setDesktopUpdatePhase(phase: DesktopUpdatePhase): void {
  emit({ phase });
}

export function failDesktopUpdate(message: string): void {
  emit({ phase: 'idle', error: message });
}

export function resetDesktopUpdateOverlay(): void {
  emit({ phase: 'idle', error: null, version: null, progress: { ...defaultProgress } });
}
