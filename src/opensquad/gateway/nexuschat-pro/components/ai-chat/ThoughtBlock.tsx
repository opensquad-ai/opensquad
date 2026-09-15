/**
 * ThoughtBlock - displays AI thinking/reasoning process.
 *
 * Shows a collapsible block with thought content, typically inside
 * a WorkflowContainer. Fenced code (```html / ```python / …) renders
 * as highlighted code blocks. While open, the body sticks to the latest line.
 */
import React, { useEffect } from 'react';
import { Brain } from 'lucide-react';
import { Collapse, FoldChevron, useFold } from '../Collapse';
import { MarkdownScrollBody } from './MarkdownScrollBody';

interface ThoughtBlockProps {
  content: string;
  defaultOpen?: boolean;
  /** Called when the user expands/collapses this block. */
  onInspectChange?: (isOpen: boolean) => void;
}

export const ThoughtBlock = React.memo(function ThoughtBlock({ content, defaultOpen = false, onInspectChange }: ThoughtBlockProps) {
  // Fold state shared with every other surface; the Markdown body mounts lazily
  // (thinking streams are long and re-render often) yet the first expand still
  // animates.
  const { open: isOpen, mounted, toggle, setOpen } = useFold(defaultOpen);

  useEffect(() => {
    if (defaultOpen) setOpen(true);
  }, [defaultOpen, setOpen]);

  if (!content) return null;

  const preview = content.length > 100 ? content.slice(0, 100) + '...' : content;

  return (
    <div className="rounded-md border border-border bg-bgLight overflow-hidden">
      <div
        className="flex items-center gap-1.5 px-2 py-1.5 cursor-pointer hover:bg-primary/10 transition-colors"
        aria-expanded={isOpen}
        onClick={() => {
          onInspectChange?.(toggle());
        }}
      >
        <Brain size={12} className="text-textMuted flex-shrink-0" />
        <span className="text-[11px] text-textMain font-medium">Thinking</span>
        {!isOpen && (
          <span className="text-[10px] text-textMuted truncate flex-1 ml-1">{preview}</span>
        )}
        <FoldChevron open={isOpen} className="ml-auto" />
      </div>
      <Collapse open={isOpen}>
        {mounted ? (
          <div className="px-2 py-1.5 border-t border-border">
            <MarkdownScrollBody
              text={content}
              follow
              muted
              maxHeightClass="max-h-[300px]"
            />
          </div>
        ) : null}
      </Collapse>
    </div>
  );
});
