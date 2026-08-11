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
    if (root.classList.contains('dark') || root.dataset.appearance === 'dark') {
      return 'dark';
    }
    const body = document.body;
    const bg =
      getComputedStyle(root).getPropertyValue('--color-bg').trim() ||
      getComputedStyle(body || root).backgroundColor ||
      '';
    if (bg.startsWith('#')) {
      const h = bg.replace('#', '');
      const full = h.length === 3 ? h.split('').map((c) => c + c).join('') : h;
      if (/^[0-9a-fA-F]{6}$/.test(full)) {
        const r = parseInt(full.slice(0, 2), 16);
        const g = parseInt(full.slice(2, 4), 16);
        const b = parseInt(full.slice(4, 6), 16);
        const luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
        if (luminance < 0.45) return 'dark';
      }
    }
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
      el.innerHTML = `<div class="ai-mermaid-svg">${svg}</div>${buildToolbar()}`;
      attachMermaidInteractions(el);
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

/* ------------------------------------------------------------------ */
/* Zoom / pan UI for rendered diagrams                                  */
/* ------------------------------------------------------------------ */

function buildToolbar(): string {
  return (
    `<div class="ai-mermaid-toolbar">` +
    `<button type="button" class="ai-mermaid-tool" data-mmd-action="zoom-out" title="缩小">−</button>` +
    `<span class="ai-mermaid-scale">100%</span>` +
    `<button type="button" class="ai-mermaid-tool" data-mmd-action="zoom-in" title="放大">+</button>` +
    `<button type="button" class="ai-mermaid-tool" data-mmd-action="reset" title="重置为 100%">重置</button>` +
    `<button type="button" class="ai-mermaid-tool" data-mmd-action="open" title="放大查看，支持滚轮缩放与拖拽平移">⛶ 放大</button>` +
    `</div>`
  );
}

/** Natural diagram size from the SVG viewBox (fallback to width/height attrs). */
function svgNaturalSize(svg: SVGSVGElement): { w: number; h: number } {
  const vb = svg.viewBox && svg.viewBox.baseVal;
  if (vb && vb.width > 0 && vb.height > 0) {
    return { w: vb.width, h: vb.height };
  }
  const w = parseFloat(svg.getAttribute('width') || '');
  const h = parseFloat(svg.getAttribute('height') || '');
  return { w: w > 0 ? w : 600, h: h > 0 ? h : 400 };
}

/** Wire the inline box: +/- / reset buttons + click to open the fullscreen viewer. */
function attachMermaidInteractions(box: HTMLElement): void {
  const svg = box.querySelector<SVGSVGElement>('.ai-mermaid-svg svg');
  if (!svg) return;
  const { w, h } = svgNaturalSize(svg);
  const scaleEl = box.querySelector<HTMLElement>('.ai-mermaid-scale');
  let scale = 1;

  const apply = () => {
    svg.style.width = `${Math.round(w * scale)}px`;
    svg.style.height = `${Math.round(h * scale)}px`;
    if (scaleEl) scaleEl.textContent = `${Math.round(scale * 100)}%`;
  };

  box.addEventListener('click', (e) => {
    const btn = (e.target as HTMLElement).closest<HTMLElement>('[data-mmd-action]');
    if (btn) {
      e.stopPropagation();
      const action = btn.getAttribute('data-mmd-action');
      if (action === 'zoom-in') scale = Math.min(5, +(scale * 1.25).toFixed(3));
      else if (action === 'zoom-out') scale = Math.max(0.2, +(scale / 1.25).toFixed(3));
      else if (action === 'reset') scale = 1;
      else if (action === 'open') {
        openMermaidModal(svg, w, h);
        return;
      }
      apply();
      return;
    }
    // Clicking the diagram itself also opens the viewer.
    openMermaidModal(svg, w, h);
  });

  apply();
}

/* ----- Fullscreen viewer (wheel zoom + drag pan) ----- */

let _mmdModal: HTMLElement | null = null;
let _mmdCleanup: (() => void) | null = null;

function closeMermaidModal(): void {
  if (!_mmdModal) return;
  try {
    _mmdCleanup?.();
  } catch {
    /* ignore */
  }
  _mmdModal.remove();
  _mmdModal = null;
  _mmdCleanup = null;
}

