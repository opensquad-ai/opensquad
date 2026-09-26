// @vitest-environment node
/**
 * Static guard: the composer's pending-attachment strip must live INSIDE the
 * composer card, not in its own row above it.
 *
 * WHY THIS EXISTS
 * ---------------
 * Reported 2026-09-26 as "agent web 发送图片应该在消息框内，而不是在框外". The strip
 * that previews attached images / files / quotes / the uploading spinner was a
 * sibling of the card, wrapped in the page gutter + `columnClass` like the other
 * pre-composer rows (terminals, approvals, pending, statusHint). So an attached
 * image floated in the message area, 16px above the rounded frame, and read as
 * part of the transcript rather than as something the user is about to send.
 *
 * Measured in Chromium at 1440px against the working tree (:5173) before the
 * fix: `img.closest('.os-composer-input-layer') === null`, thumb box
 * [290,642,64,64] vs card box [290,722,836,118] — the card did not contain it.
 * After: `closest()` resolves, card box [290,646,836,194] grew to hold the
 * thumb at [305,659,64,64].
 *
 * THE FIX
 * -------
 * The strip moved into `.os-composer-input-layer` as a flex-col child, placed
 * before the `pendingSkill` chip and given the chip's `order-2 md:order-1` so it
 * stacks above the textarea on both breakpoints. Its own `columnClass` wrapper
 * and page gutter went away — the card already provides width and padding, so
 * the strip now uses the card's inner gutter (`px-3.5 pt-3`) like the chip does.
 *
 * WHAT THIS FILE LOCKS
 * --------------------
 *   R1  the strip is mounted exactly once and sits between the card's opening
 *       and the input row, i.e. it is a child of the card
 *   R2  it carries the card gutter + the stacking order that keeps it above the
 *       textarea at md and below the collapsed toolbar at max-md
 *   R3  it no longer brings its own page gutter or `columnClass` wrapper
 *   R4  it precedes `pendingSkill` in DOM order (equal `order` values are
 *       tie-broken by document order, so this is what keeps images above chips)
 *   R5  the thumbnails are really rendered by that strip
 *
 * Mutations verified (each one makes this file fail):
 *   MA1  move the whole strip back above the card (before `os-composer-overlap`)
 *        → R1
 *   MA2  drop `md:order-1` from the strip                        → R2
 *   MA3  drop `px-3.5` (put back `px-2 sm:px-4`)                 → R2, R3
 *   MA4  re-add the `${columnClass}` wrapper div around the strip → R3
 *   MA5  place the strip after the `pendingSkill` block           → R4
 *   MA6  render `images.map` from elsewhere and empty the strip   → R5
 *
 * Known limits — do NOT over-trust this file:
 *   - It reads source order, not computed layout. R1 proves the strip is nested
 *     inside the card's JSX; it cannot prove the card is not later re-parented
 *     into a scrolling container that clips it. The 1440/393/360/320px
 *     measurements behind this were taken in a real browser and are not re-run.
 *   - `order-*` tie-breaking with `pendingSkill` (R4) relies on both keeping the
 *     same order tokens; if the chip's order changes independently, re-check the
 *     rendered stack rather than trusting this rule.
 */
import { describe, expect, it } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';

const ROOT = path.resolve(__dirname, '..');
const COMPOSER = fs
  .readFileSync(path.join(ROOT, 'components', 'ai-chat', 'AgentWebComposer.tsx'), 'utf8');

const CARD = 'os-composer-input-layer w-full flex flex-col';
const STRIP = '{(images.length > 0 || attachments.length > 0 || quotes.length > 0 || isUploading) && (';
const INPUT_ROW = '<div className="order-3 md:order-2 flex items-end';
const PENDING = '{pendingSkill ? (';

function oneIndex(needle: string, what: string): number {
  const i = COMPOSER.indexOf(needle);
  expect(i, `${what} not found — anchor drifted, re-check the fix`).toBeGreaterThan(-1);
  return i;
}

/**
 * Class tokens of the first `className="…"` after `needle` — for the strip that
 * is its own container element. Anchoring on the conditional rather than on the
 * class string matters: an anchor taken from *inside* the attribute value makes
 * the regex skip the intended element and read its first child instead.
 */
function classTokensAfter(needle: string): string[] {
  const i = oneIndex(needle, needle);
  const m = /className="([^"]*)"/.exec(COMPOSER.slice(i));
  expect(m, `no className after ${needle}`).toBeTruthy();
  return m![1].split(/\s+/).filter(Boolean);
}

describe('composer pending-attachment strip layout guard', () => {
  it('R1: the strip is mounted once, inside the composer card, above the input row', () => {
    const sites = COMPOSER.split(STRIP).length - 1;
    expect(sites, 'the attachment strip is mounted more than once').toBe(1);

    const card = oneIndex(CARD, 'the composer card');
    const strip = oneIndex(STRIP, 'the attachment strip');
    const inputRow = oneIndex(INPUT_ROW, 'the input row');
    expect(strip, 'attachment strip moved before the card — it renders outside the frame again')
      .toBeGreaterThan(card);
    expect(strip, 'attachment strip moved below the textarea').toBeLessThan(inputRow);
  });

  it('R2: the strip uses the card gutter and stacks above the textarea', () => {
    const cls = classTokensAfter(STRIP);
    for (const token of ['px-3.5', 'pt-3', 'order-2', 'md:order-1', 'max-md:px-2.5']) {
      expect(cls, `the attachment strip lost ${token}`).toContain(token);
    }
  });

  it('R3: the strip no longer wraps itself in the page gutter / column wrapper', () => {
    const strip = oneIndex(STRIP, 'the attachment strip');
    const head = COMPOSER.slice(strip, strip + 400);
    expect(head, 'the strip re-added a columnClass wrapper and escapes the card width')
      .not.toContain('columnClass');
    expect(head, 'the strip re-added the outer page gutter').not.toContain('sm:px-4');
  });

  it('R4: images stack above the pending-skill chip (document order tie-break)', () => {
    expect(
      oneIndex(STRIP, 'the attachment strip'),
      'pendingSkill now precedes the strip, so the chip paints above the thumbnails',
    ).toBeLessThan(oneIndex(PENDING, 'the pendingSkill block'));
  });

  it('R5: the thumbnails are rendered by that strip', () => {
    const strip = oneIndex(STRIP, 'the attachment strip');
    const pending = oneIndex(PENDING, 'the pendingSkill block');
    const body = COMPOSER.slice(strip, pending);
    expect(body, 'the strip no longer renders attached images').toContain('images.map(');
    expect(body, 'the strip no longer renders attached files').toContain('attachments.map(');
  });
});
