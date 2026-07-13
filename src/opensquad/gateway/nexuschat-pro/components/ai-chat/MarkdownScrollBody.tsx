/**
 * Scrollable Markdown body for thought / dialogue text.
 * Renders ```lang fences as highlighted code blocks.
 */
import React, { useMemo } from 'react';
import { FollowScrollBox } from './FollowScrollBox';
import { AI_MARKDOWN_CLASS, renderFencedMarkdown } from '../../utils/fencedMarkdown';

interface MarkdownScrollBodyProps {
  text: string;
  /** Stick to bottom while streaming (thought live updates). */
  follow?: boolean;
  className?: string;
  style?: React.CSSProperties;
  /** Softer text for thought panels */
  muted?: boolean;
  maxHeightClass?: string;
}

export const MarkdownScrollBody: React.FC<MarkdownScrollBodyProps> = ({
  text,
  follow = true,
  className = '',
  style,
  muted = false,
  maxHeightClass = 'max-h-[320px]',
}) => {
  const html = useMemo(() => renderFencedMarkdown(text), [text]);

  return (
    <FollowScrollBox
      contentKey={text.length}
      follow={follow}
      className={`${maxHeightClass} overflow-y-auto ${className}`}
      style={style}
    >
      <div
        className={`${AI_MARKDOWN_CLASS} text-[12px] leading-relaxed ${
          muted ? 'text-textMuted [&_*]:text-inherit' : 'text-textMain'
        }`}
        dangerouslySetInnerHTML={{ __html: html }}
      />
    </FollowScrollBox>
  );
};
