import { expect, test, type Page } from '@playwright/test';
import { clearSession, loginAsSmokeUser } from './helpers';

const AGENT_ID = process.env.E2E_AGENT_ID || 'agent305-001';
const AGENT_NAME = process.env.E2E_AGENT_NAME || 'Agent305';
const TURNS = Number(process.env.E2E_TURNS || '2');
const MESSAGES = (
  process.env.E2E_MESSAGES ||
  '你好，请简单介绍一下你自己，不要使用工具。|记住你刚才说的，然后回答：2+2等于多少？不要使用工具。'
).split('|');

type WsFrame = {
  ts: number;
  type: string;
  sid?: string;
  content?: string;
};

function recordWsFrames(page: Page, frames: WsFrame[]) {
  page.on('websocket', (ws) => {
    ws.on('framereceived', (event) => {
      const payload = event.payload;
      if (typeof payload !== 'string') return;
      try {
        const data = JSON.parse(payload);
        if (!data || typeof data.type !== 'string') return;
        frames.push({
          ts: Date.now(),
          type: data.type,
          sid: data.sid || data.session_id || undefined,
          content: typeof data.content === 'string' ? data.content : undefined,
        });
      } catch {
        /* non-JSON ping/frame */
      }
    });
  });
}

async function waitForNewFinal(frameBaseline: number, frames: WsFrame[], timeoutMs = 120_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const finals = frames.filter(
      (f) => f.ts >= 0 && f.type === 'message' && f.content && f.content.trim().length > 0,
    );
    if (finals.length > frameBaseline) {
      return finals[finals.length - 1];
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error(`No new final message after ${timeoutMs}ms (baseline=${frameBaseline})`);
}

test('Agent305 new session + multi-turn chat', async ({ page }) => {
  test.setTimeout(300_000);
  const wsFrames: WsFrame[] = [];
  const timeline: Array<{ event: string; ms: number; detail?: string }> = [];
  const mark = (event: string, detail?: string) => {
    timeline.push({ event, ms: Date.now(), detail });
    console.log(`[E2E_TIMELINE] ${event} ${detail || ''}`);
  };

  recordWsFrames(page, wsFrames);
  await clearSession(page);
  mark('login_start');
  await loginAsSmokeUser(page);
  mark('login_done');

  const shortcut = page.getByTitle(AGENT_NAME).first();
  await expect(shortcut).toBeVisible({ timeout: 30_000 });
  mark('agent_shortcut_visible');
  await shortcut.click();

  let composer = page.getByPlaceholder(/输入消息/).first();
  await expect(composer).toBeVisible({ timeout: 60_000 });
  mark('ai_chat_ready');

  const newSession = page.getByText('新建对话', { exact: true }).first();
  if (await newSession.isVisible().catch(() => false)) {
    await newSession.click();
    mark('new_session_clicked');
    // New session is confirmed only after the Agent rotates to a fresh sid and
    // the composer lands on the empty-session prompt. Sending before this can
    // race with the rotation and route the message to the previous session.
    const landing = page.getByPlaceholder(/帮我把这个想法/).first();
    await expect(landing).toBeVisible({ timeout: 20_000 });
    composer = landing;
  } else {
    mark('new_session_skipped');
  }

  await expect(composer).toBeEnabled({ timeout: 30_000 });

  for (let i = 0; i < Math.min(TURNS, MESSAGES.length); i++) {
    const message = MESSAGES[i];
    const frameBaseline = wsFrames.filter((f) => f.type === 'message' && f.content).length;
    mark(`turn_${i + 1}_send_start`, message.slice(0, 30));
    composer = page.getByPlaceholder(/输入消息|帮我把这个想法/).first();
    await expect(composer).toBeVisible({ timeout: 30_000 });
    await expect(composer).toBeEnabled({ timeout: 30_000 });
    await composer.fill(message);
    await page.getByTitle('Send').first().click();

    const finalFrame = await waitForNewFinal(frameBaseline, wsFrames);
    mark(`turn_${i + 1}_final_received`, finalFrame.content?.slice(0, 60) || '');

    const sendStart = timeline[timeline.length - 2].ms;
    const latency = finalFrame.ts - sendStart;
    console.log(`[E2E_TURN] turn=${i + 1} latency_ms=${latency} final_len=${finalFrame.content?.length || 0}`);

    // Let streaming/workflow settle before the next turn.
    await page.waitForTimeout(1500);
  }

  const finalMessages = wsFrames.filter((f) => f.type === 'message' && f.content);
  console.log(`[E2E_RESULT] turns=${Math.min(TURNS, MESSAGES.length)} final_messages=${finalMessages.length}`);
  console.log(`[E2E_TIMELINE_JSON] ${JSON.stringify(timeline)}`);
  expect(finalMessages.length).toBeGreaterThanOrEqual(Math.min(TURNS, MESSAGES.length));
});
