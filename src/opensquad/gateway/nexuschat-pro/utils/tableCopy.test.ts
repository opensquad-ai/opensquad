// @vitest-environment jsdom
/**
 * Table copy button: payload, decoration and wiring.
 *
 * The payload and the DOM decoration are real logic, so they are driven for
 * real here (a real rendered table, a real click, a real clipboard call). The
 * remaining rules are invariants that hold the design in place — the button is
 * built by the hook rather than emitted by the renderer, and the table must
 * survive being nested one level deeper to make room for it.
 *
 * Rules:
 *   R1  the copy payload is TSV, header row first, one line per row — a cell
 *       containing a wrap must not split its row;
 *   R2  decoration adds exactly one real <button> per table, inside the
 *       wrapper, and is idempotent (the hook re-runs on every streamed chunk);
 *   R3  the table is moved into a scroll box so the button cannot scroll away,
 *       and the *table styles* must still match at the new depth — a
 *       `> table` selector silently drops width/border-collapse there;
 *   R4  the button sits ABOVE the table's top border, in a band the wrapper's
 *       own margin reserves — it used to land on the last column's header;
 *   R5  the button is never emitted by the markdown renderer and the sanitizer
 *       allowlist is not widened for it: that allowlist is the XSS boundary;
 *   R6  every markdown host that renders agent output wires the hook.
 *
 * Mutations verified: dropping the whitespace collapse in tableToTsv, removing
 * the `is-copy-ready` guard, reverting the CSS to `.ai-table-wrap > table`, and
 * putting the button back at `top: 4px` each fail a rule here.
 */
import fs from 'node:fs';
import path from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  COPY_READY_CLASS,
  COPY_TABLE_CLASS,
  decorateMarkdownTables,
  tableToTsv,
} from '../hooks/useTableCopyButtons';
import { renderFencedMarkdown } from './fencedMarkdown';

const ROOT = path.resolve(__dirname, '..');
const read = (rel: string) => fs.readFileSync(path.join(ROOT, rel), 'utf8');

const MD = `| 日期 | 天气 | 温度 |
| --- | --- | --- |
| 7/25（周六） | 小雨 | 27~33℃ |
| 7/26（周日） | 中雨转小雨 | 26~32℃ |
`;

const writeText = vi.fn().mockResolvedValue(undefined);

let host: HTMLDivElement;

beforeEach(() => {
  vi.useFakeTimers();
  Object.defineProperty(window, 'isSecureContext', { configurable: true, value: true });
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } });
  writeText.mockClear();
  host = document.createElement('div');
  host.className = 'ai-markdown';
  host.innerHTML = renderFencedMarkdown(MD);
  document.body.appendChild(host);
});

afterEach(() => {
  host.remove();
  vi.useRealTimers();
});

describe('R1 — the payload is TSV', () => {
  it('emits a row per line and a cell per tab, header first', () => {
    const table = host.querySelector('table') as HTMLTableElement;
    expect(tableToTsv(table)).toBe(
      '日期\t天气\t温度\n7/25（周六）\t小雨\t27~33℃\n7/26（周日）\t中雨转小雨\t26~32℃',
    );
  });

  it('collapses a wrapped cell onto its own line', () => {
    // 依据原句 cells wrap onto several lines; an un-collapsed newline would
    // split the row and shift every following column.
    const table = document.createElement('table');
    table.innerHTML =
      '<tr><td>甲</td><td>第一行\n第二行</td></tr><tr><td>乙</td><td>丙</td></tr>';
    expect(tableToTsv(table)).toBe('甲\t第一行 第二行\n乙\t丙');
  });

  it('drops whitespace-only cells rather than padding them', () => {
    const table = document.createElement('table');
    table.innerHTML = '<tr><td>  </td><td>值</td></tr>';
    expect(tableToTsv(table)).toBe('\t值');
  });

  it('an empty table yields nothing to copy', () => {
    expect(tableToTsv(document.createElement('table'))).toBe('');
  });
});

