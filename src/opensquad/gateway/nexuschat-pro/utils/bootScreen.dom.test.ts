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
 *      moving until the app is up;
 *   5. "up" is the app leaving its gate (`<html data-opensquad-ready>`) **and** no
 *      longer rendering a four-quadrant loader of its own — NOT React mounting.
 *      After mounting the app shows the *same* loader while it fetches its config and
 *      then its sessions, which on a cold start is seconds; either signal alone hands
 *      those seconds back to the built-in loader.
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
  // The default shell: React has not mounted, and the app has not left its gate.
  document.body.innerHTML = '<div id="root"></div>';
  document.documentElement.removeAttribute('data-opensquad-ready');
  localStorage.removeItem('opensquad.bootScreen');
  // jsdom logs "Not implemented" for media control; the loader pauses on exit.
  vi.spyOn(window.HTMLMediaElement.prototype, 'pause').mockImplementation(() => {});
});

/** The app leaving its startup gate — the signal the loader treats as "ready". */
function mountApp(): void {
  document.documentElement.setAttribute('data-opensquad-ready', '1');
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
    // Sound is requested up front (the clips carry a BGM); a refused autoplay is what
    // mutes it — covered by 'asks for sound first and falls back to muted'.
    expect(video.muted).toBe(false);
  });

  it('plays the next clip while the app is still booting', async () => {
    stubFetch(CONFIG);
    runLoader();
    await vi.waitFor(() => expect(document.querySelector('.boot-screen')).not.toBeNull());

    const video = document.querySelector('.boot-screen video') as HTMLVideoElement;
    // No mountApp() here: the app has not left its gate, so `ended` must keep the
    // animation going instead of ending it.
    video.dispatchEvent(new Event('ended'));

    expect(video.getAttribute('src')).toBe(CLIPS[1]);
    expect(document.querySelector('.boot-screen')!.classList.contains('is-leaving')).toBe(false);
  });

  it('keeps rotating when the app is mounted but still inside its gate', async () => {
    // The reported bug: React mounts in well under a second, then the app renders the
    // same four-quadrant loader for seconds while it fetches its config. Treating
    // "mounted" as ready handed the screen over to exactly that loader.
    stubFetch(CONFIG);
    runLoader();
    await vi.waitFor(() => expect(document.querySelector('.boot-screen')).not.toBeNull());

    document.getElementById('root')!.innerHTML = '<div data-app="mounted"></div>';
    const video = document.querySelector('.boot-screen video') as HTMLVideoElement;
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

  it('does not leave while the app still shows a quad loader of its own', async () => {
    // The reported tail: past the gate the app keeps rendering the same four-quadrant
    // loader for seconds (sessions/workspace loading). Keying on the gate alone handed
    // those seconds back to the built-in loader.
    stubFetch(CONFIG);
    runLoader();
    await vi.waitFor(() => expect(document.querySelector('.boot-screen')).not.toBeNull());

    mountApp();
    // The app is past its gate but still loading: an OpenSquadLoader is on screen.
    document.getElementById('root')!.innerHTML = '<svg role="status" aria-label="加载中" width="72"></svg>';
    await new Promise((r) => setTimeout(r, 30));

    const hint = document.querySelector('.boot-screen-hint') as HTMLElement;
    expect(hint.classList.contains('is-shown'), 'not usable yet — the app is still loading').toBe(false);

    const video = document.querySelector('.boot-screen video') as HTMLVideoElement;
    video.dispatchEvent(new Event('ended'));
    expect(video.getAttribute('src')).toBe(CLIPS[1]);

    // That loader gone + the gate closed → the hint shows. But the clip ending still
    // must not take the user in on its own: leaving is the user's call.
    document.getElementById('root')!.innerHTML = '<div data-app="ready"></div>';
    await vi.waitFor(() => expect(hint.classList.contains('is-shown')).toBe(true));
    video.dispatchEvent(new Event('ended'));
    await new Promise((r) => setTimeout(r, 30));
    expect(document.querySelector('.boot-screen'), 'the user leaves, not the clip').not.toBeNull();

    document.querySelector('.boot-screen')!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    await vi.waitFor(() => expect(document.querySelector('.boot-screen')).toBeNull(), { timeout: 2000 });
  });

  it('stays put on `ended` once the app is ready — the user leaves, not the clip', async () => {
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
    const video = box.querySelector('video') as HTMLVideoElement;
    video.dispatchEvent(new Event('ended'));

    expect(box.classList.contains('is-leaving')).toBe(false);
    expect(video.getAttribute('src')).toBe(CLIPS[1]);

    box.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    await vi.waitFor(() => expect(document.querySelector('.boot-screen')).toBeNull(), { timeout: 2000 });
  });

  it('ignores a click while the app is still loading', async () => {
    // Clicking before the app is usable would drop the user onto the app's own
    // loading screen — the thing the animation exists to cover.
    stubFetch(CONFIG);
    runLoader();
    await vi.waitFor(() => expect(document.querySelector('.boot-screen')).not.toBeNull());

    const box = document.querySelector('.boot-screen') as HTMLElement;
    box.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    await new Promise((r) => setTimeout(r, 30));

    expect(document.querySelector('.boot-screen'), 'still loading — the click must not land').not.toBeNull();
  });

  it('leaves when the user clicks it once the app is ready', async () => {
    stubFetch(CONFIG);
    runLoader();
    await vi.waitFor(() => expect(document.querySelector('.boot-screen')).not.toBeNull());

    mountApp();
    const hint = document.querySelector('.boot-screen-hint') as HTMLElement;
    await vi.waitFor(() => expect(hint.classList.contains('is-shown')).toBe(true));

    document.querySelector('.boot-screen')!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
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

describe('covering the built-in quad loader', () => {
  it('starts from the cached playlist without waiting for the network', () => {
    // "一访问就是自定义动画": the last playlist is cached on this machine, so the
    // overlay is on screen before the request even answers.
    localStorage.setItem('opensquad.bootScreen', JSON.stringify({ videos: CLIPS, poster: '', holdMs: 0 }));
    vi.stubGlobal('fetch', vi.fn(() => new Promise(() => {})));

    runLoader();

    const box = document.querySelector('.boot-screen');
    expect(box, 'the cached clip covers the screen immediately').not.toBeNull();
    expect(box!.querySelector('video')!.getAttribute('src')).toBe(CLIPS[0]);
  });

  it('covers the first paint with the frame captured last time', () => {
    // The video element has no frame for the first second or two (the media request
    // queues behind the app's own boot), which used to show as a blank screen. The
    // captured first frame is set as the poster, so there is something to look at.
    localStorage.setItem('opensquad.bootScreen', JSON.stringify({ videos: CLIPS, poster: '', holdMs: 0 }));
    localStorage.setItem(
      'opensquad.bootScreen:posters',
      JSON.stringify({ map: { [CLIPS[0]]: 'data:image/jpeg;base64,AAAA' }, order: [CLIPS[0]] }),
    );
    vi.stubGlobal('fetch', vi.fn(() => new Promise(() => {})));

    runLoader();

    const video = document.querySelector('.boot-screen video') as HTMLVideoElement;
    expect(video.poster).toBe('data:image/jpeg;base64,AAAA');
  });

  it('asks for sound first and falls back to muted when the browser refuses', async () => {
    // The clips carry a BGM track. An unmuted autoplay is usually blocked on a cold
    // page, so the loader asks for sound first and, only if refused, mutes — the
    // animation must appear either way.
    stubFetch(CONFIG);
    const play = vi.spyOn(window.HTMLMediaElement.prototype, 'play')
      .mockImplementationOnce(() => Promise.reject(Object.assign(new Error('blocked'), { name: 'NotAllowedError' })))
      .mockResolvedValue(undefined as unknown as void);

    runLoader();
    const video = await vi.waitFor(() => {
      const v = document.querySelector('.boot-screen video') as HTMLVideoElement | null;
      expect(v, 'the overlay is up regardless of the autoplay policy').not.toBeNull();
      return v!;
    });

    expect(play.mock.calls.length, 'it retried after muting').toBeGreaterThan(1);
    expect(video.muted, 'refused sound → muted fallback').toBe(true);
  });

  it('stops the media for good when it leaves — no BGM left behind', async () => {
    // A paused-but-detached <video> keeps playing on its own, and our AbortError retry
    // fires 150ms later — either one leaves the BGM running over the app.
    stubFetch(CONFIG);
    runLoader();
    await vi.waitFor(() => expect(document.querySelector('.boot-screen')).not.toBeNull());

    mountApp();
    const video = document.querySelector('.boot-screen video') as HTMLVideoElement;
    await vi.waitFor(() => {
      const h = document.querySelector('.boot-screen-hint') as HTMLElement;
      expect(h.classList.contains('is-shown')).toBe(true);
    });
    document.querySelector('.boot-screen')!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    await vi.waitFor(() => expect(document.querySelector('.boot-screen')).toBeNull(), { timeout: 2000 });

    expect(video.paused, 'paused on the way out').toBe(true);
    expect(video.getAttribute('src'), 'source released, so nothing can resume it').toBeNull();
  });

  it('hides the built-in glyph instead of leaving it on screen', () => {
    // The static quad sits in #root until React replaces it. Hiding it is what makes
    // even the first moments animation-only (the node stays for the readiness check).
    document.body.innerHTML =
      '<div id="root"><div class="boot-loader-wrap"><svg role="status" width="96"></svg></div></div>';
    stubFetch(CONFIG);
    runLoader();

    const css = Array.from(document.head.querySelectorAll('style'))
      .map((s) => s.textContent || '')
      .join('\n');
    expect(css).toContain('#root .boot-loader-wrap > svg');
    expect(css).toContain('visibility:hidden');
  });

  it('takes the hint back if the app returns to a full-screen load', async () => {
    stubFetch(CONFIG);
    runLoader();
    await vi.waitFor(() => expect(document.querySelector('.boot-screen')).not.toBeNull());

    mountApp();
    const hint = document.querySelector('.boot-screen-hint') as HTMLElement;
    await vi.waitFor(() => expect(hint.classList.contains('is-shown')).toBe(true));

    // The gate re-opens: a full-screen quad is back, so "点击跳过" would be a lie and
    // the animation must not hand over.
    document.getElementById('root')!.innerHTML = '<svg role="status" aria-label="加载中" width="96"></svg>';
    await vi.waitFor(() => expect(hint.classList.contains('is-shown')).toBe(false));

    const video = document.querySelector('.boot-screen video') as HTMLVideoElement;
    video.dispatchEvent(new Event('ended'));
    expect(video.getAttribute('src'), 'still not usable → keep rotating').toBe(CLIPS[1]);
  });
});
