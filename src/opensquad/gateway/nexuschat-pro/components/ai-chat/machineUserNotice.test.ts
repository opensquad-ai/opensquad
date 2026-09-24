// @vitest-environment jsdom
/**
 * The three machine-generated user turns render as notices, in the chat and
 * inside a running turn's tool fold, live and after a refresh.
 *
 * Rendering is derived from the persisted message content (see
 * `parseMachineUserMessage`), so a refresh needs no extra state — that is why
 * these are static-markup assertions rather than store assertions.
 *
 * Mutations verified (script `C:/tmp/prov/mutate_machine_notice.py`,
 * report `C:/tmp/prov/mutation_report_machine_notice.json`):
 *   MN6 MessageBubble paints the raw marker text again          → R1/R2
 *   MN7 machine-only turns keep the user bubble +「You」        → R3
 *   MN8 SoloActivityRow drops the notice in the fold            → R4
 *   MN9 inline notice loses the notice identity                 → R5
 *   MN10 pending-queue preview stops using the notice label     → R6
 */
import fs from 'node:fs';
import path from 'node:path';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import { MessageBubble, type ChatMessage } from './MessageBubble';
import { MachineUserNotice } from './MachineUserNotice';
import { parseMachineUserMessage } from '../../utils/machineUserMessage';
import '../../i18n';

const ROOT = path.resolve(__dirname, '..', '..');
const read = (rel: string) => fs.readFileSync(path.join(ROOT, rel), 'utf8').replace(/\r\n/g, '\n');
const BUBBLE = read('components/ai-chat/MessageBubble.tsx');
const ROW = read('components/ai-chat/SoloActivityRow.tsx');
const PAGE = read('components/AIChatPage.tsx');

const FORM_WIRE = `[Form submission] 信息填写表单（示例）

\`\`\`json
{
  "姓名": "squadflow",
  "备注": "okk"
}
\`\`\``;
const GROUP_WIRE = '[Messages]\n[开发协作组 | group_id=g-default] ss: @Agent305 在吗'
  + '[Messages received, please decide how to reply based on the source]';
const REMINDER_WIRE = '[Reminder] 检查一下 SquadFlow 的清理进度';

const userMsg = (content: string) => ({ role: 'user', content }) as unknown as ChatMessage;

