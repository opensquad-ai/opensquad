import React, { useEffect, useMemo, useRef, useState } from 'react';
import { AI_MARKDOWN_CLASS, renderFencedMarkdown } from '../../utils/fencedMarkdown';
import { useMermaidHydration } from '../../hooks/useMermaidHydration';

interface StreamingMessageProps {
  content: string;
  isComplete?: boolean;
  /** Kept for API compatibility; classic agent stream is document-style (no avatar). */
  avatarSrc?: string;
  /** Kept for API compatibility; both modes render as document stream. */
  variant?: 'classic' | 'solo';
  senderName?: string;
}

/** Render fenced markdown with a safe fallback to raw text. */
function renderMarkdownSafe(raw: string): string {
  try {
    return renderFencedMarkdown(raw);
  } catch {
    return raw;
  }
}

export const StreamingMessage: React.FC<StreamingMessageProps> = ({
  content,
  isComplete,
  senderName,
}) => {
  const visibleContent = useMemo(() => {
    if (!content) return '';
    return content.replace(/<title>.*?<\/title>/gs, '');
  }, [content]);

  // 流式期间每 chunk 全量重解析 markdown 是 O(n²) 卡顿大头：
  // 首帧立即渲染，后续 chunk 防抖 ~100ms 渲染一次，isComplete 时强制立即渲染最终结果。
  const [renderedHtml, setRenderedHtml] = useState<string>(() =>
    visibleContent ? renderMarkdownSafe(visibleContent) : '',
  );
  const renderedContentRef = useRef(visibleContent);
  const debounceTimerRef = useRef<number | null>(null);

  useEffect(() => {
    if (!visibleContent) return;
    if (isComplete) {
      if (debounceTimerRef.current !== null) {
        window.clearTimeout(debounceTimerRef.current);
        debounceTimerRef.current = null;
      }
      if (renderedContentRef.current !== visibleContent) {
        renderedContentRef.current = visibleContent;
        setRenderedHtml(renderMarkdownSafe(visibleContent));
      }
      return;
    }
    // 首帧（此时还没有任何渲染结果）立即渲染，避免流式开头空白
    if (!renderedHtml) {
      renderedContentRef.current = visibleContent;
      setRenderedHtml(renderMarkdownSafe(visibleContent));
      return;
    }
    if (renderedContentRef.current === visibleContent) return;
    if (debounceTimerRef.current !== null) window.clearTimeout(debounceTimerRef.current);
    debounceTimerRef.current = window.setTimeout(() => {
      debounceTimerRef.current = null;
      if (renderedContentRef.current === visibleContent) return;
      renderedContentRef.current = visibleContent;
      setRenderedHtml(renderMarkdownSafe(visibleContent));
    }, 100);
  }, [visibleContent, isComplete, renderedHtml]);

  useEffect(
    () => () => {
      if (debounceTimerRef.current !== null) window.clearTimeout(debounceTimerRef.current);
    },
    [],
  );

  // Prefer hydrating when the stream is complete (stable fences); still try mid-stream.
  const mermaidRef = useMermaidHydration(renderedHtml, !!visibleContent);

  if (!visibleContent) return null;

  return (
    <div className="mb-6 w-full">
      <div className="text-[11px] font-medium text-textMuted/70 mb-2">
        {senderName || 'Agent'}
      </div>
      <div className="text-[15px] leading-7 text-textMain w-full min-w-0">
        <div
          ref={mermaidRef}
          className={AI_MARKDOWN_CLASS}
          dangerouslySetInnerHTML={{ __html: renderedHtml }}
        />
        {!isComplete && (
          <span className="inline-block w-1.5 h-4 bg-primary/60 animate-pulse ml-0.5 align-middle" />
        )}
      </div>
    </div>
  );
};
