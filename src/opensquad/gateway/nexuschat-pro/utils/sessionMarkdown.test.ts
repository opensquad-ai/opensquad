/**
 * Session → Markdown export.
 *
 * Watched here: the transcript shape (turns, tool flow, plans, interjections) and
 * the deliberate compactness (a call is one line, only a FAILED call keeps its
 * error text, sub-agent steps stay nested under their delegate row) plus the
 * download file name.
 *
 * Mutations verified (applied, run, reverted):
 *   MU1 stop single-lining / truncating the tool arguments -> R2 fails
 *   MU2 render sub-agent events inline                     -> R3 fails
 *   MU3 keep the error text of a SUCCEEDED call            -> R2 fails
 *   MU4 drop the file-name sanitising                      -> R5 fails
 *   MU5 drop the title→id fallback for the heading         -> R6 fails
 */
import { describe, expect, it } from 'vitest';
import { exportFileName, sessionToMarkdown } from './sessionMarkdown';
import type { TimelineEntry, WorkflowEvent } from './aiChatTimeline';

function msg(role: 'user' | 'assistant', content: string): TimelineEntry {
  return { kind: 'message', data: { role, content } as any, _uid: `${role}-1` };
}

function wf(events: WorkflowEvent[]): TimelineEntry {
  return {
    kind: 'workflow',
    data: { events, status: null, completed: true, started_ms: 1000, elapsed_ms: 5000 },
    _uid: 'w1',
  };
}

function ev(partial: Partial<WorkflowEvent>): WorkflowEvent {
  return { type: 'tool_call', content: {}, timestamp: 1000, ...partial } as WorkflowEvent;
}

const EXPORTED_AT = new Date('2026-09-25T07:30:00Z');

function render(entries: TimelineEntry[], title = '查查福州天气', sessionId = 'sid-1') {
  return sessionToMarkdown({ title, sessionId, entries, exportedAt: EXPORTED_AT });
}

describe('sessionToMarkdown', () => {
  it('renders the transcript, the tool flow, plans and interjections', () => {
    const md = render([
      msg('user', '查查当前福州天气'),
      wf([
        ev({ type: 'thought', content: '先搜索\n再看结果' }),
        ev({
          type: 'tool_call',
          content: { id: 'c1', name: 'bocha_search__ai_search', args: '{"query":"福州天气"}' },
          result: 'ok',
          resultStatus: 'success',
        }),
        ev({
          type: 'tool_call',
          content: { id: 'c2', name: 'websearch__search', args: '{"q":"x"}' },
          result: 'Bocha HTTP 500',
          resultStatus: 'error',
        }),
        ev({ type: 'plan', content: '1. 搜索\n2. 汇总' }),
        ev({ type: 'user_steer', content: '只报温度' }),
      ]),
      msg('assistant', '福州今天 28℃。'),
    ]);

    expect(md.startsWith('# 查查福州天气\n')).toBe(true);
    expect(md).toContain('- 会话：`sid-1`');
    expect(md).toContain('- 导出时间：2026-09-25 07:30 UTC');
    expect(md).toContain('## 用户\n\n查查当前福州天气');
    expect(md).toContain('## 助手\n\n福州今天 28℃。');
    expect(md).toContain('### 工具流');
    // Deep-think text survives as a blockquote, per line.
    expect(md).toContain('> 先搜索\n> 再看结果');
    expect(md).toContain('- `bocha_search__ai_search` — 完成 — {"query":"福州天气"}');
    // Only the failed call keeps its error.
    expect(md).toContain('- `websearch__search` — 失败 — {"q":"x"}');
    expect(md).toContain('  - 错误：Bocha HTTP 500');
    expect(md).toContain('1. [ ] 搜索');
    expect(md).toContain('**插话：** 只报温度');
    expect(md.endsWith('\n')).toBe(true);
  });

  it('keeps tool arguments to one bounded line and errors off successful calls', () => {
    const long = '{"cmd":"' + 'x'.repeat(500) + '"}';
    const md = render([
      wf([
        ev({ type: 'tool_call', content: { name: 'a__b', args: long }, result: 'done', resultStatus: 'success' }),
        ev({
          type: 'tool_call',
          content: { name: 'c__d', args: '{\n  "a": 1\n}' },
          result: 'done',
          resultStatus: 'success',
        }),
      ]),
    ]);
    const first = md.split('\n').find((l) => l.includes('`a__b`'))!;
    expect(first).toContain('…');
    expect(first.length).toBeLessThan(260);
    expect(first).not.toContain('\n');
    // A succeeded call must not carry a 错误 sub-line.
    expect(md).not.toContain('错误：done');
    expect(md).toContain('- `c__d` — 完成 — { "a": 1 }');
  });

  it('keeps sub-agent steps nested instead of dumping them inline', () => {
    const md = render([
      wf([
        ev({
          type: 'tool_call',
          content: { name: 'collab__delegate', args: '{}' },
          subTaskLabel: '解析财报',
          result: 'ok',
          resultStatus: 'success',
        }),
        ev({ type: 'thought', content: '子 agent 的内部推理', subAgent: true }),
        ev({ type: 'tool_call', content: { name: 'inner__tool' }, subAgent: true }),
      ]),
    ]);
    expect(md).toContain('`collab__delegate`');
    expect(md).not.toContain('子 agent 的内部推理');
    expect(md).not.toContain('inner__tool');
  });

  it('notes folded / archived sections and the model switch, ignoring app chrome', () => {
    const md = render([
      { kind: 'model_switch', data: { model: 'glm-5.3-flash', card: 'ark', text: '' } as any, _uid: 'm1' },
      { kind: 'prompt', data: {} as any, _uid: 'p1' },
      { kind: 'status_hint', data: {} as any, _uid: 's1' },
      {
        kind: 'archived_section',
        data: { messageCount: 12, eventCount: 30, entries: [], collapsed: true } as any,
        _uid: 'a1',
      },
    ]);
    expect(md).toContain('> 模型切换：glm-5.3-flash');
    expect(md).toContain('> （折叠 12 条消息、30 个事件）');
    expect(md).not.toContain('prompt');
  });

  it('survives an empty session and blank turns', () => {
    const md = render([], '', '');
    expect(md.startsWith('# Session')).toBe(true);
    expect(md).not.toContain('- 会话：');
    const withBlanks = render([msg('user', '   '), wf([ev({ type: 'thought', content: '  ' })])]);
    expect(withBlanks).not.toContain('## 用户');
    expect(withBlanks).not.toContain('### 工具流');
  });
});

describe('exportFileName', () => {
  it('derives a filesystem-safe name from the title', () => {
    expect(exportFileName('查查当前福州天气', 'sid-1')).toBe('查查当前福州天气.md');
    // Each illegal character maps to a dash (runs are left alone — the name stays
    // recognisable, which matters more than tidiness).
    expect(exportFileName('a/b:c*d?"e<f>g|h', 'sid')).toBe('a-b-c-d--e-f-g-h.md');
    expect(exportFileName('  spaced \n name ', 'sid')).toBe('spaced name.md');
  });

  it('falls back to the session id, then a constant', () => {
    expect(exportFileName(undefined, 'sid-9')).toBe('sid-9.md');
    expect(exportFileName('   ', '')).toBe('session.md');
  });
});
