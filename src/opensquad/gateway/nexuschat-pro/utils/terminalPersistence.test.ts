/**
 * A terminal must not lose its scrollback when you look away.
 *
 * The launcher keys shells by id and keeps each one's buffer (`read(since)` replays it),
 * so persistence is a matter of *addressing the same id again* and *not closing the
 * shell on unmount*. An id minted per mount (what the panel used to do) meant the panel
 * came back as a new shell with a blank screen — "open the terminal, open files, come
 * back" showed nothing.
 */
import fs from 'fs';
import path from 'path';
import { describe, expect, it } from 'vitest';
import { ephemeralTerminalId, stableTerminalId } from './terminalIdentity';

const read = (rel: string) => fs.readFileSync(path.resolve(__dirname, rel), 'utf8');
const TERMINAL = read('../components/ai-chat/TerminalPanel.tsx');
const RAIL = read('../components/ai-chat/ProjectFilesPanel.tsx');
const SHELL = read('../components/ai-chat/WorkspacePaneShell.tsx');

describe('stableTerminalId', () => {
  it('is deterministic — the same slot must find the same shell', () => {
    expect(stableTerminalId('agent-a', 'rail:/proj')).toBe(stableTerminalId('agent-a', 'rail:/proj'));
  });

  it('separates slots, agents and workspaces', () => {
    const ids = new Set([
      stableTerminalId('agent-a', 'rail:/proj'),
      stableTerminalId('agent-a', 'rail:/other'),
      stableTerminalId('agent-a', 'pane:p1'),
      stableTerminalId('agent-b', 'rail:/proj'),
    ]);
    expect(ids.size).toBe(4);
  });

  it('is safe as a launcher-side dict key', () => {
    const id = stableTerminalId('agent-a', 'rail:C:/a b/c');
    expect(id).toMatch(/^wt[0-9a-z]+$/);
  });

  it('ephemeral ids are one-off, and share no shape with a slot id', () => {
    const a = ephemeralTerminalId();
    const b = ephemeralTerminalId();
    expect(a).not.toBe(b);
    expect(a.startsWith('t')).toBe(true);
    expect(a.startsWith('wt')).toBe(false);
  });
});

describe('the panel reattaches instead of restarting', () => {
  it('addresses a keyed slot by key, and only an anonymous one by mount', () => {
    expect(TERMINAL).toContain(
      'terminalKey ? stableTerminalId(agentId, terminalKey) : ephemeralTerminalId()',
    );
  });

  it('leaves a keyed shell alive on unmount, and closes only an anonymous one', () => {
    expect(TERMINAL).toContain('if (!terminalKey) void terminalAPI.close(agentId, terminalId)');
  });

  it('starts each mount from offset 0, so the launcher replays the buffer', () => {
    // The whole point of a stable id: the poll asks for everything the launcher still
    // holds for that shell rather than for what this component has already seen.
    expect(TERMINAL).toContain('offsetRef.current = 0;');
  });
});

describe('both call sites name their slot', () => {
  it('the rail is keyed by workspace', () => {
    expect(RAIL).toContain("terminalKey={`rail:${rootPath || 'default'}`}");
  });

  it('the pane is keyed by pane', () => {
    expect(SHELL).toContain('terminalKey={`pane:${paneId}`}');
  });
});

describe('the rail keeps the terminal mounted', () => {
  it('mounts it on first activation and keeps it', () => {
    expect(RAIL).toContain('const [railTerminalMounted, setRailTerminalMounted] = useState(');
    expect(RAIL).toContain("if (railTab === 'terminal') setRailTerminalMounted(true);");
  });

  it('hides the inactive tab rather than rendering it away', () => {
    // A conditional render here is exactly the bug: it unmounts the panel, and the
    // unmount closed the shell.
    expect(RAIL).toContain("railTab === 'terminal' ? 'flex-1 min-h-0 flex flex-col' : 'hidden'");
  });
});
