// @vitest-environment jsdom
/**
 * Text selection → 复制文本 / 添加到会话（chat）or 添加到上下文（workspace file）.
 * This file locks the whole selection→composer→send pipeline.
 *
 * The two halves fail in different ways, so they are tested differently:
 *
 *  - the tag round-trip (`serializeUserQuote` ⇄ `formatUserSkillDisplayContent`)
 *    is real logic, so it is driven for real here;
 *  - the rest is wiring spread over four files (menu → pane composer → parked
 *    queue → WS text). Nothing throws when a link goes missing — the chip simply
 *    never appears, or the quote silently stops being sent — so the links are
 *    pinned by scanning the sources, the same way `followupSuggestions.scan`
 *    pins the send funnel.
 *
 * Rules:
 *   R1  the native menu is replaced ONLY for a real selection on a quotable
 *       surface (chat or workspace file), and only after every guard has passed;
 *   R1b a left-button selection does NOT open the menu by itself — acting on a
 *       passage takes a right click, so marking text to read never covers it;
 *   R1c driven for real: a right click over a chat selection and over a file
 *       selection both open it, the file one reading the selection out of its
 *       <textarea>, while a plain right click stays with the browser;
 *   R2  the add action reaches the composer of the pane that was selected in,
 *       falling back to the conversation when that pane holds no composer;
 *   R2b the surfaces mark themselves (`data-quote-source`, plus
 *       `data-quote-path` for a file) and the menu reads the textarea path;
 *   R3  a selection becomes a removable chip, never pasted textarea text;
 *   R4  the chip rides the send as `<user_quote>` — including through the parked
 *       (待发送) queue and the steer path, which build their own payloads;
 *   R5  the tag round-trips to a blockquote, source line included, so the bubble
 *       never shows the tag and the agent still reads the passage.
 *
 * Mutations verified:
 *   MA1 drop the `<user_quote>` branch of the formatter        → R5
 *   MA2 `quotes: [...quotes]` removed from the composer submit → R3
 *   MA3 `quotes: target.quotes` removed from flushPending     → R4
 *   MA4 move `e.preventDefault()` above the readSelection guard → R1
 *   MA5 suppress the native menu without a quotable selection → R1
 *   MA6 read only `window.getSelection()` (drop the textarea path) → R1c, R2b
 *   MA7 drop `data-quote-source` from the file viewer           → R2b
 *   MA8 drop `data-quote-source` from the chat timeline         → R2b
 *   MA9 dismiss on a bubbling mousedown instead of a capture one → R1b
 *   M10 re-add the mouseup auto-open                            → R1b, R1c
 *
 * Written with `React.createElement`: the vitest `include` glob is
 * `**\/*.test.ts`, so this file must not be `.tsx`.
 */
import fs from 'node:fs';
import path from 'node:path';
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { SelectionQuoteMenu, selectionLines } from '../components/ai-chat/SelectionQuoteMenu';
import '../i18n';
import { formatUserSkillDisplayContent, serializeUserQuote } from './aiChatTimeline';

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}

const ROOT = path.resolve(__dirname, '..');
const read = (rel: string) => fs.readFileSync(path.join(ROOT, rel), 'utf8');
/** Source with comments stripped — these rules are about code, not prose. */
const code = (src: string) => src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');

const MENU = read('components/ai-chat/SelectionQuoteMenu.tsx');
const COMPOSER = read('components/ai-chat/AgentWebComposer.tsx');
const PAGE = read('components/AIChatPage.tsx');

/** The `interface PendingMessage { … }` body. */
const pendingMessageBody = (() => {
  const m = code(PAGE).match(/interface PendingMessage \{([\s\S]*?)\n  \}/);
  return m ? m[1] : '';
})();

