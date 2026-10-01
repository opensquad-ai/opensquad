import { describe, expect, it } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';

import { DM_QUOTE_MAX, DM_QUOTE_START, encodeDmQuote, parseDmQuote, stripDmQuote } from '../../utils/dmQuote';
import { parseCollabTask } from '../CollabTaskCard';

const read = (rel: string) => fs.readFileSync(path.resolve(__dirname, '../..', rel), 'utf8');

describe('DM quotes', () => {
  it('round-trips a quote and keeps the visible body', () => {
    const content = `${encodeDmQuote({ id: 'm1', name: 'Agent305', text: '你好' })}收到，我看看`;
    const { quote, body } = parseDmQuote(content);
    expect(quote).toEqual({ id: 'm1', name: 'Agent305', text: '你好' });
    expect(body).toBe('收到，我看看');
    expect(body).not.toContain(DM_QUOTE_START);
    expect(stripDmQuote(content)).toBe('收到，我看看');
  });

  it('caps the quoted excerpt so bubbles and prompts stay small', () => {
    const long = 'x'.repeat(DM_QUOTE_MAX + 50);
    const { quote } = parseDmQuote(encodeDmQuote({ name: 'a', text: long }));
    expect(quote?.text).toHaveLength(DM_QUOTE_MAX + 1); // clipped + ellipsis
    expect(quote?.text.endsWith('…')).toBe(true);
  });

  it('leaves content without a valid quote untouched', () => {
    expect(parseDmQuote(undefined)).toEqual({ quote: null, body: '' });
    expect(parseDmQuote('plain text')).toEqual({ quote: null, body: 'plain text' });
    expect(parseDmQuote(`${DM_QUOTE_START}not json[[/DM_QUOTE]]\nhi`)).toEqual({
      quote: null,
      body: `${DM_QUOTE_START}not json[[/DM_QUOTE]]\nhi`,
    });
    // a marker without text is not a quote
    expect(parseDmQuote(`${DM_QUOTE_START}{"name":"a"}[[/DM_QUOTE]]\nhi`).quote).toBeNull();
  });

  it('does not confuse a quote with a collaboration card', () => {
    const quoted = `${encodeDmQuote({ name: 'a', text: 'hi' })}body`;
    expect(parseCollabTask(quoted)).toBeNull();
    const card = `[[COLLAB_TASK]]{"id":"ctask_1","collab_id":"AB12CD","kind":"invite","title":"t"}[[/COLLAB_TASK]]`;
    expect(parseDmQuote(card).quote).toBeNull();
  });

  it('is wired into the DM window and the bubble', () => {
    const dm = read('components/DirectChatWindow.tsx');
    expect(dm).toMatch(/const content = replyTo \? `\$\{encodeDmQuote\(replyTo\)\}\$\{text\}` : text/);
    expect(dm).toMatch(/parseDmQuote\(m\.content\)/);
    expect(dm).toMatch(/data-testid="dm-reply-banner"/);
    expect(dm).toMatch(/onReply=\{handleReplyStart\}/);

    const bubble = read('components/ai-chat/MessageBubble.tsx');
    expect(bubble).toMatch(/data-testid="msg-quote"/);
    expect(bubble).toMatch(/onReply\?: \(message: ChatMessage\) => void/);
    expect(bubble).toMatch(/onClick=\{\(\) => onReply\(message\)\}/);
  });
});
