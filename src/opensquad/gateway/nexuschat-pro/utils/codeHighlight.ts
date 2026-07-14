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

/** Material Palenight — shared by FileDiffBlock + ProjectFilesPanel preview. */
export const HLJS_THEME_CSS = `
  .hljs-keyword { color: #c792ea; }
  .hljs-built_in { color: #82aaff; }
  .hljs-type { color: #ffcb6b; }
  .hljs-literal { color: #ff5874; }
  .hljs-number { color: #f78c6c; }
  .hljs-operator { color: #89ddff; }
  .hljs-punctuation { color: #89ddff; }
  .hljs-property { color: #80cbc4; }
  .hljs-regexp { color: #f07178; }
  .hljs-string { color: #c3e88d; }
  .hljs-char { color: #c3e88d; }
  .hljs-subst { color: #a6accd; }
  .hljs-symbol { color: #82aaff; }
  .hljs-variable { color: #f07178; }
  .hljs-template-variable { color: #f07178; }
  .hljs-link { color: #80cbc4; text-decoration: underline; }
  .hljs-selector-id { color: #82aaff; }
  .hljs-selector-class { color: #ffcb6b; }
  .hljs-selector-attr { color: #c3e88d; }
  .hljs-selector-pseudo { color: #c792ea; }
  .hljs-attr { color: #ffcb6b; }
  .hljs-attribute { color: #c3e88d; }
  .hljs-name { color: #f07178; }
  .hljs-tag { color: #f07178; }
  .hljs-comment { color: #546e7a; font-style: italic; }
  .hljs-meta { color: #546e7a; }
  .hljs-meta .hljs-string { color: #c3e88d; }
  .hljs-section { color: #82aaff; font-weight: bold; }
  .hljs-title { color: #82aaff; font-weight: bold; }
  .hljs-title.class_ { color: #ffcb6b; }
  .hljs-title.function_ { color: #82aaff; }
  .hljs-params { color: #a6accd; }
  .hljs-formula { color: #c792ea; }
  .hljs-deletion { color: #ef5350; background-color: rgba(239,83,80,0.1); }
  .hljs-addition { color: #66bb6a; background-color: rgba(102,187,106,0.1); }
  .hljs-emphasis { font-style: italic; }
  .hljs-strong { font-weight: bold; }
`;
