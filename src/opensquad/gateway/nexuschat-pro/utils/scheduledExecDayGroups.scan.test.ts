/**
 * The 定时任务 → 执行 list groups runs by calendar day (今天 / 昨天 / M/D), matching
 * how the 运行记录 view reads. Before this it was one flat run list, so a task
 * that fires daily produced a wall of identical rows with the date in every
 * line and no way to see where "today" started.
 *
 *   R1  the execution list renders through `execDayGroups` + a day header —
 *       the flat `executions.map(…)` shape cannot come back;
 *   R2  grouping comes from `utils/time` (`groupByLocalDay` / `formatDayLabel`),
 *       the pure helpers locked by utils/time.test.ts;
 *   R3  the day label follows the UI language rather than hardcoding 今天.
 *
 * Why a fence rather than a mount: ScheduledTasksPage pulls the api layer, the
 * WS service and i18n; a jsdom mount would spend its budget on mocks and still
 * would not see the grouping.
 */

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { describe, expect, it } from 'vitest';

const PAGE_SRC = readFileSync(
  resolve(__dirname, '..', 'components', 'ai-chat', 'ScheduledTasksPage.tsx'),
  'utf8',
);
const TIME_SRC = readFileSync(resolve(__dirname, 'time.ts'), 'utf8');

describe('scheduled execution list — day grouping', () => {
  it('R1: the execution tab renders day groups with a header', () => {
    expect(PAGE_SRC).toMatch(/groupByLocalDay\(executions/);
    expect(PAGE_SRC).toMatch(/execDayGroups\.map/);
    expect(PAGE_SRC).toMatch(/formatDayLabel\(group\.items\[0\]\?\.started_at/);
    // The flat list is gone: rows only ever render inside a group.
    expect(PAGE_SRC).not.toMatch(/executions\.map\(/);
  });

  it('R2: grouping lives in utils/time as pure helpers', () => {
    expect(TIME_SRC).toMatch(/export function groupByLocalDay</);
    expect(TIME_SRC).toMatch(/export function formatDayLabel\(/);
    expect(TIME_SRC).toMatch(/export function localDayKey\(/);
  });

  it('R3: the header follows the UI language', () => {
    expect(PAGE_SRC).toMatch(/i18n\.language/);
    expect(PAGE_SRC).toMatch(/formatDayLabel\(group\.items\[0\]\?\.started_at, \{ locale: dayLabelLocale \}\)/);
  });
});
