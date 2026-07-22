import { describe, expect, it } from 'vitest';
import {
  filterGoalSubcommands,
  filterSlashCommands,
  parseGoalSendQuery,
  parseSlashInput,
  slashCommandTriggerText,
  SLASH_COMMANDS,
} from '../components/ai-chat/slashCommands';

describe('parseSlashInput', () => {
  it('opens command list on bare /', () => {
    expect(parseSlashInput('/')).toEqual({ kind: 'commands', query: '' });
    expect(parseSlashInput('  /')).toEqual({ kind: 'commands', query: '' });
  });

  it('filters commands while typing the name', () => {
    expect(parseSlashInput('/sk')).toEqual({ kind: 'commands', query: 'sk' });
    expect(parseSlashInput('/skill')).toEqual({ kind: 'commands', query: 'skill' });
    expect(parseSlashInput('/go')).toEqual({ kind: 'commands', query: 'go' });
  });

  it('opens skill arg list only after /skill + space', () => {
    expect(parseSlashInput('/skill ')).toEqual({ kind: 'skill', query: '' });
    expect(parseSlashInput('/skill foo')).toEqual({ kind: 'skill', query: 'foo' });
    expect(parseSlashInput('/Skill Bar')).toEqual({ kind: 'skill', query: 'Bar' });
  });

  it('opens goal mode after /goal + space', () => {
    expect(parseSlashInput('/goal ')).toEqual({ kind: 'goal', query: '' });
    expect(parseSlashInput('/goal pause')).toEqual({ kind: 'goal', query: 'pause' });
    expect(parseSlashInput('/goal Make tests pass')).toEqual({
      kind: 'goal',
      query: 'Make tests pass',
    });
  });

  it('opens plan mode after /plan + space', () => {
    expect(parseSlashInput('/plan ')).toEqual({ kind: 'plan', query: '' });
    expect(parseSlashInput('/plan Add split panes')).toEqual({
      kind: 'plan',
      query: 'Add split panes',
    });
  });

  it('ignores non-slash input', () => {
    expect(parseSlashInput('hello')).toBeNull();
    expect(parseSlashInput(' /nope')).toEqual({ kind: 'commands', query: 'nope' });
  });
});

describe('filterSlashCommands', () => {
  it('returns skill, goal, and plan', () => {
    expect(filterSlashCommands('').map((c) => c.id)).toEqual(['skill', 'goal', 'plan']);
    expect(filterSlashCommands('sk').map((c) => c.id)).toEqual(['skill']);
    expect(filterSlashCommands('go').map((c) => c.id)).toEqual(['goal']);
    expect(filterSlashCommands('pl').map((c) => c.id)).toEqual(['plan']);
  });
});

describe('filterGoalSubcommands / parseGoalSendQuery', () => {
  it('filters pause/resume/clear', () => {
    expect(filterGoalSubcommands('').map((c) => c.id)).toEqual(['pause', 'resume', 'clear']);
    expect(filterGoalSubcommands('pau').map((c) => c.id)).toEqual(['pause']);
    expect(filterGoalSubcommands('cle').map((c) => c.id)).toEqual(['clear']);
  });

  it('parses send query actions', () => {
    expect(parseGoalSendQuery('')).toEqual({ action: 'status' });
    expect(parseGoalSendQuery('pause')).toEqual({ action: 'pause' });
    expect(parseGoalSendQuery('Make CI green')).toEqual({
      action: 'set',
      objective: 'Make CI green',
    });
  });
});

describe('slashCommandTriggerText', () => {
  it('appends a trailing space for takesArgs commands', () => {
    const skill = SLASH_COMMANDS.find((c) => c.id === 'skill')!;
    const goal = SLASH_COMMANDS.find((c) => c.id === 'goal')!;
    const plan = SLASH_COMMANDS.find((c) => c.id === 'plan')!;
    expect(slashCommandTriggerText(skill)).toBe('/skill ');
    expect(slashCommandTriggerText(goal)).toBe('/goal ');
    expect(slashCommandTriggerText(plan)).toBe('/plan ');
  });
});