describe('R2 — decoration is one real button per table, applied once', () => {
  it('adds a focusable, labelled button inside the wrapper', () => {
    expect(decorateMarkdownTables(host)).toBe(1);
    const wrap = host.querySelector('.ai-table-wrap') as HTMLElement;
    const btn = wrap.querySelector(`:scope > .${COPY_TABLE_CLASS}`) as HTMLButtonElement;
    expect(btn).toBeTruthy();
    expect(btn.tagName).toBe('BUTTON');
    expect(btn.type).toBe('button');
    expect(btn.getAttribute('aria-label')).toBeTruthy();
    expect(wrap.classList.contains(COPY_READY_CLASS)).toBe(true);
  });

  it('re-running on a re-render does not stack buttons', () => {
    decorateMarkdownTables(host);
    expect(decorateMarkdownTables(host)).toBe(0);
    expect(host.querySelectorAll(`.${COPY_TABLE_CLASS}`)).toHaveLength(1);
  });

  it('leaves a body with no table completely alone', () => {
    const plain = document.createElement('div');
    plain.innerHTML = renderFencedMarkdown('nothing but a paragraph');
    expect(decorateMarkdownTables(plain)).toBe(0);
    expect(plain.querySelector(`.${COPY_TABLE_CLASS}`)).toBeNull();
  });

  it('clicking copies the table and reports success', async () => {
    decorateMarkdownTables(host);
    const btn = host.querySelector(`.${COPY_TABLE_CLASS}`) as HTMLButtonElement;
    btn.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    await vi.advanceTimersByTimeAsync(0);
    expect(writeText).toHaveBeenCalledWith(
      '日期\t天气\t温度\n7/25（周六）\t小雨\t27~33℃\n7/26（周日）\t中雨转小雨\t26~32℃',
    );
    expect(btn.classList.contains('is-copied')).toBe(true);

    // …and the confirmation clears itself.
    await vi.advanceTimersByTimeAsync(1300);
    expect(btn.classList.contains('is-copied')).toBe(false);
  });

  it('a failed copy does not claim success', async () => {
    writeText.mockRejectedValueOnce(new Error('denied'));
    decorateMarkdownTables(host);
    const btn = host.querySelector(`.${COPY_TABLE_CLASS}`) as HTMLButtonElement;
    btn.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    await vi.advanceTimersByTimeAsync(0);
    expect(btn.classList.contains('is-copied')).toBe(false);
  });
});

