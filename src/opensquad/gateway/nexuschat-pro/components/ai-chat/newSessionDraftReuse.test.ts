// @vitest-environment node
/**
 * 「新会话」must not hand the next message to the previous conversation.
 *
 * Reported symptom: create a new session, send a message, and the message
 * continues in the most recent session instead (`chat` frame carried the OLD
 * sid — see the gateway's websocket.log).
 *
 * Cause: `handleNewSession` reuses the agent's current sid when it believes the
 * session is still an empty draft, and that decision was made from the live
 * per-session bucket alone. An empty bucket is not proof of an empty session:
 * any "clear the view" write leaves `{ sid: [] }`, and a session whose tab was
 * painted by another surface has no bucket at all — while the transcript sits
 * in the timeline cache / on disk. The branch then reused a 12-message sid, so
 * the next send was addressed to it.
 *
 * Mutations verified (script `C:/tmp/prov/mutate_new_session_draft.py`,
 * report `C:/tmp/prov/mutation_report_new_session_draft.json`):
 *   MS1 isEmptySessionDraft ignores the cached message count         → R1
 *   MS2 isEmptySessionDraft ignores the cached timeline entries      → R4
 *   MS3 isEmptySessionDraft returns false only on live content       → R3
 *   MS4 handleNewSession drops the cache lookup (bucket only)        → R5
 *   MS5 deliverMessage sends sid-less while a rotation is pending    → R6
 */
import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';

import { buildTimelineFromSession, isEmptySessionDraft } from '../../utils/aiChatTimeline';
import { putCachedSessionTimeline, getCachedSessionTimeline, getCachedSessionTimelineMeta } from '../../utils/sessionTimelineCache';

const ROOT = path.resolve(__dirname, '..', '..');
const read = (rel: string) => fs.readFileSync(path.join(ROOT, rel), 'utf8').replace(/\r\n/g, '\n');

const PAGE = read('components/AIChatPage.tsx');

/** A conversation like the one that got continued: user ask + reply. */
const realConversation = buildTimelineFromSession(
  [
    { role: 'user', content: '福州天气' },
    { role: 'assistant', content: '今天多云，29°C。' },
  ],
  [],
);

describe('空草稿判定 —— 只有所有证据都指向"空"才能复用旧 sid', () => {
  it('R1 — an empty live bucket must not outvote the session cache', () => {
    // Exactly the leak: the bucket was cleared to [], while the cached session
    // still knows about its messages. Each count is checked on its own — a
    // gate that consults only one of them is still wrong.
    expect(isEmptySessionDraft({
      liveEntries: [],
      cachedEntries: null,
      cachedMessageCount: 12,
    })).toBe(false);
    expect(isEmptySessionDraft({
      liveEntries: [],
      cachedEntries: null,
      cachedTotalMessages: 12,
    })).toBe(false);
    expect(isEmptySessionDraft({
      liveEntries: [],
      cachedEntries: null,
      cachedMessageCount: 12,
      cachedTotalMessages: 12,
    })).toBe(false);
  });

  it('R2 — a genuinely untouched draft stays reusable (no regression)', () => {
    // Nothing anywhere: reuse is the cheap path the backend also takes.
    expect(isEmptySessionDraft({
      liveEntries: [],
      cachedEntries: null,
      cachedMessageCount: 0,
      cachedTotalMessages: 0,
    })).toBe(true);
    expect(isEmptySessionDraft({
      liveEntries: null,
      cachedEntries: null,
      cachedMessageCount: undefined,
      cachedTotalMessages: undefined,
    })).toBe(true);
    // A bucket holding only an empty placeholder message is still a draft.
    const placeholder = buildTimelineFromSession([{ role: 'user', content: '' }], []);
    expect(isEmptySessionDraft({ liveEntries: placeholder })).toBe(true);
  });

  it('R3 — live visible chat is not a draft', () => {
    expect(isEmptySessionDraft({ liveEntries: realConversation })).toBe(false);
  });

  it('R4 — cached timeline content is not a draft, even without counts', () => {
    expect(isEmptySessionDraft({
      liveEntries: [],
      cachedEntries: realConversation,
    })).toBe(false);
  });

  it('R5 — handleNewSession consults the cache, not just the live bucket', () => {
    const at = PAGE.indexOf('const handleNewSession');
    expect(at).toBeGreaterThan(-1);
    const body = PAGE.slice(at, at + 1400);
    expect(body).toContain('isEmptySessionDraft(');
    // The cache is the evidence that survives a cleared bucket.
    expect(body).toContain('getCachedSessionTimelineMeta(agentId, draftSid)');
    expect(body).toContain('getCachedSessionTimeline(agentId, draftSid)');
    expect(body).toContain('cachedMessageCount');
    // The old bucket-only test must be gone — it is what let the leak through.
    expect(body).not.toMatch(/draftEmpty\s*=\s*!timelineHasVisibleChatContent/);
  });

  it('R6 — a sid-less send is refused while the new session is unconfirmed', () => {
    const at = PAGE.indexOf('    const targetSessionId = (payload.sessionId');
    expect(at).toBeGreaterThan(-1);
    const body = PAGE.slice(at, at + 6000);
    // The guard line itself, not a substring of it: `if (false && …` must not
    // read as present.
    const guard = body.indexOf('if (!targetSessionId && newSessionPendingRef.current) {');
    expect(guard, 'deliverMessage must gate on a pending new session').toBeGreaterThan(-1);
    // …and the guard must return before the WS send, keeping the composer text.
    const nextSend = body.indexOf('sendMessage(', guard);
    expect(nextSend, 'the WS send must still be reachable below the guard').toBeGreaterThan(-1);
    expect(body.slice(guard, nextSend)).toMatch(/return;/);
  });

  it('the cache helpers used by the gate return what the gate assumes', () => {
    // Guard against a signature drift that would silently pass undefined.
    putCachedSessionTimeline('agentX', 'sidX', realConversation, {
      complete: false,
      messageCount: 2,
      totalMessages: 12,
    });
    expect(getCachedSessionTimelineMeta('agentX', 'sidX')?.messageCount).toBe(2);
    expect(getCachedSessionTimelineMeta('agentX', 'sidX')?.totalMessages).toBe(12);
    expect(getCachedSessionTimeline('agentX', 'sidX')?.length).toBe(realConversation.length);
  });
});
