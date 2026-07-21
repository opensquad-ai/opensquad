/**
 * Slash command registry + input parsing for the agent composer.
 * Add new commands to SLASH_COMMANDS; wire arg pickers in AIChatPage / SlashMenu.
 */
import type { SkillInfo } from '../../services/api';

/** Stable ids — extend as new `/` commands are added. */
export type SlashCommandId = 'skill' | 'goal' | 'plan';

export interface SlashCommandDef {
  id: SlashCommandId;
  /** Typed name after `/` (e.g. `skill`). */
  name: string;
  description: string;
  /** When true, selecting inserts `/name ` and opens an arg picker if configured. */
  takesArgs: boolean;
}

/**
 * Registered composer slash commands.
 * Append here when adding more `/` features.
 */
export const SLASH_COMMANDS: readonly SlashCommandDef[] = [
  {
    id: 'skill',
    name: 'skill',
    description: 'Attach a skill to this message',
    takesArgs: true,
  },
  {
    id: 'goal',
    name: 'goal',
    description: 'Set a long-running verifiable goal',
    takesArgs: true,
  },
  {
    id: 'plan',
    name: 'plan',
    description: 'Design first: investigate, write a plan doc, then Build',
    takesArgs: true,
  },
] as const;

export type GoalSubcommandId = 'pause' | 'resume' | 'clear';

export interface GoalSubcommandDef {
  id: GoalSubcommandId;
  name: string;
  description: string;
}

export const GOAL_SUBCOMMANDS: readonly GoalSubcommandDef[] = [
  { id: 'pause', name: 'pause', description: 'Pause the active goal (stop auto-continue)' },
  { id: 'resume', name: 'resume', description: 'Resume a paused goal' },
  { id: 'clear', name: 'clear', description: 'Clear the active goal' },
] as const;

export type SlashInputMode =
  | { kind: 'commands'; query: string }
  | { kind: 'skill'; query: string }
  | { kind: 'goal'; query: string }
  | { kind: 'plan'; query: string };

/**
 * Parse leading `/` composer input.
 * - `/` or `/sk` → command list (filter by query)
 * - `/skill ` → skill arg list (space required after command name)
 * - `/goal ` → goal subcommands + free-text objective
 * - `/plan ` → free-text topic for Cursor-style planning
 */
export function parseSlashInput(text: string): SlashInputMode | null {
  const m = text.match(/^\s*\/(.*)$/s);
  if (!m) return null;
  const rest = m[1] ?? '';

  for (const cmd of SLASH_COMMANDS) {
    // `/name …` (name + whitespace) → arg mode for that command
    const argMatch = rest.match(new RegExp(`^${escapeRegExp(cmd.name)}\\s(.*)$`, 'is'));
    if (!argMatch) continue;
    if (cmd.id === 'skill') {
      return { kind: 'skill', query: argMatch[1] ?? '' };
    }
    if (cmd.id === 'goal') {
      return { kind: 'goal', query: argMatch[1] ?? '' };
    }
    if (cmd.id === 'plan') {
      return { kind: 'plan', query: argMatch[1] ?? '' };
    }
  }

  return { kind: 'commands', query: rest };
}

export function filterSlashCommands(query: string): SlashCommandDef[] {
  const q = query.trim().toLowerCase();
  if (!q) return [...SLASH_COMMANDS];
  return SLASH_COMMANDS.filter((cmd) => {
    const hay = `${cmd.name} ${cmd.description}`.toLowerCase();
    return hay.includes(q) || cmd.name.toLowerCase().startsWith(q);
  });
}

export function filterGoalSubcommands(query: string): GoalSubcommandDef[] {
  const q = query.trim().toLowerCase();
  if (!q) return [...GOAL_SUBCOMMANDS];
  // Prefer name prefix so typing "pau" does not also hit "paused" in resume's description.
  const byName = GOAL_SUBCOMMANDS.filter((cmd) => cmd.name.toLowerCase().startsWith(q));
  if (byName.length > 0) return [...byName];
  return GOAL_SUBCOMMANDS.filter((cmd) => {
    const hay = `${cmd.name} ${cmd.description}`.toLowerCase();
    return hay.includes(q);
  });
}

export function filterSkillsForSlash(skills: SkillInfo[], query: string): SkillInfo[] {
  const q = query.trim().toLowerCase();
  if (!q) return skills;
  return skills.filter((skill) => {
    const hay = [
      skill.display_name,
      skill.name,
      skill.dir,
      skill.description,
      ...(skill.keywords || []),
    ]
      .filter(Boolean)
      .join(' ')
      .toLowerCase();
    return hay.includes(q);
  });
}

/** Insert text when a takesArgs command is chosen from the `/` list. */
export function slashCommandTriggerText(cmd: SlashCommandDef): string {
  return cmd.takesArgs ? `/${cmd.name} ` : `/${cmd.name}`;
}

/**
 * Interpret `/goal …` send text.
 * - empty → status
 * - pause|resume|clear → lifecycle action
 * - otherwise → set objective
 */
export function parseGoalSendQuery(query: string): {
  action: 'set' | 'pause' | 'resume' | 'clear' | 'status';
  objective?: string;
} {
  const q = query.trim();
  if (!q) return { action: 'status' };
  const low = q.toLowerCase();
  if (low === 'pause' || low === 'resume' || low === 'clear' || low === 'status') {
    return { action: low };
  }
  return { action: 'set', objective: q };
}

function escapeRegExp(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}
