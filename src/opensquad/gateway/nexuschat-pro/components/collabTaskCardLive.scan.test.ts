/**
 * A collaboration card must show the board's state, not the snapshot inside its own message text.
 *
 * The card travels as a marker inside a chat message, so whatever it carries is as old as the
 * message: the field kept seeing "已邀请" on a member who had accepted minutes earlier, because the
 * card rendered its own payload while the board already said accepted. The board is the live
 * record, and the card now reads it — falling back to the snapshot when the board cannot be read,
 * since a stale name beats an empty card.
 */
import fs from 'fs';
import path from 'path';
import { describe, expect, it } from 'vitest';

const src = fs.readFileSync(path.resolve(__dirname, 'CollabTaskCard.tsx'), 'utf8');

describe('the card reads the live record', () => {
  it('asks the board for the collaboration it names', () => {
    expect(src).toContain('collabBoardAPI.taskSummary(collabId)');
    expect(src).toContain('payload.collab_id');
  });

  it('prefers the board and falls back to its own snapshot', () => {
    expect(src).toMatch(/const participants = liveParticipants \?\? payload\.participants/);
  });

  it('says which of the two is on screen', () => {
    expect(src).toMatch(/data-live=\{liveParticipants \? '1' : '0'\}/);
  });

  it('does not update after it is gone, and survives a failed read', () => {
    expect(src).toContain('let alive = true');
    expect(src).toContain('alive = false');
    expect(src).toMatch(/catch \{\s*\/\* the snapshot stays on screen \*\//);
  });
});

describe('it is still driven by the payload it was given', () => {
  it('keeps the title, the kind and the open button from the message', () => {
    expect(src).toContain('{payload.title}');
    expect(src).toContain('onOpen(payload.collab_id)');
    expect(src).toContain('payload.kind');
  });
});
