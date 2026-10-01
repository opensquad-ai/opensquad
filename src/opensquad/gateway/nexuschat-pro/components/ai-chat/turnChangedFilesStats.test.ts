/**
 * Code(solo) 的「最终产出」列表要显示每个文件动了多少行。
 *
 * 行数来自工具参数（`extractFileEditInfo` 里的 addedLines / removedLines），
 * 不是事后 git diff —— 卡片刻画的正是这一轮写了什么。这里锁住采集结果，
 * 以及 solo 版走的是「路径 + 行数」列表（而不是 Work 那种文件卡片）。
 */
import { describe, expect, it } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import { collectTurnChangedFiles } from './TurnChangedFilesCard';

const read = (rel: string) => fs.readFileSync(path.join(process.cwd(), rel), 'utf8');

const writeCall = (filePath: string, content: string) => ({
  type: 'tool_call',
  content: {
    name: 'filesystem__write_file',
    args: JSON.stringify({ path: filePath, content }),
  },
});

const editCall = (filePath: string, newStr: string, oldStr = '') => ({
  type: 'tool_call',
  content: {
    name: 'filesystem__edit_file',
    args: JSON.stringify({ path: filePath, old_str: oldStr, new_str: newStr }),
  },
});

describe('collectTurnChangedFiles', () => {
  it('write 按正文行数记新增，删除为 0', () => {
    const [f] = collectTurnChangedFiles([
      { completed: true, events: [writeCall('src/a.py', 'l1\nl2\nl3')] } as never,
    ]);
    expect(f.path).toBe('src/a.py');
    expect(f.additions).toBe(3);
    expect(f.deletions).toBe(0);
  });

  it('edit 记新文本 / 旧文本行数', () => {
    const [f] = collectTurnChangedFiles([
      { completed: true, events: [editCall('src/b.py', 'new1\nnew2', 'old1')] } as never,
    ]);
    expect(f.additions).toBe(2);
    expect(f.deletions).toBe(1);
  });

  it('同一文件出现两次只保留一条', () => {
    const files = collectTurnChangedFiles([
      {
        completed: true,
        events: [writeCall('src/a.py', 'x'), writeCall('src/a.py', 'x\ny')],
      } as never,
    ]);
    expect(files).toHaveLength(1);
  });
});

describe('solo 版渲染', () => {
  const SRC = read('components/ai-chat/TurnChangedFilesCard.tsx');

  it('solo 用「路径 + 行数」列表，classic 保持文件卡片', () => {
    expect(SRC).toMatch(/if \(uiMode === 'solo'\)/);
    expect(SRC).toMatch(/data-testid="changed-files-list"/);
    expect(SRC).toMatch(/\{f\.path\}/);
    expect(SRC).toMatch(/\+{1}\{f\.additions\}/);
    expect(SRC).toMatch(/-\{f\.deletions\}/);
  });

  it('AIChatPage 把当前版面传下去', () => {
    const PAGE = read('components/AIChatPage.tsx');
    expect(PAGE).toMatch(/<TurnChangedFilesCard[\s\S]{0,120}?uiMode=\{uiMode\}/);
  });
});
