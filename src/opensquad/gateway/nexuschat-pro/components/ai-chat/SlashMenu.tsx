/**
 * SlashMenu — floating picker for `/` commands and command arg lists (skills / goal).
 */
import React, { useEffect, useRef } from 'react';
import { BookOpen, ListTree, Target } from 'lucide-react';
import type { SkillInfo } from '../../services/api';
import type { GoalSubcommandDef, SlashCommandDef } from './slashCommands';

type CommonNav = {
  highlightIndex: number;
  onHighlightIndexChange: (index: number) => void;
};

export type SlashMenuProps =
  | (CommonNav & {
      mode: 'commands';
      commands: SlashCommandDef[];
      onSelectCommand: (cmd: SlashCommandDef) => void;
    })
  | (CommonNav & {
      mode: 'skill';
      skills: SkillInfo[];
      loading?: boolean;
      onSelectSkill: (skill: SkillInfo) => void;
    })
  | (CommonNav & {
      mode: 'goal';
      subcommands: GoalSubcommandDef[];
      onSelectSubcommand: (cmd: GoalSubcommandDef) => void;
    })
  | (CommonNav & {
      mode: 'plan';
      topicHint: string;
      onConfirmTopic: () => void;
    });

function commandIcon(cmd: SlashCommandDef) {
  if (cmd.id === 'goal') return Target;
  if (cmd.id === 'plan') return ListTree;
  return BookOpen;
}

export const SlashMenu: React.FC<SlashMenuProps> = (props) => {
  const itemRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const { highlightIndex, onHighlightIndexChange } = props;

  useEffect(() => {
    itemRefs.current[highlightIndex]?.scrollIntoView({ block: 'nearest' });
  }, [highlightIndex]);

  const shell = (title: string, body: React.ReactNode) => (
    <div
      className="absolute left-2 right-2 bottom-[calc(100%+6px)] z-50 max-h-[280px] overflow-y-auto rounded-xl border border-border bg-bgLight shadow-[0_8px_30px_rgba(0,0,0,0.12)] py-1"
      role="listbox"
      aria-label={title}
    >
      <div className="px-3 py-1.5 text-[11px] text-textMuted/70 truncate">{title}</div>
      {body}
    </div>
  );

  const itemClass = (active: boolean) =>
    `w-full text-left px-3 py-2 transition-colors border-0 cursor-pointer ${
      active
        ? 'bg-black/[0.06] dark:bg-white/[0.10]'
        : 'bg-transparent hover:bg-black/[0.06] dark:hover:bg-white/[0.10]'
    }`;

  if (props.mode === 'commands') {
    const { commands, onSelectCommand } = props;
    return shell(
      'Commands · /',
      commands.length === 0 ? (
        <div className="px-3 py-3 text-[12px] text-textMuted">No matching commands</div>
      ) : (
        commands.map((cmd, index) => {
          const Icon = commandIcon(cmd);
          const active = index === highlightIndex;
          return (
            <button
              key={cmd.id}
              ref={(el) => {
                itemRefs.current[index] = el;
              }}
              type="button"
              role="option"
              aria-selected={active}
              onMouseEnter={() => onHighlightIndexChange(index)}
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => onSelectCommand(cmd)}
              className={itemClass(active)}
              title={cmd.description}
            >
              <div className="flex items-center gap-2.5">
                <Icon size={15} className="text-textMuted shrink-0" />
                <div className="min-w-0 flex-1">
                  <div className="text-[13px] font-medium text-textMain truncate">/{cmd.name}</div>
                  {cmd.description ? (
                    <div className="text-[11px] text-textMuted truncate mt-0.5">{cmd.description}</div>
                  ) : null}
                </div>
              </div>
            </button>
          );
        })
      ),
    );
  }

  if (props.mode === 'goal') {
    const { subcommands, onSelectSubcommand } = props;
    return shell(
      'Goal · /goal  — type an objective and Enter, or pick a control',
      <>
        <div className="px-3 py-1.5 text-[11px] text-textMuted">
          Example: /goal Make all unit tests pass
        </div>
        {subcommands.length === 0 ? (
          <div className="px-3 py-2 text-[12px] text-textMuted">
            Press Enter to set this text as the goal
          </div>
        ) : (
          subcommands.map((cmd, index) => {
            const active = index === highlightIndex;
            return (
              <button
                key={cmd.id}
                ref={(el) => {
                  itemRefs.current[index] = el;
                }}
                type="button"
                role="option"
                aria-selected={active}
                onMouseEnter={() => onHighlightIndexChange(index)}
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => onSelectSubcommand(cmd)}
                className={itemClass(active)}
                title={cmd.description}
              >
                <div className="text-[13px] font-medium text-textMain truncate">/goal {cmd.name}</div>
                {cmd.description ? (
                  <div className="text-[11px] text-textMuted truncate mt-0.5">{cmd.description}</div>
                ) : null}
              </button>
            );
          })
        )}
      </>,
    );
  }

  if (props.mode === 'plan') {
    const topic = (props.topicHint || '').trim();
    return shell(
      'Plan · /plan  — design first, then Build',
      <>
        <div className="px-3 py-1.5 text-[11px] text-textMuted">
          Investigate → write `.opensquad/plans/*.md` → {'<plan>'} checklist → request Build
        </div>
        <button
          type="button"
          role="option"
          aria-selected={highlightIndex === 0}
          onMouseEnter={() => onHighlightIndexChange(0)}
          onMouseDown={(e) => e.preventDefault()}
          onClick={() => props.onConfirmTopic()}
          className={itemClass(highlightIndex === 0)}
        >
          <div className="text-[13px] font-medium text-textMain truncate">
            {topic ? `Start planning: ${topic}` : 'Start planning (describe the topic after /plan )'}
          </div>
          <div className="text-[11px] text-textMuted truncate mt-0.5">
            Switches to Plan mode, then asks for Build when the doc is ready
          </div>
        </button>
      </>,
    );
  }

  const { skills, loading = false, onSelectSkill } = props;
  return shell(
    'Skills · /skill',
    loading && skills.length === 0 ? (
      <div className="px-3 py-3 text-[12px] text-textMuted">Loading skills…</div>
    ) : skills.length === 0 ? (
      <div className="px-3 py-3 text-[12px] text-textMuted">No matching skills</div>
    ) : (
      skills.map((skill, index) => {
        const id = skill.dir || skill.name;
        const title = skill.display_name || skill.name || id;
        const desc = (skill.description || '').trim();
        const active = index === highlightIndex;
        return (
          <button
            key={id}
            ref={(el) => {
              itemRefs.current[index] = el;
            }}
            type="button"
            role="option"
            aria-selected={active}
            onMouseEnter={() => onHighlightIndexChange(index)}
            onMouseDown={(e) => e.preventDefault()}
            onClick={() => onSelectSkill(skill)}
            className={itemClass(active)}
            title={desc || title}
          >
            <div className="text-[13px] font-medium text-textMain truncate">{title}</div>
            {desc ? (
              <div className="text-[11px] text-textMuted truncate mt-0.5">{desc}</div>
            ) : null}
          </button>
        );
      })
    ),
  );
};
