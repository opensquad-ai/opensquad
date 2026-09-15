// @vitest-environment jsdom
/**
 * Behavioural tests for the shared HTML sanitizer.
 *
 * MUST run on jsdom, not happy-dom — see the long explanation at the top of
 * fencedMarkdown.test.ts. Under happy-dom, DOMPurify silently reads
 * `nodeName === ''` and strips every element, which makes these assertions
 * fail in a way that looks like a broken sanitizer.
 *
 * Assertions are made against the PARSED DOM, not against substrings. Two of
 * the payloads below legitimately survive as inert text (`<img …>` inside a
 * code fence, or inside a quoted attribute value) — substring matching would
 * report those as failures and, worse, would miss a real payload that happened
 * to be re-encoded.
 */
import { describe, expect, it } from 'vitest';
import { marked } from 'marked';
import { escapeHtml, sanitizeHtml } from './safeHtml';

// --------------------------------------------------------------- DOM helpers

function intoDom(html: string): HTMLElement {
  const host = document.createElement('div');
  host.innerHTML = html;
  return host;
}

/** Elements that must never appear in sanitized output. */
const FORBIDDEN_TAGS = new Set([
  'SCRIPT', 'IFRAME', 'OBJECT', 'EMBED', 'SVG', 'MATH', 'BASE', 'META',
  'FORM', 'INPUT', 'BUTTON', 'STYLE', 'LINK', 'TEMPLATE', 'AUDIO', 'VIDEO',
  'SOURCE', 'TRACK', 'MARQUEE',
]);

const DANGEROUS_URL = /^\s*(?:javascript|vbscript|data:text\/html|data:application)/i;

interface Violation {
  where: string;
  what: string;
}

/** Every way this output could execute script. */
function violations(root: HTMLElement): Violation[] {
  const found: Violation[] = [];
  for (const el of Array.from(root.querySelectorAll('*'))) {
    const tag = el.tagName.toUpperCase();
    if (FORBIDDEN_TAGS.has(tag)) {
      found.push({ where: tag, what: 'forbidden element' });
    }
    for (const attr of Array.from(el.attributes)) {
      const name = attr.name.toLowerCase();
      if (name.startsWith('on')) {
        found.push({ where: tag, what: `event handler ${name}` });
      }
      if (name === 'style') {
        found.push({ where: tag, what: 'style attribute' });
      }
      if (name === 'srcdoc') {
        found.push({ where: tag, what: 'srcdoc attribute' });
      }
      if ((name === 'href' || name === 'src' || name === 'xlink:href') && DANGEROUS_URL.test(attr.value)) {
        found.push({ where: tag, what: `${name}="${attr.value}"` });
      }
    }
  }
  return found;
}

/** Payload → the sanitizer must leave nothing executable behind. */
const PAYLOADS: Array<[name: string, payload: string]> = [
  ['script element', '<script>alert(1)</script>'],
  ['inline event handler', '<img src=x onerror="alert(1)">'],
  ['onerror without quotes', '<img src=x onerror=alert(1)>'],
  ['iframe', '<iframe src="https://evil.test"></iframe>'],
  ['javascript: href', '<a href="javascript:alert(1)">click</a>'],
  ['javascript: href with newline', '<a href="java\nscript:alert(1)">click</a>'],
  ['javascript: href padded', '<a href="  javascript:alert(1)">click</a>'],
  ['data:text/html href', '<a href="data:text/html,<script>alert(1)</script>">x</a>'],
  ['svg payload', '<svg onload="alert(1)"></svg>'],
  ['animate onbegin', '<svg><animate onbegin="alert(1)"></animate></svg>'],
  ['style attribute', '<div style="position:fixed;top:0">t</div>'],
  ['style with url()', '<div style="background:url(javascript:alert(1))">t</div>'],
  ['onclick attribute', '<div onclick="alert(1)">t</div>'],
  ['form element', '<form action="https://evil.test"><input name="a"></form>'],
  ['math element', '<math><mtext></mtext></math>'],
  ['object element', '<object data="x"></object>'],
  ['embed element', '<embed src="x">'],
  ['base tag', '<base href="https://evil.test/">'],
  ['meta refresh', '<meta http-equiv="refresh" content="0;url=https://evil.test">'],
  ['iframe srcdoc', '<iframe srcdoc="<script>alert(1)</script>"></iframe>'],
  ['attribute breakout', '"><img src=x onerror=alert(1)>'],
  ['title attribute breakout', '<p title="<img src=x onerror=alert(1)>">t</p>'],
  ['nesting breakout', '<div><p><img src=x onerror=alert(1)></p></div>'],
  ['template element', '<template><img src=x onerror=alert(1)></template>'],
  ['malformed img', '<img src=x onerror=alert(1)//'],
];

describe('sanitizeHtml — attack payloads leave nothing executable', () => {
  it.each(PAYLOADS)('neutralizes %s', (_name, payload) => {
    const out = sanitizeHtml(payload);
    expect(violations(intoDom(out))).toEqual([]);
  });

  it('neutralizes a tool-result-shaped document end to end', () => {
    const raw = [
      '## Result',
      '',
      '```html',
      '<img src=x onerror=alert(1)>',
      '```',
      '',
      '<iframe src="javascript:alert(1)"></iframe>',
      '',
      '<a href="javascript:alert(document.cookie)">click me</a>',
    ].join('\n');
    const html = sanitizeHtml(marked.parse(raw, { breaks: true, async: false }) as string);
    const root = intoDom(html);
    expect(violations(root)).toEqual([]);
    // The payload is still *displayed*, just as inert text inside <code>.
    expect(html).toContain('Result');
    expect(html).toContain('&lt;img');
    expect(html).toContain('click me');
  });
});

