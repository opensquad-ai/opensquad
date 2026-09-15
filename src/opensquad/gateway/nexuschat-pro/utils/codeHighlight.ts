/**
 * Shared highlight.js helpers for file preview and FileDiffBlock.
 */
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

let registered = false;

function ensureHljs(): void {
  if (registered) return;
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
  registered = true;
}

const EXT_LANG: Record<string, string> = {
  js: 'javascript', jsx: 'javascript', mjs: 'javascript', cjs: 'javascript',
  ts: 'typescript', tsx: 'typescript',
  py: 'python', pyw: 'python',
  css: 'css', scss: 'css', less: 'css',
  html: 'xml', htm: 'xml', xml: 'xml', svg: 'xml', vue: 'xml',
  json: 'json', jsonc: 'json',
  sh: 'bash', bash: 'bash', zsh: 'bash', fish: 'bash', ps1: 'bash',
  yml: 'yaml', yaml: 'yaml',
  md: 'markdown', mdx: 'markdown',
  rs: 'rust',
  go: 'go',
  java: 'java',
  cpp: 'cpp', cc: 'cpp', cxx: 'cpp', c: 'cpp', h: 'cpp', hpp: 'cpp',
  cs: 'csharp',
  sql: 'sql',
  ini: 'ini', cfg: 'ini', conf: 'ini', toml: 'ini',
};

export function getLangForFile(fileName: string): string {
  ensureHljs();
  const base = fileName.split(/[/\\]/).pop() || fileName;
  const ext = base.includes('.') ? base.split('.').pop()?.toLowerCase() ?? '' : '';
  return EXT_LANG[ext] ?? 'plaintext';
}

export function escapeHtml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

/** Highlight a single line of code. Returns HTML string. */
export function highlightLine(code: string, lang: string): string {
  ensureHljs();
  if (!code) return '&nbsp;';
  try {
    const result = hljs.highlight(code, { language: lang || 'plaintext', ignoreIllegals: true });
    return result.value || escapeHtml(code);
  } catch {
    return escapeHtml(code);
  }
}

/**
 * Inject HLJS_THEME_CSS into <head> exactly once, on first use.
 *
 * Chat fenced code blocks (renderFencedMarkdown) produce `.hljs-*` spans but
 * render outside the file pane, which mounts this stylesheet locally — so
 * before this helper existed, chat syntax colours came from a separate,
 * appearance-blind Palenight subset in index.css: light pages got pastel
 * Palenight tokens on a hardcoded near-black well. Injecting here makes the
 * chat well follow the same two-palette theme (GitHub Light / Palenight dark)
 * as the file display, from a single source of truth.
 */
let themeInjected = false;

export function ensureHljsTheme(): void {
  if (themeInjected || typeof document === 'undefined') return;
  themeInjected = true;
  const style = document.createElement('style');
  style.setAttribute('data-hljs-theme', '');
  style.textContent = HLJS_THEME_CSS;
  document.head.appendChild(style);
}

/** GitHub Light by default; Material Palenight only under html.dark. */
export const HLJS_THEME_CSS = `
  .hljs-keyword { color: #cf222e; }
  .hljs-built_in { color: #0550ae; }
  .hljs-type { color: #953800; }
  .hljs-literal { color: #0550ae; }
  .hljs-number { color: #0550ae; }
  .hljs-operator { color: #1f2328; }
  .hljs-punctuation { color: #1f2328; }
  .hljs-property { color: #0550ae; }
  .hljs-regexp { color: #0a3069; }
  .hljs-string { color: #0a3069; }
  .hljs-char { color: #0a3069; }
  .hljs-subst { color: #1f2328; }
  .hljs-symbol { color: #0550ae; }
  .hljs-variable { color: #953800; }
  .hljs-template-variable { color: #953800; }
  .hljs-link { color: #0a3069; text-decoration: underline; }
  .hljs-selector-id { color: #0550ae; }
  .hljs-selector-class { color: #953800; }
  .hljs-selector-attr { color: #0a3069; }
  .hljs-selector-pseudo { color: #cf222e; }
  .hljs-attr { color: #0550ae; }
  .hljs-attribute { color: #0550ae; }
  .hljs-name { color: #116329; }
  .hljs-tag { color: #0550ae; }
  .hljs-comment { color: #656d76; font-style: italic; }
  .hljs-meta { color: #656d76; }
  .hljs-meta .hljs-string { color: #0a3069; }
  .hljs-section { color: #0550ae; font-weight: bold; }
  .hljs-title { color: #8250df; font-weight: bold; }
  .hljs-title.class_ { color: #953800; }
  .hljs-title.function_ { color: #8250df; }
  .hljs-params { color: #1f2328; }
  .hljs-formula { color: #cf222e; }
  .hljs-deletion { color: #82071e; background-color: #ffebe9; }
  .hljs-addition { color: #116329; background-color: #dafbe1; }
  .hljs-emphasis { font-style: italic; }
  .hljs-strong { font-weight: bold; }

  html.dark .hljs-keyword { color: #c792ea; }
  html.dark .hljs-built_in { color: #82aaff; }
  html.dark .hljs-type { color: #ffcb6b; }
  html.dark .hljs-literal { color: #ff5874; }
  html.dark .hljs-number { color: #f78c6c; }
  html.dark .hljs-operator { color: #89ddff; }
  html.dark .hljs-punctuation { color: #89ddff; }
  html.dark .hljs-property { color: #80cbc4; }
  html.dark .hljs-regexp { color: #f07178; }
  html.dark .hljs-string { color: #c3e88d; }
  html.dark .hljs-char { color: #c3e88d; }
  html.dark .hljs-subst { color: #a6accd; }
  html.dark .hljs-symbol { color: #82aaff; }
  html.dark .hljs-variable { color: #f07178; }
  html.dark .hljs-template-variable { color: #f07178; }
  html.dark .hljs-link { color: #80cbc4; text-decoration: underline; }
  html.dark .hljs-selector-id { color: #82aaff; }
  html.dark .hljs-selector-class { color: #ffcb6b; }
  html.dark .hljs-selector-attr { color: #c3e88d; }
  html.dark .hljs-selector-pseudo { color: #c792ea; }
  html.dark .hljs-attr { color: #ffcb6b; }
  html.dark .hljs-attribute { color: #c3e88d; }
  html.dark .hljs-name { color: #f07178; }
  html.dark .hljs-tag { color: #f07178; }
  html.dark .hljs-comment { color: #546e7a; font-style: italic; }
  html.dark .hljs-meta { color: #546e7a; }
  html.dark .hljs-meta .hljs-string { color: #c3e88d; }
  html.dark .hljs-section { color: #82aaff; font-weight: bold; }
  html.dark .hljs-title { color: #82aaff; font-weight: bold; }
  html.dark .hljs-title.class_ { color: #ffcb6b; }
  html.dark .hljs-title.function_ { color: #82aaff; }
  html.dark .hljs-params { color: #a6accd; }
  html.dark .hljs-formula { color: #c792ea; }
  html.dark .hljs-deletion { color: #ef5350; background-color: rgba(239,83,80,0.1); }
  html.dark .hljs-addition { color: #66bb6a; background-color: rgba(102,187,106,0.1); }
`;
