// @vitest-environment node
/**
 * Static guard: the AgentWebComposer toolbar has to stay usable on a phone.
 *
 * WHY THIS EXISTS
 * ---------------
 * The composer toolbar is a single non-wrapping flex row whose two ends are
 * `shrink-0`, so the row keeps its natural width (~404px) however narrow the
 * viewport gets; only the empty spacer in the middle can absorb anything. The
 * row is then clipped by the composer card (`.os-composer-input-layer`,
 * measured 286–396px) and the clip is not scrollable
 * (`documentElement.scrollWidth === innerWidth`), so the overflow is simply
 * unreachable.
 *
 * Reported 2026-09-19 as "手机访问 opensquad 无法发送 agent 会话消息". Measured in
 * a real Chromium at 393px, the Send button's box was [389, 630, 32, 32] — right
 * edge 421px, i.e. 28px past the viewport, leaving 4px of a 32px button on
 * screen. The row did NOT reflow: Send's coordinates are identical at 430 / 393
 * / 390 / 360 / 320, so only viewports wider than ~421px could send at all.
 * `enterKeyHint` is not set either, so the soft keyboard's Enter only wraps and
 * the Send button really was the one and only way to submit.
 *
 * THE FIX (plan A)
 * ----------------
 * Below `md`: the toolbar is promoted above the textarea, handed the group-chat
 * strip treatment (`bg-bgLight` + bottom hairline + card top radius), Attach and
 * Send stay pinned, and mode / model / effort fold into MobileComposerMenu. The
 * order is done with `order-*` utilities rather than a second markup tree, so
 * desktop keeps its exact DOM order and there is nothing to keep in sync.
 *
 * WHAT THIS FILE LOCKS
 * --------------------
 *   R1  the toolbar is the FIRST row below md (and the last at md+) and carries
 *       the strip treatment
 *   R2  Attach stays pinned while every settings control is `max-md:hidden`
 *   R3  the overflow menu is narrow-viewport only, and really applies the
 *       placement class it is handed
 *   R4  Send/Stop is mounted exactly twice with complementary breakpoints, so
 *       one copy is always reachable and never both
 *   R5  no `os-composer-*` layer clips — the popovers (slash menu, voice panel,
 *       overflow menu) are all absolutely positioned inside it
 *   R6  the collapse breakpoint equals the app's own compact agent-web
 *       breakpoint (767px / Tailwind `md` = 768px)
 *
 * Mutations verified (each one makes this file fail):
 *   MC1  drop `order-1 md:order-3` from the toolbar                → R1
 *   MC2  drop `max-md:bg-bgLight` from the toolbar                 → R1
 *   MC3  drop `max-md:border-b` (leaving `…border-border`)         → R1
 *   MC4  drop `max-md:min-h-[36px]` from the textarea              → R1
 *   MC5  drop `order-3 md:order-2` from the input row              → R1
 *   MC6  add `max-md:hidden` to the Attach group                   → R2
 *   MC7  drop `max-md:hidden` from the ModePicker wrapper          → R2
 *   MC8  drop `max-md:hidden` from the upload button               → R2
 *   MC9  drop `className="md:hidden"` on MobileComposerMenu        → R3
 *   MC10 drop `${className}` from the menu root                    → R3
 *   MC11 drop `${POPOVER_SURFACE_CLASS}` from the menu panel       → R3
 *   MC12 replace the trigger icon with `<span />`                  → R3
 *   MC13 put `relative` back on the menu root (panel anchored to
 *        the 32px trigger instead of the card)                     → R3
 *   MC14 drop the `calc(100vw-3rem)` clamp on the panel width      → R3
 *   MC15 drop the input-row `{sendOrStop}` copy                    → R4
 *   MC16 `md:hidden` → `max-md:hidden` on the input-row Send copy  → R4
 *   MC17 drop the toolbar `{sendOrStop}` copy                      → R4
 *   MC18 add `overflow: hidden` to `.os-composer-overlap`          → R5
 *   MC19 add `overflow-hidden` to the composer card class string   → R5
 *   MC20 `useIsCompactAgentWeb` back to `(max-width: 900px)`       → R6
 *
 * Known limits — do NOT over-trust this file:
 *   - It reads class strings, so a class assembled at runtime (`order-${n}`) is
 *     invisible to it. Tailwind would not emit that class either, so the
 *     blindness is shared with the build.
 *   - **Every class rule must assert on class *tokens*, never `toContain` on the
 *     raw class string.** `'max-md:border-b'` is a prefix of
 *     `'max-md:border-border'`, so the substring form kept passing after the
 *     hairline was deleted (MC3). The same trap applies to identifiers:
 *     `MoreHorizontal` and `POPOVER_SURFACE_CLASS` are also in the import list,
 *     so `toContain` on the bare name survived deleting the actual render
 *     (MC12) and the actual interpolation (MC11). Assert the *use*, not the
 *     name.
 *   - It cannot see *widths*. R1–R4 guarantee the mobile row only contains the
 *     controls we intend; they cannot prove those controls still fit. The
 *     measurements above were taken in a real browser
 *     (320/360/375/390/393/412/430px) and are not re-run here.
 */
