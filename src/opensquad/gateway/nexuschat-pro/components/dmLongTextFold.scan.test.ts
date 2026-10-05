/**
 * The direct-message window folds a long body; the agent session does not.
 *
 * Same bubble component behind both, so the fold is opt-in: `DirectChatWindow` asks for it (the
 * 文件助手 on the other side of a DM is a person or an agent pasting prose, which is what covered
 * the screen) while the agent session leaves bodies whole, since a long answer there is a result to
 * read rather than a wall to skip. Without the flag the old path runs unchanged.
 */
import fs from 'fs';
import path from 'path';
import { describe, expect, it } from 'vitest';

const read = (rel: string) => fs.readFileSync(path.resolve(__dirname, rel), 'utf8');

const BUBBLE = read('./ai-chat/MessageBubble.tsx');
const DM = read('./DirectChatWindow.tsx');
const CHAT = read('./ChatWindow.tsx');

describe('the direct-message window', () => {
  it('asks the bubble to fold', () => {
    expect(DM).toContain('foldLongText\n');
    expect(DM).toContain('<MessageBubble');
  });
});

describe('the bubble', () => {
  it('folds a long body by default, and can be told not to', () => {
    expect(BUBBLE).toContain("foldLongText && message.role === 'user' && shouldFold(safeContent) ? (");
    expect(BUBBLE).toContain('foldLongText = true');
    expect(BUBBLE).toContain('foldLongText?: boolean;');
  });

  it('never folds what an agent wrote, however long it is', () => {
    // The rule lives in the bubble, so it covers the direct-message window, the agent session and
    // the group chat in one place; the group chat applies the same test through the sender.
    expect(BUBBLE).toContain("message.role === 'user'");
    expect(CHAT).toContain('sender?.is_agent !== true && shouldFold(msg.content) ? (');
  });

  it('folds the raw content and renders each view through the same pipeline', () => {
    const folded = BUBBLE.slice(
      BUBBLE.indexOf("foldLongText && message.role === 'user' && shouldFold(safeContent) ? ("),
      BUBBLE.indexOf('{isStoppedTurn && !isUser &&', BUBBLE.indexOf("foldLongText && message.role === 'user' && shouldFold(safeContent) ? (")),
    );

    expect(folded).toContain('<LongTextFold');
    expect(folded).toContain('text={safeContent}');
    expect(folded).toContain('render={(text) => (');
  });

  it('leaves the body untouched otherwise, stopped badge and all', () => {
    expect(BUBBLE).toContain('dangerouslySetInnerHTML={{ __html: renderedHtml }}');
    expect(BUBBLE).toContain('data-testid="msg-stopped-badge"');
  });
});

describe('the group chat', () => {
  it('folds both of its message bodies, both gated on a human sender', () => {
    const sites = (CHAT.match(/sender\?\.is_agent !== true && shouldFold\(msg\.content\) \? \(/g) || []).length;

    expect(sites).toBe(2);
    expect(CHAT).toContain('<LongTextFold');
  });
});
