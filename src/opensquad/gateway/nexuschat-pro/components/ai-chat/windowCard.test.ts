import { describe, expect, it } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';

import { parseWindowCard, stripWindowCardMarker } from '../WindowCard';
import { parseCollabTask } from '../CollabTaskCard';
import { parseDmQuote } from '../../utils/dmQuote';

const read = (rel: string) => fs.readFileSync(path.resolve(__dirname, '../..', rel), 'utf8');

const cardContent = (overrides: Record<string, unknown> = {}) => {
  const payload = {
    v: 1,
    id: 'wcard_ab12cd34ef56',
    kind: 'report',
    title: '自建应用发布申请 · 自动审核通过',
    summary: '系统基于免审规则已自动审核通过。',
    view: {
      kind: 'table',
      columns: ['项', '结果'],
      rows: [
        ['申请人', 'quanker'],
        ['结论', '自动通过'],
      ],
    },
    actions: [{ id: 'act_1', label: '查看审核详情', intent: 'open_url', url: 'https://example.com' }],
    ...overrides,
  };
  return `[[WINDOW_CARD]]${JSON.stringify(payload)}[[/WINDOW_CARD]]\n🪟 卡片`;
};

describe('window cards', () => {
  it('parses a card out of message content', () => {
    const payload = parseWindowCard(cardContent());
    expect(payload?.id).toBe('wcard_ab12cd34ef56');
    expect(payload?.title).toContain('自动审核通过');
    expect(payload?.view.kind).toBe('table');
    expect(payload?.view.columns).toEqual(['项', '结果']);
    expect(payload?.actions?.[0].intent).toBe('open_url');
  });

  it('accepts every view kind', () => {
    for (const view of [
      { kind: 'sections', blocks: [{ title: '需求', text: 'x' }] },
      { kind: 'flow', steps: [{ title: '确定需求', status: 'done' }] },
      { kind: 'metrics', items: [{ label: '通过率', value: '98%' }] },
      { kind: 'raw', text: 'plain' },
    ]) {
      expect(parseWindowCard(cardContent({ view }))?.view.kind).toBe(view.kind);
    }
  });

  it('rejects content without a usable card', () => {
    expect(parseWindowCard('')).toBeNull();
    expect(parseWindowCard('plain text')).toBeNull();
    expect(parseWindowCard('[[WINDOW_CARD]]not json[[/WINDOW_CARD]]')).toBeNull();
    // id + title + view are all required to open a window
    expect(parseWindowCard('[[WINDOW_CARD]]{"title":"t","view":{"kind":"raw"}}[[/WINDOW_CARD]]')).toBeNull();
    expect(parseWindowCard('[[WINDOW_CARD]]{"id":"w1","view":{"kind":"raw"}}[[/WINDOW_CARD]]')).toBeNull();
    expect(parseWindowCard('[[WINDOW_CARD]]{"id":"w1","title":"t"}[[/WINDOW_CARD]]')).toBeNull();
  });

  it('does not collide with the other markers', () => {
    const collab = '[[COLLAB_TASK]]{"id":"ctask_1","collab_id":"AB12CD","kind":"invite","title":"t"}[[/COLLAB_TASK]]';
    const quote = '[[DM_QUOTE]]{"name":"a","text":"hi"}[[/DM_QUOTE]]\nbody';
    expect(parseWindowCard(collab)).toBeNull();
    expect(parseCollabTask(cardContent())).toBeNull();
    expect(parseDmQuote(cardContent()).quote).toBeNull();

    const stripped = stripWindowCardMarker(cardContent());
    expect(stripped).not.toContain('[[WINDOW_CARD]]');
    expect(stripped).toContain('🪟 卡片');
    expect(stripWindowCardMarker(collab)).toBe(collab);
  });

  it('is rendered in group chat and in the DM window', () => {
    const chatWindow = read('components/ChatWindow.tsx');
    expect(chatWindow).toMatch(/parseWindowCard\(msg\.content \|\| ''\)/);
    expect(chatWindow).toMatch(/return <WindowCard payload=\{windowCard\} messageId=\{msg\.id\} \/>/);
    expect(chatWindow).toMatch(/interactiveWindowCard/);

    const dmWindow = read('components/DirectChatWindow.tsx');
    expect(dmWindow).toMatch(/parseWindowCard\(b\.message\.content\)/);
    expect(dmWindow).toMatch(/<WindowCard payload=\{windowCard\} messageId=\{b\.id\} \/>/);
  });

  it('opens a generic window from the payload alone', () => {
    const app = read('App.tsx');
    expect(app).toMatch(/window\.addEventListener\('openWindowCard'/);
    expect(app).toMatch(/<WindowCardWindow/);

    const card = read('components/WindowCard.tsx');
    expect(card).toMatch(/window\.dispatchEvent\(new CustomEvent\('openWindowCard'/);

    const win = read('components/WindowCardWindow.tsx');
    for (const kind of ['table', 'flow', 'metrics', 'sections', 'raw']) {
      expect(win).toContain(`kind === '${kind}'`);
    }
    // an unknown kind degrades to the raw payload instead of rendering nothing
    expect(win).toMatch(/!\[['"]table['"], ['"]flow['"], ['"]metrics['"], ['"]sections['"], ['"]raw['"]\]\.includes\(kind\)/);
  });

  it('runs action buttons without a server round trip', () => {
    const card = read('components/WindowCard.tsx');
    expect(card).toMatch(/intent === 'open_url'/);
    expect(card).toMatch(/intent === 'copy'/);
    expect(card).toMatch(/intent === 'open_collab_task'/);
    expect(card).toMatch(/new CustomEvent\('windowCardAction'/);
  });

  it('carries an interactive form and the recorded answer', () => {
    const withForm = cardContent({
      view: {
        kind: 'sections',
        blocks: [{ title: '需求', text: 'x' }],
        form: {
          submit_label: '确认',
          fields: [
            { id: 'decision', label: '是否通过', type: 'radio', required: true, options: [{ id: 'yes', label: '通过' }] },
          ],
        },
      },
    });
    const payload = parseWindowCard(withForm);
    expect(payload?.view.form?.fields[0].id).toBe('decision');
    expect(payload?.view.form?.submit_label).toBe('确认');

    // after answering, the card carries the response and stops being 'open'
    const answered = parseWindowCard(
      cardContent({ state: 'answered', response: { action_id: 'submit', values: { decision: 'yes' }, by: 'aa' } }),
    );
    expect(answered?.state).toBe('answered');
    expect(answered?.response?.values).toEqual({ decision: 'yes' });
  });

  it('posts an answer back from the card and from the window', () => {
    const card = read('components/WindowCard.tsx');
    expect(card).toMatch(/export function answerWindowCard\(/);
    expect(card).toMatch(/windowCardAPI\.respond\(payload\.id/);
    expect(card).toMatch(/messageId=\{msg\.id\}|messageId,/);
    // confirm/decline buttons answer through the same endpoint
    expect(card).toMatch(/action\.intent === 'respond' \|\| action\.intent === 'confirm' \|\| action\.intent === 'decline'/);

    const win = read('components/WindowCardWindow.tsx');
    expect(win).toMatch(/data-testid="window-card-form"/);
    expect(win).toMatch(/data-testid="window-card-submit"/);
    expect(win).toMatch(/data-testid="window-card-answer"/);
    expect(win).toMatch(/await answerWindowCard\(payload, actionId, values, messageId\)/);

    const api = read('services/api.ts');
    expect(api).toMatch(/export const windowCardAPI/);
    expect(api).toMatch(/\/window-cards\/\$\{encodeURIComponent\(cardId\)\}\/respond/);

    // the window gets the message id it needs to answer
    const app = read('App.tsx');
    expect(app).toMatch(/onOpenWindowCard/);
    expect(app).toMatch(/messageId=\{openWindowCard\.messageId\}/);
  });
});
