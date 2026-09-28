// @vitest-environment jsdom
/**
 * Retry-storm folding: the same call repeated back to back renders as ONE row
 * with a “×N”, instead of a wall of identical copies.
 *
 * Reported symptom (2026-09-28): a session looked like it had gone into
 * "infinite repetition" — the same file card and the same 403 error over and
 * over. The blocks are real history (session `20260928_070309_n8ls` writes
 * `commandcode_space-bunny-alpha.json` 15 times while an endpoint answers
 * `PermissionDeniedError 403`, 10 of them inside a single turn), so the pane was
 * not inventing rows; but a retry storm renders as an unreadable wall, which is
 * indistinguishable from a rendering bug.
 *
 * Rules:
 *   C1 identical consecutive calls (tool + args + file edit + status + error
 *      text) fold into the first row with `repeatCount` = run length;
 *   C2 only *identical* calls fold — different args, a different error, another
 *      call type, or anything in between stops the run;
 *   C3 a running call never folds (it is the only one of its kind so far);
 *   C4 the fold is a display concern: `buildLines` (and therefore the header's
 *      tool counts and the output-rate sampling) still sees every call, and the
 *      "完整" expand level renders each one;
 *   C5 folded rows keep a stable identity while the run length is unchanged, so
 *      React.memo on the row is not defeated by the folding itself.
 *
 * Mutations verified:
 *   MC1 drop the status/error from the signature                → C2
 *   MC2 remove BOTH adjacency guards (head must be the previous
 *       tool row + a non-tool line clears `lastSig`)            → C2
 *       — either guard alone is redundant, so only the pair moves behaviour
 *   MC3 allow running rows to fold                              → C3
 *   MC4 fold inside buildLines / ignore the expand level        → C4
 *   MC5 rebuild the merged object on every call                 → C5
 */
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import { SoloActivityRow, buildLines, collapseRepeatedToolLines } from './SoloActivityRow';
import type { WorkflowBlock, WorkflowEvent } from '../../utils/aiChatTimeline';
import i18n from '../../i18n';

let seq = 0;

const WRITE_TOOL = 'filesystem__write_file';

/**
 * One finished `write_file` call — the shape `WorkflowBlock.events` actually
 * holds in the pane (the timeline pairs the `tool_result` into the call event;
 * a raw call/result *pair* is two lines and folds nothing).
 */
function writeCall(opts: {
  path?: string;
  content?: string;
  result?: string;
  status?: 'success' | 'error';
} = {}): WorkflowEvent {
  seq += 1;
  const failed = opts.status === 'error';
  return {
    _uid: `c${seq}`,
    type: 'tool_call',
    content: {
      name: WRITE_TOOL,
      id: `call-${seq}`,
      call_id: `call-${seq}`,
      arguments: JSON.stringify({ path: opts.path ?? 'x.json', content: opts.content ?? '{}' }),
    },
    result: opts.result ?? 'ok',
    resultStatus: failed ? 'error' : 'success',
    timestamp: 1_700_000_000_000 + seq,
  } as WorkflowEvent;
}

/** A call that never got a result — still in flight. */
function openCall(): WorkflowEvent {
  seq += 1;
  return {
    _uid: `o${seq}`,
    type: 'tool_call',
    content: {
      name: WRITE_TOOL,
      call_id: `open-${seq}`,
      arguments: JSON.stringify({ path: 'x.json', content: '{}' }),
    },
    timestamp: 1_700_000_000_000 + seq,
  } as WorkflowEvent;
}

const think = (): WorkflowEvent => {
  seq += 1;
  return { _uid: `t${seq}`, type: 'thought', content: { text: '想一下' }, timestamp: 1_700_000_000_000 + seq } as WorkflowEvent;
};

function block(events: WorkflowEvent[], completed = true): WorkflowBlock {
  return { events, status: null, completed, started_ms: 1_700_000_000_000 };
}

const linesOf = (b: WorkflowBlock) => buildLines(b, {}, i18n.t.bind(i18n));
const toolsOf = (b: WorkflowBlock) => linesOf(b).filter((l) => l.kind === 'tool');

const DENIED = "Error: PermissionDeniedError - Error code: 403 - {'type': 'permission_error'}";
const retries = (n: number, result = DENIED) =>
  Array.from({ length: n }, () => writeCall({ status: 'error', result }));

