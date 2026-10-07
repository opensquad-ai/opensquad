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
 *   2. it always cleans itself up — on `ended`, on click, and on `holdMs`;
 *   3. the three fallbacks (no declaration / request failure / reduced motion)
 *      produce no overlay and never block startup.
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

const CONFIG = {
  enabled: true,
  source: 'theme',
  plugin: 'theme',
  video: '/api/plugins/static/theme/assets/boot.mp4',
  poster: '/api/plugins/static/theme/assets/boot.jpg',
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
  document.body.innerHTML = '<div id="root"></div>';
  // jsdom logs "Not implemented" for media control; the loader pauses on exit.
  vi.spyOn(window.HTMLMediaElement.prototype, 'pause').mockImplementation(() => {});
});

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
    expect(video.getAttribute('src')).toBe(CONFIG.video);
    expect(video.getAttribute('poster')).toBe(CONFIG.poster);
    expect(video.muted).toBe(true); // autoplay policy: must start muted
  });

  it('leaves on `ended`', async () => {
    stubFetch(CONFIG);
    runLoader();
    await vi.waitFor(() => expect(document.querySelector('.boot-screen')).not.toBeNull());

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
