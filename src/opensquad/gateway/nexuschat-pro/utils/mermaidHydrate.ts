/**
 * Client-side Mermaid hydration for Agent Web markdown.
 * Fenced ```mermaid blocks are emitted as .ai-mermaid[data-src] placeholders;
 * this module turns them into SVG after the HTML is in the DOM.
 */
let _initTheme: string | null = null;
let _seq = 0;

function detectMermaidTheme(): 'dark' | 'default' {
  try {
    const root = document.documentElement;
    const body = document.body;
    const cls = `${root.className} ${body?.className || ''}`;
    if (/\btheme-(dark|midnight|oled|black)\b/i.test(cls)) return 'dark';
    if (/\bdark\b/i.test(cls)) return 'dark';
    const bg = getComputedStyle(body || root).backgroundColor || '';
    const m = bg.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/i);
    if (m) {
      const r = Number(m[1]);
      const g = Number(m[2]);
      const b = Number(m[3]);
      const luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
      if (luminance < 0.45) return 'dark';
    }
  } catch {
    /* ignore */
  }
  return 'default';
}

async function ensureMermaid(theme: 'dark' | 'default') {
  const mermaid = (await import('mermaid')).default;
  if (_initTheme !== theme) {
    mermaid.initialize({
      startOnLoad: false,
      theme,
      securityLevel: 'strict',
      fontFamily: 'ui-sans-serif, system-ui, sans-serif',
    });
    _initTheme = theme;
  }
  return mermaid;
}

function decodeSrc(el: HTMLElement): string {
  const raw = el.getAttribute('data-src') || '';
  if (!raw) return (el.textContent || '').trim();
  try {
    return decodeURIComponent(raw);
  } catch {
    return raw;
  }
}

/**
 * Render all pending `.ai-mermaid` nodes under *root*.
 * Incomplete / invalid diagrams fall back to a code preview.
 */
export async function hydrateMermaidIn(root: HTMLElement | null): Promise<void> {
  if (!root || typeof document === 'undefined') return;
  const nodes = Array.from(
    root.querySelectorAll<HTMLElement>('.ai-mermaid:not([data-rendered])'),
  );
  if (!nodes.length) return;

  const theme = detectMermaidTheme();
  let mermaid: Awaited<ReturnType<typeof ensureMermaid>>;
  try {
    mermaid = await ensureMermaid(theme);
  } catch (err) {
    console.warn('[mermaid] failed to load', err);
    return;
  }

  for (const el of nodes) {
    const code = decodeSrc(el).trim();
    if (!code) {
      el.setAttribute('data-rendered', '1');
      continue;
    }
    // Skip obviously incomplete streaming diagrams
    if (/^```|```$/m.test(code) || code.split('\n').length < 2) {
      el.innerHTML =
        `<pre class="ai-mermaid-fallback"><code>${escapeHtml(code)}</code></pre>`;
      // Do not mark rendered — allow retry when stream completes with fuller source
      continue;
    }

    const id = `mmd-${Date.now()}-${++_seq}`;
    try {
      const { svg } = await mermaid.render(id, code);
      el.innerHTML = svg;
      el.setAttribute('data-rendered', '1');
      el.removeAttribute('data-src');
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      el.innerHTML =
        `<div class="ai-mermaid-error">Mermaid 渲染失败：${escapeHtml(msg)}</div>` +
        `<pre class="ai-mermaid-fallback"><code>${escapeHtml(code)}</code></pre>`;
      el.setAttribute('data-rendered', '1');
    }
  }
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
