/**
 * The thought tail is a *spatial* gradient, so most of it is not reachable by
 * a component test: the only place the fade actually exists is `index.css`.
 * This file pins the parts a behavioural test cannot see — that the ramp is
 * smooth and readable, that it has exactly one home, and that the live gate is
 * still wired to the same flag as auto-follow.
 *
 * Rules:
 *   R1  `.os-thought-tail` is declared exactly once, with both the prefixed and
 *       unprefixed `mask-image` (the prefix is what Electron/Chromium-era
 *       webviews need; dropping either silently kills the effect on one engine);
 *   R2  the ramp is a real gradient, not a hard cut: top→bottom alpha is
 *       monotonically non-increasing, starts fully opaque, and stops at 45 % —
 *       never 0 %, so text scrolled back to read is dimmed, not erased;
 *   R3  the tool-flow thought body turns the tail on with the *same* condition
 *       as auto-follow (`!!line.running`), so a finished body can never keep a
 *       half-faded tail;
 *   R4  the mask is dropped in reading mode: `MarkdownScrollBody` gates it on
 *       `stuck`, and `FollowScrollBox` publishes stick changes on edges only
 *       (a notification per scroll frame would re-render the streamer);
 *   R5  the mount fade is neutralised under `prefers-reduced-motion`.
 *
 * Mutations verified — `C:/tmp/prov/mutate_thoughttail.py`:
 *   MB1 flatten the ramp to two stops            → R2
 *   MB2 last stop 0.45 → 0 (erases older text)   → R2
 *   MB3 drop the `-webkit-mask-image` copy       → R1
 *   MB4 move the declaration into the component  → R1
 *   MB5 drop the reduced-motion guard            → R5
 */
import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';

const ROOT = path.resolve(__dirname, '..');
const read = (rel: string) => fs.readFileSync(path.join(ROOT, rel), 'utf8');

/** Source with comments stripped — these rules are about code, not prose. */
const code = (src: string) => src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');

const CSS = read('index.css');
const SOLO = read('components/ai-chat/SoloActivityRow.tsx');
const BODY = read('components/ai-chat/MarkdownScrollBody.tsx');
const SCROLL = read('components/ai-chat/FollowScrollBox.tsx');

/** The one `.os-thought-tail { … }` rule body. */
const tailBlock = (() => {
  const m = CSS.match(/\.os-thought-tail\s*\{([\s\S]*?)\n\}/);
  return m ? m[1] : '';
})();

/** `[alpha, percent]` stops of the unprefixed `mask-image` ramp, in order. */
const stops = (() => {
  const m = tailBlock.match(/(?<!-webkit-)mask-image:\s*linear-gradient\(([\s\S]*?)\);/);
  if (!m) return [];
  return [...m[1].matchAll(/rgba\(\s*0\s*,\s*0\s*,\s*0\s*,\s*([\d.]+)\s*\)\s*([\d.]+)%/g)].map(
    (s) => [Number(s[1]), Number(s[2])] as const,
  );
})();