describe('表单 / 提醒 / 群消息 —— 显示为提示，而不是原文', () => {
  it('R1 — a submitted form renders the 已提交 notice and hides the wire text', () => {
    const html = renderToStaticMarkup(React.createElement(MessageBubble, {
      message: userMsg(FORM_WIRE),
    }));
    expect(html).toContain('已提交');
    expect(html).toContain('信息填写表单（示例）');
    expect(html).toContain('data-machine-notice="submitted"');
    // The wire text and the payload must not be painted.
    expect(html).not.toContain('[Form submission]');
    expect(html).not.toContain('squadflow');
    expect(html).not.toContain('```');
    // …but the payload stays reachable behind the toggle.
    expect(html).toContain('data-machine-notice-toggle="1"');
  });

  it('R2 — reminders and group messages get their own notices', () => {
    const reminder = renderToStaticMarkup(React.createElement(MessageBubble, {
      message: userMsg(REMINDER_WIRE),
    }));
    expect(reminder).toContain('data-machine-notice="reminder"');
    expect(reminder).toContain('检查一下 SquadFlow 的清理进度');
    expect(reminder).not.toContain('[Reminder]');
    // Single line: summary is the whole content, so no toggle.
    expect(reminder).not.toContain('data-machine-notice-toggle="1"');

    const group = renderToStaticMarkup(React.createElement(MessageBubble, {
      message: userMsg(GROUP_WIRE),
    }));
    expect(group).toContain('data-machine-notice="group"');
    expect(group).toContain('开发协作组 · @Agent305 在吗');
    expect(group).not.toContain('[Messages]');
    expect(group).not.toContain('Messages received');
  });

  it('R3 — a machine-only turn is not attributed to the user', () => {
    for (const variant of ['solo', 'classic'] as const) {
      const html = renderToStaticMarkup(React.createElement(MessageBubble, {
        message: userMsg(FORM_WIRE),
        variant,
      }));
      // No user bubble chrome (either layout), no「You」name line above a notice.
      expect(html, variant).not.toContain('bg-chatBubbleSelf');
      expect(html, variant).not.toContain('rounded-br-md');
      expect(html, variant).not.toContain('max-w-[min(85%,36rem)]');
      expect(html, variant).not.toContain('>You</span>');
    }
  });

  it('R4 — inside a running turn the notice rides along in the tool fold', () => {
    // (a) the steer line carries the parsed notice…
    const steerAt = ROW.indexOf("if (evt.type === 'user_steer') {");
    expect(steerAt).toBeGreaterThan(-1);
    const steer = ROW.slice(steerAt, steerAt + 900);
    expect(steer).toContain('parseMachineUserMessage(');
    expect(steer).toContain('machineNotice:');
    // (b) …and its row renders the notice instead of marker text.
    const rowAt = ROW.indexOf("if (line.kind === 'steer') {");
    expect(rowAt).toBeGreaterThan(-1);
    const row = ROW.slice(rowAt, rowAt + 700);
    expect(row).toContain('line.machineNotice');
    expect(row).toContain("variant=\"inline\"");
    // (c) the in-fold surface still renders the same notice component.
    const html = renderToStaticMarkup(React.createElement(MachineUserNotice, {
      notice: parseMachineUserMessage(FORM_WIRE).notice!,
      variant: 'inline',
    }));
    expect(html).toContain('data-machine-notice="submitted"');
    expect(html).not.toContain('[Form submission]');
  });

  it('R5 — the bubble path is the shared parser, not a second format guess', () => {
    expect(BUBBLE).toContain('parseMachineUserMessage(displayContent)');
    expect(BUBBLE).toContain('<MachineUserNotice');
    // A machine-only row must skip the user bubble wrapper (single condition, both
    // the wrapper and the name line) — otherwise the notice is styled as the
    // user's own message again. Both layout variants carry the gate.
    expect(BUBBLE.match(/isUser && !machineOnly/g)?.length).toBe(2);
    expect(BUBBLE).toContain('{!machineOnly && (');
    expect(BUBBLE).toContain('{!hideSenderLabel && !machineOnly && (');
  });

  it('R6 — the queued-message preview labels a machine message', () => {
    // The parse must actually feed the preview — a label without the notice is
    // useless, and a notice without the label renders the JSON again.
    expect(PAGE).toContain('const pmNotice = parseMachineUserMessage(pm.text).notice;');
    expect(PAGE).toContain('machineNoticeSummary(pmNotice)');
    expect(PAGE).toContain('aiChat.machineNotice.');
    // …and it is gated on the parse, so plain queued text keeps its own preview.
    expect(PAGE).toMatch(/pmNotice\s*\n?\s*\?/);
  });

  it('R7 — a normal user message is untouched (regression guard)', () => {
    const solo = renderToStaticMarkup(React.createElement(MessageBubble, {
      message: userMsg('帮我把这段代码整理一下'),
      variant: 'solo',
    }));
    expect(solo).toContain('帮我把这段代码整理一下');
    expect(solo).not.toContain('data-machine-notice');
    // Still the user's own bubble in both layouts.
    expect(solo).toContain('bg-chatBubbleSelf');
    const classic = renderToStaticMarkup(React.createElement(MessageBubble, {
      message: userMsg('帮我把这段代码整理一下'),
      variant: 'classic',
    }));
    expect(classic).toContain('rounded-br-md');
    expect(classic).toContain('max-w-[min(85%,36rem)]');
    expect(classic).not.toContain('data-machine-notice');
  });

  it('R8 — user words plus appended group traffic show both', () => {
    const html = renderToStaticMarkup(React.createElement(MessageBubble, {
      message: userMsg('帮我把群里的报错整理一下\n\n[Simultaneously received group messages]\n[A组 | group_id=g-1] bob: 报错在这'),
    }));
    expect(html).toContain('帮我把群里的报错整理一下');
    expect(html).toContain('data-machine-notice="group"');
    expect(html).not.toContain('[Simultaneously received group messages]');
  });
});
