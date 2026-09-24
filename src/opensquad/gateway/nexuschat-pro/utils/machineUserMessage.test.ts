// @vitest-environment node
/**
 * Machine-generated user turns must be recognised from their wire text alone.
 *
 * Reported symptom (screenshot): submitting the interactive form filled the chat
 * with `[Form submission] 信息填写表单（示例）` plus a ```json block, and reminders /
 * inbound group messages read the same way. The marker text has to keep reaching
 * the model, so the split happens at render time — which is also what makes a
 * refresh show the notice instead of the raw text.
 *
 * The wire formats below are copied from real sessions on this machine
 * (agent305 `data/history/20260722_064336_2uz5.json` for group traffic,
 * `20260711_085732_scjl.json` for a reminder, `current_session.json` for the
 * form submission).
 *
 * Mutations verified (script `C:/tmp/prov/mutate_machine_notice.py`,
 * report `C:/tmp/prov/mutation_report_machine_notice.json`):
 *   MN1 form marker no longer recognised              → R1
 *   MN2 form payload keeps the fence + marker         → R2
 *   MN3 group trailer never stripped                  → R6
 *   MN4 appended group segment swallowed (user text lost) → R8
 *   MN5 continuation lines start a new entry          → R7
 */
import { describe, expect, it } from 'vitest';

import {
  machineNoticeDetail,
  machineNoticeHasDetail,
  machineNoticeLabelKey,
  machineNoticeSummary,
  parseMachineUserMessage,
} from './machineUserMessage';

const FORM_WIRE = `[Form submission] 信息填写表单（示例）

\`\`\`json
{
  "姓名": "squadflow",
  "日期": "",
  "优先级": "高",
  "备注": "okk"
}
\`\`\``;

const GROUP_WIRE =
  '[Messages]\n[开发协作组 | group_id=g-default] ss: @Agent305 现在收到消息了嘛'
  + '[Messages received, please decide how to reply based on the source]';

describe('机器消息识别 —— 从落到会话里的原文还原', () => {
  it('R1 — a form submission becomes a notice, not a bubble of text', () => {
    const r = parseMachineUserMessage(FORM_WIRE);
    expect(r.text).toBe('');
    expect(r.notice?.kind).toBe('form_submission');
    expect(machineNoticeLabelKey(r.notice!)).toBe('submitted');
    expect(machineNoticeSummary(r.notice!)).toBe('信息填写表单（示例）');
  });

  it('R2 — the payload is the JSON body, fence and marker stripped', () => {
    const notice = parseMachineUserMessage(FORM_WIRE).notice;
    const payload = machineNoticeDetail(notice!);
    expect(payload.startsWith('{')).toBe(true);
    expect(JSON.parse(payload)).toEqual({
      姓名: 'squadflow',
      日期: '',
      优先级: '高',
      备注: 'okk',
    });
    expect(payload).not.toContain('```');
    expect(payload).not.toContain('[Form submission]');
    // Chinese marker is the same kind (locale-dependent producer).
    expect(parseMachineUserMessage('[表单提交] 报表\n\n```json\n{"a":1}\n```').notice).toEqual({
      kind: 'form_submission',
      title: '报表',
      payload: '{"a":1}',
    });
  });

  it('R3 — a reminder is a notice with its text', () => {
    const notice = parseMachineUserMessage(
      '[Reminder] Check if user has sent any instructions for the SquadFlow project cleanup',
    ).notice;
    expect(notice?.kind).toBe('reminder');
    expect(machineNoticeLabelKey(notice!)).toBe('reminder');
    expect(machineNoticeSummary(notice!)).toBe(
      'Check if user has sent any instructions for the SquadFlow project cleanup',
    );
    // Single line: nothing extra to expand (the component drops the toggle).
    expect(machineNoticeSummary(notice!)).toBe(machineNoticeDetail(notice!));
  });

  it('R4 — the task-watch supervision reminder counts as a reminder too', () => {
    const wire = '[TASK_WATCH:REMINDER] Task: "整理 skills 目录"\nStatus: Agent has been idle for 900s\n\n'
      + 'Please:\n1. Report your current progress with task_watch.update(progress)';
    const notice = parseMachineUserMessage(wire).notice;
    expect(notice?.kind).toBe('reminder');
    expect(machineNoticeSummary(notice!)).toBe('Task: "整理 skills 目录"');
    expect(machineNoticeHasDetail(notice!)).toBe(true);
    expect(machineNoticeDetail(notice!)).toContain('task_watch.update(progress)');
  });

  it('R5 — a [Messages] batch becomes one group notice per sender', () => {
    const notice = parseMachineUserMessage(GROUP_WIRE).notice;
    expect(notice?.kind).toBe('group');
    expect(machineNoticeLabelKey(notice!)).toBe('group');
    if (notice?.kind !== 'group') throw new Error('expected group');
    expect(notice.entries).toEqual([
      { dm: false, chat: '开发协作组', groupId: 'g-default', sender: 'ss', text: '@Agent305 现在收到消息了嘛' },
    ]);
  });

  it('R6 — the runner instruction glued to the last message is not content', () => {
    const notice = parseMachineUserMessage(GROUP_WIRE).notice;
    if (notice?.kind !== 'group') throw new Error('expected group');
    expect(machineNoticeDetail(notice)).not.toContain('Messages received');
    expect(machineNoticeSummary(notice)).toBe('开发协作组 · @Agent305 现在收到消息了嘛');
  });

  it('R7 — a multi-line group message stays one entry', () => {
    const wire = '[Messages]\n[A组 | group_id=g-1] bob: 第一行\n第二行还是他说的\n'
      + '[B组 | group_id=g-2] eve: 另一条'
      + '[Messages received, please decide how to reply based on the source]';
    const notice = parseMachineUserMessage(wire).notice;
    if (notice?.kind !== 'group') throw new Error('expected group');
    expect(notice.entries).toHaveLength(2);
    expect(notice.entries[0]).toMatchObject({ chat: 'A组', sender: 'bob', text: '第一行\n第二行还是他说的' });
    expect(notice.entries[1]).toMatchObject({ chat: 'B组', sender: 'eve', text: '另一条' });
    expect(machineNoticeSummary(notice)).toBe('A组 · bob +1');
  });

  it('R8 — group traffic appended to the user\'s own turn keeps the user\'s words', () => {
    const wire = `帮我把这个群里的问题整理一下\n\n[Simultaneously received group messages]\n`
      + '[A组 | group_id=g-1] bob: 请看下这个报错';
    const r = parseMachineUserMessage(wire);
    expect(r.text).toBe('帮我把这个群里的问题整理一下');
    expect(r.notice?.kind).toBe('group');
    if (r.notice?.kind !== 'group') throw new Error('expected group');
    expect(r.notice.entries[0]).toMatchObject({ sender: 'bob', text: '请看下这个报错' });
  });

  it('R9 — a DM is a notice of its own', () => {
    const notice = parseMachineUserMessage('[DM] eve: 在吗').notice;
    expect(notice?.kind).toBe('group');
    expect(machineNoticeLabelKey(notice!)).toBe('dm');
    if (notice?.kind !== 'group') throw new Error('expected group');
    expect(notice.entries).toEqual([{ dm: true, sender: 'eve', text: '在吗' }]);
  });

  it('R10 — ordinary user text is left completely alone', () => {
    for (const plain of [
      '帮我看看这个表格',
      '我打了 [Form submission] 这几个字在中间',
      '',
    ]) {
      const r = parseMachineUserMessage(plain);
      expect(r.notice).toBeNull();
      expect(r.text).toBe(plain);
    }
  });
});
