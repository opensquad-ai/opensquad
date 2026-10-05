/**
 * Being @-mentioned highlights the name, not the message.
 *
 * The bubble used to turn amber whenever it mentioned you: `bg-yellow-50` plus a yellow border and a
 * ring, which painted a whole paragraph — and any table inside it — the colour of a warning. The
 * thing worth spotting is the two characters that carry the name, so the highlight moved onto the
 * mention span and the bubble went back to being a bubble.
 */
import fs from 'fs';
import path from 'path';
import { describe, expect, it } from 'vitest';

import { MENTION_CLASS } from './mentions';

const read = (rel: string) => fs.readFileSync(path.resolve(__dirname, rel), 'utf8');
const CHAT = read('../components/ChatWindow.tsx');

describe('the mention span', () => {
  it('carries the highlight: a light grey chip, in either appearance', () => {
    expect(MENTION_CLASS).toContain('bg-black/[0.06]');
    expect(MENTION_CLASS).toContain('dark:bg-white/[0.10]');
    expect(MENTION_CLASS).toContain('rounded');
  });

  it('keeps the name readable and clickable', () => {
    expect(MENTION_CLASS).toContain('mention-link');
    expect(MENTION_CLASS).toContain('text-primary');
    expect(MENTION_CLASS).toContain('cursor-pointer');
    expect(MENTION_CLASS).toContain('hover:underline');
  });
});

describe('the bubble', () => {
  it('no longer styles itself for a mention', () => {
    // Scoped to the bubble's own className: `bg-yellow-50` also appears as a hover on a floating
    // button further down the file, and that one has nothing to do with mentions.
    const bubble = CHAT.slice(
      CHAT.indexOf('isInteractiveCard\n                ?'),
      CHAT.indexOf('{/* Reply Context UI */}'),
    );

    expect(bubble.length).toBeGreaterThan(0);
    expect(bubble).not.toContain('bg-yellow-50');
    expect(bubble).not.toContain('ring-yellow-100');
    // Two arms — self and other — and no mention arm. The prop still exists (the row comparator and
    // the call site use it), it just styles nothing.
    expect(bubble).not.toContain('isMentioned ?');
  });

  it('still renders one bubble style for others, one for itself', () => {
    expect(CHAT).toContain('bg-chatBubbleOther');
    expect(CHAT).toContain('bg-chatBubbleSelf');
  });
});
