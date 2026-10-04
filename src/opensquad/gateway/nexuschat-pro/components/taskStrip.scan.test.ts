/**
 * The strip has to be above the composer (where a message cannot push it away), made of every
 * group's tasks, and able to open the task window that already exists.
 */
import fs from 'fs';
import path from 'path';
import { describe, expect, it } from 'vitest';

const read = (rel: string) => fs.readFileSync(path.resolve(__dirname, rel), 'utf8');

describe('the task strip is mounted above the composer', () => {
  it('sits inside the input area, before the composer host', () => {
    const src = read('../components/ChatWindow.tsx');

    expect(src).toContain('<TaskStrip tasks={useStripTasks()} />');
    const strip = src.indexOf('<TaskStrip');
    const composer = src.indexOf('<ChatComposerHost', strip);

    expect(strip).toBeGreaterThan(-1);
    expect(composer).toBeGreaterThan(strip);
  });

  it('reads the cross-group list, and refreshes when the window comes back', () => {
    const hook = read('../hooks/useStripTasks.ts');

    expect(hook).toContain('collabBoardAPI.listTasks()');
    expect(hook).toContain("document.addEventListener('visibilitychange'");
    expect(hook).toContain('window.setInterval(load, pollMs)');
    expect(hook).toContain('sameTasks(prev, next) ? prev : next');
  });
});

describe('the strip itself', () => {
  it('opens the task window by task id, using the path the chat already uses', () => {
    const src = read('../components/TaskStrip.tsx');

    expect(src).toContain('openCollabTaskWindow(row.taskId)');
    expect(src).toContain("from './CollabTaskCard'");
  });

  it('collapses, and shows nothing when there is nothing to show', () => {
    const src = read('../components/TaskStrip.tsx');

    expect(src).toContain('aria-expanded={expanded}');
    expect(src).toMatch(/if \(rows\.length === 0\) return null;/);
    expect(src).toContain('data-task-id={row.taskId}');
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
    }
  });
});
