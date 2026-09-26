/**
 * Voice entry points — the mic records; the + menu owns the panel.
 *
 * The mic button used to *expand* the voice panel and leave the actual recording
 * to a second click inside it. It now records on the first click, and the panel
 * (tabs + model-card config) moved behind the 语音 row of the attach menu.
 *
 * Nothing throws when that wiring is lost — the mic just goes back to opening a
 * panel, which is the behaviour this file exists to prevent — so the links are
 * pinned by scanning the sources. The runtime behaviour (mic → recording pill →
 * transcribe into the box, panel closed throughout) was driven in a real
 * Chromium with a fake capture device.
 *
 *   R1  the mic starts capture through the composer's capture API — it does not
 *       open the panel, and the API really exposes `startRecord`;
 *   R2  the panel is reachable from the attach menu's voice row;
 *   R3  a capture failure the user cannot see (panel closed) opens the panel,
 *       so a denied microphone is never a silently dead button.
 *
 * Mutations verified:
 *   MA1 point the mic back at `toggleVoicePanel`      → R1
 *   MA2 drop `onOpenVoice` from the composer          → R2
 *   MA3 drop the `onError` wiring                     → R3
 */
import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';

const ROOT = path.resolve(__dirname, '..');
const read = (rel: string) => fs.readFileSync(path.join(ROOT, rel), 'utf8');
/** Source with comments stripped — these rules are about code, not prose. */
const code = (src: string) => src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');

const COMPOSER = code(read('components/ai-chat/AgentWebComposer.tsx'));
const MENU = code(read('components/ai-chat/SoloAttachMenu.tsx'));
const PANEL = code(read('components/ai-chat/VoicePanel.tsx'));

/** The composer's mic button — the branch that runs when no call is active. */
const micButton = (() => {
  const from = COMPOSER.indexOf(') : voiceEnabled ? (');
  if (from < 0) return '';
  const to = COMPOSER.indexOf(') : null}', from);
  return to < 0 ? '' : COMPOSER.slice(from, to);
})();

describe('R1 — the mic records instead of expanding', () => {
  it('starts capture straight from the click', () => {
    expect(micButton).toMatch(/else startVoiceRecord\(\)/);
    expect(COMPOSER).toMatch(/const startVoiceRecord = useCallback/);
    expect(COMPOSER).toMatch(/api\.startRecord\(\)/);
  });

  it('only falls back to the panel while a call is running', () => {
    // The panel is the only place to hang up / mute, so that branch stays.
    expect(micButton).toMatch(/if \(voiceCallActive\) toggleVoicePanel\(\)/);
    expect(micButton).not.toMatch(/onClick=\{toggleVoicePanel\}/);
  });

  it('the capture API really exposes startRecord to the composer', () => {
    expect(PANEL).toMatch(/interface VoiceCaptureApi \{[\s\S]{0,80}?startRecord: \(\) => void;/);
    expect(PANEL).toMatch(/captureApiRef\.current = \{\s*startRecord: \(\) => void startRecord\(\),\s*stopRecord,\s*\}/);
    expect(COMPOSER).toMatch(/useRef<VoiceCaptureApi \| null>\(null\)/);
  });

  it('and an unfocused pane is retried, not dropped', () => {
    // Capture lives in the focused pane's VoicePanel; on another pane the ref
    // is not set yet, so the click focuses and retries for a frame.
    expect(COMPOSER).toMatch(/requestAnimationFrame\(\(\) => attempt\(triesLeft - 1\)\)/);
  });
});

describe('R2 — the panel lives behind the attach menu', () => {
  it('the menu renders a voice row that opens it', () => {
    expect(MENU).toMatch(/onOpenVoice\?: \(\) => void;/);
    expect(MENU).toMatch(/onClick=\{\(\) => run\(onOpenVoice\)\}/);
    expect(MENU).toMatch(/aiChat\.voice/);
  });

  it('the composer wires that row to the panel toggle', () => {
    expect(COMPOSER).toMatch(/onOpenVoice=\{toggleVoicePanel\}/);
    expect(COMPOSER).toMatch(/voiceEnabled=\{voiceEnabled\}/);
  });

  it('and the label is translated, not an English literal', () => {
    const zh = JSON.parse(read('locales/zh.json'));
    const en = JSON.parse(read('locales/en.json'));
    expect(zh.aiChat.voice).toBeTruthy();
    expect(en.aiChat.voice).toBeTruthy();
  });
});

describe('R3 — a hidden capture failure opens the panel', () => {
  it('the panel reports the failure to the composer', () => {
    expect(PANEL).toMatch(/onError\?: \(message: string\) => void;/);
    expect(PANEL).toMatch(/if \(message\) onErrorRef\.current\?\.\(message\)/);
  });

  it('and the composer reveals the panel to show it', () => {
    expect(COMPOSER).toMatch(/onError=\{\(\) => onVoicePanelOpenChange\?\.\(true\)\}/);
  });
});
