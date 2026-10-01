import { describe, expect, it } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';

import { parseCollabTask, stripCollabTaskMarker } from '../CollabTaskCard';
import { parseCollabApproval } from '../CollabStepApprovalCard';

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
    expect(chatWindow).toMatch(/isInteractiveCard = !!\(interactiveApproval \|\| interactiveProposal \|\| interactiveCollabTask\)/);

    const dmWindow = read('components/DirectChatWindow.tsx');
    expect(dmWindow).toMatch(/parseCollabTask\(b\.message\.content\)/);
    expect(dmWindow).toMatch(/<CollabTaskCard/);
  });

  it('opens the task window from the card through the host', () => {
    const app = read('App.tsx');
    expect(app).toMatch(/window\.addEventListener\('openCollabTask'/);
    expect(app).toMatch(/<CollabTaskWindow collabId=\{openCollabTaskId\}/);

    const card = read('components/CollabTaskCard.tsx');
    expect(card).toMatch(/window\.dispatchEvent\(new CustomEvent\('openCollabTask'/);
  });

  it('loads the window from the board summary endpoint', () => {
    const api = read('services/api.ts');
    expect(api).toMatch(/taskSummary: \(taskId: string\) =>/);
    expect(api).toMatch(/\/ai-web\/collab-board\/tasks\/\$\{encodeURIComponent\(taskId\)\}\/summary/);

    const win = read('components/CollabTaskWindow.tsx');
    expect(win).toMatch(/collabBoardAPI\.taskSummary\(collabId\)/);
    // the window covers every section the task record has
    for (const key of ['requirement', 'plan', 'assign', 'progress', 'files', 'skills', 'discussion']) {
      expect(win).toContain(`collabTask.${key}`);
    }
  });
});
