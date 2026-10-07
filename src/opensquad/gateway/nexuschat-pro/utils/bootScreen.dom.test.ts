// @vitest-environment jsdom
/**
 * Plugin-contributed boot screen (缝 B) — the loader, actually executed.
 *
 * `bootScreen.scan.test.ts` only proves the source *contains* the right calls.
 * This file runs the real script out of `index.html` in a DOM and answers the
 * questions that matter to a user staring at a startup screen:
 *
 *   1. a declared animation is injected as a <video> **on `document.body`** —
 *      not inside `#root`, which React replaces on mount (a #root-hosted
 *      overlay would vanish before it is ever seen, and the desktop app would
 *      show nothing at all);
 *   2. it always cleans itself up — on `ended` once the app is ready, on click, on
 *      a whole lap of load failures, and on `holdMs`;
 *   3. the three fallbacks (no declaration / request failure / reduced motion)
 *      produce no overlay and never block startup;
 *   4. it plays the *playlist*: while the app is still booting, an `ended` clip is
 *      followed by the next one (wrapping around), so the startup screen keeps
 *      moving until React is up.
 */
import fs from 'node:fs';
import path from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const SHELL = fs.readFileSync(path.resolve(__dirname, '..', 'index.html'), 'utf8');

/** The loader is the last classic `<script>` before the module entry. */
function loaderSource(): string {
  const head = SHELL.slice(0, SHELL.indexOf('<script type="module"'));
  const start = head.lastIndexOf('<script>');
  const end = head.lastIndexOf('</script>');
  if (start < 0 || end < start) throw new Error('the boot-screen loader is missing from index.html');
  return head.slice(start + '<script>'.length, end);
}

const CLIPS = [
  '/api/ai-web/boot-screen/asset?kind=video&index=0&v=1',
  '/api/ai-web/boot-screen/asset?kind=video&index=1&v=2',
];
const CONFIG = {
  enabled: true,
  source: 'theme',
  plugin: 'theme',
  videos: CLIPS,
  poster: '/api/ai-web/boot-screen/asset?kind=poster&v=1',
  holdMs: 0,
};

function stubFetch(payload: unknown) {
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => payload });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

function runLoader(): void {
  // eslint-disable-next-line no-new-func
  new Function(loaderSource())();
}

beforeEach(() => {
  // The default shell: #root still holds the boot loader, i.e. React has not mounted.
  document.body.innerHTML = '<div id="root"><div class="boot-loader-wrap"></div></div>';
  // jsdom logs "Not implemented" for media control; the loader pauses on exit.
  vi.spyOn(window.HTMLMediaElement.prototype, 'pause').mockImplementation(() => {});
});

