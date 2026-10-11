// @vitest-environment jsdom
/**
 * The inline diagram must enlarge on click — including a click that twitches.
 *
 * `index.css` gives the box `cursor: zoom-in` and the wiring opens the fullscreen viewer
 * on click, but the SVG's labels are selectable/draggable, and a native drag suppresses
 * `click`: the gesture silently became "点图拖拽" instead of "点图放大" (2026-10-10).
 * These pin both the twitch case (no `click` event at all) and a genuine drag.
 *
 * jsdom has no `PointerEvent`, so the pointer sequence is dispatched as mouse events with
 * the pointer type strings — the handler only reads clientX/clientY/button.
 */
import { afterEach, describe, expect, it } from 'vitest';

import { attachMermaidInteractions } from './mermaidHydrate';

function makeBox(): HTMLDivElement {
  const box = document.createElement('div');
  box.className = 'ai-mermaid';
  box.innerHTML =
    '<div class="ai-mermaid-svg">' +
    '<svg viewBox="0 0 200 100"><text>A</text></svg>' +
    '</div>' +
    '<div class="ai-mermaid-toolbar">' +
    '<button type="button" class="ai-mermaid-tool" data-mmd-action="zoom-out">−</button>' +
    '<span class="ai-mermaid-scale">100%</span>' +
    '<button type="button" class="ai-mermaid-tool" data-mmd-action="zoom-in">+</button>' +
    '<button type="button" class="ai-mermaid-tool" data-mmd-action="reset">重置</button>' +
    '<button type="button" class="ai-mermaid-tool" data-mmd-action="open">放大</button>' +
    '</div>';
  document.body.appendChild(box);
  return box;
}

const pointer = (type: string, x: number, y: number) =>
  new MouseEvent(type, { clientX: x, clientY: y, button: 0, bubbles: true, cancelable: true });

const viewer = () => document.querySelector('.ai-mmd-modal');
const svgOf = (box: HTMLElement) => box.querySelector('svg')!;

afterEach(() => {
  // The viewer closes on Esc; that also drops its document-level listener.
  document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
  viewer()?.remove();
  document.body.innerHTML = '';
});

describe('attachMermaidInteractions — clicking the diagram', () => {
  it('opens the viewer for a click that twitched, even when no click event arrives', () => {
    const box = makeBox();
    attachMermaidInteractions(box);
    const svg = svgOf(box);

    svg.dispatchEvent(pointer('pointerdown', 10, 10));
    svg.dispatchEvent(pointer('pointermove', 12, 11)); // 2px: a hand twitch, not a drag
    svg.dispatchEvent(pointer('pointerup', 12, 11));

    expect(viewer()).not.toBeNull();
  });

  it('does not open the viewer for a real drag', () => {
    const box = makeBox();
    attachMermaidInteractions(box);
    const svg = svgOf(box);

    svg.dispatchEvent(pointer('pointerdown', 10, 10));
    svg.dispatchEvent(pointer('pointermove', 70, 45));
    svg.dispatchEvent(pointer('pointerup', 70, 45));

    expect(viewer()).toBeNull();
  });

  it('opens exactly one viewer for a normal click (pointer sequence plus click)', () => {
    const box = makeBox();
    attachMermaidInteractions(box);
    const svg = svgOf(box);

    svg.dispatchEvent(pointer('pointerdown', 10, 10));
    svg.dispatchEvent(pointer('pointerup', 10, 10));
    svg.dispatchEvent(new MouseEvent('click', { clientX: 10, clientY: 10, bubbles: true }));

    expect(document.querySelectorAll('.ai-mmd-modal')).toHaveLength(1);
  });

  it('keeps selection and native drag off the diagram', () => {
    const box = makeBox();
    attachMermaidInteractions(box);
    const svg = svgOf(box);

    expect(svg.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true }))).toBe(false);
    expect(svg.dispatchEvent(new Event('dragstart', { bubbles: true, cancelable: true }))).toBe(false);
  });
});

describe('attachMermaidInteractions — toolbar', () => {
  it('zooms in place without opening the viewer', () => {
    const box = makeBox();
    attachMermaidInteractions(box);

    const plus = box.querySelector<HTMLElement>('[data-mmd-action="zoom-in"]')!;
    plus.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));

    expect(box.querySelector('.ai-mermaid-scale')!.textContent).toBe('125%');
    expect(viewer()).toBeNull();
  });
});
