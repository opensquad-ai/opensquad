import { describe, expect, it } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';

import { parseCollabTask, stripCollabTaskMarker } from '../CollabTaskCard';
import { isResolvedApprovalMessage, parseCollabApproval } from '../CollabStepApprovalCard';

const cardContent = (overrides: Record<string, unknown> = {}) => {
  const payload = {
    v: 1,
    id: 'ctask_abc123',
    kind: 'invite',
    collab_id: 'AB12CD',
    title: '发布流程',
    summary: '做一个审核流程',
    card: 'software_dev_team',
    participants: [
      { agent_id: 'qa', name: 'QA', state: 'invited' },
      { agent_id: 'coder', name: 'coder', state: 'accepted' },
    ],
    ...overrides,
  };
  return `[[COLLAB_TASK]]${JSON.stringify(payload)}[[/COLLAB_TASK]]\n🤝 协作邀请：发布流程\nTask ID: AB12CD`;
};

// This test file lives in components/ai-chat/, so ../.. is nexuschat-pro.
const read = (rel: string) =>
  fs.readFileSync(path.resolve(__dirname, '../..', rel), 'utf8');

describe('collaboration-task card', () => {
  it('parses a card out of message content', () => {
    const payload = parseCollabTask(cardContent());
    expect(payload?.collab_id).toBe('AB12CD');
    expect(payload?.kind).toBe('invite');
    expect(payload?.title).toBe('发布流程');
    expect(payload?.card).toBe('software_dev_team');
    expect(payload?.participants?.map((p: { state: string }) => p.state)).toEqual(['invited', 'accepted']);
  });

  it('rejects content without a valid card', () => {
    expect(parseCollabTask('')).toBeNull();
    expect(parseCollabTask('plain text')).toBeNull();
    expect(parseCollabTask('[[COLLAB_TASK]]not json[[/COLLAB_TASK]]')).toBeNull();
    // missing collab_id / id is not a card we can open
    expect(parseCollabTask('[[COLLAB_TASK]]{"id":"ctask_1"}[[/COLLAB_TASK]]')).toBeNull();
    expect(parseCollabTask('[[COLLAB_TASK]]{"collab_id":"AB12CD"}[[/COLLAB_TASK]]')).toBeNull();
  });

  it('does not collide with the approval markers', () => {
    const approvalContent = '[[GROUP_APPROVAL]]{"id":"appr_1","title":"t"}[[/GROUP_APPROVAL]]\n✋ 批准请求';
    expect(parseCollabTask(approvalContent)).toBeNull();
    expect(parseCollabApproval(cardContent())).toBeNull();
    // stripping only removes its own marker
    const stripped = stripCollabTaskMarker(cardContent());
    expect(stripped).not.toContain('[[COLLAB_TASK]]');
    expect(stripped).toContain('Task ID: AB12CD');
    expect(stripCollabTaskMarker(approvalContent)).toBe(approvalContent);
  });

  it('is rendered by group chat and by the DM window', () => {
    const chatWindow = read('components/ChatWindow.tsx');
    expect(chatWindow).toMatch(/parseCollabTask/);
    expect(chatWindow).toMatch(/<CollabTaskCard/);
    // interactive cards drop the bubble chrome in group chat
    expect(chatWindow).toMatch(/isInteractiveCard = !!\(/);
    expect(chatWindow).toMatch(/interactiveCollabTask/);

    const dmWindow = read('components/DirectChatWindow.tsx');
    expect(dmWindow).toMatch(/parseCollabTask\(b\.message\.content\)/);
    expect(dmWindow).toMatch(/<CollabTaskCard/);
  });

  it('opens the task window from the card through the host', () => {
    const app = read('App.tsx');
    expect(app).toMatch(/window\.addEventListener\('openCollabTask'/);
    // the window is mounted with the id it was opened for (props may span lines)
    expect(app).toMatch(/<CollabTaskWindow[\s\S]{0,120}?collabId=\{openCollabTaskId\}/);
    expect(app).toContain('viewerName={currentUser?.name');

    const card = read('components/CollabTaskCard.tsx');
    expect(card).toMatch(/window\.dispatchEvent\(new CustomEvent\('openCollabTask'/);
  });

  it('lets an invite be accepted from the card', () => {
    const chatWindow = read('components/ChatWindow.tsx');
    expect(chatWindow).toMatch(/respondCollabTask\(groupId, collabTask\.collab_id, action/);

    const api = read('services/api.ts');
    expect(api).toMatch(/respondCollabTask: async \(/);
    expect(api).toMatch(/\/groups\/\$\{groupId\}\/collab-tasks\/\$\{collabId\}\/respond/);
  });

  it('loads the window from the board summary endpoint', () => {
    const api = read('services/api.ts');
    expect(api).toMatch(/taskSummary: \(taskId: string\) =>/);
    expect(api).toMatch(/\/ai-web\/collab-board\/tasks\/\$\{encodeURIComponent\(taskId\)\}\/summary/);

    const win = read('components/CollabTaskWindow.tsx');
    expect(win).toMatch(/collabBoardAPI\.taskSummary\(collabId\)/);
    // the window covers every section the task record has.
    // No `progress`: the auto-synced tool feed was retired — the collaboration
    // mechanism no longer needs it (see collabTaskWindow.test.ts).
    for (const key of ['requirement', 'plan', 'assign', 'files', 'skills', 'discussion']) {
      expect(win).toContain(`collabTask.${key}`);
    }
    // ...including the approval gates, resolved from inside the window
    expect(win).toContain('collabTask.approvals');
    expect(win).toMatch(/data-testid="collab-task-gates"/);
    expect(win).toMatch(/data-testid="collab-task-approvals"/);
    expect(win).toMatch(/GATES = \['确定需求', '讨论方案', '任务分配', '任务验收'\]/);
    expect(win).toMatch(/messageAPI\.resolveCollabApproval\(groupId, approvalId, action, \{ messageId \}\)/);
    // a gate that was never posted to a group explains itself instead of failing
    expect(win).toContain('collabTask.approvalUnavailable');

    // attachments: images preview, everything downloads, URLs resolved against
    // the gateway that stores them
    expect(win).toContain('collabTask.attachments');
    expect(win).toMatch(/data-testid="collab-attachment"/);
    expect(win).toMatch(/a\.kind === 'image'/);
    expect(win).toMatch(/const href = a\.url\.startsWith\('http'\) \? a\.url : `\$\{SERVER_BASE_URL\}\$\{a\.url\}`/);

    const apiTypes = read('services/api.ts');
    expect(apiTypes).toMatch(/export interface CollabBoardAttachment/);
    expect(apiTypes).toMatch(/attachments: CollabBoardAttachment\[\]/);
  });

  it('an answered approval leaves the chat, a pending one stays', () => {
    const marker = (status: string) =>
      `[[COLLAB_APPROVAL]]${JSON.stringify({ id: 'a1', kind: 'collab_step', title: '任务验收', status })}[[/COLLAB_APPROVAL]]`;

    expect(isResolvedApprovalMessage(marker('pending'))).toBe(false);
    expect(isResolvedApprovalMessage(marker('approved'))).toBe(true);
    expect(isResolvedApprovalMessage(marker('rejected'))).toBe(true);
    expect(isResolvedApprovalMessage('普通消息')).toBe(false);

    const chatWindow = read('components/ChatWindow.tsx');
    expect(chatWindow).toContain('isResolvedApprovalMessage(m.content');
    const card = read('components/CollabStepApprovalCard.tsx');
    expect(card).toContain('if (!pending) return null;');
  });

  it('a gate verdict is a quiet line, not a bubble', () => {
    const win = read('components/ChatWindow.tsx');

    // the verdicts join the plain-tip branch: small, muted, no bubble
    expect(win).toContain('✅\\s*协作环节已批准');
    expect(win).toContain('❌\\s*协作环节已拒绝');
    expect(win).toContain('text-[11px]');
    // a rejection still reads as one, in a quiet rose rather than a banner
    expect(win).toContain('text-rose-500/80');
    // the soft banner remains only for notices that are not plain tips
    expect(win).toMatch(/plainTip \? \(/);
  });

  it('a pinned collaboration card is drawn as the card, not as wire text', () => {
    const win = read('components/ChatWindow.tsx');
    const pinStart = win.indexOf('{pinnedMessages.map(pm => {');
    // the whole pinned-row block, plain-text fallback included
    const pinBlock = win.slice(pinStart, win.indexOf('})}', pinStart));

    // pinned rows are message text, so the marker arrives raw — parse it before printing it
    expect(pinBlock).toContain('parseCollabTask(pm.content)');
    expect(pinBlock).toContain('data-testid="pinned-collab-task"');
    // the same card the chat draws, opening the same window
    expect(pinBlock).toContain('<CollabTaskCard payload={pinnedTask}');
    expect(pinBlock).toContain('onOpen={openCollabTaskWindow}');
    // and the card's clicks must not also jump the chat behind it
    expect(pinBlock).toContain('stopPropagation');
    // the plain-text fallback stays for ordinary pinned messages
    expect(pinBlock).toContain('line-clamp-2');
  });
});
