// @vitest-environment jsdom
/**
 * The ghost "Agent" signature.
 *
 * Reported symptom (screenshot): between the collapsed tool-flow headline and
 * the answer body there was a stray line reading exactly
 *
 *     Agent305            ← the name, correctly shown above the activity fold
 *     执行 5 条命令，多媒体处理 1 次 · 1m >
 *     Agent               ← the ghost
 *     我有读图工具，…
 *
 * Cause: the assistant reply that follows a workflow group passed
 * `senderName={undefined}` to suppress the duplicate name — but the bubble
 * renders `{senderName || label}` and `label` falls back to `'Agent'`. Passing
 * "nothing" therefore produced a *generic* signature instead of none.
 *
 * The fix is an explicit `hideSenderLabel` that removes the line outright. It
 * must be threaded from both timelines and it must survive the memo comparator,
 * or a re-render can resurrect the line with stale props.
 *
 * Mutations verified (script `C:/tmp/prov/mutate_sender_label.py`,
 * report `C:/tmp/prov/mutation_report_sender_label.json`):
 *   MS1 MessageBubble renders the line unconditionally (`hideSenderLabel` ignored) → R1/R3
 *   MS2 MessageBubble drops it from `areMessageBubblePropsEqual`               → R2
 *   MS3 AIChatPage stops gating on the previous entry being a workflow         → R4
 *   MS4 SessionChatPane stops gating on the previous entry being a workflow    → R4
 *   MS5 StreamingMessage ignores the flag (live stream keeps the ghost)        → R5
 */
import fs from 'node:fs';
import path from 'node:path';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import { MessageBubble, areMessageBubblePropsEqual, type ChatMessage } from './MessageBubble';
import { StreamingMessage } from './StreamingMessage';
import '../../i18n';

const ROOT = path.resolve(__dirname, '..', '..');
const read = (rel: string) => fs.readFileSync(path.join(ROOT, rel), 'utf8').replace(/\r\n/g, '\n');

const PAGE = read('components/AIChatPage.tsx');
const PANE = read('components/ai-chat/SessionChatPane.tsx');
const BUBBLE = read('components/ai-chat/MessageBubble.tsx');
const STREAM = read('components/ai-chat/StreamingMessage.tsx');

const msg = { role: 'assistant', content: '正文' } as unknown as ChatMessage;

/** The name line renders as `<div class="…">NAME</div>` — match the text node. */
const nameLineShown = (html: string, name: string) => html.includes(`>${name}</`);

describe('助手回复紧跟工作流组时 —— 名字只出现一次', () => {
  it('control: with no senderName the bubble still falls back to "Agent"', () => {
    // This is the trap: "don't duplicate the name" was expressed as `undefined`,
    // which lands on the generic fallback rather than on nothing.
    const html = renderToStaticMarkup(React.createElement(MessageBubble, { message: msg }));
    expect(nameLineShown(html, 'Agent')).toBe(true);
  });

  it('R1 — hideSenderLabel removes the line instead of degrading to "Agent"', () => {
    const html = renderToStaticMarkup(
      React.createElement(MessageBubble, { message: msg, senderName: 'Agent305', hideSenderLabel: true }),
    );
    expect(nameLineShown(html, 'Agent305')).toBe(false);
    expect(nameLineShown(html, 'Agent')).toBe(false);
    // …and the body still renders.
    expect(html).toContain('正文');
  });

  it('R2 — the memo comparator sees the flag (a re-render cannot keep stale props)', () => {
    const base = { message: msg, senderName: 'Agent305' } as const;
    expect(areMessageBubblePropsEqual(base as never, { ...base, hideSenderLabel: true } as never)).toBe(false);
  });

  it('R4 — both timelines gate the flag on the previous entry being a workflow group', () => {
    for (const [label, src] of [['AIChatPage', PAGE], ['SessionChatPane', PANE]] as const) {
      const at = src.indexOf('hideSenderLabel:');
      expect(at, `${label} must pass hideSenderLabel`).toBeGreaterThan(-1);
      // The gate is the invariant: an assistant reply NOT preceded by a workflow
      // group must keep its normal signature.
      const assignment = src.slice(at, at + 260);
      expect(assignment, `${label} must gate on the preceding workflow group`).toContain("kind === 'workflow'");
      expect(assignment, `${label} must gate on the assistant role`).toContain('assistant');
    }
  });

  it('R5 — the live stream honours it too (same ghost, streaming variant)', () => {
    const shown = renderToStaticMarkup(React.createElement(StreamingMessage, { content: '正文', isComplete: false }));
    expect(nameLineShown(shown, 'Agent')).toBe(true);
    const hidden = renderToStaticMarkup(
      React.createElement(StreamingMessage, { content: '正文', isComplete: false, hideSenderLabel: true }),
    );
    expect(nameLineShown(hidden, 'Agent')).toBe(false);
  });

  it('R3 — the line is gated in source, not merely defaulted off', () => {
    expect(BUBBLE).toMatch(/!hideSenderLabel\s*&&/);
    expect(STREAM).toMatch(/!hideSenderLabel\s*&&/);
  });
});
