// @vitest-environment jsdom
/**
 * The agent's visualization must sit on the conversation surface, not on a slab
 * of its own colour.
 *
 * Reported symptom (screenshot): the embedded form page ships
 * `body{background:#f0f4f8}` of its own, so the chat showed a grey panel with a
 * white card inside it. The host cannot restyle the page's content (it is the
 * agent's HTML in a sandboxed iframe) — it must decide, inside the page, whether
 * the *canvas* can be dropped.
 *
 * The decision has to be conservative in both directions: a page whose canvas is
 * dark on a light chat (or light on a dark chat) keeps its background, because
 * that canvas is what its text was designed against. Verified with the real page
 * from the reported session (`form_demo.html`, `body{background:#f0f4f8}`).
 *
 * Mutations verified (script `C:/tmp/prov/mutate_embed_canvas.py`,
 * report `C:/tmp/prov/mutation_report_embed_canvas.json`):
 *   ME1 blend ignores the host appearance (always blends)   → R3/R4
 *   ME2 dark canvases are blended too                       → R4
 *   ME3 a designed (image) canvas is blended                → R6
 *   ME4 the live appearance message type drifts             → R8/R9
 *   ME5 the host never posts the appearance change          → R9
 */
import fs from 'node:fs';
import path from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';

import { blendCanvasScript } from './HtmlEmbedBlock';

const ROOT = path.resolve(__dirname, '..', '..');
const read = (rel: string) => fs.readFileSync(path.join(ROOT, rel), 'utf8').replace(/\r\n/g, '\n');
const EMBED = read('components/ai-chat/HtmlEmbedBlock.tsx');

const BLEND_STYLE = 'style[data-opensquad-blend="1"]';

/** Run the injected script against a page whose canvas is as described. */
function runScript(opts: {
  hostDark: boolean;
  bodyBg?: string;
  bodyImage?: string;
  rootBg?: string;
}): { blended: () => boolean; send: (data: unknown) => void } {
  document.head.innerHTML = '';
  document.body.removeAttribute('style');
  document.documentElement.removeAttribute('style');
  // Longhands, not the `background` shorthand: jsdom drops a background-image
  // assigned on top of the shorthand, so the designed-canvas case never armed.
  if (opts.bodyBg) document.body.style.backgroundColor = opts.bodyBg;
  if (opts.bodyImage) document.body.style.backgroundImage = opts.bodyImage;
  if (opts.rootBg) document.documentElement.style.backgroundColor = opts.rootBg;

  const script = blendCanvasScript(opts.hostDark);
  const code = script.slice(script.indexOf('>') + 1, script.lastIndexOf('</script>'));
  // eslint-disable-next-line no-new-func
  new Function(code)();
  return {
    blended: () => !!document.head.querySelector(BLEND_STYLE),
    send: (data: unknown) => {
      window.dispatchEvent(new MessageEvent('message', { data }));
    },
  };
}

afterEach(() => {
  document.head.innerHTML = '';
  document.body.removeAttribute('style');
  document.documentElement.removeAttribute('style');
});

describe('可视图层的画布背景 —— 融进会话背景，但别把文字弄瞎', () => {
  it('R1 — the reported page (light canvas, light chat) loses its grey slab', () => {
    // `form_demo.html` from the session: body { background: #f0f4f8 }
    const page = runScript({ hostDark: false, bodyBg: '#f0f4f8' });
    expect(page.blended()).toBe(true);
  });

  it('R2 — a page with no canvas of its own is left as it is (nothing to drop)', () => {
    const page = runScript({ hostDark: false });
    // No background at all still counts as "light canvas" -> the style may be
    // attached, but it overrides nothing.
    expect(page.blended()).toBe(true);
  });

  it('R3 — a light canvas on a dark chat keeps its background (text was built for it)', () => {
    const page = runScript({ hostDark: true, bodyBg: '#f0f4f8' });
    expect(page.blended()).toBe(false);
  });

  it('R4 — a dark canvas on a light chat keeps its background too', () => {
    const page = runScript({ hostDark: false, bodyBg: '#101418' });
    expect(page.blended()).toBe(false);
  });

  it('R5 — a dark canvas on a dark chat blends', () => {
    const page = runScript({ hostDark: true, bodyBg: '#101418' });
    expect(page.blended()).toBe(true);
  });

  it('R6 — a designed canvas (image/gradient) is never wiped', () => {
    const page = runScript({
      hostDark: false,
      bodyBg: '#f0f4f8',
      bodyImage: 'linear-gradient(#fff, #eef)',
    });
    expect(page.blended()).toBe(false);
  });

  it('R7 — the html element is consulted when the body has no colour', () => {
    expect(runScript({ hostDark: false, rootBg: '#101418' }).blended()).toBe(false);
    expect(runScript({ hostDark: false, rootBg: '#f7f8fa' }).blended()).toBe(true);
    // A fully transparent canvas counts as "light" (there is nothing to clash).
    expect(runScript({ hostDark: false, bodyBg: 'rgba(0, 0, 0, 0)' }).blended()).toBe(true);
  });

  it('R8 — a live theme switch re-decides without a reload', () => {
    const page = runScript({ hostDark: false, bodyBg: '#f0f4f8' });
    expect(page.blended()).toBe(true);
    page.send({ type: 'os_host_appearance', dark: true });
    expect(page.blended()).toBe(false);
    page.send({ type: 'os_host_appearance', dark: false });
    expect(page.blended()).toBe(true);
    // Unrelated messages must not touch the decision.
    page.send({ type: 'os_form_submit', payload: {} });
    page.send('os_host_appearance');
    expect(page.blended()).toBe(true);
  });

  it('R9 — both ends of the theme message agree on its type', () => {
    // The page listens for the string the host posts; a drift here means a theme
    // switch silently stops reaching the embedded page.
    const listened = /data\.type !== '([a-z_]+)'/.exec(EMBED)?.[1];
    const posted = /contentWindow\?\.postMessage\(\{ type: '([a-z_]+)'/.exec(EMBED)?.[1];
    expect(listened).toBe('os_host_appearance');
    expect(posted).toBe('os_host_appearance');
  });

  it('R10 — the seamless embed injects the blend script, the chrome one does not', () => {
    expect(EMBED).toContain('blendCanvasScript(hostDarkRef.current)');
    // The srcDoc snapshot must not depend on the live value, or a theme switch
    // would reload the iframe and lose in-page form state.
    expect(EMBED).toMatch(/\}, \[payload\.html, seamless\]\);/);
    expect(EMBED).not.toContain('}, [payload.html, seamless, hostDark]);');
  });
});
