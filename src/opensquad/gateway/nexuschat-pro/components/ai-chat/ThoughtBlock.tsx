/**
 * ThoughtBlock - displays AI thinking/reasoning process.
 *
 * Shows a collapsible block with thought content, typically inside
 * a WorkflowContainer. Fenced code (```html / ```python / …) renders
 * as highlighted code blocks. While open, the body sticks to the latest line.
 */
import React, { useEffect, useState } from 'react';
import { Brain, ChevronDown, ChevronRight } from 'lucide-react';
import { MarkdownScrollBody } from './MarkdownScrollBody';

interface ThoughtBlockProps {
  content: string;
  defaultOpen?: boolean;
  /** Called when the user expands/collapses this block. */
  onInspectChange?: (isOpen: boolean) => void;
}

export const ThoughtBlock: React.FC<ThoughtBlockProps> = ({ content, defaultOpen = false, onInspectChange }) => {
  const [isOpen, setIsOpen] = useState(defaultOpen);

  useEffect(() => {
    if (defaultOpen) setIsOpen(true);
  }, [defaultOpen]);

  if (!content) return null;

  const preview = content.length > 100 ? content.slice(0, 100) + '...' : content;

  return (
    <div className="rounded-md border border-gray-200 bg-white/80 overflow-hidden">
      <div
        className="flex items-center gap-1.5 px-2 py-1.5 cursor-pointer hover:bg-gray-50 transition-colors"
        onClick={() => {
          const next = !isOpen;
          setIsOpen(next);
          onInspectChange?.(next);
        }}
      >
        <Brain size={12} className="text-gray-600 flex-shrink-0" />
        <span className="text-[11px] text-gray-800 font-medium">Thinking</span>
        {!isOpen && (
          <span className="text-[10px] text-textMuted truncate flex-1 ml-1">{preview}</span>
        )}
        {isOpen
          ? <ChevronDown size={12} className="text-gray-500 ml-auto flex-shrink-0" />
          : <ChevronRight size={12} className="text-gray-500 ml-auto flex-shrink-0" />
        }
      </div>
      {isOpen && (
        <div className="px-2 py-1.5 border-t border-gray-200">
          <MarkdownScrollBody
            text={content}
            follow
            muted
            maxHeightClass="max-h-[300px]"
          />
        </div>
      )}
    </div>
  );
};