describe('R1 — the tail has exactly one home', () => {
  it('is declared once in index.css', () => {
    const decls = CSS.match(/^\.os-thought-tail\s*\{/gm) ?? [];
    expect(decls).toHaveLength(1);
  });

  it('is not re-declared by a component', () => {
    for (const [name, src] of [['MarkdownScrollBody', BODY], ['SoloActivityRow', SOLO]] as const) {
      expect(code(src), `${name} must not restyle the mask`).not.toMatch(
        /mask-image:\s*linear-gradient/,
      );
    }
  });

  it('carries both the prefixed and unprefixed property', () => {
    expect(tailBlock).toMatch(/-webkit-mask-image:\s*linear-gradient\(/);
    expect(tailBlock).toMatch(/(?<!-webkit-)mask-image:\s*linear-gradient\(/);
  });

  it('is a vertical ramp', () => {
    expect(tailBlock).toMatch(/linear-gradient\(\s*to bottom/);
  });

  it('every tail class the component applies is declared in the stylesheet', () => {
    // The silent failure this catches is the same one as a dead window event:
    // a class name that drifts on one side only. Nothing throws — the element
    // simply carries a selector that styles nothing, and the feature looks
    // "not implemented". Renaming either side must break this.
    const applied = new Set(
      [...code(BODY).matchAll(/'(os-thought-[a-z-]+)'/g)].map((m) => m[1]),
    );
    expect(applied.size).toBeGreaterThan(0);
    const undeclared = [...applied].filter(
      (cls) => !new RegExp(`^\\.${cls}\\s*\\{`, 'm').test(CSS),
    );
    expect(
      undeclared,
      'a class applied by the component with no rule in index.css renders nothing',
    ).toEqual([]);
  });
});

describe('R2 — the ramp is smooth and readable', () => {
  it('has enough stops to read as a gradient', () => {
    expect(stops.length, 'a two-stop ramp is a hard cut, not a fade').toBeGreaterThanOrEqual(4);
  });

  it('fades monotonically from the top to the newest line', () => {
    const alphas = stops.map(([a]) => a);
    expect(alphas[0]).toBe(1);
    for (let i = 1; i < alphas.length; i += 1) {
      expect(alphas[i], `stop ${i} must not be more opaque than ${i - 1}`).toBeLessThanOrEqual(
        alphas[i - 1],
      );
    }
    expect(alphas[alphas.length - 1]).toBeLessThan(1);
  });

  it('the newest line dissolves into the mist — history stays solid above', () => {
    // Design update (user-requested "fleeting thought" effect): the newest
    // streaming line now fades to (near) 0 so text emerges from the bottom
    // mist instead of merely dimming. History readability is still guaranteed
    // by the solid region covering the box down past the halfway mark —
    // asserted by "keeps the fade on the newest lines" (≥50%).
    const alphas = stops.map(([a]) => a);
    expect(
      alphas[alphas.length - 1],
      'the tail must actually dissolve the newest line (near-0), not just dim it',
    ).toBeLessThanOrEqual(0.2);
    // …and the dissolve must be gradual, never a hard cut from solid to 0.
    const lastSolidIdx = alphas.lastIndexOf(1);
    const partial = alphas.slice(lastSolidIdx + 1, -1).filter((a) => a > 0 && a < 1);
    expect(
      partial.length,
      'a hard cut from solid to transparent reads as flicker, not mist',
    ).toBeGreaterThanOrEqual(2);
  });

  it('keeps the fade on the newest lines, not on the whole box', () => {
    // The ask was "the newest few lines", not "everything above the fold is
    // half-faded": the body has to stay solid down past the halfway mark.
    const solid = stops.filter(([a]) => a === 1).map(([, p]) => p);
    expect(Math.max(...solid), 'the ramp starts too high up the box').toBeGreaterThanOrEqual(50);
  });

  it('walks the percentages strictly downwards', () => {
    const pcts = stops.map(([, p]) => p);
    for (let i = 1; i < pcts.length; i += 1) {
      expect(pcts[i]).toBeGreaterThan(pcts[i - 1]);
    }
    expect(pcts[pcts.length - 1]).toBe(100);
  });
});

describe('R3 — the tool-flow thought body is gated on the live flag', () => {
  it('turns the tail on with the same condition as auto-follow', () => {
    expect(code(SOLO)).toMatch(
      /follow=\{!!line\.running\}[\s\S]{0,160}softEdge=\{!!line\.running\}/,
    );
  });

  it('the plan-only live branch gates it on the active thought too', () => {
    expect(code(SOLO)).toMatch(
      /follow=\{thinkingActive && i === thoughtBodies\.length - 1\}[\s\S]{0,160}softEdge=\{thinkingActive && i === thoughtBodies\.length - 1\}/,
    );
  });

  it('the completed thought-only document does not use it', () => {
    // That branch renders `isThoughtOnly && !isLiveTurn` — a finished document
    // whose whole job is to be read, so it must stay at full weight.
    const docBranch = code(SOLO).split('if (isThoughtOnly && !isLiveTurn)')[1]?.split('if (isCompressionOnly)')[0] ?? '';
    expect(docBranch).toContain('MarkdownScrollBody');
    expect(docBranch).not.toContain('softEdge');
  });
});

describe('R4 — reading mode is crisp', () => {
  it('the mask is applied only while pinned to the bottom', () => {
    expect(code(BODY)).toMatch(/const tail = softEdge && stuck;/);
    expect(code(BODY)).toMatch(/tail \? 'os-thought-tail' : ''/);
  });

  it('and the stick signal is subscribed only when the tail is wanted', () => {
    expect(code(BODY)).toMatch(/onStickChange=\{softEdge \? setStuck : undefined\}/);
  });

  it('FollowScrollBox notifies on edges, not on every scroll frame', () => {
    expect(code(SCROLL)).toMatch(/onStickChange\?: \(stuck: boolean\) => void/);
    expect(code(SCROLL)).toMatch(/if \(stickRef\.current === next\) return;/);
    expect(code(SCROLL)).toMatch(/notifyRef\.current\?\.\(next\)/);
  });

  it('a new live body re-arms at the bottom', () => {
    expect(code(BODY)).toMatch(/if \(softEdge\) setStuck\(true\);/);
  });
});

describe('R5 — reduced motion', () => {
  it('neutralises the mount fade', () => {
    expect(CSS).toMatch(
      /@media \(prefers-reduced-motion: reduce\) \{\s*\.os-thought-settle \{\s*animation: none;/,
    );
  });
});