describe('R5 — the quote tag round-trips', () => {
  it('serializes a trimmed selection as a user_quote block', () => {
    expect(serializeUserQuote('  选中的文本  ')).toBe('<user_quote>\n选中的文本\n</user_quote>');
  });

  it('an empty / whitespace-only selection serializes to nothing', () => {
    // Otherwise a stray right-click could send an empty tag as the whole message.
    expect(serializeUserQuote('   \n\t ')).toBe('');
    expect(serializeUserQuote('')).toBe('');
  });

  it('displays as a blockquote and keeps the user\u2019s own text below it', () => {
    const sent = `${serializeUserQuote('第一行\n第二行')}\n\n这段有问题吗？`;
    expect(formatUserSkillDisplayContent(sent)).toBe('> 第一行\n> 第二行\n\n这段有问题吗？');
  });

  it('a selection sent on its own still reads as a quote', () => {
    expect(formatUserSkillDisplayContent(serializeUserQuote('只有引用'))).toBe('> 只有引用');
  });

  it('marks blank lines inside the quote too', () => {
    // An unmarked blank line would end the blockquote and split the passage.
    expect(formatUserSkillDisplayContent(serializeUserQuote('a\n\nb'))).toBe('> a\n>\n> b');
  });

  it('the skill tag is still collapsed once the quote has been unwrapped', () => {
    const sent = `<user_send_skill>audit</user_send_skill>\n\n${serializeUserQuote('引用')}\n\n干活`;
    const shown = formatUserSkillDisplayContent(sent);
    expect(shown).toContain('/audit');
    expect(shown).toContain('> 引用');
    expect(shown).toContain('干活');
    expect(shown).not.toContain('<user_quote>');
  });

  it('a file selection carries its source as the quote’s first line', () => {
    expect(serializeUserQuote('use strict;', 'bin/opensquad.js:12')).toBe(
      '<user_quote>\nbin/opensquad.js:12\nuse strict;\n</user_quote>',
    );
  });

  it('and that line shows up as the first line of the blockquote', () => {
    expect(formatUserSkillDisplayContent(serializeUserQuote('const a = 1;', 'a/b.js:3-4'))).toBe(
      '> a/b.js:3-4\n> const a = 1;',
    );
  });

  it('a label without a selection is still nothing to send', () => {
    expect(serializeUserQuote('   ', 'a/b.js:1')).toBe('');
  });
});

/** The `readTimelineSelection` body — the one gate both triggers go through. */
const reader = (() => {
  const src = code(MENU);
  const from = src.indexOf('function readSelection(');
  return from < 0 ? '' : src.slice(from, src.indexOf('function anchorToSelection'));
})();

/** The `onContextMenu` body — the only place that may suppress the native menu. */
const contextMenuBody = (() => {
  const src = code(MENU);
  const from = src.indexOf('const onContextMenu');
  return from < 0 ? '' : src.slice(from, src.indexOf("document.addEventListener('contextmenu'"));
})();

