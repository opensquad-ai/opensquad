/**
 * ToolCallBlock - displays a single tool invocation with args + result.
 *
 * Collapsed: shows only tool name + status icon (one line).
 * Expanded:
 *   - Arguments: formatted JSON
 *   - Result: Markdown rendered (with toggle to raw text)
 *
 * Special handling:
 *   - File edit/write operations → renders FileDiffBlock (GitHub-style diff)
 *
 * tool_call and tool_result are MERGED into one block by AIChatPage,
 * so this component always shows a single unified entry.
 */
import React, { useState, useMemo } from 'react';
import {
  ChevronDown, ChevronRight,
  CheckCircle, XCircle, Loader2,
  Code2, AlignLeft, List,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { marked } from 'marked';
import { FileDiffBlock, extractFileEditInfo } from './FileDiffBlock';

// ---- Markdown renderer (reuses the app-wide prose styles) ----

function renderMarkdown(text: string): string {
  try {
    return marked.parse(text, { breaks: true, async: false }) as string;
  } catch {
    return text;
  }
}

/** Heuristic: does this string look like it has Markdown formatting worth rendering? */
function looksLikeMarkdown(text: string): boolean {
  return /^#{1,6}\s|`|\*\*|\*[^*]|^-\s|\[.+?\]\(.+?\)|^>\s/m.test(text);
}

// ---- Result pane ----

interface ResultPaneProps {
  result: string;
}

const ResultPane: React.FC<ResultPaneProps> = ({ result }) => {
  // Try to parse result as JSON for structured display
  const parsedResult = useMemo(() => {
    try {
      const obj = JSON.parse(result);
      if (obj && typeof obj === 'object' && !Array.isArray(obj)) return obj;
    } catch {}
    return null;
  }, [result]);

  const hasMarkdown = useMemo(() => looksLikeMarkdown(result), [result]);
  const [viewMode, setViewMode] = useState<'structured' | 'md' | 'raw'>(
    parsedResult ? 'structured' : hasMarkdown ? 'md' : 'raw'
  );

  const renderedHtml = useMemo(() => {
    if (viewMode !== 'md') return '';
    return renderMarkdown(result);
  }, [result, viewMode]);

  const btnCls = (active: boolean) =>
    `flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[10px] transition-colors ${
      active ? 'bg-panel text-primary shadow-sm' : 'text-textMuted hover:text-textMain'
    }`;

  return (
    <div>
      {/* Toggle bar */}
      <div className="flex items-center justify-between mb-1">
        <span className="text-[10px] text-textMuted font-medium">Result</span>
        <div className="flex items-center gap-0.5 bg-black/10 rounded-md p-0.5">
          {parsedResult && (
            <button onClick={() => setViewMode('structured')} className={btnCls(viewMode === 'structured')} title="Structured view">
              <List size={10} /> KV
            </button>
          )}
          <button onClick={() => setViewMode('md')} className={btnCls(viewMode === 'md')} title="Markdown view">
            <AlignLeft size={10} /> MD
          </button>
          <button onClick={() => setViewMode('raw')} className={btnCls(viewMode === 'raw')} title="Raw text">
            <Code2 size={10} /> Raw
          </button>
        </div>
      </div>

      {/* Content */}
      {viewMode === 'structured' && parsedResult ? (
        <div className="space-y-1 bg-black/5 rounded px-2.5 py-1.5 max-h-[400px] overflow-y-auto">
          {Object.entries(parsedResult).map(([k, v]) => {
            const valStr = typeof v === 'string' ? v : JSON.stringify(v, null, 2);
            const isLong = typeof valStr === 'string' && (valStr.length > 120 || valStr.includes('\n'));
            return (
              <div key={k} className={isLong ? 'space-y-0.5' : 'flex items-start gap-2'}>
                <span className="text-[10px] font-mono font-semibold text-emerald-600 dark:text-emerald-400 flex-shrink-0 leading-relaxed">{k}</span>
                {isLong ? (
                  <pre className="text-[10px] text-textMuted whitespace-pre-wrap break-all font-mono bg-black/5 rounded px-1.5 py-1 max-h-[160px] overflow-y-auto leading-relaxed">{valStr}</pre>
                ) : (
                  <span className="text-[10px] text-textMuted font-mono break-all leading-relaxed">{valStr}</span>
                )}
              </div>
            );
          })}
        </div>
      ) : viewMode === 'md' ? (
        <div
          className="prose prose-sm prose-invert max-w-none break-words overflow-x-auto ai-markdown
                     text-[12px] leading-relaxed
                     max-h-[400px] overflow-y-auto
                     bg-black/5 rounded px-2.5 py-2"
          dangerouslySetInnerHTML={{ __html: renderedHtml }}
        />
      ) : (
        <pre className="text-[10px] text-textMuted whitespace-pre-wrap break-all max-h-[300px] overflow-y-auto font-mono bg-black/5 rounded px-1.5 py-1">
          {result}
        </pre>
      )}
    </div>
  );
};

// ---- Main component ----

interface ToolCallBlockProps {
  toolName: string;
  args?: any;
  result?: string;
  status?: 'running' | 'success' | 'error';
  /** True when this tool call was made by a sub-agent; shown with purple styling. */
  subAgent?: boolean;
  /** Optional label describing the sub-agent task, shown in the header. */
  subTaskLabel?: string;
  /** Global persistence key for open/close state. */
  persistKey?: string;
}

export const ToolCallBlock: React.FC<ToolCallBlockProps> = ({
  toolName,
  args,
  result,
  status = 'running',
  subAgent = false,
  subTaskLabel,
  persistKey,
}) => {
  const { t } = useTranslation();
  const storageKey = persistKey ? `tool_call_open_${persistKey}` : null;
  const [isOpen, setIsOpen] = useState(false);

  // Restore persisted open/close state when key changes
  React.useEffect(() => {
    if (!storageKey) {
      setIsOpen(false);
      return;
    }
    try {
      const raw = localStorage.getItem(storageKey);
      if (raw === 'true') setIsOpen(true);
      else if (raw === 'false') setIsOpen(false);
      else setIsOpen(false);
    } catch {
      setIsOpen(false);
    }
  }, [storageKey]);

  // Theme colours: amber for normal tool calls, violet for sub-agent calls
  const borderBg      = subAgent ? 'border-violet-500/20 bg-violet-500/5'  : 'border-amber-500/20 bg-amber-500/5';
  const hoverBg       = subAgent ? 'hover:bg-violet-500/10' : 'hover:bg-amber-500/10';
  const dividerBorder = subAgent ? 'border-violet-500/10'   : 'border-amber-500/10';

  // ---- Detect file edit operations ----
  const parsedArgs = useMemo(() => {
    if (!args) return null;
    if (typeof args === 'object') return args;
    try { return JSON.parse(args); } catch { return null; }
  }, [args]);

  const fileEditInfo = useMemo(() => {
    if (!parsedArgs) return null;
    return extractFileEditInfo(toolName, parsedArgs);
  }, [toolName, parsedArgs]);

  // ---- Derived strings (needed before early return) ----
  const argsStr = args
    ? typeof args === 'string' ? args : JSON.stringify(args, null, 2)
    : '';
  const resultStr = result || '';

  const isWebFilePushTool = /send_file_to_web|web\.send_file/i.test(toolName);
  const hasDeliveryWarning = /sent_to\s*=\s*0|not delivered to any connected web client|Open AI Web chat/i.test(resultStr);

  // Delegate to FileDiffBlock for file edit/write ops
  if (fileEditInfo) {
    // Extract a short note from result (first non-empty line, max 120 chars)
    const noteText = resultStr
      ? resultStr.split('\n').map(l => l.trim()).find(l => l.length > 0)?.slice(0, 120)
      : undefined;
    return <FileDiffBlock info={fileEditInfo} status={status} note={noteText} />;
  }

  // ---- Generic tool call rendering ----

  const statusIcon = status === 'success'
    ? <CheckCircle size={12} className="text-emerald-500 flex-shrink-0" />
    : status === 'error'
    ? <XCircle size={12} className="text-red-500 flex-shrink-0" />
    : <Loader2 size={12} className={`${subAgent ? 'text-violet-400' : 'text-amber-500'} animate-spin flex-shrink-0`} />;

  const hasDetails = !!(argsStr || resultStr);

  return (
    <div data-tool-expanded={isOpen || undefined} className={`rounded-md border ${borderBg} overflow-hidden`}>
      {/* Header */}
      <div
        className={`flex items-center gap-1.5 px-2 py-1.5 ${hasDetails ? `cursor-pointer ${hoverBg}` : ''} transition-colors select-none`}
        onClick={() => {
          if (!hasDetails) return;
          const next = !isOpen;
          setIsOpen(next);
          if (storageKey) {
            try { localStorage.setItem(storageKey, String(next)); } catch {}
          }
        }}
      >
        {statusIcon}
        {/* Sub-agent prefix badge */}
        {subAgent && (
          <span className="text-[9px] text-violet-400 font-semibold leading-none flex-shrink-0">&#x21B3;&nbsp;Sub</span>
        )}
        <span className="text-[11px] text-gray-800 dark:text-gray-200 font-mono font-medium truncate flex-1">
          {toolName}
        </span>
        {subAgent && subTaskLabel && (
          <span className="text-[9px] text-violet-400/70 truncate max-w-[120px] flex-shrink-0 ml-1" title={subTaskLabel}>
            {subTaskLabel}
          </span>
        )}
        {hasDetails && (
          isOpen
            ? <ChevronDown size={12} className="text-textMuted flex-shrink-0" />
            : <ChevronRight size={12} className="text-textMuted flex-shrink-0" />
        )}
      </div>

      {/* Expanded: Arguments + Result */}
      {isOpen && (
        <div className={`border-t ${dividerBorder}`}>
          {isWebFilePushTool && hasDeliveryWarning && (
            <div className="mx-2 mt-2 mb-1 rounded-md border border-red-500/30 bg-red-500/10 px-2 py-1.5">
              <div className="text-[10px] text-red-300 font-medium">Delivery warning</div>
              <div className="text-[10px] text-red-200/90 mt-0.5">
                {t('toolCall.deliveryWarning')}
              </div>
            </div>
          )}
          {argsStr && (
            <div className="px-2 py-1.5">
              <div className="text-[10px] text-textMuted font-medium mb-0.5">Arguments</div>
              {parsedArgs && typeof parsedArgs === 'object' && !Array.isArray(parsedArgs) ? (
                <div className="space-y-1 bg-black/5 rounded px-2 py-1.5 max-h-[280px] overflow-y-auto">
                  {Object.entries(parsedArgs).map(([k, v]) => {
                    const valStr = typeof v === 'string' ? v : JSON.stringify(v, null, 2);
                    const isLong = typeof valStr === 'string' && (valStr.length > 120 || valStr.includes('\n'));
                    return (
                      <div key={k} className={isLong ? 'space-y-0.5' : 'flex items-start gap-2'}>
                        <span className="text-[10px] font-mono font-semibold text-amber-600 dark:text-amber-400 flex-shrink-0 leading-relaxed">{k}</span>
                        {isLong ? (
                          <pre className="text-[10px] text-textMuted whitespace-pre-wrap break-all font-mono bg-black/5 rounded px-1.5 py-1 max-h-[160px] overflow-y-auto leading-relaxed">{valStr}</pre>
                        ) : (
                          <span className="text-[10px] text-textMuted font-mono break-all leading-relaxed">{valStr}</span>
                        )}
                      </div>
                    );
                  })}
                </div>
              ) : (
                <pre className="text-[10px] text-textMuted whitespace-pre-wrap break-all max-h-[200px] overflow-y-auto font-mono bg-black/5 rounded px-1.5 py-1">
                  {argsStr}
                </pre>
              )}
            </div>
          )}
          {resultStr && (
            <div className={`px-2 py-1.5 ${argsStr ? `border-t ${dividerBorder}` : ''}`}>
              <ResultPane result={resultStr} />
            </div>
          )}
        </div>
      )}
    </div>
  );
};
