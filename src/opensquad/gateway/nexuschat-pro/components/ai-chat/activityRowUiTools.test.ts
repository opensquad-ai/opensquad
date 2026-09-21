// @vitest-environment jsdom
/**
 * Tool-flow stats must not count *UI-only* interactions.
 *
 * Reported symptom (screenshot): the collapsed activity headline read
 * "…，向用户确认 1 次 · 1m" for a turn whose last act was
 * `followup_tools__suggest_followups`. The offer already renders as tappable
 * chips at the tail of the answer — the headline was a second, louder
 * statement of the same thing, and it made the fold look like work was done.
 *
 * The three namespaces in the `interaction` bucket (`choice_tools`,
 * `followup_tools`, `agent_mode`) all have a dedicated front-end card, so none
 * of them is "work" and none may appear in the headline or in the expanded
 * step list.
 *
 * This renders the real component with `renderToStaticMarkup` — the summary is
 * built from the *filtered* line list, so only a render proves the two agree.
 *
 * Mutations verified (script `C:/tmp/prov/mutate_activity_ui_tools.py`,
 * report `C:/tmp/prov/mutation_report_activity_ui_tools.json`):
 *   MA1 drop the `isUiOnlyTool` guard in `summarizeWorkTools`   → headline returns
 *   MA2 drop the `isUiOnlyTool` guard in `buildLines`           → step list returns
 *   MA3 widen `isUiOnlyTool` to every tool (over-filter)        → control case dies
 *   MA4 narrow `classifyWorkTool`'s interaction set to `choice_tools` only → MA1/MA2 paths re-open
 */
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import { SoloActivityRow, buildLines } from './SoloActivityRow';
import type { WorkflowBlock, WorkflowEvent } from '../../utils/aiChatTimeline';
import i18n from '../../i18n';

/** A real, countable tool (filesystem__read_file → 读取 N 个文件). */
const READ_TOOL = 'filesystem__read_file';
/** A pure UI tool — its real UI is the follow-up chip row, not a step. */
const FOLLOWUP_TOOL = 'followup_tools__suggest_followups';

let seq = 0;

function toolCall(name: string): WorkflowEvent {
  seq += 1;
  return {
    _uid: `c${seq}`,
    type: 'tool_call',
    content: { name, call_id: `call-${seq}`, args: { path: 'a.txt' } },
    timestamp: 1_700_000_000_000 + seq,
  };
}

function toolResult(name: string): WorkflowEvent {
  seq += 1;
  return {
    _uid: `r${seq}`,
    type: 'tool_result',
    content: { name, call_id: `call-${seq}` },
    result: 'ok',
    resultStatus: 'success',
    timestamp: 1_700_000_000_000 + seq,
  };
}

function block(events: WorkflowEvent[]): WorkflowBlock {
  return { events, status: null, completed: true, started_ms: 1_700_000_000_000 };
}

const READ_ONLY = () => [toolCall(READ_TOOL), toolResult(READ_TOOL)];
const FOLLOWUP_ONLY = () => [toolCall(FOLLOWUP_TOOL), toolResult(FOLLOWUP_TOOL)];
const MIXED = () => [...READ_ONLY(), ...FOLLOWUP_ONLY()];

/** Render the row so the headline (built from the filtered lines) is asserted. */
function render(b: WorkflowBlock): string {
  return renderToStaticMarkup(
    React.createElement(SoloActivityRow, {
      block: b,
      uiMode: 'classic',
      embedVisualizations: false,
      turnDelivered: true,
    }),
  );
}

/** Non-vacuous step-list check: the fold is closed by default, so the expanded
 *  list never reaches the markup — assert the list the row is built from. */
const stepCount = (b: WorkflowBlock) =>
  buildLines(b, {}, i18n.t.bind(i18n)).filter((l) => l.kind === 'tool').length;

describe('工具流 — 纯 UI 交互工具既不进统计，也不占步骤行', () => {
  it('control: a real tool is counted and rendered', () => {
    const html = render(block(READ_ONLY()));
    expect(stepCount(block(READ_ONLY()))).toBe(2);
    expect(html).toContain('读取 1 个文件');
    expect(html).not.toContain('向用户确认');
  });

  it('R1 — a follow-up call adds no headline entry and no step', () => {
    const html = render(block(MIXED()));
    // The real work is still reported, at the same count as without the UI call.
    expect(html).toContain('读取 1 个文件');
    expect(stepCount(block(MIXED()))).toBe(stepCount(block(READ_ONLY())));
    // … and the offer is not re-stated as if it were a tool.
    expect(html).not.toContain('向用户确认');
    expect(html).not.toContain('追问建议');
    expect(html).not.toContain('suggest_followups');
  });

  it('R2 — a turn that only offered follow-ups renders no activity row at all', () => {
    // Not a lone header with an empty body: an activity fold with nothing in it
    // is exactly the "blank Activity row" this component already refuses to draw.
    expect(stepCount(block(FOLLOWUP_ONLY()))).toBe(0);
    expect(render(block(FOLLOWUP_ONLY()))).toBe('');
  });

  it('R3 — every UI namespace is exempt, not just the follow-up one', () => {
    const uiNames = ['choice_tools__ask_user', 'followup_tools__suggest_followups', 'agent_mode__set_mode'];
    for (const name of uiNames) {
      const evs = [...READ_ONLY(), toolCall(name), toolResult(name)];
      expect(stepCount(block(evs))).toBe(2);
      expect(render(block(evs))).not.toContain('向用户确认');
    }
  });

  it('R4 — 系统控制工具（等待 / 定时提醒）有正式名称与归类，不再以裸名充当"其他工具"', () => {
    const evs = [
      ...READ_ONLY(),
      toolCall('system.wait'), toolResult('system.wait'),
      toolCall('reminder.set'), toolResult('reminder.set'),
    ];
    // 统计标题进入"系统控制"分类，而不是裸名/其他工具。
    expect(render(block(evs))).toContain('系统控制 2 次');
    // 行标签来自 toolFlow.fn / toolFlow.ns 映射，而非原始工具名。
    const labels = buildLines(block(evs), {}, i18n.t.bind(i18n))
      .filter((l) => l.kind === 'tool')
      .map((l) => l.primary);
    expect(labels).toContain('等待');
    expect(labels).toContain('定时提醒');
    expect(labels).not.toContain('wait');
    expect(labels).not.toContain('set');
    // 系统控制是真实动作：计入统计（不像纯 UI 工具被过滤）。
    expect(stepCount(block(evs))).toBe(stepCount(block(READ_ONLY())) + 4);
  });
});