describe('sanitizeHtml — benign markup survives', () => {
  it('keeps headings, emphasis, links, images and tables', () => {
    const out = sanitizeHtml(
      marked.parse(
        [
          '# Title',
          '',
          '**bold** and *italic* and `code`',
          '',
          '- item one',
          '- item two',
          '',
          '[link](https://example.com)',
          '',
          '![alt](https://example.com/a.png)',
          '',
          '| a | b |',
          '| - | - |',
          '| 1 | 2 |',
        ].join('\n'),
        { breaks: true, async: false },
      ) as string,
    );
    expect(out).toContain('<h1>');
    expect(out).toContain('<strong>');
    expect(out).toContain('<em>');
    expect(out).toContain('<code>');
    expect(out).toContain('<li>');
    expect(out).toContain('<table>');
    expect(out).toContain('href="https://example.com"');
    expect(out).toContain('src="https://example.com/a.png"');
  });

  it('keeps mark / kbd / details / summary', () => {
    const out = sanitizeHtml(
      '<p>a <mark>m</mark> b <kbd>K</kbd></p><details open><summary>s</summary>body</details>',
    );
    expect(out).toContain('<mark>');
    expect(out).toContain('<kbd>');
    expect(out).toContain('<details');
    expect(out).toContain('<summary>');
  });

  it('keeps the app-generated mention span including data-username', () => {
    // data-username is allowlisted ON PURPOSE so the ChatWindow / MessageInput
    // mention spans survive sanitizing. If someone drops it from ALLOWED_ATTR
    // the click-to-jump mention feature silently breaks — this locks it.
    const out = sanitizeHtml(
      '<span class="mention-link text-primary font-bold" data-username="bob">@bob</span>',
    );
    expect(out).toContain('data-username="bob"');
    expect(out).toContain('mention-link');
  });

  it('keeps the mermaid placeholder payload', () => {
    const out = sanitizeHtml('<div class="ai-mermaid" data-src="graph%20TD"></div>');
    expect(out).toContain('data-src="graph%20TD"');
  });
});

describe('sanitizeHtml — linksToNewTab', () => {
  // DOMPurify ≥3 deletes `target` from `<a>` (reverse-tabnabbing). The old
  // ChatWindow code re-wrote `<a href=` into `<a target="_blank" … href=` as a
  // string, which DOMPurify then undid — so the "open links in a new tab"
  // behaviour silently died the moment sanitizing was introduced. The option
  // restores it through an afterSanitizeAttributes hook.
  it('is off by default', () => {
    const out = sanitizeHtml('<a href="https://e.com">x</a>');
    expect(out).not.toContain('target=');
  });

  it('adds target/rel to every surviving anchor', () => {
    const out = sanitizeHtml('<a href="https://e.com">x</a>', { linksToNewTab: true });
    const anchors = Array.from(intoDom(out).querySelectorAll('a'));
    expect(anchors).toHaveLength(1);
    expect(anchors[0].getAttribute('target')).toBe('_blank');
    expect(anchors[0].getAttribute('rel')).toBe('noopener noreferrer');
  });

  it('does not add target to anchors whose href was sanitized away', () => {
    const out = sanitizeHtml('<a href="javascript:alert(1)">x</a>', { linksToNewTab: true });
    expect(out).not.toContain('target=');
    expect(violations(intoDom(out))).toEqual([]);
  });

  it('does not leak the hook into the default (no-option) instance', () => {
    sanitizeHtml('<a href="https://e.com">x</a>', { linksToNewTab: true });
    const plain = sanitizeHtml('<a href="https://other.com">y</a>');
    expect(plain).not.toContain('target=');
  });
});

describe('sanitizeHtml — post-processing order invariant', () => {
  // The ChatWindow fix relies on sanitizing LAST:
  //   parse -> inject mention spans -> sanitize({ linksToNewTab })
  // If the order ever flips, injected markup is stripped (feature regress) or
  // the payload survives (XSS). This pins the order.
  it('mention spans and link targets survive while the payload dies', () => {
    const parsed = marked.parse(
      'hi @bob [x](https://e.com) <img src=x onerror=alert(1)> <script>1</script>',
      { breaks: true, async: false },
    ) as string;
    const withMentions = parsed.replace(
      /@(\w+)/g,
      '<span class="mention-link text-primary font-bold cursor-pointer hover:underline" data-username="$1">@$1</span>',
    );
    const out = sanitizeHtml(withMentions, { linksToNewTab: true });
    const root = intoDom(out);

    expect(out).toContain('data-username="bob"');
    expect(out).toContain('mention-link');
    expect(root.querySelector('a')?.getAttribute('target')).toBe('_blank');
    expect(violations(root)).toEqual([]);
  });
});

describe('sanitizeHtml — edge cases', () => {
  it('returns empty string for empty input', () => {
    expect(sanitizeHtml('')).toBe('');
  });

  it('is not a no-op (guards against a silently disabled sanitizer)', () => {
    expect(sanitizeHtml('<b>x</b>')).toContain('<b>');
    expect(sanitizeHtml('<b onclick="1">x</b>')).not.toContain('onclick');
  });
});

describe('escapeHtml', () => {
  it('escapes all five HTML-significant characters', () => {
    expect(escapeHtml(`&<>"'`)).toBe('&amp;&lt;&gt;&quot;&#039;');
  });

  it('neutralizes a tag', () => {
    expect(escapeHtml('<img src=x onerror=alert(1)>')).not.toContain('<img');
  });

  it('leaves plain text untouched', () => {
    expect(escapeHtml('hello world 你好')).toBe('hello world 你好');
  });

  it('output is inert when injected', () => {
    const out = escapeHtml('<script>alert(1)</script> and <img src=x onerror=alert(1)>');
    expect(violations(intoDom(out))).toEqual([]);
  });
});