describe('R3 — the table survives being nested for the button', () => {
  it('moves the table into a scroll box inside the wrapper', () => {
    decorateMarkdownTables(host);
    const wrap = host.querySelector('.ai-table-wrap') as HTMLElement;
    const scroller = wrap.querySelector(':scope > .ai-table-scroll');
    expect(scroller).toBeTruthy();
    expect(scroller!.querySelector(':scope > table')).toBeTruthy();
  });

  it('the table styles match at the new depth', () => {
    // The regression this pins: a `> table` selector matched the table as a
    // direct child, so nesting it silently dropped width/border-collapse and
    // the table stopped filling its column.
    const css = read('index.html').replace(/\/\*[\s\S]*?\*\//g, '');
    expect(css).toMatch(/\.ai-markdown \.ai-table-wrap table \{/);
    expect(css).not.toMatch(/\.ai-markdown \.ai-table-wrap > table \{/);
    expect(css).toMatch(/\.ai-table-wrap\.is-copy-ready > \.ai-table-scroll \{[\s\S]{0,80}?overflow-x: auto;/);
  });

  it('the wrapper no longer clips, but the scroller still rounds the corners', () => {
    // The button sits above the top border, so the wrapper must be unclipped —
    // which would leave the table's square corners poking out of the rounded
    // border unless the scroller takes the radius over.
    const css = read('index.html').replace(/\/\*[\s\S]*?\*\//g, '');
    const ready = css.match(/\.ai-table-wrap\.is-copy-ready \{([^}]*)\}/)?.[1] ?? '';
    expect(ready).toMatch(/overflow: visible;/);
    expect(ready).not.toMatch(/overflow: hidden;/);
    expect(css).toMatch(/\.ai-table-wrap\.is-copy-ready > \.ai-table-scroll \{[\s\S]{0,120}?border-radius: inherit;/);
  });
});

describe('R4 — the button sits outside the table, not on it', () => {
  // It used to sit at `top: 4px; right: 4px`, i.e. on top of the last column's
  // header cell ("成分数" in the report that prompted this).
  const css = read('index.html').replace(/\/\*[\s\S]*?\*\//g, '');
  const buttonRule = css.match(/\.ai-table-copy \{([^}]*)\}/)?.[1] ?? '';
  const wrapRule = css.match(/\.ai-markdown \.ai-table-wrap \{([^}]*)\}/)?.[1] ?? '';

  it('anchors the button above the top border', () => {
    expect(buttonRule).toMatch(/top: -\d+px;/);
  });

  it('flushes it with the table’s right edge', () => {
    expect(buttonRule).toMatch(/right: 0;/);
  });

  it('and the band it sits in is reserved by the wrapper’s own margin', () => {
    // Reserved in CSS rather than added by the decorator: the decorator lands a
    // frame after the html, so a JS-added band would shift the page on every
    // streamed chunk.
    const band = Number(wrapRule.match(/margin: ([\d.]+)em/)?.[1] ?? 0) * 16;
    const offset = Number(buttonRule.match(/top: -(\d+)px;/)?.[1] ?? 0);
    const height = Number(buttonRule.match(/height: (\d+)px;/)?.[1] ?? 0);
    // The button's bottom edge must stop ABOVE the border: offset - height > 0.
    // That is the whole point — it no longer covers the header cell.
    expect(offset - height).toBeGreaterThan(0);
    // ...and its top edge must not poke out of the reserved band into the
    // previous block.
    expect(band - offset).toBeGreaterThanOrEqual(2);
  });
});

describe('R5 — the button is chrome, not markdown', () => {
  it('the renderer never emits a button', () => {
    // It could not survive anyway: the sanitizer strips button/svg, and that
    // allowlist is what keeps agent-authored html from reaching the DOM raw.
    const renderer = read('utils/fencedMarkdown.ts');
    expect(renderer).not.toMatch(/<button/);
    expect(renderer).not.toMatch(/ai-table-copy/);
  });

  it('and the sanitizer allowlist was not widened to allow it', () => {
    const safe = read('utils/safeHtml.ts');
    const tags = safe.match(/ALLOWED_TAGS:\s*\[([\s\S]*?)\]/)?.[1] ?? '';
    expect(tags).not.toContain("'button'");
    expect(tags).not.toContain("'svg'");
    expect(safe).not.toMatch(/ALLOWED_ATTR[\s\S]*?'onclick'/);
  });
});

describe('R6 — every agent-output markdown host wires the hook', () => {
  const HOSTS = [
    'components/ai-chat/MessageBubble.tsx',
    'components/ai-chat/StreamingMessage.tsx',
    'components/ai-chat/MarkdownScrollBody.tsx',
    'components/ai-chat/ToolCallBlock.tsx',
  ];

  it.each(HOSTS)('%s decorates its markdown container', (rel) => {
    const src = read(rel);
    expect(src).toMatch(/from '\.\.\/\.\.\/hooks\/useTableCopyButtons'/);
    expect(src).toMatch(/useTableCopyButtons\(/);
    // The hook decorates by ref, so the container it is handed must exist.
    const call = src.match(/useTableCopyButtons\(\s*(\w+)/)?.[1] ?? '';
    expect(call).toBeTruthy();
    expect(src).toContain(`ref={${call}}`);
  });
});