describe('C1 — 一串完全相同的重试折成一行 + 计数', () => {
  it('27 次相同写入 → 1 行 ×27', () => {
    const b = block(retries(27));
    expect(toolsOf(b)).toHaveLength(27);
    const collapsed = collapseRepeatedToolLines(linesOf(b));
    expect(collapsed.filter((l) => l.kind === 'tool')).toHaveLength(1);
    expect(collapsed[0].repeatCount).toBe(27);
    expect(collapsed[0].toolName).toBe(WRITE_TOOL);
  });

  it('两次相同调用也折（×2），单次调用不加计数', () => {
    const twice = collapseRepeatedToolLines(linesOf(block([writeCall(), writeCall()])));
    expect(twice.filter((l) => l.kind === 'tool')).toHaveLength(1);
    expect(twice[0].repeatCount).toBe(2);
    const once = collapseRepeatedToolLines(linesOf(block([writeCall()])));
    expect(once.filter((l) => l.kind === 'tool')).toHaveLength(1);
    expect(once[0].repeatCount).toBeUndefined();
  });
});

describe('C2 — 只有"完全相同"才折', () => {
  it('args 不同 → 不折（同一文件的不同改动是两份工作）', () => {
    const b = block([writeCall({ content: '{"a":1}' }), writeCall({ content: '{"a":2}' })]);
    expect(collapseRepeatedToolLines(linesOf(b)).filter((l) => l.kind === 'tool')).toHaveLength(2);
  });

  it('文件不同 → 不折', () => {
    const b = block([writeCall({ path: 'a.json' }), writeCall({ path: 'b.json' })]);
    expect(collapseRepeatedToolLines(linesOf(b)).filter((l) => l.kind === 'tool')).toHaveLength(2);
  });

  it('args 相同但错误信息不同 → 不折（是两次不同的失败）', () => {
    const b = block([
      writeCall({ status: 'error', result: DENIED }),
      writeCall({ status: 'error', result: 'Error: Connection closed' }),
    ]);
    expect(collapseRepeatedToolLines(linesOf(b)).filter((l) => l.kind === 'tool')).toHaveLength(2);
  });

  it('成功和失败不混折', () => {
    const b = block([writeCall(), writeCall({ status: 'error', result: DENIED })]);
    const folded = collapseRepeatedToolLines(linesOf(b)).filter((l) => l.kind === 'tool');
    expect(folded).toHaveLength(2);
    expect(folded.every((l) => !l.repeatCount)).toBe(true);
  });

  it('中间隔了一条别的行 → 不折（必须是连续的重试）', () => {
    const b = block([
      writeCall({ status: 'error', result: DENIED }),
      think(),
      writeCall({ status: 'error', result: DENIED }),
    ]);
    const lines = linesOf(b);
    const folded = collapseRepeatedToolLines(lines);
    // 一行都没被吞掉：两条调用各自成行，中间那条思考行也没被当成折叠头
    expect(folded).toHaveLength(lines.length);
    expect(folded.filter((l) => l.kind === 'tool')).toHaveLength(2);
    expect(folded.filter((l) => l.kind === 'thought')).toHaveLength(1);
    expect(folded.every((l) => !l.repeatCount)).toBe(true);
  });
});

describe('C3 — 正在跑的调用不折', () => {
  it('未完成块里没有结果的同类调用各自成行', () => {
    const b = block([openCall(), openCall()], false);
    const folded = collapseRepeatedToolLines(linesOf(b));
    expect(folded.filter((l) => l.kind === 'tool')).toHaveLength(2);
    expect(folded.every((l) => !l.repeatCount)).toBe(true);
  });
});

describe('C4/C5 — 折叠只影响显示，且不破坏行的记忆化', () => {
  it('“完整”展开级别渲染每一次调用，默认级别渲染一行 ×3', () => {
    // Incomplete block → the fold is open, so the step rows reach the markup.
    const b = block(retries(3), false);
    const html = (level: 'thoughts' | 'full') =>
      renderToStaticMarkup(
        React.createElement(SoloActivityRow, {
          block: b,
          expandLevel: level,
          uiMode: 'solo',
          embedVisualizations: false,
          turnDelivered: false,
        }),
      );
    const folded = html('thoughts');
    expect(folded).toContain('×3');
    expect(folded.match(/写入 x\.json 失败/g) || []).toHaveLength(1);
    const full = html('full');
    expect(full).not.toContain('×3');
    expect((full.match(/写入 x\.json 失败/g) || [])).toHaveLength(3);
  });

  it('同样的行连续折叠两次拿到同一个对象（run 长度没变就不重建）', () => {
    const lines = linesOf(block([writeCall(), writeCall()]));
    const first = collapseRepeatedToolLines(lines);
    const second = collapseRepeatedToolLines(lines);
    expect(first[0].repeatCount).toBe(2);
    expect(second[0]).toBe(first[0]);

    // run 变长 → 必须是一个新对象，否则计数不会更新
    const three = collapseRepeatedToolLines(linesOf(block([writeCall(), writeCall(), writeCall()])));
    expect(three[0].repeatCount).toBe(3);
    expect(three[0]).not.toBe(first[0]);
  });
});
