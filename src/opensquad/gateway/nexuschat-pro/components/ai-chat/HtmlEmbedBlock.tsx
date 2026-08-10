/**
 * HtmlEmbedBlock — sandboxed iframe for visualization.create HTML embeds.
 * Classic Agent Web: tool call stays in the activity stream; the interactive
 * iframe is rendered below the assistant's final reply (not inside the fold).
 */
import React, { useMemo, useState } from 'react';
import { Check, Copy, ExternalLink, Maximize2, Minimize2 } from 'lucide-react';
import type { TimelineEntry, WorkflowEvent } from '../../utils/aiChatTimeline';

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

function embedDedupeKey(embed: HtmlEmbedPayload): string {
  if (embed.id) return `id:${embed.id}`;
  if (embed.filename) return `file:${embed.filename}`;
  return `html:${embed.html.slice(0, 96)}`;
}

/** Collect html_embed payloads from workflow tool_call events (with results). */
export function collectHtmlEmbedsFromEvents(events: WorkflowEvent[] | null | undefined): HtmlEmbedPayload[] {
  if (!events?.length) return [];
  const out: HtmlEmbedPayload[] = [];
  const seen = new Set<string>();
  for (const evt of events) {
    if (evt.type !== 'tool_call') continue;
    const data = typeof evt.content === 'object' && evt.content ? evt.content : {};
    const name = String((data as { name?: string; tool?: string }).name || (data as { tool?: string }).tool || '');
    const args =
      (data as { arguments?: unknown; args?: unknown }).arguments ??
      (data as { args?: unknown }).args ??
      data;
    const embed = extractHtmlEmbed(name, args as Record<string, unknown> | string | null, evt.result);
    if (!embed?.html) continue;
    const key = embedDedupeKey(embed);
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(embed);
  }
  return out;
}

/**
 * Walk timeline entries before `messageIndex` (until the previous user turn)
 * and collect visualization embeds from intervening workflow blocks.
 */
export function collectHtmlEmbedsPrecedingMessage(
  entries: TimelineEntry[],
  messageIndex: number,
): HtmlEmbedPayload[] {
  if (!entries.length || messageIndex <= 0) return [];
  const collected: HtmlEmbedPayload[] = [];
  const seen = new Set<string>();
  for (let i = messageIndex - 1; i >= 0; i--) {
    const entry = entries[i];
    if (entry.kind === 'message' && entry.data.role === 'user') break;
    if (entry.kind === 'workflow') {
      const embeds = collectHtmlEmbedsFromEvents(entry.data.events);
      // Scan backwards → prepend so chronological order is preserved.
      for (let j = embeds.length - 1; j >= 0; j--) {
        const emb = embeds[j];
        const key = embedDedupeKey(emb);
        if (seen.has(key)) continue;
        seen.add(key);
        collected.unshift(emb);
      }
    }
  }
  return collected;
}

function clampHeight(h?: number): number {
  if (typeof h !== 'number' || Number.isNaN(h)) return DEFAULT_HEIGHT;
  return Math.max(MIN_HEIGHT, Math.min(MAX_HEIGHT, Math.round(h)));
}

interface HtmlEmbedBlockProps {
  payload: HtmlEmbedPayload;
  /**
   * `chrome` — bordered card with title / copy / expand (legacy widget look).
   * `seamless` — no host chrome; flows like agent text in the chat stream.
   */
  variant?: 'chrome' | 'seamless';
  className?: string;
}

export const HtmlEmbedBlock: React.FC<HtmlEmbedBlockProps> = ({
  payload,
  variant = 'chrome',
  className = '',
}) => {
  const [expanded, setExpanded] = useState(false);
  const [copied, setCopied] = useState(false);
  const seamless = variant === 'seamless';
  const height = clampHeight(payload.height);
  const displayHeight = expanded
    ? Math.min(MAX_HEIGHT, Math.max(height, seamless ? 900 : 720))
    : height;
  const title = payload.title || payload.filename || 'Visualization';

  const srcDoc = useMemo(() => {
    const html = payload.html || '';
    // Ensure charset if missing; keep agent HTML otherwise intact.
    let doc =
      /<meta[^>]+charset=/i.test(html) || /<!DOCTYPE/i.test(html)
        ? html
        : `<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>${html}</body></html>`;
    if (seamless) {
      // Only collapse host margins — do NOT force transparent backgrounds.
      // Viz pages often use dark themes + white text; wiping body bg makes them unreadable.
      const seamlessCss =
        '<style data-opensquad-seamless="1">' +
        'html,body{margin:0;padding:0;}' +
        '</style>';
      if (/<\/head>/i.test(doc)) {
        doc = doc.replace(/<\/head>/i, `${seamlessCss}</head>`);
      } else if (/<body[^>]*>/i.test(doc)) {
        doc = doc.replace(/<body([^>]*)>/i, `<head>${seamlessCss}</head><body$1>`);
      } else {
        doc = `<!DOCTYPE html><html><head><meta charset="utf-8">${seamlessCss}</head><body>${doc}</body></html>`;
      }
    }
    return doc;
  }, [payload.html, seamless]);

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

  if (seamless) {
    return (
      <div
        className={`w-full my-2 overflow-hidden border-0 shadow-none rounded-none ${className}`}
        data-html-embed="1"
        data-html-embed-variant="seamless"
      >
        <iframe
          title={title}
          srcDoc={srcDoc}
          sandbox="allow-scripts allow-forms allow-modals"
          referrerPolicy="no-referrer"
          className="w-full border-0 block"
          style={{ height: displayHeight }}
        />
      </div>
    );
  }

  return (
    <div
      className={`my-3 w-full rounded-xl border border-border/60 bg-bgLight shadow-[0_2px_12px_rgba(0,0,0,0.04)] overflow-hidden ${className}`}
      data-html-embed="1"
      data-html-embed-variant="chrome"
    >
      <div className="flex items-center gap-2 px-3 py-2 border-b border-border/50 bg-bgLight">
        <span className="text-[12px] font-medium text-textMain truncate flex-1 min-w-0">{title}</span>
        <button
          type="button"
          onClick={() => void handleCopy()}
          className="p-1 rounded-md text-textMuted hover:text-textMain hover:bg-primary/10 border-0 bg-transparent cursor-pointer"
          title="Copy HTML"
        >
          {copied ? <Check size={14} /> : <Copy size={14} />}
        </button>
        <button
          type="button"
          onClick={openBlank}
          className="p-1 rounded-md text-textMuted hover:text-textMain hover:bg-primary/10 border-0 bg-transparent cursor-pointer"
          title="Open in new tab"
        >
          <ExternalLink size={14} />
        </button>
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="p-1 rounded-md text-textMuted hover:text-textMain hover:bg-primary/10 border-0 bg-transparent cursor-pointer"
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
        className="w-full border-0 block bg-bgLight"
        style={{ height: displayHeight }}
      />
    </div>
  );
};
