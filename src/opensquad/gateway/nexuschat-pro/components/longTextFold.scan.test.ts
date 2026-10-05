/**
 * The fold must show plain text while folded and the rendered body while expanded.
 *
 * That split is the whole safety property: the body is HTML by the time it is rendered, so a fold
 * that sliced it would cut tags. Folding the raw text and rendering the markup only when expanded
 * keeps both honest. These checks read the sources, as the rest of this suite does.
 */
import fs from 'fs';
import path from 'path';
import { describe, expect, it } from 'vitest';

const read = (rel: string) => fs.readFileSync(path.resolve(__dirname, rel), 'utf8');

const FOLD = read('./LongTextFold.tsx');
const CHAT = read('./ChatWindow.tsx');

describe('the folded view', () => {
  it('renders the truncated text, so a folded message keeps its formatting', () => {
    expect(FOLD).toContain('const folded = foldedText(text, limit);');
    expect(FOLD).toContain('{render(folded)}');
    expect(FOLD).toContain('{render(text)}');
    expect(FOLD).not.toContain('dangerouslySetInnerHTML');
  });

  it('renders the real body only when expanded', () => {
    const expanded = FOLD.slice(FOLD.indexOf('if (expanded)'));
    expect(expanded).toContain('{render(text)}');
    expect(expanded).toContain("t('chat.collapseText'");
  });

  it('keeps the text crisp and blurs only a masked copy at the edge', () => {
    // The bug these guard: a mask or blur on an empty overlay shows nothing, and a blur on the text
    // itself softens the whole message. So the crisp layer carries the fade mask, and the blur
    // lives on a second rendering of the same truncated text, masked to the bottom.
    expect(FOLD).toMatch(/className="os-thought-tail"/);
    expect(FOLD).not.toMatch(/className="[^"]*\bos-thought-tail\b[^"]*blur-/);

    const blur = FOLD.slice(FOLD.indexOf('data-testid="long-text-blur"'));
    expect(blur).toContain('{render(folded)}');
    expect(blur).toMatch(/blur-\[[\d.]+px\]/);
    expect(blur).toContain('[mask-image:linear-gradient(to_bottom,transparent_58%,black_100%)]');
    expect(blur).toContain('pointer-events-none');
    expect(blur).toContain('select-none');
  });

  it('uses the same primitive the reasoning panel fades out with', () => {
    const css = fs.readFileSync(path.resolve(__dirname, '../index.css'), 'utf8');

    expect(css).toContain('.os-thought-tail');
    expect(css).toMatch(/\.os-thought-tail \{[\s\S]*?mask-image: linear-gradient/);
  });

  it('offers both directions, by name', () => {
    expect(FOLD).toContain("t('chat.expandText'");
    expect(FOLD).toContain("t('chat.collapseText'");
    expect(FOLD).toMatch(/data-expanded="0"/);
    expect(FOLD).toMatch(/data-expanded="1"/);
  });
});

describe('where it is used', () => {
  it('the chat folds a long body instead of rendering it whole', () => {
    expect(CHAT).toContain('shouldFold(msg.content) ? (');
    expect(CHAT).toContain('<LongTextFold');
    expect(CHAT).toContain('render={(text) => (');
    expect(CHAT).toContain('parseContent(text, msg.id)');
  });

  it('is translated in both locales, differently', () => {
    const zh = JSON.parse(read('../locales/zh.json'));
    const en = JSON.parse(read('../locales/en.json'));

    for (const key of ['expandText', 'collapseText']) {
      expect(zh.chat[key]).toBeTruthy();
      expect(en.chat[key]).toBeTruthy();
      expect(zh.chat[key]).not.toBe(en.chat[key]);
    }
    expect(zh.chat.expandText).toMatch(/[\u4e00-\u9fff]/);
  });
});
