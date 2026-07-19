import React, { useMemo } from 'react';
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

export const StreamingMessage: React.FC<StreamingMessageProps> = ({
  content,
  isComplete,
  senderName,
}) => {
  const visibleContent = useMemo(() => {
    if (!content) return '';
    return content.replace(/<title>.*?<\/title>/gs, '');
  }, [content]);

  const renderedHtml = useMemo(() => {
    if (!visibleContent) return '';
    try {
      return renderFencedMarkdown(visibleContent);
    } catch {
      return visibleContent;
    }
  }, [visibleContent]);

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
