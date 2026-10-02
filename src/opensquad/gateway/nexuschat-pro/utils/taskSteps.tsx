/**
 * Task-step parsing, shared by the collaboration board and the task window.
 *
 * Both surfaces show the same thing — what a worker was given and how far each part got —
 * so they read the same task text with the same parser and draw the same row. Duplicating
 * the parser would let the two drift, and the window already drifted once: it printed the
 * raw plan text *and* a second list of the same subtasks underneath it.
 */
import React from 'react';
import { CheckCircle2, Circle } from 'lucide-react';

import { OpenSquadLoader } from '../components/OpenSquadLoader';
import type { CollabBoardItem } from '../services/api';

export const PlanStatusIcon: React.FC<{ status: string }> = ({ status }) => {
  if (status === 'done') return <CheckCircle2 size={15} className="text-emerald-400 shrink-0" />;
  if (status === 'doing') return <OpenSquadLoader size={16} className="shrink-0" />;
  return <Circle size={15} className="text-textMuted shrink-0" />;
};

export interface ParsedStep {
  title: string;
  detail: string;
  status: string;
  children?: ParsedStep[];
}

export const normalizeStepStatus = (s?: string) => {
  if (s === 'done' || s === 'doing' || s === 'pending' || s === 'blocked') return s;
  return 'pending';
};

/**
 * Parse task content into hierarchical steps.
 *
 * Recognizes two formats:
 * 1. "## 主任务: ..." → main task (parent) with "### 子任务: ..." as children
 * 2. Flat "[ ] task" checklist items (no hierarchy)
 *
 * Metadata fields (**负责人**, **文件范围**, etc.) are stripped, not rendered as steps.
 * Structured assignments (``extra.structured`` + ``extra.subtasks``, what assign_task
 * writes) are used directly — there the steps are already data, not prose.
 */
