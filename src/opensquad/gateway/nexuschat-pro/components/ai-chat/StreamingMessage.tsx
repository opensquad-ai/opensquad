import React, { useEffect, useMemo, useRef, useState } from 'react';
import { AI_MARKDOWN_CLASS, renderFencedMarkdown } from '../../utils/fencedMarkdown';
import { escapeHtml } from '../../utils/safeHtml';
import { useMermaidHydration } from '../../hooks/useMermaidHydration';
import { useTableCopyButtons } from '../../hooks/useTableCopyButtons';
import { FollowScrollBox } from './FollowScrollBox';
import { getLocalAvatarFallback } from '../../utils/image';

interface StreamingMessageProps {
  content: string;
  isComplete?: boolean;
  /** Avatar shown beside the bubble in the chat (messenger) layout. */
  avatarSrc?: string;
  /** classic/solo = document stream; messenger = 气泡（聊天版面）。 */
  variant?: 'classic' | 'solo' | 'messenger';
  senderName?: string;
  /** Suppress the name line entirely (no "Agent" fallback). See MessageBubble. */
  hideSenderLabel?: boolean;
}

/** Render fenced markdown with a safe fallback to escaped raw text. */
function renderMarkdownSafe(raw: string): string {
  try {
    return renderFencedMarkdown(raw);
  } catch {
    // Escape, never return `raw`: this string goes straight into innerHTML.
    return escapeHtml(raw);
  }
}

export const StreamingMessage: React.FC<StreamingMessageProps> = ({
  content,
  isComplete,
  avatarSrc,
  variant = 'classic',
  senderName,
  hideSenderLabel,
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

  // Hydrate mermaid only after the stream is complete — incomplete fences
  // fail mermaid.render on every 100ms flush and spike CPU.
  const mermaidRef = useMermaidHydration(renderedHtml, !!isComplete && !!visibleContent);
  useTableCopyButtons(mermaidRef, renderedHtml);

  if (!visibleContent) return null;

  const body = (
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
  );

  return (
    variant === 'messenger' ? (
      <div className="mb-4 w-full flex gap-2" data-msg-variant="messenger">
        <img
          src={avatarSrc || getLocalAvatarFallback('agent', senderName || 'Agent')}
          alt=""
          className="h-8 w-8 shrink-0 rounded-full object-cover bg-border"
          loading="lazy"
          onError={(e) => {
            const img = e.currentTarget;
            if (img.dataset.fallbackApplied) return;
            img.dataset.fallbackApplied = '1';
            img.src = getLocalAvatarFallback('agent', senderName || 'Agent');
          }}
        />
        <div className="flex flex-col min-w-0 max-w-[min(78%,34rem)] gap-1 items-start">
          {!hideSenderLabel ? (
            <div className="px-1 text-[11px] font-medium text-textMuted/80">{senderName || 'Agent'}</div>
          ) : null}
          <div className="w-full px-3.5 py-2.5 text-[14px] leading-relaxed text-textMain break-words shadow-sm border border-border rounded-2xl rounded-tl-sm bg-chatBubbleOther">
            {isComplete ? (
              body
            ) : (
              <FollowScrollBox
                contentKey={visibleContent.length}
                follow
                className="max-h-[min(40vh,280px)] overflow-y-auto"
              >
                {body}
              </FollowScrollBox>
            )}
          </div>
        </div>
      </div>
    ) : (
    <div className="mb-6 w-full">
      {!hideSenderLabel && (
        <div className="text-[11px] font-medium text-textMuted/70 mb-2">
          {senderName || 'Agent'}
        </div>
      )}
      {/* Cap in-progress stream height so live tool rows above the footer stay on screen. */}
      {isComplete ? (
        body
      ) : (
        <FollowScrollBox
          contentKey={visibleContent.length}
          follow
          className="max-h-[min(40vh,280px)] overflow-y-auto"
        >
          {body}
        </FollowScrollBox>
      )}
    </div>
    )
  );
};
