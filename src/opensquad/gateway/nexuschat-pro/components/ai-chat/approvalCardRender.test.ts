/**
 * An approval card must never be painted as its own raw JSON.
 *
 * Reported from using it: a step gate (`[[COLLAB_APPROVAL]]{"v":1,…}[[/COLLAB_APPROVAL]]` plus its
 * readable text) showed up as one big chat bubble, and after clicking 确定 it stayed there instead
 * of collapsing to a quiet line. Two causes, both pinned here:
 *
 *   1. The parser required the closing tag, so a card whose end marker was lost parsed as *no
 *      card at all* — and the chat rendered the marker literally. That also kept the backend from
 *      patching the decision back in (same regex), so the card never read as answered.
 *   2. Only the group chat had an approval branch. A DM or an agent-chat bubble fell through to
 *      the plain-text path even when the marker was perfectly well formed.
 *
 * The rule this file locks: a *pending* card is a card; an *answered* one is one small grey line
 * with no bubble.
 */
import fs from 'fs';
import path from 'path';
import { describe, expect, it } from 'vitest';

import {
  approvalQuietLine,
  isResolvedApprovalMessage,
  parseCollabApproval,
  readApprovalMarker,
  stripCollabApprovalMarker,
} from '../CollabStepApprovalCard';

const approve = (status = 'pending', extra = '') =>
  `[[COLLAB_APPROVAL]]{"v":1,"id":"appr_1","kind":"collab_step","title":"任务验收","status":"${status}"${extra}}[[/COLLAB_APPROVAL]]`;

const read = (rel: string) => fs.readFileSync(path.resolve(__dirname, rel), 'utf8');

describe('tolerating a lost closing tag', () => {
  it('parses a marker that has no end tag', () => {
    const body = '[[COLLAB_APPROVAL]]{"v":1,"id":"appr_1","title":"任务验收","status":"approved"}';

    const found = readApprovalMarker(body);

    expect(found?.payload.id).toBe('appr_1');
    expect(isResolvedApprovalMessage(body)).toBe(true);
  });

  it('parses braces inside a value (a summary full of JSON)', () => {
    const body = approve('approved', ',"summary":"用例 {\\"a\\": 1} 通过"');

    const payload = parseCollabApproval(body);

    expect(payload?.summary).toBe('用例 {"a": 1} 通过');
    expect(payload?.status).toBe('approved');
  });

  it('parses the legacy GROUP_APPROVAL marker too', () => {
    const body = '[[GROUP_APPROVAL]]{"id":"appr_2","kind":"mode_switch","title":"切换","status":"pending"}';

    expect(parseCollabApproval(body)?.id).toBe('appr_2');
  });

  it('reports the span so the marker can be dropped from the text', () => {
    const body = `📋 协作批准请求：任务验收\n${approve('pending')}\n请在下方卡片中点击「确定」或「拒绝」。`;

    const stripped = stripCollabApprovalMarker(body);

    expect(stripped).toBe('');
    expect(parseCollabApproval(body)).not.toBeNull();
  });

  it('still refuses content that is not a card', () => {
    expect(parseCollabApproval('just a message')).toBeNull();
    expect(parseCollabApproval('[[COLLAB_APPROVAL]] not json [[/COLLAB_APPROVAL]]')).toBeNull();
  });
});

describe('the quiet line an approval leaves behind', () => {
  it('says approved / rejected / still pending', () => {
    expect(approvalQuietLine(parseCollabApproval(approve('approved'))!)).toBe('✅ 协作环节已批准：任务验收');
    expect(approvalQuietLine(parseCollabApproval(approve('rejected'))!)).toBe('❌ 协作环节已拒绝：任务验收');
    expect(approvalQuietLine(parseCollabApproval(approve('pending'))!)).toBe('📋 批准请求：任务验收');
  });
});

describe('every renderer that can meet a marker', () => {
  it('the group chat card renders for pending and filters answered ones out', () => {
    const chat = read('../ChatWindow.tsx');

    expect(chat).toContain('parseCollabApproval');
    expect(chat).toContain('isResolvedApprovalMessage(m.content');
    expect(chat).toContain('<CollabStepApprovalCard');
  });

  it('a pinned approval is a card, and an answered one is the quiet line', () => {
    const chat = read('../ChatWindow.tsx');

    expect(chat).toContain('pinned-collab-approval');
    expect(chat).toContain('approvalQuietLine(pinnedApproval)');
  });

  it('the DM window has an approval branch (it used to have none)', () => {
    const dm = read('../DirectChatWindow.tsx');

    expect(dm).toContain('parseCollabApproval(b.message.content)');
    expect(dm).toContain('<CollabStepApprovalCard');
    expect(dm).toContain('approvalQuietLine(approval)');
    expect(dm).toContain('resolveCollabApproval(approvalGroup, approval.id');
  });

  it('the agent-chat bubble shows the quiet line, never the marker', () => {
    const bubble = read('MessageBubble.tsx');

    expect(bubble).toContain('approval-quiet-line');
    expect(bubble).toContain('approvalQuietLine(approvalCard)');
    expect(bubble).toContain('parseCollabApproval(safeContent)');
  });
});
