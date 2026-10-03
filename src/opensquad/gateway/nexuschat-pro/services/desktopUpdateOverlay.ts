/**
 * Desktop update state — single source of truth for the update UX.
 *
 * Two surfaces read this store:
 *  - `UpdateChangelogDialog` — the small "what's new" popup shown when a new
 *    version is discovered (and reopenable from the notification).
 *  - `UpdateNotification`    — the always-on notification card. Its progress UI
 *    (bar + percent + bytes) sits *under* the version line while downloading.
 *
 * Flow: idle → available (offered, changelog open) → downloading (background,
 * non-blocking) → downloaded (installer ready, awaiting user) → preparing →
 * launching → shutting-down. `error` carries a failed download/install; the
 * phase stays `available` so the user can retry.
 */

export type DesktopUpdatePhase =
  | 'idle'
  | 'available'
  | 'downloading'
  | 'downloaded'
  | 'preparing'
  | 'launching'
  | 'shutting-down';

export interface DesktopUpdateProgress {
  percent: number;
  transferred: number;
  total: number;
}

/** Subset of the Electron `UpdateInfo` the UI needs to render the changelog. */
export interface DesktopUpdateOffer {
  version: string;
  releaseNotes?: string;
  releaseUrl?: string;
  isBeta?: boolean;
  downloadUrl: string;
  fileName: string;
}

export interface DesktopUpdateOverlayState {
  phase: DesktopUpdatePhase;
  progress: DesktopUpdateProgress;
  error: string | null;
  version: string | null;
  releaseNotes: string | null;
  releaseUrl: string | null;
  isBeta: boolean;
  downloadUrl: string | null;
  fileName: string | null;
  /** Whether the changelog dialog is open (independent of phase). */
  changelogOpen: boolean;
}

const defaultProgress: DesktopUpdateProgress = { percent: 0, transferred: 0, total: 0 };

const initialState: DesktopUpdateOverlayState = {
  phase: 'idle',
  progress: defaultProgress,
  error: null,
  version: null,
  releaseNotes: null,
  releaseUrl: null,
  isBeta: false,
  downloadUrl: null,
  fileName: null,
  changelogOpen: false,
};

let state: DesktopUpdateOverlayState = { ...initialState };

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

/** A new version was found: offer it and pop the changelog. */
export function offerDesktopUpdate(offer: DesktopUpdateOffer): void {
  // A background (auto) download may already be running — or finished — for this
  // exact version by the time the offer arrives. Never reset that back to
  // 'available', or the in-flight progress / ready installer state is lost.
  const sameVersionInProgress =
    state.version === offer.version &&
    (state.phase === 'downloading' || state.phase === 'downloaded');

  if (sameVersionInProgress) {
    emit({
      releaseNotes: offer.releaseNotes ?? state.releaseNotes,
      releaseUrl: offer.releaseUrl ?? state.releaseUrl,
      isBeta: Boolean(offer.isBeta),
      downloadUrl: offer.downloadUrl,
      fileName: offer.fileName,
    });
    return;
  }

  emit({
    phase: 'available',
    version: offer.version,
    releaseNotes: offer.releaseNotes ?? null,
    releaseUrl: offer.releaseUrl ?? null,
    isBeta: Boolean(offer.isBeta),
    downloadUrl: offer.downloadUrl,
    fileName: offer.fileName,
    error: null,
    progress: { ...defaultProgress },
    changelogOpen: true,
  });
}

export function openDesktopUpdateChangelog(): void {
  if (state.phase === 'idle') return;
  emit({ changelogOpen: true });
}

export function closeDesktopUpdateChangelog(): void {
  emit({ changelogOpen: false });
}

export function beginDesktopUpdate(version: string | null): void {
  emit({
    phase: 'downloading',
    version: version ?? state.version,
    error: null,
    changelogOpen: false,
    progress: { ...defaultProgress },
  });
}

export function setDesktopUpdateProgress(progress: DesktopUpdateProgress): void {
  emit({ phase: 'downloading', progress });
}

export function markDesktopUpdateDownloaded(): void {
  emit({
    phase: 'downloaded',
    progress: { ...state.progress, percent: 100 },
  });
}

export function setDesktopUpdatePhase(phase: DesktopUpdatePhase): void {
  emit({ phase });
}

export function failDesktopUpdate(message: string): void {
  // Fall back to `available` so the notification offers a retry instead of
  // vanishing; `idle` would hide the card and lose the download offer.
  emit({ phase: state.version ? 'available' : 'idle', error: message });
}

export function resetDesktopUpdateOverlay(): void {
  emit({ ...initialState, progress: { ...defaultProgress } });
}

/** User dismissed the notification entirely (keeps nothing pending). */
export function dismissDesktopUpdateOverlay(): void {
  emit({ ...initialState, progress: { ...defaultProgress } });
}
