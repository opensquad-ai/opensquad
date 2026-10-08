import React from 'react';
import type { ModNode } from './modSlotStore';

/**
 * Renders one validated mod element.
 *
 * The whitelist lives in Python (`mods_compat.RENDER_ELEMENTS`) and is applied
 * before the tree ever leaves the agent; this side renders *only* what that
 * whitelist allows, so a hand-crafted frame cannot smuggle in a class name or an
 * event handler. Anything unrecognised renders nothing.
 */

const TEXT_COLOR: Record<string, string> = {
  green: 'text-emerald-400',
  red: 'text-red-400',
  cyan: 'text-cyan-400',
  magenta: 'text-fuchsia-400',
  yellow: 'text-amber-400',
  blue: 'text-sky-400',
  primary: 'text-primary',
  muted: 'text-textMuted',
  text: 'text-textMain',
};

const BORDER: Record<string, string> = {
  round: 'rounded-lg border border-border',
  single: 'rounded border border-border',
  double: 'rounded border-2 border-border',
  bold: 'rounded border-2 border-border',
};

function textClass(props: Record<string, unknown>): string {
  const parts: string[] = [];
  const color = typeof props.color === 'string' ? TEXT_COLOR[props.color] : undefined;
  parts.push(color || 'text-textMain');
  if (props.bold) parts.push('font-semibold');
  if (props.italic) parts.push('italic');
  if (props.underline) parts.push('underline');
  if (props.dimColor) parts.push('opacity-60');
  if (props.inverse) parts.push('bg-textMain/10 rounded px-0.5');
  return parts.join(' ');
}

const wrapClass = (wrap: unknown): string =>
  wrap === 'truncate-end' || wrap === 'truncate'
    ? 'truncate'
    : wrap === 'truncate-start'
      ? 'truncate [direction:rtl] text-left'
      : 'whitespace-pre-wrap break-words';

function boxClass(props: Record<string, unknown>): string {
  const parts = ['flex', props.flexDirection === 'row' ? 'flex-row' : 'flex-col'];
  if (props.flexWrap === 'wrap') parts.push('flex-wrap');
  if (typeof props.alignItems === 'string') {
    const align: Record<string, string> = {
      center: 'items-center',
      'flex-start': 'items-start',
      'flex-end': 'items-end',
      stretch: 'items-stretch',
    };
    if (align[props.alignItems]) parts.push(align[props.alignItems]);
  }
  if (props.flexDirection === 'row' && props.justifyContent === 'space-between') parts.push('justify-between');
  // `gap` wins over the axis-specific ones when both are present.
  const gap = Number(props.gap ?? props.rowGap ?? props.columnGap);
  if (gap > 0) parts.push(`gap-${Math.min(Math.round(gap), 6)}`);
  const px = Number(props.paddingX ?? props.padding);
  if (px > 0) parts.push('px-2');
  const py = Number(props.paddingY ?? props.padding);
  if (py > 0) parts.push('py-1');
  if (Number(props.marginTop) > 0) parts.push('mt-1');
  if (Number(props.marginBottom) > 0) parts.push('mb-1');
  if (typeof props.borderStyle === 'string') parts.push(BORDER[props.borderStyle] || 'border border-border');
  if (props.width === 'full') parts.push('w-full');
  return parts.join(' ');
}

/** Text content of a node, ignoring nested elements. */
function flatText(children: ModNode['children']): string {
  if (!children) return '';
  return children
    .filter((c): c is string => typeof c === 'string')
    .join('');
}

export const ModElement: React.FC<{
  node: ModNode;
  /** Invoked with the host-minted action id. Absent ⇒ the button renders inert. */
  onPress?: (action: string) => void;
}> = ({ node, onPress }) => {
  const props = node.props || {};
  switch (node.type) {
    case 'Text':
      return <span className={`${textClass(props)} ${wrapClass(props.wrap)}`}>{flatText(node.children)}</span>;
    case 'Box':
      return (
        <div className={boxClass(props)}>
          {(node.children || []).map((child, i) =>
            typeof child === 'string' ? (
              <span key={i}>{child}</span>
            ) : (
              // `onPress` must survive nesting, or every button inside a layout
              // box is dead (found by the band test).
              <ModElement key={i} node={child} onPress={onPress} />
            ),
          )}
        </div>
      );
    case 'Button': {
      // The press sends the host-minted id back; the host owns the invocation
      // (铁律 2) and the mod never sees a caller handle.
      const action = typeof props.action === 'string' ? props.action : '';
      const live = Boolean(action) && Boolean(onPress);
      return (
        <button
          type="button"
          disabled={!live}
          onClick={live ? () => onPress!(action) : undefined}
          title={live ? undefined : 'Mod 按钮不可用（缺少回传通道）'}
          className={`shrink-0 px-2 py-0.5 rounded border text-[11px] transition-colors ${
            live
              ? 'border-primary/40 bg-primary/10 text-primary hover:bg-primary/20'
              : 'border-border bg-bgLight text-textMuted cursor-not-allowed'
          }`}
        >
          {typeof props.label === 'string' ? props.label : 'button'}
        </button>
      );
    }
    case 'Code':
      return (
        <pre className="font-mono text-[11px] whitespace-pre-wrap break-words text-textMain">
          {flatText(node.children)}
        </pre>
      );
    case 'Markdown':
      // Rendered as plain text on purpose: no untrusted markdown pipeline runs
      // in the host UI yet. The capability is advisory, and it says so in the
      // matrix (`$.ui.resolve`) rather than pretending to be rich text.
      return <span className="whitespace-pre-wrap break-words text-textMain">{flatText(node.children)}</span>;
    default:
      return null;
  }
};