function openMermaidModal(src: SVGSVGElement, naturalW: number, naturalH: number): void {
  closeMermaidModal();

  const overlay = document.createElement('div');
  overlay.className = 'ai-mmd-modal';
  overlay.innerHTML =
    `<div class="ai-mmd-toolbar">` +
    `<span class="ai-mmd-title">Mermaid 图</span>` +
    `<button type="button" class="ai-mmd-btn" data-mmd-modal="zoom-out" title="缩小">−</button>` +
    `<span class="ai-mmd-scale">100%</span>` +
    `<button type="button" class="ai-mmd-btn" data-mmd-modal="zoom-in" title="放大">+</button>` +
    `<button type="button" class="ai-mmd-btn" data-mmd-modal="fit" title="适应窗口">适应</button>` +
    `<button type="button" class="ai-mmd-btn" data-mmd-modal="reset" title="重置为 100%">重置</button>` +
    `<button type="button" class="ai-mmd-btn ai-mmd-close" data-mmd-modal="close" title="关闭 (Esc)">×</button>` +
    `</div>` +
    `<div class="ai-mmd-body">${src.outerHTML}</div>`;
  document.body.appendChild(overlay);

  const body = overlay.querySelector<HTMLElement>('.ai-mmd-body')!;
  const svg = body.querySelector<SVGSVGElement>('svg')!;
  const scaleEl = overlay.querySelector<HTMLElement>('.ai-mmd-scale')!;
  const { w, h } = svgNaturalSize(svg);
  let scale = 1;

  const apply = () => {
    svg.style.width = `${Math.round(w * scale)}px`;
    svg.style.height = `${Math.round(h * scale)}px`;
    scaleEl.textContent = `${Math.round(scale * 100)}%`;
  };

  const fit = () => {
    const availW = body.clientWidth - 48;
    const availH = body.clientHeight - 48;
    scale = Math.min(availW / w, availH / h, 4);
    if (!Number.isFinite(scale) || scale <= 0) scale = 1;
    apply();
    // Center the diagram (when smaller than the viewport).
    body.scrollLeft = Math.max(0, (body.scrollWidth - body.clientWidth) / 2);
    body.scrollTop = Math.max(0, (body.scrollHeight - body.clientHeight) / 2);
  };

  const onKey = (ev: KeyboardEvent) => {
    if (ev.key === 'Escape') closeMermaidModal();
  };
  document.addEventListener('keydown', onKey);
  _mmdCleanup = () => document.removeEventListener('keydown', onKey);

  // Wheel zoom
  body.addEventListener(
    'wheel',
    (e) => {
      e.preventDefault();
      const factor = e.deltaY < 0 ? 1.15 : 1 / 1.15;
      scale = Math.min(8, Math.max(0.05, scale * factor));
      apply();
    },
    { passive: false },
  );

  // Drag to pan
  let dragging = false;
  let startX = 0;
  let startY = 0;
  let sl = 0;
  let st = 0;
  body.addEventListener('pointerdown', (e) => {
    dragging = true;
    startX = e.clientX;
    startY = e.clientY;
    sl = body.scrollLeft;
    st = body.scrollTop;
    try {
      body.setPointerCapture(e.pointerId);
    } catch {
      /* ignore */
    }
    body.style.cursor = 'grabbing';
  });
  body.addEventListener('pointermove', (e) => {
    if (!dragging) return;
    body.scrollLeft = sl - (e.clientX - startX);
    body.scrollTop = st - (e.clientY - startY);
  });
  const endDrag = () => {
    dragging = false;
    body.style.cursor = 'grab';
  };
  body.addEventListener('pointerup', endDrag);
  body.addEventListener('pointercancel', endDrag);

  // Toolbar actions + backdrop close
  overlay.addEventListener('click', (e) => {
    const btn = (e.target as HTMLElement).closest<HTMLElement>('[data-mmd-modal]');
    if (btn) {
      const action = btn.getAttribute('data-mmd-modal');
      if (action === 'close') closeMermaidModal();
      else if (action === 'zoom-in') {
        scale = Math.min(8, scale * 1.25);
        apply();
      } else if (action === 'zoom-out') {
        scale = Math.max(0.05, scale / 1.25);
        apply();
      } else if (action === 'fit') fit();
      else if (action === 'reset') {
        scale = 1;
        apply();
      }
      return;
    }
    if (e.target === overlay) closeMermaidModal();
  });

  _mmdModal = overlay;
  fit();
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