export const parseTaskSteps = (
  task: CollabBoardItem,
  t: (key: string, opts?: Record<string, any>) => string,
): ParsedStep[] => {
  const extraSteps = task.extra?.steps;
  const extra = task.extra;

  // PRIORITY 1: Structured subtasks from assign_task() — use directly from extra.subtasks
  if (extra?.structured && Array.isArray(extra.subtasks) && extra.subtasks.length > 0) {
    return extra.subtasks.map((st: any) => ({
      title: st.title || t('collabBoard.unnamedSubtask'),
      detail: [st.description, st.note].filter(Boolean).join('\n') || '',
      status: normalizeStepStatus(st.status),
      children: [],
    }));
  }

  // PRIORITY 2: Extra steps array (legacy)
  if (Array.isArray(extraSteps) && extraSteps.length > 0) {
    return extraSteps.map((s) => ({
      title: String(s?.title || '').trim() || task.title || task.item_key || t('collabBoard.unnamedTask'),
      detail: String(s?.detail || '').trim(),
      status: normalizeStepStatus(s?.status),
    }));
  }

  const lines = (task.content || '')
    .split('\n')
    .map((l) => l.trim())
    .filter(Boolean);
  const out: ParsedStep[] = [];

  // Metadata field pattern — these should be stripped, not rendered as steps
  const metaField =
    /^\*\*(?:负责人|文件范围|依赖|截止时间|验收标准|Owner|Scope|Dependencies|Deadline|Acceptance|交付物|风险)\*\*[:：\s]/i;

  // Main task header: "## 主任务: xxx" or "## Main Task: xxx"
  const mainTaskRe = /^#{1,2}\s*(?:主任务|Main Task)[:：]\s*(.+)$/i;
  // Subtask header: "### 子任务 X.Y: xxx" or "### Subtask X.Y: xxx"
  // Also tolerant of missing ### prefix: "子任务 X.Y: xxx" or "Subtask X.Y: xxx"
  const subTaskRe = /^#{0,6}\s*(?:子任务|Subtask)\s*\d+[\.:]\s*(.+)$/i;
  // Checklist: "[ ] item", "[x] item", "[>] item"
  const checklistRe = /^\[(x|>|\s)\]\s*(.+)$/i;

  const deriveStatus = (line: string, fallback: string) => {
    const m = line.match(/^\[(x|>|\s)\]\s*/i);
    if (m) {
      if (m[1].toLowerCase() === 'x') return 'done';
      if (m[1] === '>') return 'doing';
      return 'pending';
    }
    if (/已完成|done/i.test(line)) return 'done';
    if (/进行中|处理中|doing/i.test(line)) return 'doing';
    if (/阻塞|blocked/i.test(line)) return 'blocked';
    return fallback;
  };

  // Two-pass approach:
  // Pass 1: Detect if content uses the "## 主任务 / ### 子任务" hierarchical format
  const hasMainTasks = lines.some((l) => mainTaskRe.test(l));

  if (hasMainTasks) {
    // Hierarchical mode: build parent→child structure
    let currentMain: ParsedStep | null = null;
    let currentSub: ParsedStep | null = null;

    for (const raw of lines) {
      const line = raw.replace(/^[-*]\s+/, '');

      // Skip metadata fields entirely
      if (metaField.test(line)) continue;

      // Main task header → new parent
      const mainMatch = line.match(mainTaskRe);
      if (mainMatch) {
        // Flush previous subtask
        if (currentSub && currentMain) {
          currentMain.children!.push(currentSub);
          currentSub = null;
        }
        // Flush previous main task
        if (currentMain) {
          out.push(currentMain);
        }
        currentMain = {
          title: mainMatch[1].trim(),
          detail: '',
          status: task.status || 'pending',
          children: [],
        };
        continue;
      }

      // Subtask header → new child under current main
      const subMatch = line.match(subTaskRe);
      if (subMatch) {
        if (currentSub && currentMain) {
          currentMain.children!.push(currentSub);
        }
        currentSub = {
          title: subMatch[1].trim(),
          detail: '',
          status: 'pending',
          children: [],
        };
        continue;
      }

      // Checklist item → child under current subtask (or under main if no subtask)
      const clMatch = line.match(checklistRe);
      if (clMatch) {
        const clTitle = clMatch[2].trim();
        const clStatus = deriveStatus(line, 'pending');
        if (currentSub) {
          // Checklist is detail under the current subtask
          currentSub.detail = currentSub.detail ? currentSub.detail + '\n' + clTitle : clTitle;
          if (currentSub.status === 'pending') currentSub.status = clStatus;
        } else if (currentMain) {
          // Orphan checklist under main task
          currentMain.children!.push({
            title: clTitle,
            detail: '',
            status: clStatus,
            children: [],
          });
        }
        continue;
      }

      // Generic heading (fallback)
      const headingMatch = line.match(/^(#{1,6}\s+|\d+[.)]\s+)(.+)$/);
      if (headingMatch) {
        const hTitle = headingMatch[2].trim();
        if (currentSub) {
          currentSub.detail = currentSub.detail ? currentSub.detail + '\n' + hTitle : hTitle;
        } else if (currentMain) {
          currentMain.children!.push({
            title: hTitle,
            detail: '',
            status: 'pending',
            children: [],
          });
        }
        continue;
      }

      // Plain text → attach as detail
      if (currentSub) {
        currentSub.detail = currentSub.detail ? currentSub.detail + '\n' + line : line;
      } else if (currentMain) {
        // Attach to last child or create a detail entry
        const lastChild = currentMain.children![currentMain.children!.length - 1];
        if (lastChild) {
          lastChild.detail = lastChild.detail ? lastChild.detail + '\n' + line : line;
        }
      }
    }

    // Flush remaining
    if (currentSub && currentMain) {
      currentMain.children!.push(currentSub);
    }
    if (currentMain) {
      out.push(currentMain);
    }

    return out;
  }

  // Flat mode: original behavior for non-hierarchical content
  let current: { title: string; detailLines: string[]; status: string } | null = null;

  const flush = () => {
    if (!current) return;
    out.push({
      title: current.title,
      detail: current.detailLines.join('\n').trim(),
      status: current.status,
    });
    current = null;
  };

  for (const raw of lines) {
    const line = raw.replace(/^[-*]\s+/, '');

    // Skip metadata fields
    if (metaField.test(line)) continue;

    const checklist = line.match(checklistRe);
    const heading = line.match(/^(#{1,6}\s+|\d+[.)]\s+)(.+)$/);

    if (heading) {
      flush();
      current = {
        title: heading[2].trim(),
        detailLines: [],
        status: deriveStatus(line, task.status || 'pending'),
      };
      continue;
    }

    if (checklist) {
      flush();
      current = {
        title: checklist[2].trim(),
        detailLines: [],
        status: deriveStatus(line, task.status || 'pending'),
      };
      continue;
    }

    if (!current) {
      current = {
        title: line,
        detailLines: [],
        status: task.status || 'pending',
      };
    } else {
      current.detailLines.push(line);
    }
  }

  flush();

  if (out.length === 0) {
    out.push({
      title: task.title || task.item_key || t('collabBoard.unnamedTask'),
      detail: task.content || '',
      status: task.status || 'pending',
    });
  }

  return out;
};

/**
 * Which worker a task item belongs to.
 *
 * Newer items carry the worker in ``agent_id``; older ones were written by the PM with the
 * worker only mentioned in the text, so the mention/负责人/主任务 line is read as a fallback.
 */
export const resolveWorkerId = (item: CollabBoardItem): string => {
  const content = item.content || '';
  const mainTaskMatch = content.match(/##\s*(?:主任务|Main Task)[:：]\s*[^(]*\(@?([\w-]+)\)/i);
  if (mainTaskMatch) return mainTaskMatch[1];
  const mainTaskAt = content.match(/##\s*(?:主任务|Main Task)[:：]\s*@([\w-]+)/i);
  if (mainTaskAt) return mainTaskAt[1];
  const ownerMatch = content.match(/\*\*(?:负责人|Owner)\*\*[:：]\s*([\w-]+)/i);
  if (ownerMatch) return ownerMatch[1];
  const firstMention = content.match(/@([\w-]+)/);
  if (firstMention) return firstMention[1];
  return item.agent_id || 'unassigned';
};

/** Group board items by the worker they belong to, newest task first. */
export const groupByWorker = (
  items: CollabBoardItem[],
): { agentId: string; items: CollabBoardItem[] }[] => {
  const map = new Map<string, CollabBoardItem[]>();
  for (const item of items) {
    const aid = resolveWorkerId(item);
    if (!map.has(aid)) map.set(aid, []);
    map.get(aid)!.push(item);
  }
  return Array.from(map.entries()).map(([agentId, list]) => ({
    agentId,
    items: list.sort((a, b) => String(b.updated_at || '').localeCompare(String(a.updated_at || ''))),
  }));
};
