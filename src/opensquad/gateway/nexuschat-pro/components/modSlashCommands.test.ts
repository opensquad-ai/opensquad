/**
 * Mod commands in the composer's slash menu.
 *
 * Discovery only: typing `/replay` works whether or not the menu knows about it
 * (the agent intercepts the text), so a stale menu is a nuisance, never a
 * breakage. What must hold is that a *builtin* never arrives through this path.
 */
import { afterEach, describe, expect, it } from 'vitest';

import { filterSlashCommands, modSlashCommands, setModSlashCommands } from './ai-chat/slashCommands';

afterEach(() => setModSlashCommands([]));

describe('mod slash commands', () => {
  it('accepts mod-contributed commands and labels their source', () => {
    setModSlashCommands([{ name: 'replay', help: 'Replay the last turn', source: 'replay-theater' }]);
    expect(modSlashCommands().map((c) => c.name)).toEqual(['replay']);
    expect(modSlashCommands()[0].id).toBe('mod:replay');
    expect(modSlashCommands()[0].description).toBe('Replay the last turn');
  });

  it('refuses anything a mod may not own', () => {
    setModSlashCommands([
      { name: 'help', help: 'mine now', source: 'builtin' },
      { name: '', help: 'nameless', source: 'm' },
      { name: 'ok', help: 'fine', source: 'm' },
    ] as never);
    expect(modSlashCommands().map((c) => c.name)).toEqual(['ok']);
  });

  it('merges into the menu, builtins first', () => {
    setModSlashCommands([{ name: 'replay', help: 'Replay', source: 'replay-theater' }]);
    const all = filterSlashCommands('');
    expect(all.map((c) => c.name)).toContain('skill');
    expect(all.map((c) => c.name)).toContain('replay');
    expect(all.findIndex((c) => c.name === 'skill')).toBeLessThan(all.findIndex((c) => c.name === 'replay'));
  });

  it('filters by name and by description', () => {
    setModSlashCommands([{ name: 'replay', help: 'step through edits', source: 'replay-theater' }]);
    expect(filterSlashCommands('rep').map((c) => c.name)).toContain('replay');
    expect(filterSlashCommands('edits').map((c) => c.name)).toContain('replay');
    expect(filterSlashCommands('zzz').map((c) => c.name)).not.toContain('replay');
  });

  it('goes back to builtins only when the mods withdraw', () => {
    setModSlashCommands([{ name: 'replay', help: 'Replay', source: 'replay-theater' }]);
    expect(filterSlashCommands('replay').map((c) => c.name)).toEqual(['replay']);
    setModSlashCommands([]);
    expect(filterSlashCommands('replay')).toEqual([]);
  });
});