import { describe, expect, it } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';

const require_ = createRequire(import.meta.url);
const ROOT = path.resolve(__dirname, '..');
const read = (rel: string) => fs.readFileSync(path.join(ROOT, rel), 'utf8');

const COMPOSER = read(path.join('components', 'ai-chat', 'AgentWebComposer.tsx'));
const MENU = read(path.join('components', 'ai-chat', 'MobileComposerMenu.tsx'));
const CSS = read('index.css');
const MATCH_MEDIA = read(path.join('hooks', 'useMatchMedia.ts'));

/** Class list of the first element opening at `needle`. */
function classListAt(src: string, needle: string): string {
  const i = src.indexOf(needle);
  expect(i, `anchor not found in source: ${needle}`).toBeGreaterThan(-1);
  const m = /className="([^"]*)"/.exec(src.slice(i));
  expect(m, `no className immediately after ${needle}`).toBeTruthy();
  return m![1];
}

/**
 * Class *tokens* of the first element opening at `needle`.
 *
 * Rule assertions must use these, never `toContain` on the raw class string:
 * `'max-md:border-b'` is a prefix of `'max-md:border-border'`, so a substring
 * check keeps passing after the hairline is deleted (mutant MC3 read as
 * SURVIVED until this was introduced).
 */
function classTokensAt(src: string, needle: string): string[] {
  return classListAt(src, needle)
    .split(/\s+/)
    .filter(Boolean);
}

/** The `size` characters of source immediately preceding the first `needle`. */
function before(src: string, needle: string, size = 200): string {
  const i = src.indexOf(needle);
  expect(i, `anchor not found in source: ${needle}`).toBeGreaterThan(-1);
  return src.slice(Math.max(0, i - size), i);
}

