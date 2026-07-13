/**
 * Shared Markdown renderer for assistant dialogue + thought text.
 * Renders fenced code blocks (```html / ```python / …) as styled <pre><code>,
 * with highlight.js when a language is known.
 *
 * Not used for tool-call args/results (those stay plain / existing ToolCallBlock).
 */
import { marked } from 'marked';
import hljs from 'highlight.js/lib/core';
import javascript from 'highlight.js/lib/languages/javascript';
import typescript from 'highlight.js/lib/languages/typescript';
import python from 'highlight.js/lib/languages/python';
import css from 'highlight.js/lib/languages/css';
import xml from 'highlight.js/lib/languages/xml';
import json from 'highlight.js/lib/languages/json';
import bash from 'highlight.js/lib/languages/bash';
import yaml from 'highlight.js/lib/languages/yaml';
import markdown from 'highlight.js/lib/languages/markdown';
import rust from 'highlight.js/lib/languages/rust';
import go from 'highlight.js/lib/languages/go';
import java from 'highlight.js/lib/languages/java';
import cpp from 'highlight.js/lib/languages/cpp';
import csharp from 'highlight.js/lib/languages/csharp';
import sql from 'highlight.js/lib/languages/sql';
import ini from 'highlight.js/lib/languages/ini';
import plaintext from 'highlight.js/lib/languages/plaintext';

let _registered = false;

function ensureHljs(): void {
  if (_registered) return;
  _registered = true;
  hljs.registerLanguage('javascript', javascript);
  hljs.registerLanguage('typescript', typescript);
  hljs.registerLanguage('python', python);
  hljs.registerLanguage('css', css);
  hljs.registerLanguage('xml', xml);
  hljs.registerLanguage('json', json);
  hljs.registerLanguage('bash', bash);
  hljs.registerLanguage('yaml', yaml);
  hljs.registerLanguage('markdown', markdown);
  hljs.registerLanguage('rust', rust);
  hljs.registerLanguage('go', go);
  hljs.registerLanguage('java', java);
  hljs.registerLanguage('cpp', cpp);
  hljs.registerLanguage('csharp', csharp);
  hljs.registerLanguage('sql', sql);
  hljs.registerLanguage('ini', ini);
  hljs.registerLanguage('plaintext', plaintext);
}

/** Map fence language tags → highlight.js language ids. */
const LANG_ALIAS: Record<string, string> = {
  js: 'javascript',
  jsx: 'javascript',
  mjs: 'javascript',
  cjs: 'javascript',
  ts: 'typescript',
  tsx: 'typescript',
  py: 'python',
  html: 'xml',
  htm: 'xml',
  svg: 'xml',
  vue: 'xml',
  sh: 'bash',
  zsh: 'bash',
  shell: 'bash',
  yml: 'yaml',
  md: 'markdown',
  rs: 'rust',
  c: 'cpp',
  h: 'cpp',
  hpp: 'cpp',
  cs: 'csharp',
  toml: 'ini',
  conf: 'ini',
  text: 'plaintext',
  txt: 'plaintext',
  plain: 'plaintext',
};

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function decodeBasicEntities(s: string): string {
  return s
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&amp;/g, '&');
}

/** Close an odd number of opening fences so streaming mid-fence still renders. */
export function closeOpenCodeFences(text: string): string {
  const matches = text.match(/^```/gm);
  if (!matches || matches.length % 2 === 0) return text;
  return `${text}\n\`\`\``;
}

function resolveLang(raw: string): string {
  const key = (raw || '').trim().toLowerCase().split(/[\s,{]/)[0] || '';
  if (!key) return 'plaintext';
  return LANG_ALIAS[key] || key;
}

function highlightCode(code: string, langHint: string): string {
  ensureHljs();
  const lang = resolveLang(langHint);
  try {
    if (lang && hljs.getLanguage(lang)) {
      return hljs.highlight(code, { language: lang, ignoreIllegals: true }).value;
    }
  } catch {
    /* fall through */
  }
  try {
    return hljs.highlightAuto(code).value;
  } catch {
    return escapeHtml(code);
  }
}

/**
 * Convert Markdown (incl. ```lang fences) to HTML with highlighted code blocks.
 */
export function renderFencedMarkdown(text: string): string {
  if (!text) return '';
  const src = closeOpenCodeFences(text);
  let html: string;
  try {
    html = marked.parse(src, { breaks: true, async: false }) as string;
  } catch {
    return `<pre class="ai-code-block"><code>${escapeHtml(text)}</code></pre>`;
  }

  // <pre><code class="language-xxx">…</code></pre>
  html = html.replace(
    /<pre><code class="language-([^"]+)">([\s\S]*?)<\/code><\/pre>/gi,
    (_m, lang: string, body: string) => {
      const code = decodeBasicEntities(body.replace(/\n$/, ''));
      const highlighted = highlightCode(code, lang);
      const label = escapeHtml((lang || '').split(/[\s,{]/)[0] || 'code');
      return (
        `<div class="ai-code-wrap">` +
        `<div class="ai-code-lang">${label}</div>` +
        `<pre class="ai-code-block"><code class="language-${escapeHtml(lang)} hljs">${highlighted}</code></pre>` +
        `</div>`
      );
    },
  );

  // Bare <pre><code>…</code></pre> (no language)
  html = html.replace(
    /<pre><code(?! class=)>([\s\S]*?)<\/code><\/pre>/gi,
    (_m, body: string) => {
      const code = decodeBasicEntities(body.replace(/\n$/, ''));
      const highlighted = highlightCode(code, 'plaintext');
      return (
        `<div class="ai-code-wrap">` +
        `<pre class="ai-code-block"><code class="hljs">${highlighted}</code></pre>` +
        `</div>`
      );
    },
  );

  return html;
}

/** Tailwind-friendly classes for prose + fenced code chrome. */
export const AI_MARKDOWN_CLASS =
  'prose prose-sm prose-invert max-w-none break-words overflow-x-auto ai-markdown ' +
  'prose-pre:my-2 prose-code:before:content-none prose-code:after:content-none';
