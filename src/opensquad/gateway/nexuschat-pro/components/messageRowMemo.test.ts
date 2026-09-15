// @vitest-environment happy-dom
//
// Regression lock for O4a.
//
// ChatWindow used to render every message through a 478-line inline `.map()`.
// It now renders through a React.memo'd <MessageRow> (see MessageRowImpl /
// areMessageRowPropsEqual in ChatWindow.tsx).  That only pays off if the memo
// comparator is *complete*: miss one prop and the row silently stops updating —
// a stale bubble, a stale reply preview, an edit box that refuses to follow the
// keystrokes.  Nothing else in this repo would catch that.
//
// So this test forces every field of MessageRowProps to be *classified*:
//   · affects the markup  → must break equality
//   · identity-stable     → must not break equality (deliberate)
//   · conditional         → ignored unless an explicit escape hatch is set
//
// Adding a required prop to MessageRowProps breaks the object literal below at
// type-check time; making it optional still trips the "everything is classified"
// assertion.  Either way you cannot add a prop without thinking about memo.

import { describe, expect, it, vi } from 'vitest';

import { areMessageRowPropsEqual, type MessageRowActions, type MessageRowProps } from './ChatWindow';
import { MessageType, type Message, type User } from '../types';

const mkActions = (): MessageRowActions => ({
  saveEdit: vi.fn(),
  cancelEdit: vi.fn(),
  startEditing: vi.fn(),
  setReplyTo: vi.fn(),
  copyToClipboard: vi.fn(),
  showCopyToast: vi.fn(),
  contentClick: vi.fn(),
  loadMessagesAround: vi.fn(async () => {}),
  setDownloads: vi.fn(),
  setLightboxImages: vi.fn(),
  setLightboxIndex: vi.fn(),
  setShowLightbox: vi.fn(),
  setEditContent: vi.fn(),
  onPinMessage: vi.fn(),
  onDeleteMessage: vi.fn(),
  onUndoRecall: vi.fn(),
  onPermanentDelete: vi.fn(),
  onSendMessage: vi.fn(),
});

const mkMsg = (id: string, over: Partial<Message> = {}): Message => ({
  id,
  senderId: 'u1',
  content: 'hello',
  timestamp: 1700000000000,
  type: MessageType.TEXT,
  ...over,
});

const mkUser = (id: string, name = 'Alice'): User => ({
  id,
  name,
  avatar: '',
  status: 'online',
});

const baseProps = (actions: MessageRowActions): MessageRowProps => ({
  msg: mkMsg('m1'),
  isSelf: false,
  sender: undefined,
  isSequence: false,
  isMentioned: false,
  isEditing: false,
  editContent: '',
  isRecentMessage: false,
  replyTargetMsg: null,
  replyTargetUserName: undefined,
  groupId: 'g1',
  parseContent: () => '',
  actions,
});

/** One mutator per prop: has to be kept in sync with MessageRowProps. */
const MUTATORS: Record<string, (p: MessageRowProps) => void> = {
  msg: (p) => {
    p.msg = mkMsg('m2');
  },
  isSelf: (p) => {
    p.isSelf = true;
  },
  sender: (p) => {
    p.sender = mkUser('u1');
  },
  isSequence: (p) => {
    p.isSequence = true;
  },
  isMentioned: (p) => {
    p.isMentioned = true;
  },
  isEditing: (p) => {
    p.isEditing = true;
  },
  editContent: (p) => {
    p.editContent = 'changed';
  },
  isRecentMessage: (p) => {
    p.isRecentMessage = true;
  },
  replyTargetMsg: (p) => {
    p.replyTargetMsg = mkMsg('m0');
  },
  replyTargetUserName: (p) => {
    p.replyTargetUserName = 'Bob';
  },
  groupId: (p) => {
    p.groupId = 'g2';
  },
  parseContent: (p) => {
    p.parseContent = () => '<p/>';
  },
  actions: (p) => {
    p.actions = mkActions();
  },
};

/** Deliberately excluded from memo comparison. */
const IDENTITY_STABLE = new Set(['actions', 'parseContent']);
/** Only consulted for the single row that is currently being edited. */
const CONDITIONAL = new Set(['editContent']);

describe('areMessageRowPropsEqual (O4a memo comparator)', () => {
  it('classifies every prop of MessageRowProps', () => {
    const allKeys = Object.keys(baseProps(mkActions())).sort();
    const classified = [
      ...new Set([...Object.keys(MUTATORS), ...IDENTITY_STABLE, ...CONDITIONAL]),
    ].sort();
    expect(classified).toEqual(allKeys);
  });

  it('treats two structurally identical prop sets as equal', () => {
    const a = baseProps(mkActions());
    expect(areMessageRowPropsEqual(a, { ...a })).toBe(true);
  });

  it('breaks equality for every markup-affecting prop', () => {
    for (const key of Object.keys(MUTATORS)) {
      if (IDENTITY_STABLE.has(key) || CONDITIONAL.has(key)) continue;

      const a = baseProps(mkActions());
      const b: MessageRowProps = { ...a };
      MUTATORS[key](b);

      expect(areMessageRowPropsEqual(a, b), `prop "${key}" must force a re-render`).toBe(false);
    }
  });

  it('ignores the identity-stable props (that is what makes memo work at all)', () => {
    for (const key of IDENTITY_STABLE) {
      const a = baseProps(mkActions());
      const b: MessageRowProps = { ...a };
      MUTATORS[key](b);

      expect(areMessageRowPropsEqual(a, b), `prop "${key}" must stay memo-neutral`).toBe(true);
    }
  });

  it('ignores editContent unless the row is the one being edited', () => {
    const idle = baseProps(mkActions());

    // Not editing: the keystrokes belong to some other row, so this one must not re-render.
    expect(areMessageRowPropsEqual(idle, { ...idle, editContent: 'typing…' })).toBe(true);

    // Editing: every keystroke has to reach the textarea.
    const editing = { ...idle, isEditing: true };
    expect(areMessageRowPropsEqual(editing, { ...editing, editContent: 'typing…' })).toBe(false);
  });
});
