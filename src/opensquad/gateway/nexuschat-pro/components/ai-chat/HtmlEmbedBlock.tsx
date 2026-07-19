/**
 * HtmlEmbedBlock — sandboxed iframe for visualization.create HTML embeds.
 * Classic Agent Web only; do not mount in Solo unless explicitly enabled.
 */
import React, { useMemo, useState } from 'react';
import { Check, Copy, ExternalLink, Maximize2, Minimize2 } from 'lucide-react';

export interface HtmlEmbedPayload {
  kind: 'html_embed';
  html: string;
  title?: string;
  filename?: string;
  height?: number;
  id?: string;
  ok?: boolean;
}

const MAX_HEIGHT = 1200;
const MIN_HEIGHT = 200;
const DEFAULT_HEIGHT = 480;

/** Detect visualization tool names: visualization / visualization.create / … */
export function isVisualizationToolName(name: string | null | undefined): boolean {
  if (!name) return false;
  const n = name.toLowerCase().replace(/_/g, '.');
  return (
    n === 'visualization' ||
    n === 'visualization.create' ||
    n.startsWith('visualization.') ||
    /(?:^|[.\s])visualization(?:$|[.\s])/.test(n)
  );
}

function tryParseJson(raw: unknown): any | null {
  if (raw == null) return null;
  if (typeof raw === 'object') return raw;
  if (typeof raw !== 'string') return null;
  const s = raw.trim();
  if (!s) return null;
  try {
    return JSON.parse(s);
  } catch {
    // Some results wrap JSON in markdown fences
    const m = s.match(/```(?:json)?\s*([\s\S]*?)```/i);
    if (m) {
      try {
        return JSON.parse(m[1].trim());
      } catch {
        return null;
      }
    }
    return null;
  }
}

/**
 * Extract html_embed payload from tool args and/or result.
 * Prefers result.kind === html_embed; falls back to args.html for visualization tools.
 */
export function extractHtmlEmbed(
  toolName: string | null | undefined,
  args: Record<string, unknown> | string | null | undefined,
  result: string | Record<string, unknown> | null | undefined,
): HtmlEmbedPayload | null {
  const parsedArgs = tryParseJson(args) || (typeof args === 'object' && args ? args : null);
  const parsedResult = tryParseJson(result);

  const fromResult =
    parsedResult &&
    typeof parsedResult === 'object' &&
    (parsedResult.kind === 'html_embed' || parsedResult.ok === true) &&
    typeof parsedResult.html === 'string' &&
    parsedResult.html.trim()
      ? (parsedResult as HtmlEmbedPayload)
      : null;

  if (fromResult?.html) {
    return {
      kind: 'html_embed',
      html: fromResult.html,
      title: typeof fromResult.title === 'string' ? fromResult.title : undefined,
      filename: typeof fromResult.filename === 'string' ? fromResult.filename : undefined,
      height: typeof fromResult.height === 'number' ? fromResult.height : undefined,
      id: typeof fromResult.id === 'string' ? fromResult.id : undefined,
      ok: fromResult.ok !== false,
    };
  }

  // Args fallback when tool is visualization.* and html is in the call
  if (isVisualizationToolName(toolName) && parsedArgs && typeof parsedArgs.html === 'string') {
    const html = parsedArgs.html.trim();
    if (!html) return null;
    return {
      kind: 'html_embed',
      html,
      title: typeof parsedArgs.title === 'string' ? parsedArgs.title : undefined,
      filename: typeof parsedArgs.filename === 'string' ? parsedArgs.filename : undefined,
      height: typeof parsedArgs.height === 'number' ? parsedArgs.height : undefined,
      ok: true,
    };
  }

  return null;
}

function clampHeight(h?: number): number {
  if (typeof h !== 'number' || Number.isNaN(h)) return DEFAULT_HEIGHT;
  return Math.max(MIN_HEIGHT, Math.min(MAX_HEIGHT, Math.round(h)));
}

interface HtmlEmbedBlockProps {
  payload: HtmlEmbedPayload;
  /** Show a compact chrome bar with title / copy / expand */
  className?: string;
}

export const HtmlEmbedBlock: React.FC<HtmlEmbedBlockProps> = ({ payload, className = '' }) => {
  const [expanded, setExpanded] = useState(false);
  const [copied, setCopied] = useState(false);
  const height = clampHeight(payload.height);
  const displayHeight = expanded ? Math.min(MAX_HEIGHT, Math.max(height, 720)) : height;
  const title = payload.title || payload.filename || 'Visualization';

  const srcDoc = useMemo(() => {
    const html = payload.html || '';
    // Ensure charset if missing; keep agent HTML otherwise intact.
    if (/<meta[^>]+charset=/i.test(html) || /<!DOCTYPE/i.test(html)) return html;
    return `<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>${html}</body></html>`;
  }, [payload.html]);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(payload.html);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      /* ignore */
    }
  };

  const openBlank = () => {
    const blob = new Blob([srcDoc], { type: 'text/html;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    window.open(url, '_blank', 'noopener,noreferrer');
    window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
  };

  return (
    <div
      className={`my-3 w-full rounded-xl border border-border/60 bg-white dark:bg-[#1a1a1c] shadow-[0_2px_12px_rgba(0,0,0,0.04)] overflow-hidden ${className}`}
      data-html-embed="1"
    >
      <div className="flex items-center gap-2 px-3 py-2 border-b border-border/50 bg-black/[0.02] dark:bg-white/[0.03]">
        <span className="text-[12px] font-medium text-textMain truncate flex-1 min-w-0">{title}</span>
        <button
          type="button"
          onClick={() => void handleCopy()}
          className="p-1 rounded-md text-textMuted hover:text-textMain hover:bg-black/[0.04] dark:hover:bg-white/[0.06] border-0 bg-transparent cursor-pointer"
          title="Copy HTML"
        >
          {copied ? <Check size={14} /> : <Copy size={14} />}
        </button>
        <button
          type="button"
          onClick={openBlank}
          className="p-1 rounded-md text-textMuted hover:text-textMain hover:bg-black/[0.04] dark:hover:bg-white/[0.06] border-0 bg-transparent cursor-pointer"
          title="Open in new tab"
        >
          <ExternalLink size={14} />
        </button>
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="p-1 rounded-md text-textMuted hover:text-textMain hover:bg-black/[0.04] dark:hover:bg-white/[0.06] border-0 bg-transparent cursor-pointer"
          title={expanded ? 'Collapse' : 'Expand'}
        >
          {expanded ? <Minimize2 size={14} /> : <Maximize2 size={14} />}
        </button>
      </div>
      <iframe
        title={title}
        srcDoc={srcDoc}
        sandbox="allow-scripts allow-forms allow-modals"
        referrerPolicy="no-referrer"
        className="w-full border-0 block bg-white"
        style={{ height: displayHeight }}
      />
    </div>
  );
};
