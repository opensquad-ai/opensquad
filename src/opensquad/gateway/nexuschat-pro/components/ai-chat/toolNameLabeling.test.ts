// @vitest-environment jsdom
/**
 * Every tool name that can reach the UI must carry a real label — no raw
 * English fallback, and no silent slide into the catch-all "其他工具" bucket.
 *
 * Reported symptom: the tool-flow headline / step list showed the literal
 * tool name for two families:
 *   - `long_memory__memory_write` → "memory write"  (and it was *counted* as
 *     file editing, because the bare-name `write` regex grabbed it)
 *   - `filesystem__get_allowed_dirs` / `list_allowed_dirs` → "get allowed dirs",
 *     counted as "调用工具 N 次"
 *
 * Root cause: the long-memory toolset is registered under **two different
 * namespaces** depending on the boot path (`memory` in runner_bootstrap,
 * `long_memory` in agents_boot), but only one of them was in the label /
 * category tables. The allowed-directory family had no entry at all.
 *
 * What this locks:
 *   - L1 both long-memory namespaces agree (category + label)
 *   - L2 a memory write is never miscounted as a file edit
 *   - L3 the allowed-directory family is labelled and categorised
 *   - L4 system / workspace / agent_setup stragglers are labelled
 *   - L5 headline (summarizeWorkTools) and step list (buildLines) stay in sync
 *     — the same name must never be labelled in one and raw in the other
 */
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import { SoloActivityRow, buildLines } from './SoloActivityRow';
import type { WorkflowBlock, WorkflowEvent } from '../../utils/aiChatTimeline';
import i18n from '../../i18n';

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

const labelsOf = (b: WorkflowBlock): string[] =>
  buildLines(b, {}, i18n.t.bind(i18n))
    .filter((l) => l.kind === 'tool')
    .map((l) => l.primary);

/** Headline + step labels for one tool, asserted together. */
function probe(name: string): { headline: string; labels: string[] } {
  const b = block([toolCall(name), toolResult(name)]);
  return { headline: render(b), labels: labelsOf(b) };
}

/** A label is "raw" when it still looks like a snake_case identifier. */
const isRaw = (s: string): boolean => /^[a-z0-9_]+(\s[a-z0-9_]+)*$/.test(s.trim());

describe('工具流 — 工具名必须标签化（不落裸名、不误分类）', () => {
  it('control: a baseline filesystem tool is labelled', () => {
    const { headline, labels } = probe('filesystem__read_file');
    expect(headline).toContain('读取 1 个文件');
    expect(labels).toContain('读取文件');
  });

  it('L1 — 长期记忆的两个命名空间（memory / long_memory）标签与归类一致', () => {
    const a = probe('memory__memory_write');
    const b = probe('long_memory__memory_write');
    expect(a.labels).toEqual(b.labels);
    expect(a.headline).toContain('记忆读写 1 次');
    expect(b.headline).toContain('记忆读写 1 次');
    // 裸名兜底会把 fn 的下划线直接摊成英文 —— 这正是用户看到的 "memory write"。
    for (const l of [...a.labels, ...b.labels]) expect(isRaw(l)).toBe(false);
    expect(b.headline).not.toContain('memory write');
  });

  it('L2 — memory_write 不能被 write 正则误判成"编辑文件"', () => {
    for (const name of ['long_memory__memory_write', 'memory__memory_write']) {
      const { headline } = probe(name);
      expect(headline).toContain('记忆读写 1 次');
      expect(headline).not.toContain('编辑');
    }
  });

  it('L3 — 允许目录（allowed dirs）有中文标签且归入"浏览目录"', () => {
    const names = [
      'filesystem__get_allowed_dirs',
      'filesystem__list_allowed_dirs',
    ];
    for (const name of names) {
      const { headline, labels } = probe(name);
      for (const l of labels) expect(isRaw(l)).toBe(false);
      expect(headline).toContain('浏览目录 1 次');
      expect(headline).not.toContain('调用工具');
    }
    // 设置 / 添加允许目录也是目录范围操作，不是文件编辑。
    const set = probe('filesystem__add_allowed_dir');
    expect(set.labels.every((l) => !isRaw(l))).toBe(true);
    expect(set.headline).toContain('浏览目录 1 次');
  });

  it('L4 — system / workspace / agent_setup 的遗留工具不再是英文裸名', () => {
    // 注意：`system__check_job` 不在此列 —— 它是终端任务的附属查询，行会被
    // attachShellJobsToDisplayItems 折进所属终端条（0 行是正确行为，不是缺标签）。
    const names = [
      'system__get_system_info',
      'system__get_time',
      'system__stop_job',
      'system__send_file_to_web',
      'workspace__switch',
      'agent_setup__reload_plugins',
    ];
    for (const name of names) {
      const { labels } = probe(name);
      expect(labels.length).toBeGreaterThan(0);
      for (const l of labels) expect(isRaw(l)).toBe(false);
    }
  });

  it('L5 — 标题与明细同源：同一工具不会一个中文一个裸名', () => {
    const names = [
      'long_memory__memory_write',
      'long_memory__memory_query',
      'filesystem__get_allowed_dirs',
      'workspace__switch',
    ];
    for (const name of names) {
      const { headline, labels } = probe(name);
      for (const l of labels) {
        // 明细里出现的标签，标题里若原样出现裸名即视为不同源。
        if (isRaw(l)) {
          throw new Error(`${name}: raw step label ${JSON.stringify(l)} (headline: ${headline})`);
        }
      }
      expect(headline.length).toBeGreaterThan(0);
    }
  });
});
