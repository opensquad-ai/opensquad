import { existsSync } from 'node:fs';
import { describe, it, expect } from 'vitest';
import { buildTimelineFromSession, demoteIntermediateAssistantMessages } from './aiChatTimeline';
import { collectTurnChangedFilesBefore } from '../components/ai-chat/TurnChangedFilesCard';

// The two `REAL ...` cases below are probes against samples captured from a
// running deployment, not fixtures: one is a session history from the local
// runtime dir, the other a gateway payload parked in `.tmpscratch/`. Both live
// outside the repo (`.tmpscratch/` is gitignored), so on any other machine —
// CI included — reading them is a guaranteed ENOENT and used to fail the whole
// frontend job before `npm run build` ever ran. They are skipped unless the
// sample is actually present, which keeps them useful on the box that captured
// them without pretending they are portable.
const REAL_SESSION_FILE =
  'C:/ai_work/pro0/opensquad_runtime_deploy/agents/agent305/data/history/20260913_132459_nmgl.json';
const REAL_PAGED_FILE = 'c:/ai_work/pro0/opensquad_deploy_test/.tmpscratch/paged_payload.json';

describe('turn files card survives history reload', () => {
  it('collects changed files from a reloaded (persisted) turn', () => {
    const messages = [
      { role: 'user', content: '创建改文件，内容是水仙花函数，用cmd验证', timestamp: '2026-09-13T02:00:00.000Z' },
      { role: 'assistant', content: '文件已创建并验证通过。', timestamp: '2026-09-13T02:00:30.000Z' },
    ];
    const events = [
      {
        type: 'thought',
        data: { text: '先写文件再跑命令' },
        timestamp: '2026-09-13T02:00:01.000Z',
      },
      {
        type: 'tool_call',
        data: { id: 'c1', name: 'filesystem.write_file', args: { path: 'narcissistic.py', content: 'def is_narcissistic(n): ...' } },
        timestamp: '2026-09-13T02:00:02.000Z',
      },
      {
        type: 'tool_result',
        data: { id: 'c1', name: 'filesystem.write_file', result: 'ok' },
        timestamp: '2026-09-13T02:00:02.500Z',
      },
      {
        type: 'tool_call',
        data: { id: 'c2', name: 'system.run_session_job', args: { command: 'python narcissistic.py' } },
        timestamp: '2026-09-13T02:00:03.000Z',
      },
      {
        type: 'tool_result',
        data: { id: 'c2', name: 'system.run_session_job', result: '153' },
        timestamp: '2026-09-13T02:00:04.000Z',
      },
    ];
    const tl = buildTimelineFromSession(messages, events);
    console.log('timeline kinds:', tl.map((e) => e.kind).join(','));
    const idx = tl.findIndex((e) => e.kind === 'message' && (e.data as any).role === 'assistant');
    console.log('assistant idx:', idx);
    const wf = tl.find((e) => e.kind === 'workflow');
    if (wf) {
      const eventsShape = (wf as any).data.events.map((ev: any) => ({
        type: ev.type,
        name: ev.content && typeof ev.content === 'object' ? ev.content.name : undefined,
        argsType: ev.content && typeof ev.content === 'object' ? typeof ev.content.arguments : undefined,
      }));
      console.log('workflow events:', JSON.stringify(eventsShape));
      console.log('completed:', (wf as any).data.completed);
    }
    const files = collectTurnChangedFilesBefore(tl as any, idx);
    console.log('collected files:', JSON.stringify(files));
    expect(files).toHaveLength(1);
    expect(files[0].name).toBe('narcissistic.py');
  });

  it('variant A: args persisted as JSON string', () => {
    const messages = [
      { role: 'user', content: '创建文件', timestamp: '2026-09-13T02:00:00.000Z' },
      { role: 'assistant', content: '完成', timestamp: '2026-09-13T02:00:30.000Z' },
    ];
    const events = [
      {
        type: 'tool_call',
        data: { id: 'c1', name: 'filesystem.write_file', args: JSON.stringify({ path: 'a.py', content: 'x' }) },
        timestamp: '2026-09-13T02:00:02.000Z',
      },
      { type: 'tool_result', data: { id: 'c1', result: 'ok' }, timestamp: '2026-09-13T02:00:02.500Z' },
    ];
    const tl = buildTimelineFromSession(messages, events);
    const idx = tl.findIndex((e) => e.kind === 'message' && (e.data as any).role === 'assistant');
    const files = collectTurnChangedFilesBefore(tl as any, idx);
    console.log('variant A files:', JSON.stringify(files));
    expect(files).toHaveLength(1);
  });

  it('variant B: api_sync reply persisted BEFORE tool events (ts race)', () => {
    const messages = [
      { role: 'user', content: '创建文件', timestamp: '2026-09-13T02:00:00.000Z' },
      // api_sync reply saved at 02:00:05 — BEFORE tools flush at 02:00:10
      { role: 'assistant', content: '完成', timestamp: '2026-09-13T02:00:05.000Z' },
    ];
    const events = [
      {
        type: 'tool_call',
        data: { id: 'c1', name: 'filesystem.write_file', args: { path: 'b.py', content: 'x' } },
        timestamp: '2026-09-13T02:00:10.000Z',
      },
      { type: 'tool_result', data: { id: 'c1', result: 'ok' }, timestamp: '2026-09-13T02:00:10.500Z' },
      { type: 'thought', data: { text: '收尾' }, timestamp: '2026-09-13T02:00:11.000Z' },
    ];
    const tl = buildTimelineFromSession(messages, events);
    console.log('variant B kinds:', tl.map((e) => e.kind).join(','));
    const idx = tl.findIndex((e) => e.kind === 'message' && (e.data as any).role === 'assistant');
    console.log('variant B assistant idx:', idx, 'of', tl.length);
    const files = collectTurnChangedFilesBefore(tl as any, idx);
    console.log('variant B files:', JSON.stringify(files));
    expect(files.length).toBe(1);
  });

  it.skipIf(!existsSync(REAL_SESSION_FILE))('REAL session: narcissistic turn from disk', () => {
    const fs = require('fs');
    const raw = fs.readFileSync(REAL_SESSION_FILE, 'utf-8');
    const session = JSON.parse(raw);
    const tl = buildTimelineFromSession(session.messages, session.events, session.archived_messages, session.archived_events);
    console.log('REAL kinds:', tl.map((e) => e.kind).join(','));
    const idx = tl.findIndex((e) => e.kind === 'message' && (e.data as any).role === 'assistant' && String((e.data as any).content || '').includes('文件已创建并验证通过'));
    console.log('REAL reply idx:', idx, 'total', tl.length);
    for (let k = Math.max(0, idx - 4); k <= Math.min(tl.length - 1, idx + 1); k++) {
      const e = tl[k] as any;
      console.log(`  [${k}] ${e.kind}${e.kind === 'workflow' ? ` completed=${e.data.completed} events=${e.data.events.length} types=${e.data.events.map((x: any) => x.type).join('/')}` : e.kind === 'message' ? ` role=${e.data.role} type=${e.data.type} content=${String(e.data.content || '').slice(0, 30)}` : ''}`);
    }
    const files = collectTurnChangedFilesBefore(tl as any, idx);
    console.log('REAL files:', JSON.stringify(files));
    expect(files.length).toBe(1);
  });

  it.skipIf(!existsSync(REAL_PAGED_FILE))('REAL paged payload from gateway', () => {
    const fs = require('fs');
    const session = JSON.parse(fs.readFileSync(REAL_PAGED_FILE, 'utf-8'));
    const tl = buildTimelineFromSession(session.messages, session.events, session.archived_messages, session.archived_events);
    console.log('PAGED kinds:', tl.map((e) => e.kind).join(','));
    const idx = tl.findIndex((e) => e.kind === 'message' && (e.data as any).role === 'assistant' && String((e.data as any).content || '').includes('文件已创建并验证通过'));
    console.log('PAGED reply idx:', idx, 'total', tl.length);
    for (let k = Math.max(0, idx - 3); k <= Math.min(tl.length - 1, idx); k++) {
      const e = tl[k] as any;
      console.log(`  [${k}] ${e.kind}${e.kind === 'workflow' ? ` completed=${e.data.completed} events=${e.data.events.length}` : e.kind === 'message' ? ` role=${e.data.role} content=${String(e.data.content || '').slice(0, 24)}` : ''}`);
    }
    const files = collectTurnChangedFilesBefore(tl as any, idx);
    console.log('PAGED files:', JSON.stringify(files));
    expect(files.length).toBe(1);
  });
});

