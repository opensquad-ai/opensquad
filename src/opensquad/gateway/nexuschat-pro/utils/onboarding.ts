/**
 * "Has this install already seen the tour?" — a cosmetic flag, kept separate
 * from the first-run wizard's real gate.
 *
 * App.tsx deliberately does NOT trust localStorage for the registration gate
 * (a stale key from another deployment must not skip the wizard). This flag is
 * different in kind: the worst a stale value can do is skip an introduction the
 * user has already read once on this browser profile. It is only consulted
 * after the backend has already said the deployment is unregistered.
 */
const KEY = 'opensquad_onboarding_done';

export function hasSeenOnboarding(): boolean {
  try {
    return localStorage.getItem(KEY) === '1';
  } catch {
    // Private mode / storage disabled: show the tour rather than crash.
    return false;
  }
}

export function markOnboardingSeen(): void {
  try {
    localStorage.setItem(KEY, '1');
  } catch {
    /* nothing to do — the flag is cosmetic */
  }
}

/** Testing / debugging escape hatch: `?onboarding=1` always shows the tour. */
export function forceOnboarding(): boolean {
  try {
    return new URLSearchParams(window.location.search).get('onboarding') === '1';
  } catch {
    return false;
  }
}
