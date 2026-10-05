/**
 * 补充: the third answer an approval card was missing.
 *
 * 确定 and 拒绝 are both decisions, and neither fits "not this — here is what I need instead". The
 * only way to say that was to type into the group chat, where it was exposed to everyone and the
 * gate sat untouched. The small 补充 link opens an input whose text goes to the collaboration's
 * task window — the agent reads it there and is woken by it — and the gate stays pending, so the
 * user can still approve once they are satisfied. It is not a decision, so it must never reach the
 * resolve endpoint.
 */
import fs from 'fs';
import path from 'path';
import { describe, expect, it } from 'vitest';

const CARD = fs.readFileSync(path.resolve(__dirname, 'CollabStepApprovalCard.tsx'), 'utf8');

const supplementBody = CARD.slice(CARD.indexOf('const sendSupplement'), CARD.indexOf('const handle'));

describe('the supplement', () => {
  it('is offered on the collaboration gates, and only there', () => {
    expect(CARD).toContain("const canSupplement = kind === 'collab_step' && !!payload.collab_id;");
    expect(CARD).toContain('data-testid="approval-supplement-toggle"');
    expect(CARD).toContain('{canSupplement ? (');
  });

  it('posts into the task window, where the agent reads it', () => {
    expect(supplementBody).toContain('collabBoardAPI.postTaskMessage(String(payload.collab_id), text)');
  });

  it('is not a decision: it never resolves the gate', () => {
    expect(supplementBody).not.toContain('onResolve');
    expect(CARD).toContain('卡片的审批保持待办');
  });

  it('opens an input, sends, clears and says so', () => {
    expect(CARD).toContain('data-testid="approval-supplement"');
    expect(CARD).toContain('发送补充');
    expect(CARD).toMatch(/setSupplement\(''\)/);
    expect(CARD).toContain('data-testid="approval-supplement-sent"');
    expect(CARD).toContain('已补充，等待 agent 回应');
  });

  it('still has both decisions beside it', () => {
    expect(CARD).toContain("handle('approve')");
    expect(CARD).toContain("handle('reject')");
  });
});