describe('narrow-viewport composer layout guard', () => {
  it('R1: the toolbar is promoted to the first row below md, with the group-chat strip treatment', () => {
    const toolbar = classTokensAt(
      COMPOSER,
      '<div className="flex items-center gap-1.5 px-2.5 pb-2.5 pt-0.5',
    );
    // order-1 below md → it is painted above the textarea; md:order-3 restores
    // the desktop stack (chip → input → toolbar).
    expect(toolbar, 'toolbar is not promoted above the input row below md').toContain('order-1');
    expect(toolbar, 'toolbar no longer restores its desktop position at md+').toContain(
      'md:order-3',
    );
    for (const cls of [
      'max-md:bg-bgLight',
      'max-md:border-b',
      'max-md:border-border',
      'max-md:rounded-t-[22px]',
      'max-md:min-h-[38px]',
    ]) {
      expect(toolbar, `toolbar strip lost ${cls}`).toContain(cls);
    }

    const inputRow = classTokensAt(COMPOSER, '<div className="order-3 md:order-2 flex items-end');
    expect(inputRow, 'input row no longer sits below the toolbar below md').toContain('order-3');
    expect(inputRow, 'input row no longer restores its desktop position at md+').toContain(
      'md:order-2',
    );
    expect(inputRow, 'input row lost its narrow-viewport gutter').toContain('max-md:px-2');
    // The compact box height is what keeps the two rows short enough to fit.
    expect(COMPOSER, 'textarea lost its compact narrow-viewport height').toContain(
      'max-md:min-h-[36px]',
    );
  });

  it('R2: Attach is pinned; every settings control collapses into the menu', () => {
    // Attach is the one left-hand control that must survive the collapse.
    expect(
      before(COMPOSER, '<SoloAttachMenu'),
      'Attach is hidden on narrow viewports — nothing left to attach files with',
    ).not.toContain('max-md:hidden');

    for (const anchor of ['<ModePicker', '<SoloModelPicker', '<EffortPicker']) {
      expect(
        before(COMPOSER, anchor),
        `${anchor} still sits in the narrow-viewport row and can push Send off screen`,
      ).toContain('max-md:hidden');
    }
    // The standalone "upload file" button duplicates Attach's first entry.
    expect(
      COMPOSER,
      'the redundant upload button is still on screen below md',
    ).toMatch(/className="[^"]*max-md:hidden"\s*\n\s*title="上传文件"/);
  });

  it('R3: the overflow menu is narrow-viewport only and applies its placement class', () => {
    const i = COMPOSER.indexOf('<MobileComposerMenu');
    expect(i, 'MobileComposerMenu is not rendered by the composer').toBeGreaterThan(-1);
    expect(
      COMPOSER.slice(i, i + 160),
      'MobileComposerMenu is no longer retired at md+',
    ).toContain('className="md:hidden"');

    // A placement class passed but not applied is inert — pin the root, and pin
    // the fact that the root is NOT a positioning context. The panel anchors to
    // the composer card; anchored to this 32px trigger instead, `right-2` plus a
    // 280px panel put its left edge at -23px on a 320px screen (measured).
    const root = /<div ref=\{rootRef\} className=\{`([^`]*)`\}/.exec(MENU);
    expect(root, 'the menu root markup changed shape — re-anchor this rule').toBeTruthy();
    const rootTokens = root![1].split(/\s+/).filter(Boolean);
    expect(rootTokens, 'the menu root no longer applies the placement class').toContain(
      '${className',
    );
    for (const pos of ['relative', 'absolute', 'fixed', 'sticky']) {
      expect(rootTokens, `the menu root became a positioning context (${pos})`).not.toContain(pos);
    }

    // The trigger is an icon button, not a comment. Anchoring on the JSX use
    // rather than the bare name matters: `MoreHorizontal` also appears in the
    // import list, which alone satisfied an earlier, weaker version of this
    // rule (mutant MC12 read as SURVIVED).
    expect(MENU, 'the menu trigger icon is no longer rendered').toMatch(
      /<MoreHorizontal\s+size=\{18\}/,
    );

    // The panel must not rely on a translucent theme surface, and the check has
    // to look at the *panel's* class expression — the identifier alone is also
    // in the import (mutant MC11).
    const panel = /role="menu"[\s\S]{0,160}?className=\{`([\s\S]*?)`\}/.exec(MENU);
    expect(panel, 'the menu panel markup changed shape — re-anchor this rule').toBeTruthy();
    const panelCls = panel![1];
    expect(panelCls, 'the menu panel lost its opaque surface').toContain(
      '${POPOVER_SURFACE_CLASS}',
    );
    expect(panelCls, 'the menu panel lost its pop-in/out animation').toContain('os-pop-menu');
    expect(
      panelCls,
      'the panel is sized without a viewport clamp and will overflow a 320px screen',
    ).toContain('calc(100vw-3rem)');
  });

  it('R4: Send/Stop is mounted once per layout, never twice and never zero times', () => {
    const sites = COMPOSER.split('{sendOrStop}').length - 1;
    expect(sites, 'sendOrStop must be rendered exactly twice (input row + toolbar)').toBe(2);
    expect(
      COMPOSER,
      'the narrow-viewport Send (in the input row) is missing',
    ).toContain('className="md:hidden shrink-0 pb-0.5"');
    expect(
      COMPOSER,
      'the desktop Send (last control of the toolbar) is missing',
    ).toContain('className="max-md:hidden">{sendOrStop}');
  });

  it('R5: no composer layer clips, so the popovers stay visible', () => {
    // Every layer that wraps or hosts the composer. `.os-composer-landing-hero`
    // is deliberately absent: it is a *sibling* of the body that holds the
    // composer and its overflow belongs to the hero's own collapse animation.
    const LAYERS = [
      'os-composer-overlap',
      'os-composer-plan-layer',
      'os-composer-input-layer',
      'os-composer-landing-dock',
      'os-composer-landing-body',
    ];
    let checked = 0;
    for (const [, selector, body] of CSS.matchAll(/([^{}]*)\{([^}]*)\}/g)) {
      if (!LAYERS.some((layer) => selector.includes(layer))) continue;
      checked += 1;
      expect(
        /overflow\s*:\s*(hidden|clip)/.test(body),
        `${selector.trim()} would clip the slash menu / overflow menu`,
      ).toBe(false);
    }
    expect(checked, 'no .os-composer-* layout rule found in index.css').toBeGreaterThan(0);

    // The class string itself must not bring its own clip either.
    expect(
      COMPOSER,
      'the composer card clips its own popovers',
    ).not.toMatch(/os-composer-input-layer[^"`]*overflow-hidden/);
  });

  it('R6: the collapse breakpoint is the app’s own compact breakpoint (768px)', () => {
    const tailwind = require_(path.join(ROOT, 'tailwind.config.cjs')) as {
      theme?: { screens?: Record<string, string>; extend?: { screens?: Record<string, string> } };
    };
    const md = tailwind?.theme?.extend?.screens?.md ?? tailwind?.theme?.screens?.md;
    expect(md ?? '768px', 'Tailwind `md` moved — max-md: no longer means "phone"').toBe('768px');

    // Compact agent-web layout flips at max-width: 767px, i.e. the same place.
    expect(
      MATCH_MEDIA,
      'useIsCompactAgentWeb no longer switches at 767px, so the toolbar collapses ' +
        'and the compact layout no longer flip together',
    ).toMatch(/useIsCompactAgentWeb[\s\S]{0,160}max-width:\s*767px/);
  });
});