describe('intermediate assistant output demotion (过程输出)', () => {
  const msg = (role: 'user' | 'assistant', content: string, ts: string, extra: any = {}) => ({
    kind: 'message',
    data: { role, content, timestamp: ts, ...extra },
    _uid: `${role}-${ts}-${content.slice(0, 6)}`,
  });
  const wf = (completed: boolean, ts: number, nEvents = 2) => ({
    kind: 'workflow',
    data: {
      events: Array.from({ length: nEvents }, (_, i) => ({
        _uid: `e${ts}-${i}`,
        type: 'tool_call',
        content: { name: 'filesystem.read_file', arguments: { path: `f${i}.py` } },
        timestamp: ts + i,
        result: 'ok',
      })),
      status: null,
      completed,
    },
    _uid: `wf-${ts}`,
  });

  it('demotes interim message into the FOLLOWING workflow block front', () => {
    const tl: any[] = [
      msg('user', '开始', '2026-09-13T02:00:00.000Z'),
      wf(true, 1000),
      msg('assistant', '现在让我用 Playwright 验证一下', '2026-09-13T02:05:00.000Z'),
      wf(true, 2000),
      msg('assistant', '最终回复', '2026-09-13T02:09:00.000Z'),
    ];
    const out = demoteIntermediateAssistantMessages(tl as any);
    // 中间气泡消失，只剩 user + final 两条消息
    expect(out.filter((e) => e.kind === 'message')).toHaveLength(2);
    const wfBlocks = out.filter((e) => e.kind === 'workflow') as any[];
    expect(wfBlocks).toHaveLength(2);
    const target = wfBlocks.find((b) => b.data.events.some((e: any) => e.type === 'process_output'));
    expect(target).toBeTruthy();
    expect(target.data.events[0].type).toBe('process_output');
    expect(target.data.events[0].content).toBe('现在让我用 Playwright 验证一下');
  });

  it('demotes interim message into the PRECEDING workflow block end when no workflow follows', () => {
    const tl: any[] = [
      msg('user', '开始', '2026-09-13T02:00:00.000Z'),
      wf(true, 1000),
      msg('assistant', '阶段总结：一切正常', '2026-09-13T02:05:00.000Z'),
      msg('assistant', '最终回复', '2026-09-13T02:09:00.000Z'),
    ];
    const out = demoteIntermediateAssistantMessages(tl as any);
    expect(out.filter((e) => e.kind === 'message')).toHaveLength(2); // user + final
    const wfBlocks = out.filter((e) => e.kind === 'workflow') as any[];
    expect(wfBlocks).toHaveLength(1);
    const lastEvt = wfBlocks[0].data.events[wfBlocks[0].data.events.length - 1];
    expect(lastEvt.type).toBe('process_output');
    expect(lastEvt.content).toBe('阶段总结：一切正常');
  });

  it('demotes interim text even when the turn has no workflow block at all', () => {
    // 模型把 tool_call 写成正文（不支持原生 FC）时事件流里没有 workflow，
    // 旧规则「两者皆无则保持普通消息」会把每一段叙述都留在时间线上当回复。
    const tl1: any[] = [
      msg('user', 'hi', '2026-09-13T02:00:00.000Z'),
      msg('assistant', '第一步', '2026-09-13T02:01:00.000Z'),
      msg('assistant', '最终回复', '2026-09-13T02:02:00.000Z'),
    ];
    const out1 = demoteIntermediateAssistantMessages(tl1 as any);
    expect(out1.filter((e) => e.kind === 'message')).toHaveLength(2); // user + final
    const built = out1.find((e) => e.kind === 'workflow') as any;
    expect(built).toBeTruthy();
    expect(built.data.completed).toBe(true);
    expect(built.data.events.map((e: any) => e.type)).toEqual(['process_output']);
    expect(built.data.events[0].content).toBe('第一步');
  });

  it('demoteTrailing folds the turn tail only when the caller says work follows', () => {
    const tail: any[] = [
      msg('user', 'hi', '2026-09-13T02:00:00.000Z'),
      msg('assistant', '先说一句，随后调用工具', '2026-09-13T02:01:00.000Z'),
      wf(false, 2000),
    ];
    // 落盘/重建：最后一条 assistant 文本永远当作真正的用户输出
    expect(demoteIntermediateAssistantMessages(tail as any).filter((e) => e.kind === 'message')).toHaveLength(2);
    // 实时：调用方正在追加父级 tool_call，尾随文本必然是过程输出
    const live = demoteIntermediateAssistantMessages(tail as any, { demoteTrailing: true });
    expect(live.filter((e) => e.kind === 'message')).toHaveLength(1);
    const wfBlock = live.find((e) => e.kind === 'workflow') as any;
    expect(wfBlock.data.events[0].type).toBe('process_output');
    expect(wfBlock.data.events[0].content).toBe('先说一句，随后调用工具');
  });

  it('never demotes the final reply or media messages', () => {
    // 带图片的中间消息不降级
    const tl2: any[] = [
      msg('user', '看图', '2026-09-13T02:00:00.000Z'),
      wf(true, 1000),
      msg('assistant', '这是截图', '2026-09-13T02:05:00.000Z', { images: ['/uploads/a.png'] }),
      msg('assistant', '最终回复', '2026-09-13T02:09:00.000Z'),
    ];
    const out2 = demoteIntermediateAssistantMessages(tl2 as any);
    expect(out2.filter((e) => e.kind === 'message')).toHaveLength(3);
    expect(out2.some((e) => e.kind === 'workflow' && (e as any).data.events.some((ev: any) => ev.type === 'process_output'))).toBe(false);

    // end_task 消息永不降级
    const tl3: any[] = [
      msg('user', '做任务', '2026-09-13T02:00:00.000Z'),
      wf(true, 1000),
      msg('assistant', '任务完成总结', '2026-09-13T02:09:00.000Z', { end_task: true }),
    ];
    const out3 = demoteIntermediateAssistantMessages(tl3 as any);
    expect(out3.filter((e) => e.kind === 'message')).toHaveLength(2);
    expect(out3.some((e) => e.kind === 'workflow' && (e as any).data.events.some((ev: any) => ev.type === 'process_output'))).toBe(false);
  });
});
