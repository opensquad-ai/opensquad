/**
 * 开机自启这条链在三个文件里，缺一处就是"开关点了没效果"：
 * `electron/main.ts`（注册 + 应用）→ `electron/preload.ts`（暴露给渲染进程）→
 * `electron.d.ts`（类型）。渲染层（System Settings）只在桌面端渲染这个开关。
 *
 * 另外钉住两条刻意的选择：
 *   1. 默认**开启** —— 这个能力的价值就是没人需要记得手动启动；
 *   2. 开关显示的是 **OS 的答案**（`getLoginItemSettings()`），不是我们写进文件的偏好，
 *      否则用户在任务管理器里禁用了启动项、界面还在骗他。
 */
import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';

const ROOT = path.resolve(__dirname, '..');
const read = (...parts: string[]) => fs.readFileSync(path.join(ROOT, ...parts), 'utf8');
const strip = (src: string) => src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');

const MAIN = strip(read('electron', 'main.ts'));
const PRELOAD = strip(read('electron', 'preload.ts'));
const TYPES = read('electron.d.ts');
const PAGE = strip(read('components', 'SystemConfigPage.tsx'));

describe('桌面端登录自启', () => {
  it('主进程注册登录项，默认开启，并在开窗之前应用', () => {
    expect(MAIN).toMatch(/app\.setLoginItemSettings\(\{ openAtLogin: startupPrefs\.openAtLogin \}\)/);
    expect(MAIN).toMatch(/const DEFAULT_STARTUP_PREFS: StartupPrefs = \{ openAtLogin: true \}/);
    // 必须早于 createWindow：登录自启的意义就是"不用人手动开"
    const ready = MAIN.indexOf('app.whenReady()');
    const apply = MAIN.indexOf('applyStartupPrefs()', ready);
    const window = MAIN.indexOf('await createWindow()', ready);
    expect(apply).toBeGreaterThan(ready);
    expect(apply).toBeLessThan(window);
  });

  it('开关读的是操作系统的答案，而不是我们写的那个文件', () => {
    expect(MAIN).toMatch(/app\.getLoginItemSettings\(\)/);
  });

  it('IPC 名字在 main / preload / 类型三处一致', () => {
    for (const channel of ['electron:get-startup-prefs', 'electron:set-startup-prefs']) {
      expect(MAIN).toContain(channel);
      expect(PRELOAD).toContain(channel);
    }
    expect(PRELOAD).toContain('getStartupPrefs:');
    expect(PRELOAD).toContain('setStartupPrefs:');
    expect(TYPES).toContain('getStartupPrefs?:');
    expect(TYPES).toContain('setStartupPrefs?:');
  });

  it('网页版不渲染这个开关（没这个能力就不要暗示有）', () => {
    expect(PAGE).toMatch(/window\.electronEnv\?\.getStartupPrefs \? <StartupToggle \/> : null/);
  });
});
