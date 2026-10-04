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
  it('shows the raw text, never the parsed markup', () => {
    expect(FOLD).toContain('foldedText(text, limit)');
    expect(FOLD).not.toContain('dangerouslySetInnerHTML');
    expect(FOLD).toContain('React.ReactNode');
  });

  it('renders the real body only when expanded', () => {
    const expanded = FOLD.slice(FOLD.indexOf('if (expanded)'));
    expect(expanded).toContain('{renderFull()}');
    expect(expanded).toContain("t('chat.collapseText'");
  });

  it('fades the text itself, with the panel\'s own graduated mask', () => {
    // The bug this guards: a mask or blur on an empty overlay blurs nothing and shows nothing.
    const fade = FOLD.slice(FOLD.indexOf('data-testid="long-text-fade"'), FOLD.indexOf('</div>', FOLD.indexOf('data-testid="long-text-fade"')));
    expect(fade).toContain('os-thought-tail');
    expect(fade).toMatch(/blur-\[[\d.]+px\]/);
    expect(fade).toContain('foldedText(text, limit)');
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
    expect(CHAT).toContain('renderFull={() => (');
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
