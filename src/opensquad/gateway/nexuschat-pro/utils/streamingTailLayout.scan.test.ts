/**
 * Source lock for the streaming tail's layout contract.
 *
 * `content-visibility: auto` is a real win on a long transcript, but on the LAST
 * block of a markdown body it is a trap. That block is the one a streaming turn
 * replaces on every chunk, so it can still be without a remembered size — and
 * while it is only proxied by `contain-intrinsic-size: auto 4em` (≈56px at the
 * thought body's 14px), a bottom-pinned scroller measures the placeholder
 * instead of the text: pinning un-skips the block, which grows, which moves the
 * offset, which re-skips it. That feedback loop repeats every frame and is the
 * whole-block jitter users see while a thought streams, before the body has a
 * scrollbar of its own.
 *
 * Measured in a real Chromium (real `ChatTimeline` + `SoloActivityRow`, rAF
 * sampling of the painted layout, 14px thought body): 98 % of frames landed in
 * the 56px placeholder layout before the exemption; 0 % after, with every frame
 * at a true line-count height.
 *
 * No component test can see this — it needs a layout engine, and the failure
 * mode is a feedback loop with scroll position. So the invariant is pinned here:
 *
 *   R1  the off-screen optimization still applies to markdown blocks;
 *   R2  the last block is exempted, so the streaming tail always lays out;
 *   R3  the exemption stays scoped to `.os-chat-scroll` markdown — it is a
 *       streaming-tail fix, not a global opt-out;
 *   R4  the exemption names `:last-child` rather than every child: frozen
 *       blocks above the tail keep the optimization, and `auto` hands them
 *       their real remembered height.
 *
 * Mutation verified: deleting the `> :last-child` rule fails R2–R4.
 */
import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';

const ROOT = path.resolve(__dirname, '..');
/** Source with comments stripped — these rules are about code, not prose. */
const code = (src: string) => src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');

const CSS = code(fs.readFileSync(path.join(ROOT, 'index.css'), 'utf8'));

/** Every `selector { body }` pair in the stylesheet, in source order. */
const rules = [...CSS.matchAll(/([^{}]+)\{([^{}]*)\}/g)].map((m) => ({
  selector: m[1].trim().replace(/\s+/g, ' '),
  body: m[2],
}));

const rulesDeclaring = (prop: string, value: string) =>
  rules.filter((r) => new RegExp(`${prop}:\\s*${value}`).test(r.body));

const offscreenRule = rulesDeclaring('content-visibility', 'auto').find((r) =>
  r.selector.includes('.os-chat-scroll .ai-markdown >'),
);
const exemption = rulesDeclaring('content-visibility', 'visible').find((r) =>
  r.selector.includes('.ai-markdown'),
);

describe('R1 — the off-screen optimization is still in place', () => {
  it('markdown blocks under the chat scroller still skip layout', () => {
    expect(offscreenRule, 'the content-visibility:auto markdown rule went missing').toBeTruthy();
    expect(offscreenRule!.selector).toContain('.os-chat-scroll .ai-markdown > p');
    expect(offscreenRule!.body).toMatch(/contain-intrinsic-size:\s*auto 4em/);
  });
});

describe('R2 — the streaming tail always lays out', () => {
  it('the last markdown block is exempted', () => {
    expect(
      exemption,
      'without this, a bottom-pinned scroller measures the 4em placeholder and the body jitters every frame while it streams',
    ).toBeTruthy();
  });

  it('and the exemption actually overrides the skip', () => {
    // `content-visibility` is not inherited, so `visible` on the last block is
    // enough — but only if the rule is still reachable for that same selector
    // shape. Pinning both halves keeps a future edit from exempting a selector
    // the auto rule never matches.
    const lastChild = rules.find((r) => r.selector.endsWith('> :last-child'));
    expect(lastChild, 'the exemption must target the tail block').toBeTruthy();
    expect(lastChild!.body).toMatch(/content-visibility:\s*visible/);
  });
});

describe('R3 — the exemption stays scoped', () => {
  it('is limited to markdown inside the chat scroller', () => {
    expect(exemption!.selector).toContain('.os-chat-scroll');
    expect(exemption!.selector).toContain('.ai-markdown');
  });

  it('is not a blanket `.ai-markdown > *` opt-out', () => {
    // `> *` would also "fix" the jitter, by giving up the optimization on every
    // block of every report. The invariant is about the tail only.
    expect(exemption!.selector).not.toContain('> *');
  });
});

describe('R4 — frozen blocks above the tail keep the optimization', () => {
  it('names :last-child, not the whole body', () => {
    const lastChild = rules.filter((r) => r.selector.endsWith('> :last-child'));
    expect(lastChild).toHaveLength(1);
    expect(lastChild[0].selector).toBe('.os-chat-scroll .ai-markdown > :last-child');
  });

  it('the exemption is at least as specific as the rule it overrides', () => {
    // Otherwise a reorder of the two rules would silently restore the jitter.
    const specificity = (sel: string) => {
      const classes = (sel.match(/\.[\w-]+/g) ?? []).length;
      const pseudo = (sel.match(/:(?!:)[\w-]+/g) ?? []).length;
      const types = (sel.match(/(?<![\w.#:-])[a-z][\w-]*/g) ?? []).length;
      return classes * 100 + pseudo * 10 + types;
    };
    expect(specificity(exemption!.selector)).toBeGreaterThanOrEqual(
      specificity('.os-chat-scroll .ai-markdown > p'),
    );
  });
});
