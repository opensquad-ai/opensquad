import { beforeEach, describe, expect, it } from 'vitest';
import {
  beginDesktopUpdate,
  getDesktopUpdateOverlayState,
  markDesktopUpdateDownloaded,
  offerDesktopUpdate,
  resetDesktopUpdateOverlay,
  setDesktopUpdateProgress,
} from './desktopUpdateOverlay';

const OFFER = {
  version: '1.2.3',
  downloadUrl: 'https://github.com/opensquad-ai/opensquad/releases/download/v1.2.3/x.exe',
  fileName: 'x.exe',
};

beforeEach(() => resetDesktopUpdateOverlay());

describe('offerDesktopUpdate', () => {
  it('offers the version and opens the changelog when idle', () => {
    offerDesktopUpdate(OFFER);
    const s = getDesktopUpdateOverlayState();
    expect(s.phase).toBe('available');
    expect(s.version).toBe('1.2.3');
    expect(s.changelogOpen).toBe(true);
  });

  it('does not reset an in-flight background download for the same version', () => {
    offerDesktopUpdate(OFFER);
    beginDesktopUpdate('1.2.3');
    setDesktopUpdateProgress({ percent: 42, transferred: 42, total: 100 });

    // A later update-available for the same release must not downgrade to
    // 'available' and drop the progress.
    offerDesktopUpdate(OFFER);

    const s = getDesktopUpdateOverlayState();
    expect(s.phase).toBe('downloading');
    expect(s.progress.percent).toBe(42);
  });

  it('does not downgrade a finished download for the same version', () => {
    offerDesktopUpdate(OFFER);
    beginDesktopUpdate('1.2.3');
    markDesktopUpdateDownloaded();

    offerDesktopUpdate(OFFER);

    expect(getDesktopUpdateOverlayState().phase).toBe('downloaded');
  });

  it('offers a different version normally', () => {
    offerDesktopUpdate(OFFER);
    beginDesktopUpdate('1.2.3');
    markDesktopUpdateDownloaded();

    offerDesktopUpdate({ ...OFFER, version: '1.2.4' });

    const s = getDesktopUpdateOverlayState();
    expect(s.phase).toBe('available');
    expect(s.version).toBe('1.2.4');
  });
});
