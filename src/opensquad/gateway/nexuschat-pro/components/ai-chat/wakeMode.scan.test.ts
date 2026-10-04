/**
 * Wake mode, chosen from the composer's "+" menu.
 *
 * It writes the agent's own `default_wake_mode` — the value its boot reads into the state manager
 * and the message router consults when deciding whether a message wakes a sleeping agent. A local
 * preference would have looked right and changed nothing on the agent side.
 */
import fs from 'fs';
import path from 'path';
import { describe, expect, it } from 'vitest';

const read = (rel: string) => fs.readFileSync(path.resolve(__dirname, rel), 'utf8');

const MENU = read('./SoloAttachMenu.tsx');
const COMPOSER = read('./AgentWebComposer.tsx');

describe('the menu offers both wake modes behind a second-level row', () => {
  it('is one row that names the mode in force, not a pair of rows', () => {
    expect(MENU).toContain('data-testid="wake-mode-row"');
    expect(MENU).toContain(
      "t(wakeMode === 'strict' ? 'aiChat.attach.wakeModeStrict' : 'aiChat.attach.wakeModeNormal')",
    );
    expect(MENU).toContain('<ChevronRight size={13}');
  });

  it('opens its own panel, kept mounted so the pop-out can play', () => {
    expect(MENU).toContain('usePopMenuMounted(wakeOpen)');
    expect(MENU).toContain('{wakeMounted && (');
  });

  it('renders one choice per mode, tagged for tests', () => {
    expect(MENU).toContain('data-wake-mode={option.mode}');
    expect(MENU).toContain("mode: 'strict' as const");
    expect(MENU).toContain("mode: 'normal' as const");
  });

  it('marks the mode in force', () => {
    expect(MENU).toContain('wakeMode === option.mode');
    expect(MENU).toContain('<Check size={13}');
  });

  it('reports the choice and closes the menu', () => {
    expect(MENU).toContain('onClick={() => run(() => onWakeMode(option.mode))}');
  });

  it('is only shown when the composer wires it up', () => {
    expect(MENU).toContain('{onWakeMode ? (');
  });

  it('and the skills panel are never open together', () => {
    expect(MENU).toContain('setSkillsOpen(false); setWakeOpen(false);');
    expect(MENU).toMatch(/setWakeOpen\(true\);\s*\n\s*setSkillsOpen\(false\);/);
  });
});

describe('the composer persists it as the agent’s wake mode', () => {
  it('reads the stored value for this agent', () => {
    expect(COMPOSER).toContain("(res?.config || {})['default_wake_mode']");
    expect(COMPOSER).toContain('adminAPI.getConfig(agentId)');
  });

  it('writes it back without dropping the rest of the config', () => {
    expect(COMPOSER).toContain("config['default_wake_mode'] = mode;");
    expect(COMPOSER).toContain('await adminAPI.updateConfig(agentId, config)');
    expect(COMPOSER).toContain('...((res?.config || {}) as Record<string, unknown>)');
  });

  it('passes both the value and the setter to the menu', () => {
    expect(COMPOSER).toContain('wakeMode={wakeMode}');
    expect(COMPOSER).toContain('onWakeMode={changeWakeMode}');
  });
});

describe('its wording', () => {
  it('is translated in both locales, with Chinese differing from English', () => {
    const zh = JSON.parse(read('../../locales/zh.json'));
    const en = JSON.parse(read('../../locales/en.json'));

    for (const key of ['wakeMode', 'wakeModeStrict', 'wakeModeStrictHint', 'wakeModeNormal', 'wakeModeNormalHint']) {
      expect(zh.aiChat.attach[key], `zh ${key}`).toBeTruthy();
      expect(en.aiChat.attach[key], `en ${key}`).toBeTruthy();
      expect(zh.aiChat.attach[key]).not.toBe(en.aiChat.attach[key]);
    }
    expect(zh.aiChat.attach.wakeModeStrict).toMatch(/[\u4e00-\u9fff]/);
  });
});
