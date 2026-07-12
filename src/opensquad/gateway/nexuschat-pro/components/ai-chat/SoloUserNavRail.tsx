/**
 * SoloUserNavRail — Cursor-style right-edge markers for user messages.
 * Default: thin dashes. Hover: preview + dash. Latest: theme primary tint.
 * Click scrolls the matching message into view.
 *
 * Note: theme colors are CSS variables; Tailwind opacity modifiers like
 * bg-textMuted/35 often do not apply — use color-mix inline styles instead.
 */
import React, { useState } from 'react';

export interface SoloUserNavNode {
  id: string;
  preview: string;
}

interface SoloUserNavRailProps {
  nodes: SoloUserNavNode[];
  /** Highlighted node — typically the latest user message */
  activeId?: string;
  onJump: (id: string) => void;
}

export function previewUserMessage(content: string, maxLen = 18): string {
  const flat = content
    .replace(/\r\n/g, '\n')
    .replace(/\[File:[^\]]*\](?:\([^)]*\))?/g, '')
    .replace(/!\[[^\]]*\]\([^)]*\)/g, '')
    .replace(/\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/[#>*_`~]/g, '')
    .replace(/\s+/g, ' ')
    .trim();
  if (!flat) return '…';
  if (flat.length <= maxLen) return flat;
  return `${flat.slice(0, maxLen)}…`;
}

const mutedDash = 'color-mix(in srgb, var(--color-text-muted) 45%, transparent)';
const primaryDash = 'color-mix(in srgb, var(--color-primary) 75%, transparent)';
const primaryDashSoft = 'color-mix(in srgb, var(--color-primary) 50%, transparent)';
const mutedLabel = 'color-mix(in srgb, var(--color-text-muted) 70%, transparent)';

export const SoloUserNavRail: React.FC<SoloUserNavRailProps> = ({
  nodes,
  activeId,
  onJump,
}) => {
  const [hoveredId, setHoveredId] = useState<string | null>(null);

  if (!nodes.length) return null;

  const expanded = hoveredId != null;

  return (
    <nav
      className={`flex flex-col items-end gap-3 py-3 max-h-[70vh] overflow-y-auto transition-all duration-150 ${
        expanded
          ? 'px-3 rounded-2xl border shadow-[0_4px_20px_rgba(0,0,0,0.08)]'
          : 'px-1.5'
      }`}
      style={
        expanded
          ? {
              backgroundColor:
                'color-mix(in srgb, var(--color-primary) 12%, var(--color-bg-light, #fff))',
              borderColor: 'color-mix(in srgb, var(--color-primary) 28%, transparent)',
            }
          : undefined
      }
      aria-label="User message navigation"
      onMouseLeave={() => setHoveredId(null)}
    >
      {nodes.map((node) => {
        const isActive = node.id === activeId;
        const isHovered = node.id === hoveredId;
        const showLabel = expanded;

        return (
          <button
            key={node.id}
            type="button"
            onClick={() => onJump(node.id)}
            onMouseEnter={() => setHoveredId(node.id)}
            className="group flex items-center justify-end gap-2.5 max-w-[220px] bg-transparent border-0 p-0 cursor-pointer"
            title={node.preview}
          >
            {showLabel && (
              <span
                className="text-[11px] leading-none truncate text-right"
                style={{ color: isActive || isHovered ? 'var(--color-primary)' : mutedLabel }}
              >
                {node.preview}
              </span>
            )}
            <span
              className="block rounded-full shrink-0 transition-all duration-150"
              style={{
                width: isActive ? 16 : isHovered ? 14 : 12,
                height: isActive ? 3.5 : 2.5,
                backgroundColor: isActive
                  ? primaryDash
                  : isHovered
                    ? primaryDashSoft
                    : mutedDash,
              }}
            />
          </button>
        );
      })}
    </nav>
  );
};
