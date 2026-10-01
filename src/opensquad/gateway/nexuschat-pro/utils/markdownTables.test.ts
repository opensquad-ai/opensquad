import fs from 'node:fs';
import path from 'node:path';

import { describe, expect, it } from 'vitest';

import { MENTION_CLASS, markMentions, renderMentionSpans } from './mentions';
import { wrapMarkdownTables } from './markdownTables';

const read = (rel: string) => fs.readFileSync(path.resolve(__dirname, '..', rel), 'utf8');

describe('@mentions', () => {
  it('decorates a mention with the clickable span', () => {
    const html = markMentions('请 @coder-001 看一下');
    expect(html).toContain(`class="${MENTION_CLASS}"`);
    expect(html).toContain('data-username="coder-001"');
    expect(html).toContain('>@coder-001<');
  });

  it('handles the names agents actually have', () => {
    for (const name of ['调查员', '子代2号', 'coder-001', 'qa.bot']) {
      expect(markMentions(`hi @${name}`)).toContain(`data-username="${name}"`);
    }
  });

  it('leaves e-mail addresses and bare @ alone', () => {
    expect(markMentions('mail me at x@y.com')).toBe('mail me at x@y.com');
    expect(markMentions('@')).toBe('@');
  });

  it('does not double-wrap an already decorated mention', () => {
    const once = markMentions('hi @qa');
    const twice = markMentions(once);
    expect(twice.match(/data-username=/g)).toHaveLength(1);
    // the html-side pass is a no-op on the same markup
    expect(renderMentionSpans(once)).toBe(once);
  });

  it('escapes the name it injects', () => {
    const html = renderMentionSpans('@x&quot;y');
    expect(html).not.toContain('"y"');
  });
});

describe('markdown tables in group chat', () => {
  it('wraps rendered tables in the container the chat CSS expects', () => {
    const html = '<p>hi</p><table><thead><tr><th>a</th></tr></thead><tbody><tr><td>1</td></tr></tbody></table>';
    const wrapped = wrapMarkdownTables(html);
    expect(wrapped).toContain('<div class="ai-table-wrap"><table>');
    expect(wrapped).toContain('</table></div>');
    // non-table html is untouched
    expect(wrapMarkdownTables('<p>no table here</p>')).toBe('<p>no table here</p>');
    expect(wrapMarkdownTables('')).toBe('');
  });

  it('is idempotent', () => {
    const html = '<table><tr><td>1</td></tr></table>';
    expect(wrapMarkdownTables(wrapMarkdownTables(html))).toBe(wrapMarkdownTables(html));
  });

  it('wraps every table in a document', () => {
    const html = '<table><tr><td>1</td></tr></table><p>x</p><table><tr><td>2</td></tr></table>';
    const wrapped = wrapMarkdownTables(html);
    expect(wrapped.match(/class="ai-table-wrap"/g)).toHaveLength(2);
  });

  it('is used by group chat, alongside the agent-web markdown class', () => {
    const chatWindow = read('components/ChatWindow.tsx');
    // the same container class agent web uses, so `.ai-markdown` styles apply
    expect(chatWindow).toMatch(/className="ai-markdown prose prose-sm/);
    // tables parsed by plain marked get the wrap renderFencedMarkdown does
    expect(chatWindow).toMatch(/wrapMarkdownTables\(parsed\)/);
    // and mentions go through the shared decorator, not a local `@(\w+)` hack
    expect(chatWindow).toMatch(/markMentions\(withExplicitLinks\)/);
    expect(chatWindow).not.toMatch(/replace\(\/@\(\\w\+\)/);
  });

  it('decorates mentions for every markdown surface (agent web + DMs)', () => {
    const fenced = read('utils/fencedMarkdown.ts');
    expect(fenced).toMatch(/marked\.parse\(markMentions\(src\)/);
  });
});