/** React replacing the boot loader — the signal the loader treats as "app is ready". */
function mountApp(): void {
  document.getElementById('root')!.innerHTML = '<div data-app="ready"></div>';
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('a declared animation', () => {
  it('is injected on body — not inside #root', async () => {
    stubFetch(CONFIG);
    runLoader();

    await vi.waitFor(() => expect(document.querySelector('.boot-screen')).not.toBeNull());
    const box = document.querySelector('.boot-screen') as HTMLElement;
    expect(box.parentElement).toBe(document.body);
    expect(document.getElementById('root')!.contains(box)).toBe(false);

    const video = box.querySelector('video') as HTMLVideoElement;
    expect(video.getAttribute('src')).toBe(CLIPS[0]);
    expect(video.getAttribute('poster')).toBe(CONFIG.poster);
    expect(video.muted).toBe(true); // autoplay policy: must start muted
  });

  it('plays the next clip while the app is still booting', async () => {
    stubFetch(CONFIG);
    runLoader();
    await vi.waitFor(() => expect(document.querySelector('.boot-screen')).not.toBeNull());

    const video = document.querySelector('.boot-screen video') as HTMLVideoElement;
    // No mountApp() here: React has not replaced the default loader, so `ended`
    // must keep the animation going instead of ending it.
    video.dispatchEvent(new Event('ended'));

    expect(video.getAttribute('src')).toBe(CLIPS[1]);
    expect(document.querySelector('.boot-screen')!.classList.contains('is-leaving')).toBe(false);
  });

  it('wraps back to the first clip after the last one', async () => {
    stubFetch(CONFIG);
    runLoader();
    await vi.waitFor(() => expect(document.querySelector('.boot-screen')).not.toBeNull());

    const video = document.querySelector('.boot-screen video') as HTMLVideoElement;
    video.dispatchEvent(new Event('ended'));
    video.dispatchEvent(new Event('ended'));

    expect(video.getAttribute('src')).toBe(CLIPS[0]);
  });

  it('gives up only after every clip has failed once', async () => {
    stubFetch(CONFIG);
    runLoader();
    await vi.waitFor(() => expect(document.querySelector('.boot-screen')).not.toBeNull());

    const video = document.querySelector('.boot-screen video') as HTMLVideoElement;
    video.dispatchEvent(new Event('error'));
    // One dead clip rotates past; it does not end the animation.
    expect(document.querySelector('.boot-screen')).not.toBeNull();
    expect(video.getAttribute('src')).toBe(CLIPS[1]);

    video.dispatchEvent(new Event('error'));
    await vi.waitFor(() => expect(document.querySelector('.boot-screen')).toBeNull(), { timeout: 2000 });
  });

  it('leaves on `ended` once the app is ready', async () => {
    stubFetch(CONFIG);
    runLoader();
    await vi.waitFor(() => expect(document.querySelector('.boot-screen')).not.toBeNull());

    mountApp();
    // `ready` arrives through a MutationObserver (async) — the hint appearing is
    // how the loader signals it. Dispatching `ended` before that just rotates.
    await vi.waitFor(() => {
      const hint = document.querySelector('.boot-screen-hint') as HTMLElement;
      expect(hint.classList.contains('is-shown')).toBe(true);
    });

    const box = document.querySelector('.boot-screen') as HTMLElement;
    box.querySelector('video')!.dispatchEvent(new Event('ended'));

    expect(box.classList.contains('is-leaving')).toBe(true);
    await vi.waitFor(() => expect(document.querySelector('.boot-screen')).toBeNull(), { timeout: 2000 });
  });

  it('leaves when the user clicks it', async () => {
    stubFetch(CONFIG);
    runLoader();
    await vi.waitFor(() => expect(document.querySelector('.boot-screen')).not.toBeNull());

    const box = document.querySelector('.boot-screen') as HTMLElement;
    box.dispatchEvent(new MouseEvent('click', { bubbles: true }));

    await vi.waitFor(() => expect(document.querySelector('.boot-screen')).toBeNull(), { timeout: 2000 });
  });

  it('leaves when holdMs elapses', async () => {
    stubFetch({ ...CONFIG, holdMs: 30 });
    runLoader();
    await vi.waitFor(() => expect(document.querySelector('.boot-screen')).not.toBeNull());

    await vi.waitFor(() => expect(document.querySelector('.boot-screen')).toBeNull(), { timeout: 2000 });
  });
});

describe('the skip affordance', () => {
  it('stays hidden until the app is ready, then invites a click', async () => {
    stubFetch(CONFIG);
    runLoader();
    await vi.waitFor(() => expect(document.querySelector('.boot-screen')).not.toBeNull());

    const hint = document.querySelector('.boot-screen-hint') as HTMLElement;
    expect(hint).not.toBeNull();
    expect(hint.classList.contains('is-shown'), 'no "you can leave" hint while the app is still booting').toBe(false);

    mountApp();
    await vi.waitFor(() => expect(hint.classList.contains('is-shown')).toBe(true));
    expect(hint.textContent).toMatch(/跳过/);
  });

  it('does not cut the clip short when the app becomes ready', async () => {
    // The regression a user hit: the animation ended the moment the UI appeared.
    stubFetch(CONFIG);
    runLoader();
    await vi.waitFor(() => expect(document.querySelector('.boot-screen')).not.toBeNull());

    mountApp();
    await new Promise((r) => setTimeout(r, 30));

    const box = document.querySelector('.boot-screen');
    expect(box, 'the overlay must outlive the app mounting — it plays to the end').not.toBeNull();
    expect(box!.classList.contains('is-leaving')).toBe(false);
  });

  it('leaves as soon as it is clicked', async () => {
    stubFetch(CONFIG);
    runLoader();
    await vi.waitFor(() => expect(document.querySelector('.boot-screen')).not.toBeNull());
    mountApp();

    document.querySelector('.boot-screen')!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    await vi.waitFor(() => expect(document.querySelector('.boot-screen')).toBeNull(), { timeout: 2000 });
  });
});

describe('the fallbacks', () => {
  it('does nothing when no plugin declares one', async () => {
    stubFetch({ enabled: false });
    runLoader();
    await new Promise((r) => setTimeout(r, 20));
    expect(document.querySelector('.boot-screen')).toBeNull();
  });

  it('does nothing when the request fails', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')));
    runLoader();
    await new Promise((r) => setTimeout(r, 20));
    expect(document.querySelector('.boot-screen')).toBeNull();
  });

  it('does not even ask when the user prefers reduced motion', () => {
    vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: true })));
    const fetchMock = stubFetch(CONFIG);
    runLoader();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
