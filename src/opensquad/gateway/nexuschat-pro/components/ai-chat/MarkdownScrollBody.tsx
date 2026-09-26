/**
 * Scrollable Markdown body for thought / dialogue text.
 * Renders ```lang fences as highlighted code blocks.
 */
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { FollowScrollBox } from './FollowScrollBox';
import { useTableCopyButtons } from '../../hooks/useTableCopyButtons';
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
  /**
   * Live thought only: give the body a "tail" — the newest lines are still
   * settling and solidify as the follow-scroll pushes them up. Off by default,
   * so a finished body is always rendered at full weight for reading.
   */
  softEdge?: boolean;
}

export const MarkdownScrollBody: React.FC<MarkdownScrollBodyProps> = ({
  text,
  follow = true,
  className = '',
  style,
  muted = false,
  maxHeightClass = 'max-h-[320px]',
  softEdge = false,
}) => {
  const html = useMemo(() => renderFencedMarkdown(text), [text]);
  const htmlRef = useRef<HTMLDivElement>(null);
  useTableCopyButtons(htmlRef, html);

  // Reading mode: while the reader is up in the text the tail is dropped, so
  // nothing is ever dimmed under their eyes mid-sentence. Re-armed every time a
  // live body appears (a new streaming thought starts pinned to the bottom).
  const [stuck, setStuck] = useState(true);
  useEffect(() => {
    if (softEdge) setStuck(true);
  }, [softEdge]);

  // "Drift" upgrade: once the body is long enough to actually scroll, the
  // bottom-only tail becomes a both-edge mask — older lines fade out at the
  // top while the newest settle at the bottom (fleeting-thought effect).
  const [overflowing, setOverflowing] = useState(false);

  const tail = softEdge && stuck;
  const drift = tail && overflowing;

  return (
    <FollowScrollBox
      contentKey={text.length}
      follow={follow}
      onStickChange={softEdge ? setStuck : undefined}
      onOverflowChange={softEdge ? setOverflowing : undefined}
      className={`${maxHeightClass} overflow-y-auto [scrollbar-gutter:stable] ${
        drift ? 'os-thought-drift' : tail ? 'os-thought-tail' : ''
      } ${
        softEdge ? 'os-thought-settle' : ''
      } ${className}`}
      style={style}
    >
      <div
        ref={htmlRef}
        className={`${AI_MARKDOWN_CLASS} text-[12px] leading-relaxed ${
          muted ? 'text-textMuted [&_*]:text-inherit' : 'text-textMain'
        }`}
        dangerouslySetInnerHTML={{ __html: html }}
      />
    </FollowScrollBox>
  );
};
