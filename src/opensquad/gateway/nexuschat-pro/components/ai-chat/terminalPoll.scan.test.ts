/**
 * The poll must not overlap, and must never go backwards.
 *
 * Two responses arriving out of order let the older offset win, so the next poll asked for output
 * the panel had already shown and appended it again — the same line, over and over, until refresh.
 */
import fs from 'fs';
import path from 'path';
import { describe, expect, it } from 'vitest';

const src = fs.readFileSync(path.resolve(__dirname, 'TerminalPanel.tsx'), 'utf8');

describe('the terminal poll', () => {
  it('runs one request at a time', () => {
    expect(src).toContain('if (inFlight) return;');
    expect(src).toContain('inFlight = true;');
    expect(src).toContain('inFlight = false;');
  });

  it('ignores a response at or behind the offset it already has', () => {
    expect(src).toContain('nextOffset <= offsetRef.current');
    expect(src).toMatch(/if \(res\?\.chunk && !\(/);
  });

  it('keeps the offset monotone', () => {
    expect(src).toContain('Math.max(offsetRef.current, nextOffset)');
    expect(src).not.toContain('Number(res.offset) || offsetRef.current');
  });

  it('still appends what is genuinely new', () => {
    expect(src).toContain('setOutput((prev) => (prev + res.chunk).slice(-MAX_CHARS))');
  });
});