describe('R1 — the native menu is replaced only when it should be', () => {
  it('listens for contextmenu and needs a non-collapsed selection', () => {
    expect(code(MENU)).toMatch(/document\.addEventListener\('contextmenu'/);
    expect(reader).toMatch(/sel\.isCollapsed/);
  });

  it('requires the click to land on a timeline', () => {
    expect(reader).toMatch(/target\.closest\(SOURCE_SELECTOR\)/);
    expect(MENU).toMatch(/const SOURCE_SELECTOR = '\[data-quote-source\]'/);
  });

  it('rejects a selection that started outside the timeline', () => {
    expect(reader).toMatch(/source\.contains\(range\.commonAncestorContainer\)/);
  });

  it('only suppresses the browser menu after every guard has passed', () => {
    // preventDefault above the guard would eat the native menu on any
    // right-click, including ones with nothing selected.
    expect(contextMenuBody.indexOf('e.preventDefault()')).toBeGreaterThan(
      contextMenuBody.indexOf('readSelection('),
    );
    expect(contextMenuBody).toMatch(/if \(!hit\) return;/);
  });

  it('delegates to the shared clipboard helper', () => {
    // The custom menu removed the browser's own 复制, so it must give one back.
    // The implementation moved to utils/clipboard.ts (the table copy button
    // shares it); what matters here is that the menu still calls it.
    expect(code(MENU)).toMatch(/from '\.\.\/\.\.\/utils\/clipboard'/);
    expect(code(MENU)).toMatch(/writeClipboard\(menu\.text\)/);
  });

  it('and keeps 复制文本 available where the native 复制 steps aside', () => {
    // A file selection now trades the native menu for this one too, so the
    // replacement has to actually offer the thing it took away.
    expect(code(MENU)).toMatch(/aiChat\.copyText/);
  });

  it('and the helper keeps the fallback for non-secure contexts', () => {
    const clip = code(read('utils/clipboard.ts'));
    expect(clip).toMatch(/navigator\.clipboard\.writeText/);
    expect(clip).toMatch(/document\.execCommand\('copy'\)/);
  });
});

describe('R1b — the menu waits for a right click', () => {
  it('has no mouseup trigger at all', () => {
    // The whole point of R1b: marking a passage with the left button is not a
    // request to act on it, so nothing may open the menu on selection alone.
    expect(code(MENU)).not.toMatch(/addEventListener\('mouseup'/);
    expect(code(MENU)).not.toMatch(/onMouseUp/);
  });

  it('opens at the pointer, not anchored to the selection', () => {
    const src = code(MENU);
    expect(src).toMatch(/e\.clientX \+ MENU_GAP/);
    expect(src).toMatch(/e\.clientY \+ MENU_GAP/);
    // No selection-anchoring machinery left behind to be re-wired by mistake.
    expect(src).not.toMatch(/getClientRects\(\)/);
  });

  it('dismisses on a capture-phase mousedown, so the composer cannot block it', () => {
    // The composer stops mousedown propagation at its root for pane focus, which
    // also stops the native event before a bubble listener on document sees it.
    expect(code(MENU)).toMatch(/document\.addEventListener\('mousedown', onPointerDown, true\)/);
    expect(code(MENU)).not.toMatch(/document\.addEventListener\('mousedown', onPointerDown\)/);
  });
});

describe('R1c — driven for real: a right click opens the menu', () => {
  let host: HTMLDivElement;
  let root: Root;
  let quotes: Array<[string | null, string]>;

  const h = React.createElement;

  beforeEach(() => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    // jsdom has no layout engine, so neither `Range.getClientRects` nor
    // `getBoundingClientRect` exists there. The anchor falls back to the latter,
    // so both are stubbed to an empty measurement: the placement itself is
    // asserted in a real browser, these cases are about *whether* the menu opens.
    (Range.prototype as unknown as { getClientRects: () => DOMRectList }).getClientRects = () =>
      [] as unknown as DOMRectList;
    (Range.prototype as unknown as { getBoundingClientRect: () => DOMRect }).getBoundingClientRect =
      () => ({ x: 0, y: 0, top: 0, left: 0, bottom: 0, right: 0, width: 0, height: 0 }) as DOMRect;
    host = document.createElement('div');
    document.body.appendChild(host);
    root = createRoot(host);
    quotes = [];
    act(() => {
      root.render(
        h(SelectionQuoteMenu, {
          onAddQuote: (paneId: string | null, text: string, label?: string) =>
            quotes.push([paneId, label ? `${label} | ${text}` : text]),
        }),
      );
    });
  });

  afterEach(() => {
    act(() => root.unmount());
    host.remove();
    window.getSelection()?.removeAllRanges();
  });

  /** A timeline inside a pane, nested the way the app nests it. */
  const timeline = (): HTMLParagraphElement => {
    const pane = document.createElement('div');
    pane.setAttribute('data-pane-id', 'pane-1');
    const scroll = document.createElement('div');
    scroll.className = 'os-chat-scroll';
    scroll.setAttribute('data-quote-source', 'chat');
    const para = document.createElement('p');
    para.textContent = '这是一段可以被选中的会话文本';
    scroll.appendChild(para);
    pane.appendChild(scroll);
    host.appendChild(pane);
    return para;
  };

  const select = (para: HTMLParagraphElement, from: number, to: number) => {
    const range = document.createRange();
    range.setStart(para.firstChild as Node, from);
    range.setEnd(para.firstChild as Node, to);
    const sel = window.getSelection() as Selection;
    sel.removeAllRanges();
    sel.addRange(range);
  };

  /** A right click on `target`; returns the event so a case can read `defaultPrevented`. */
  const rightClick = (target: Node, at: { x?: number; y?: number } = {}): MouseEvent => {
    const ev = new MouseEvent('contextmenu', {
      bubbles: true,
      cancelable: true,
      clientX: at.x ?? 40,
      clientY: at.y ?? 40,
    });
    act(() => {
      target.dispatchEvent(ev);
    });
    return ev;
  };

  /** The mouseup that ends a left-button drag — the gesture that must do nothing. */
  const leftMouseUp = (target: Node) =>
    act(() => {
      target.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, button: 0 }));
    });

  const items = () =>
    Array.from(host.querySelectorAll('[role="menuitem"]')).map((n) => n.textContent);

  it('opens on a right click over a chat selection, replacing the native menu', () => {
    const para = timeline();
    select(para, 0, 6);
    const ev = rightClick(para, { x: 120, y: 80 });
    expect(items()).toEqual(['复制文本', '添加到会话']);
    expect(ev.defaultPrevented).toBe(true);
  });

  it('but a left-button selection on its own opens nothing', () => {
    // The bug this locks: the menu used to pop over the passage the reader was
    // merely marking to read.
    const para = timeline();
    select(para, 0, 6);
    leftMouseUp(para);
    expect(items()).toEqual([]);
  });

  it('and a right click with nothing selected keeps the browser menu', () => {
    const ev = rightClick(timeline());
    expect(items()).toEqual([]);
    expect(ev.defaultPrevented).toBe(false);
  });

  it('and not when the selection started outside the timeline', () => {
    const para = timeline();
    const outside = document.createElement('p');
    outside.textContent = '侧栏里的文字';
    host.appendChild(outside);
    const range = document.createRange();
    range.setStart(outside.firstChild as Node, 0);
    range.setEnd(para.firstChild as Node, 3);
    const sel = window.getSelection() as Selection;
    sel.removeAllRanges();
    sel.addRange(range);
    const ev = rightClick(para);
    expect(items()).toEqual([]);
    expect(ev.defaultPrevented).toBe(false);
  });

  it('picking an item closes the menu', () => {
    const para = timeline();
    const text = para.textContent as string;
    select(para, 0, text.length);
    rightClick(para);
    const add = host.querySelectorAll('[role="menuitem"]')[1] as HTMLElement;
    act(() => {
      add.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    expect(quotes).toEqual([['pane-1', text]]);
    expect(items()).toEqual([]);
  });

  /** A workspace file tab: a pane whose text lives in a transparent textarea. */
  const fileEditor = (value: string) => {
    const pane = document.createElement('div');
    pane.setAttribute('data-pane-id', 'pane-file');
    const file = document.createElement('div');
    file.setAttribute('data-quote-source', 'file');
    file.setAttribute('data-quote-path', 'bin/opensquad.js');
    const area = document.createElement('textarea');
    area.value = value;
    file.appendChild(area);
    pane.appendChild(file);
    host.appendChild(pane);
    return area;
  };

  it('a right click over a file selection opens the 添加到上下文 menu', () => {
    const area = fileEditor('#!/usr/bin/env node\nuse strict;\nconst a = 1;\n');
    area.setSelectionRange(19, 31); // "use strict;\n"
    const ev = rightClick(area);
    expect(items()).toEqual(['复制文本', '添加到上下文']);
    // The file editor's own 复制/剪切/粘贴 gives way here, for a selection only:
    // 复制文本 is offered instead, and the keyboard shortcuts still work.
    expect(ev.defaultPrevented).toBe(true);
  });

  it('and the quote carries the file and its line range', () => {
    const area = fileEditor('line one\nline two\nline three\n');
    area.setSelectionRange(0, 17); // through the end of line two
    rightClick(area);
    act(() => {
      (host.querySelectorAll('[role="menuitem"]')[1] as HTMLElement).dispatchEvent(
        new MouseEvent('click', { bubbles: true }),
      );
    });
    expect(quotes).toEqual([['pane-file', 'bin/opensquad.js:1-2 | line one\nline two']]);
  });

  it('a right click in a file with nothing selected keeps the browser menu', () => {
    const area = fileEditor('editable text\n');
    const ev = rightClick(area);
    expect(items()).toEqual([]);
    expect(ev.defaultPrevented).toBe(false);
  });

  it('the composer’s own textarea is not a quotable surface', () => {
    // Otherwise typing a message and selecting in it would offer to quote it.
    const area = document.createElement('textarea');
    area.value = 'draft message';
    host.appendChild(area);
    area.setSelectionRange(0, 5);
    const ev = rightClick(area);
    expect(items()).toEqual([]);
    expect(ev.defaultPrevented).toBe(false);
  });
});

