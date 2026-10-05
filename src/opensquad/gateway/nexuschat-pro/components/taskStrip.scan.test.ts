/**
 * The strip is a control on the composer's toolbar line — not a block of list above it — and it
 * has to be fed the cross-group list from the window that owns the fetch.
 */
import fs from 'fs';
import path from 'path';
import { describe, expect, it } from 'vitest';

const read = (rel: string) => fs.readFileSync(path.resolve(__dirname, rel), 'utf8');

describe('where it sits', () => {
  it('is on the toolbar line, right after the @ button', () => {
    const src = read('../components/MessageInput.tsx');

    const divider = src.indexOf('h-4 w-px bg-gray-300');
    const ai = src.indexOf('{/* AI 按钮 */}');
    const strip = src.indexOf('<TaskStrip');

    expect(ai).toBeGreaterThan(divider);
    expect(strip).toBeGreaterThan(ai);
  });

  it('is fed the cross-group list, fetched once by the chat window', () => {
    const src = read('../components/ChatWindow.tsx');

    expect(src).toContain('tasks={useStripTasks()}');
    expect(src).toContain('tasks={tasks}');
    expect(src).toContain('tasks?: CollabBoardTask[]');
  });

  it('reads the list from the route that merges paired machines, and refreshes on return', () => {
    const hook = read('../hooks/useStripTasks.ts');

    expect(hook).toContain('collabBoardAPI.listTasks()');
    expect(hook).toContain("document.addEventListener('visibilitychange'");
    expect(hook).toContain('window.setInterval(load, pollMs)');
    expect(hook).toContain('sameTasks(prev, next) ? prev : next');
  });
});

describe('how it looks', () => {
  it('is a compact pill that opens upward, since the composer is at the bottom', () => {
    const src = read('../components/TaskStrip.tsx');

    expect(src).toContain('data-task-strip-trigger');
    expect(src).toContain('absolute bottom-full');
    expect(src).toContain('rounded-full');
    expect(src).toContain('backdrop-blur');
    expect(src).toContain('shadow-xl');
  });

  it('counts what is running, not everything it lists', () => {
    // The pill read 2 while both rows were 已结束: the number has to mean 进行中, and say 无 when
    // there are none — the list may still hold what finished within the day.
    const src = read('../components/TaskStrip.tsx');

    expect(src).toContain("const running = rows.filter((row) => row.state === 'running').length;");
    expect(src).toContain("{running > 0 ? running : t('taskStrip.none', { defaultValue: '无' })}");
    expect(src).not.toMatch(/>\s*\{rows\.length\}\s*</);
  });

  it('shows no list until it is opened, then one row per task that opens the task window', () => {
    const src = read('../components/TaskStrip.tsx');

    expect(src).toMatch(/if \(rows\.length === 0\) return null;/);
    expect(src).toContain('{open && (');
    expect(src).toContain('openCollabTaskWindow(row.taskId)');
    expect(src).toContain('data-task-id={row.taskId}');
    expect(src).toContain("from './CollabTaskCard'");
  });

  it('closes on an outside click or Escape', () => {
    const src = read('../components/TaskStrip.tsx');

    expect(src).toContain("document.addEventListener('mousedown'");
    expect(src).toContain("event.key === 'Escape'");
  });

  it('takes its rows from the shared selector', () => {
    const src = read('../components/TaskStrip.tsx');

    expect(src).toContain('selectStripTasks(tasks, nowMs ?? Date.now())');
    expect(src).toContain("from '../utils/taskStrip'");
  });
});

describe('its wording', () => {
  it('is translated in both locales', () => {
    for (const lang of ['zh', 'en']) {
      const locale = JSON.parse(read(`../locales/${lang}.json`));

      expect(locale.taskStrip.title).toBeTruthy();
      expect(locale.taskStrip.within).toBeTruthy();
      expect(locale.taskStrip.finished).toBeTruthy();
    }
  });
});