describe('R2 — the add action reaches the right composer', () => {
  it('resolves the pane from the DOM the grid already marks', () => {
    expect(code(MENU)).toMatch(/closest\('\[data-pane-id\]'\)/);
  });

  it('is mounted once for the agent', () => {
    expect(code(PAGE)).toMatch(/<SelectionQuoteMenu[\s\S]{0,200}onAddQuote=/);
  });

  it('attaches through the pane composer handle', () => {
    expect(code(PAGE)).toMatch(/composerApiByPaneRef\.current\.get\(paneId\)/);
    expect(code(PAGE)).toMatch(/addQuote\(text, label\)/);
  });

  it('the handle exposes addQuote', () => {
    expect(code(COMPOSER)).toMatch(/addQuote: \(text: string, label\?: string\) => void;/);
    expect(code(COMPOSER)).toMatch(/useImperativeHandle\([\s\S]{0,220}addQuote,/);
  });

  it('and falls back to the session being chatted in when the pane has no composer', () => {
    // A file tab lives in a pane whose tab is a file, so that pane holds no
    // composer — the quote belongs to the conversation, not to the file tab.
    expect(code(PAGE)).toMatch(/resolveComposerApi\(currentSessionIdRef\.current\)/);
  });
});

describe('R2b — the file surface marks itself quotable', () => {
  const EDITOR = read('components/ai-chat/WorkspaceFileEditor.tsx');
  const TIMELINE = read('components/ai-chat/ChatTimeline.tsx');

  it('the workspace file viewer declares itself a file source, with its path', () => {
    expect(code(EDITOR)).toMatch(/data-quote-source="file"/);
    expect(code(EDITOR)).toMatch(/data-quote-path=\{relPath\}/);
  });

  it('the chat timeline declares itself a chat source', () => {
    expect(code(TIMELINE)).toMatch(/data-quote-source="chat"/);
  });

  it('the source editor’s selection is read from its textarea', () => {
    // The real selection there lives in a transparent <textarea>; window
    // .getSelection() is empty, so the quote would never see it.
    expect(reader).toMatch(/target\.closest\('textarea'\)/);
    expect(reader).toMatch(/const \{ selectionStart, selectionEnd, value \}/);
  });

  it('and the label names the lines the selection spans', () => {
    expect(selectionLines('a\nb\nc', 0, 3)).toEqual([1, 2]);
    expect(selectionLines('a\nb\nc', 2, 3)).toEqual([2, 2]);
    expect(selectionLines('a\nb\nc', 0, 5)).toEqual([1, 3]);
    // The surface decides the label; the reader only supplies the lines.
    expect(reader).toMatch(/sourceLabel\(areaSource, selectionLines\(value, selectionStart, selectionEnd\)\)/);
  });
});

describe('R3 — a selection is a chip, not pasted text', () => {
  it('keeps its own state beside images and attachments', () => {
    expect(code(COMPOSER)).toMatch(/const \[quotes, setQuotes\] = useState<ComposerQuote\[\]>\(\[\]\)/);
  });

  it('renders one chip per selection, with a remove control', () => {
    expect(code(COMPOSER)).toMatch(/\{quotes\.map\(/);
    expect(code(COMPOSER)).toMatch(/setQuotes\(\(prev\) => prev\.filter\(\(_, idx\) => idx !== i\)\)/);
  });

  it('shows the selection itself, not just a label', () => {
    expect(code(COMPOSER)).toMatch(/\{quotePreview\(quote\.text\)\}/);
  });

  it('and names the file when the selection came from one', () => {
    expect(code(COMPOSER)).toMatch(/\{quote\.label \|\| t\('aiChat\.selectedText'/);
  });

  it('attaching the same passage twice does not stack chips', () => {
    expect(code(COMPOSER)).toMatch(/prev\.some\(\(q\) => q\.text === body && q\.label === source\)/);
  });

  it('the chip list mounts with the other attachment chrome', () => {
    expect(code(COMPOSER)).toMatch(/quotes\.length > 0 \|\| isUploading/);
  });

  it('sends the chips and clears them with the rest of the composer', () => {
    expect(code(COMPOSER)).toMatch(/quotes: \[\.\.\.quotes\]/);
    // A selection-only message is a valid send (the quote is the body).
    expect(code(COMPOSER)).toMatch(/quotes\.length === 0 && !pendingSkill/);
    expect(code(COMPOSER)).toMatch(/setQuotes\(\[\]\)/);
  });
});

describe('R4 — the quote survives every send path', () => {
  it('is serialized into the agent-facing text, not the display text', () => {
    expect(code(PAGE)).toMatch(/serializeUserQuote\(q\.text, q\.label\)/);
    expect(code(PAGE)).toMatch(/wsText = wsText \? `\$\{quoteBlock\}\\n\\n\$\{wsText\}` : quoteBlock;/);
  });

  it('the bubble shows it as a quote rather than dropping it', () => {
    expect(code(PAGE)).toMatch(/formatUserSkillDisplayContent\(quoteBlock \? `\$\{quoteBlock\}\\n\\n\$\{text\}` : text\)/);
  });

  it('the parked-message record carries the chips', () => {
    expect(pendingMessageBody).toMatch(/quotes\?: ComposerQuote\[\]/);
    expect(code(PAGE)).toMatch(/quotes: payload\.quotes/);
  });

  it('and every replay of a parked message re-sends them', () => {
    // flushPendingMessage (queue drain) and steerPendingSnapshot (引导注入) each
    // build their own payload — a quote dropped here is dropped silently.
    expect(code(PAGE)).toMatch(/quotes: target\.quotes/);
    expect(code(PAGE)).toMatch(/quotes: snapshot\.quotes/);
  });
});
