/**
 * SoloActivityRow — Cursor-style document-flow activity for Solo UI.
 *
 * Outer fold (turn-level): collapses the whole thought+tool process once the
 * agent finishes the turn. Inner lines:
 *   - thought: faded text body
 *   - file edit/write: fold shows +N -M; expand → embedded FileDiffBlock
 *   - other tools (websearch, etc.): expand → light box with Args + Result
 */
import React, { useCallback, useEffect, useLayoutEffect, useMemo, useState, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import {
  Globe,
  Search,
  FileText,
  Pencil,
  Terminal,
  Sparkles,
  CheckSquare,
  Folder,
  Wrench,
  Zap,
  Lightbulb,
  Server,
  MessageCircleQuestion,
  Users,
  Database,
  Target,
  Image,
  Clock,
} from 'lucide-react';
import type { WorkflowBlock, WorkflowEvent } from '../../utils/aiChatTimeline';
import { isFinalFlag, isToolResultFailure } from '../../utils/aiChatTimeline';
import { hasOpenAsyncDelegate, isUiOnlyToolName } from '../../utils/aiChatTimeline';
import { FileDiffBlock, extractFileEditInfo, parsePartialFileToolArgs, applyEditDiffContext, type FileEditInfo } from './FileDiffBlock';
import { formatElapsedAtLeastOneSecond } from '../../utils/formatElapsed';
import { buildDisplayWorkflowItems, type DelegateBundle } from '../../utils/delegateGrouping';
import {
  attachShellJobsToDisplayItems,
  type ShellJobBundle,
  type ShellStreamState,
} from '../../utils/shellJobGrouping';
import { DelegateFold } from './DelegateFold';
import { ShellJobFold } from './ShellJobFold';
import { Collapse, useFold } from '../Collapse';
import { parsePlanContent, PlanBlock, type PlanStep } from './PlanBlock';
import { FollowScrollBox } from './FollowScrollBox';
import { MarkdownScrollBody } from './MarkdownScrollBody';
import { extractHtmlEmbed, isVisualizationToolName } from './HtmlEmbedBlock';
import { PulseDotsOrbit, PulseDotsStatus } from './PulseDotsStatus';
import {
  type WorkflowExpandLevel,
  workflowExpandFlags,
} from '../../utils/workflowExpandPref';

/** When Solo workflow step count exceeds this, nest lines in a scroll box. */
const SOLO_STEPS_SCROLL_THRESHOLD = 10;
const SOLO_STEPS_SCROLL_MAX_CLASS = 'max-h-[280px]';
/** Collapsed headers scroll smoothly natively; window only huge lists. */
const STEP_VIRT_AFTER = 80;
const STEP_EST_PX = 26;
const STEP_OVERSCAN = 18;

function virtTailWindow(length: number, clientH = 280): { start: number; end: number } {
  const visible = Math.ceil((clientH || 280) / STEP_EST_PX) + STEP_OVERSCAN * 2;
  return { start: Math.max(0, length - visible), end: length };
}

/** Grow the rendered window to cover the viewport. Never shrink while scrolling
 *  — changing spacer height with a fixed row estimate is what makes the thumb jitter. */
function expandVirtWindow(
  n: number,
  scrollTop: number,
  clientH: number,
  current: { start: number; end: number },
): { start: number; end: number } {
  const visStart = Math.max(0, Math.floor(scrollTop / STEP_EST_PX));
  const visEnd = Math.min(n, Math.ceil((scrollTop + (clientH || 280)) / STEP_EST_PX));
  const start = Math.min(
    Math.max(0, Math.min(current.start, n)),
    Math.max(0, visStart - STEP_OVERSCAN),
  );
  const end = Math.max(
    Math.min(n, Math.max(current.end, 0)),
    Math.min(n, visEnd + STEP_OVERSCAN),
  );
  return { start, end: Math.max(start, end) };
}

interface SoloActivityRowProps {
  block: WorkflowBlock;
  /**
   * 任务已交付（该工作流组之后已有 assistant 最终回复）→ 即使块仍
   * completed=false / 有未闭合工具，也不再播放"执行中"动画与流光。
   */
  turnDelivered?: boolean;
  /**
   * Progressive auto-expand for thought / plan / tool folds.
   * Only seeds defaults; never overrides a fold the user has toggled.
   */
  expandLevel?: WorkflowExpandLevel;
  turnStartedMs?: number;
  /** Live shell job stdout keyed by tool call_id */
  shellStreams?: Record<string, ShellStreamState>;
  /** Open a project file in the right-side files panel */
  onOpenFile?: (path: string) => void;
  /**
   * Classic Agent Web only: embed visualization.create HTML in the dialog.
   * Solo must leave this false.
   */
  embedVisualizations?: boolean;
  /**
   * Chat layout mode. Work (classic) shows a Chinese tool-summary headline on
   * the completed outer fold ("已读取 3 个文件，搜索 2 次文件"); Solo keeps
   * the Cursor-style "Worked" headline.
   */
  uiMode?: 'classic' | 'solo';
}

function toolNameOf(evt: WorkflowEvent): string {
  const data = typeof evt.content === 'object' && evt.content ? evt.content : {};
  return String(data.name || data.tool || 'Tool');
}

function thoughtText(evt: WorkflowEvent): string {
  return typeof evt.content === 'string' ? evt.content : JSON.stringify(evt.content ?? '');
}

function parseArgs(raw: unknown): Record<string, unknown> | null {
  if (raw == null) return null;
  if (typeof raw === 'object' && !Array.isArray(raw)) return raw as Record<string, unknown>;
  if (typeof raw === 'string') {
    try {
      const parsed = JSON.parse(raw);
      if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) return parsed;
    } catch {
      return null;
    }
  }
  return null;
}

function formatResult(result: unknown): string {
  if (result == null) return '';
  if (typeof result === 'string') return result;
  try {
    return JSON.stringify(result, null, 2);
  } catch {
    return String(result);
  }
}

const PRETTY_JSON_MAX = 12_000;

function prettyJson(value: unknown): string {
  let out = '';
  if (value == null) return '';
  if (typeof value === 'string') {
    try {
      out = JSON.stringify(JSON.parse(value), null, 2);
    } catch {
      out = value;
    }
  } else {
    try {
      out = JSON.stringify(value, null, 2);
    } catch {
      out = String(value);
    }
  }
  if (out.length > PRETTY_JSON_MAX) {
    return `${out.slice(0, PRETTY_JSON_MAX)}\n… (${out.length - PRETTY_JSON_MAX} more chars)`;
  }
  return out;
}

/** Every thinking segment presents as "深度思考" — the per-segment duration
 *  is appended by buildLines from neighbouring event timestamps. */
function thoughtLabel(t: TFunction): { primary: string; secondary: string } {
  return { primary: t('aiChat.toolFlow.line.thoughtDeep'), secondary: '' };
}

type LineKind = 'thought' | 'tool' | 'info' | 'summary' | 'progress' | 'delegation' | 'plan' | 'shell_job' | 'process';

interface ActivityLine {
  key: string;
  kind: LineKind;
  primary: string;
  secondary: string;
  detail: string;
  running?: boolean;
  /** Live trailing thought: epoch ms the thought started — row ticks elapsed locally. */
  elapsedStartMs?: number;
  /** Structured tool payload for rich Solo expand panels */
  toolName?: string;
  toolArgs?: Record<string, unknown> | null;
  toolResult?: string;
  fileEdit?: FileEditInfo | null;
  toolStatus?: 'running' | 'success' | 'error';
  /** Context-compression summary flags */
  summaryDone?: boolean;
  summaryPending?: boolean;
  /** Cursor-style delegate bundle (opens SubAgentPanel) */
  delegation?: DelegateBundle;
  /** system.start_job / run_session_job live CMD panel */
  shellJob?: ShellJobBundle;
  /** Parsed <plan> steps for Solo plan fold */
  planSteps?: PlanStep[];
}

function fileEditEqual(a?: FileEditInfo | null, b?: FileEditInfo | null): boolean {
  if (a === b) return true;
  if (!a || !b) return !a && !b;
  return (
    a.kind === b.kind
    && a.filePath === b.filePath
    && a.fileName === b.fileName
    && a.newStr === b.newStr
    && a.oldStr === b.oldStr
    && a.addedLines === b.addedLines
    && a.removedLines === b.removedLines
    && a.lineRange === b.lineRange
    && a.startLine === b.startLine
  );
}

function activityLineEqual(a: ActivityLine, b: ActivityLine): boolean {
  if (a === b) return true;
  return (
    a.key === b.key
    && a.kind === b.kind
    && a.primary === b.primary
    && a.secondary === b.secondary
    && a.detail === b.detail
    && a.running === b.running
    && a.toolName === b.toolName
    && a.toolResult === b.toolResult
    && a.toolStatus === b.toolStatus
    && a.summaryDone === b.summaryDone
    && a.summaryPending === b.summaryPending
    && a.elapsedStartMs === b.elapsedStartMs
    && a.delegation === b.delegation
    && a.shellJob === b.shellJob
    && a.planSteps === b.planSteps
    && a.toolArgs === b.toolArgs
    && fileEditEqual(a.fileEdit, b.fileEdit)
  );
}

function eventToLines(evt: WorkflowEvent, key: string, blockCompleted: boolean, t: TFunction): ActivityLine[] {
  const lines: ActivityLine[] = [];

  if (evt.type === 'thought') {
    const text = thoughtText(evt);
    if (!text.trim()) return lines;
    const { primary, secondary } = thoughtLabel(t);
    lines.push({ key, kind: 'thought', primary, secondary, detail: text });
    return lines;
  }

  if (evt.type === 'summary_stream') {
    const data = typeof evt.content === 'object' && evt.content ? evt.content : {};
    const text = typeof data.text === 'string' ? data.text : '';
    const done = !!data.done;
    const pending = !!data.pending;
    lines.push({
      key,
      kind: 'summary',
      primary: done
        ? (text ? t('aiChat.toolFlow.line.summaryContext') : t('aiChat.toolFlow.line.summaryDone'))
        : (pending ? t('aiChat.toolFlow.line.summaryWaiting') : t('aiChat.toolFlow.line.summaryCompressing')),
      secondary: done ? t('aiChat.toolFlow.line.done') : t('aiChat.toolFlow.line.live'),
      detail: pending ? '' : (text || (done ? '' : t('aiChat.toolFlow.line.summarizing'))),
      running: !done,
      summaryDone: done,
      summaryPending: pending,
    });
    return lines;
  }

  if (evt.type === 'compression_progress') {
    const data = typeof evt.content === 'object' && evt.content ? evt.content : {};
    const text =
      typeof evt.content === 'string'
        ? evt.content
        : String(data.text || data.message || '');
    if (!text.trim()) return lines;
    const isFinal = isFinalFlag(evt.content);
    lines.push({
      key,
      kind: 'progress',
      primary: text.length > 72 ? `${text.slice(0, 72)}…` : text,
      secondary: isFinal ? 'done' : '',
      detail: text,
      running: !isFinal && !blockCompleted,
    });
    return lines;
  }

  if (evt.type === 'process_output') {
    // 中间过程输出（非最终回复的阶段性总结）—— 💡"过程输出" 折叠行。
    const text =
      typeof evt.content === 'string'
        ? evt.content
        : String((evt.content as any)?.text || (evt.content as any)?.message || '');
    if (!text.trim()) return lines;
    lines.push({
      key,
      kind: 'process',
      primary: t('aiChat.toolFlow.line.processOutput'),
      secondary: '',
      detail: text,
    });
    return lines;
  }

  if (evt.type === 'tool_call') {
    // Open tools are running only while the fold is live. A completed fold
    // with missing results is a cancelled/stopped turn (refresh after Stop).
    // Mid-turn hydrate that wrongly sealed is reopened in buildTimelineFromSession.
    const running = !evt.result && !blockCompleted;
    const name = toolNameOf(evt);
    const content = typeof evt.content === 'object' && evt.content ? evt.content : {};
    const rawArgs = content.arguments ?? content.args ?? content.input;
    const argsObj =
      parseArgs(rawArgs) ||
      (typeof rawArgs === 'string' ? parsePartialFileToolArgs(rawArgs) : null) ||
      (typeof rawArgs === 'object' && rawArgs ? rawArgs : null);
    const resultStr = formatResult(evt.result);
    const fileEdit = applyEditDiffContext(
      extractFileEditInfo(name, argsObj || rawArgs || {}),
      {
        diffOld: evt.diffOld,
        diffNew: evt.diffNew,
        diffStartLine: evt.diffStartLine,
      },
    );
    const failed =
      !running &&
      (evt.resultStatus === 'error' || isToolResultFailure(evt.result) || isToolResultFailure(resultStr));
    const status: 'running' | 'success' | 'error' = running
      ? 'running'
      : failed
        ? 'error'
        : 'success';

    let primary = friendlyToolName(name, t);
    let secondary = '';
    if (fileEdit) {
      if (fileEdit.kind === 'read') {
        primary = running
          ? t('aiChat.toolFlow.line.reading', { name: fileEdit.fileName })
          : failed
            ? t('aiChat.toolFlow.line.readFailed', { name: fileEdit.fileName })
            : t('aiChat.toolFlow.line.read', { name: fileEdit.fileName });
        secondary = fileEdit.lineRange || '';
      } else if (fileEdit.kind === 'write') {
        primary = running
          ? t('aiChat.toolFlow.line.writing', { name: fileEdit.fileName })
          : failed
            ? t('aiChat.toolFlow.line.writeFailed', { name: fileEdit.fileName })
            : t('aiChat.toolFlow.line.wrote', { name: fileEdit.fileName });
      } else {
        primary = running
          ? t('aiChat.toolFlow.line.editing', { name: fileEdit.fileName })
          : failed
            ? t('aiChat.toolFlow.line.editFailed', { name: fileEdit.fileName })
            : t('aiChat.toolFlow.line.edited', { name: fileEdit.fileName });
      }
    } else if (failed) {
      secondary = 'fail';
    }

    lines.push({
      key,
      kind: 'tool',
      primary,
      secondary,
      detail: '',
      running,
      toolName: name,
      toolArgs: argsObj,
      toolResult: resultStr,
      fileEdit: failed ? (fileEdit ? { ...fileEdit, addedLines: undefined, removedLines: undefined } : null) : fileEdit,
      toolStatus: status,
    });
    return lines;
  }

  if (evt.type === 'tool_result') {
    const data = typeof evt.content === 'object' ? evt.content : {};
    const name = String(data.name || data.tool || 'Tool');
    const result = formatResult(data.result ?? data.output ?? data);
    const failed = !!(data.error || isToolResultFailure(result));
    lines.push({
      key,
      kind: 'tool',
      primary: friendlyToolName(name, t),
      secondary: failed ? 'fail' : '',
      detail: '',
      toolName: name,
      toolArgs: null,
      toolResult: result,
      fileEdit: null,
      toolStatus: failed ? 'error' : 'success',
    });
    return lines;
  }

  if (evt.type === 'info') {
    const text =
      typeof evt.content === 'string'
        ? evt.content
        : evt.content?.text || evt.content?.message || '';
    if (!text) return lines;
    // Lifecycle noise (also filtered in timeline convert / live WS).
    if (/^New session started$/i.test(String(text).trim()) || /^Workflow started$/i.test(String(text).trim())) {
      return lines;
    }
    // 模型切换（工作流进行中并入块的 info 事件）→ 本地化文案。
    const infoAny = typeof evt.content === 'object' && evt.content ? (evt.content as any) : null;
    const label = infoAny?.event === 'model_card_switched' && infoAny?.model
      ? t('aiChat.modelSwitched', { model: String(infoAny.model) })
      : text;
    lines.push({
      key,
      kind: 'info',
      primary: label.length > 60 ? `${label.slice(0, 60)}…` : label,
      secondary: '',
      detail: text,
    });
    return lines;
  }

  if (evt.type === 'plan') {
    const steps = parsePlanContent(evt.content);
    if (steps.length === 0) return lines;
    const running = steps.some((s) => s.status === 'running');
    const done = steps.filter((s) => s.status === 'done').length;
    lines.push({
      key,
      kind: 'plan',
      primary: 'plan',
      secondary: String(steps.length),
      detail: done > 0 ? `${done}/${steps.length}` : '',
      running: running && !blockCompleted,
      planSteps: steps,
    });
  }

  return lines;
}

/** 导出仅供单元测试：构建工作流块的行列表（含深度思考耗时推断）。 */
export function buildLines(
  block: WorkflowBlock,
  shellStreams: Record<string, ShellStreamState> = {},
  t: TFunction,
  lineCache?: WeakMap<WorkflowEvent, ActivityLine[]>,
): ActivityLine[] {
  const lines: ActivityLine[] = [];
  // 上下文摘要块内去重：只保留最后一条 summary_stream（与实时 WS 行为一致）。
  // 历史重建 / 相邻块合并后可能出现"流式帧 + 完成帧"两条，渲染成重复的
  // "上下文摘要"折叠；合并块里也只会有一条。
  let blockEvents = block.events;
  let lastSummaryIdx = -1;
  for (let i = blockEvents.length - 1; i >= 0; i--) {
    if (blockEvents[i].type === 'summary_stream') {
      lastSummaryIdx = i;
      break;
    }
  }
  if (lastSummaryIdx >= 0) {
    let hasEarlier = false;
    for (let i = 0; i < lastSummaryIdx; i++) {
      if (blockEvents[i].type === 'summary_stream') {
        hasEarlier = true;
        break;
      }
    }
    if (hasEarlier) {
      blockEvents = blockEvents.filter(
        (e, i) => e.type !== 'summary_stream' || i === lastSummaryIdx,
      );
    }
  }
  const baseItems = buildDisplayWorkflowItems(blockEvents);
  const items = attachShellJobsToDisplayItems(baseItems, shellStreams);

  for (let i = 0; i < items.length; i++) {
    const item = items[i];
    if (item.kind === 'delegation') {
      lines.push({
        key: item.key,
        kind: 'delegation',
        primary: item.bundle.running ? t('aiChat.toolFlow.line.exploring') : t('aiChat.toolFlow.line.explored'),
        secondary: item.bundle.label,
        detail: '',
        running: item.bundle.running,
        delegation: item.bundle,
      });
      continue;
    }
    if (item.kind === 'shell_job') {
      // 优先展示 agent 提供的调用目的说明；缺省回退到"已执行命令 + 命令"。
      const desc = (item.bundle.description || '').trim();
      lines.push({
        key: item.key,
        kind: 'shell_job',
        primary: desc || (item.bundle.running
          ? t('aiChat.toolFlow.shell.running')
          : item.bundle.errored
            ? t('aiChat.toolFlow.shell.failed')
            : t('aiChat.toolFlow.shell.ran')),
        secondary: desc ? '' : item.bundle.command,
        detail: item.bundle.output,
        running: item.bundle.running,
        shellJob: item.bundle,
      });
      continue;
    }
    // Skip orphan sub-agent events that somehow weren't nested (still hide from main stream
    // when they carry the flag — they belong in a delegate window).
    if (item.event.subAgent) continue;
    // 纯 UI 工具（追问建议等）只在各自的前端卡片里出现，不在工具流明细里占行。
    if (
      (item.event.type === 'tool_call' || item.event.type === 'tool_result')
      && isUiOnlyTool(toolNameOf(item.event))
    ) {
      continue;
    }
    let built = lineCache?.get(item.event);
    if (!built) {
      built = eventToLines(item.event, item.key, !!block.completed, t);
      lineCache?.set(item.event, built);
    }
    for (const l of built) {
      if (l.kind === 'thought') {
        // Deep-think duration = time from this thought until the next event
        // arrived (copy, never mutate — `built` is cached per event).
        const ts = item.event.timestamp;
        const isLastItem = i === items.length - 1;
        const next = items[i + 1];
        // Next item may be a folded shell/delegate job — its parent event carries the timestamp.
        const nextEvt =
          next && 'event' in next ? next.event : next && 'bundle' in next ? next.bundle?.parent : undefined;
        let nextTs = nextEvt?.timestamp;
        // 历史数据防御：flush 补写的思考事件时间戳可能晚于其后的工具事件
        // （时间戳倒挂）。向前扫描第一个更晚的事件作为耗时终点。
        if (typeof ts === 'number' && typeof nextTs === 'number' && nextTs <= ts) {
          for (let k = i + 2; k < items.length; k++) {
            const later = items[k];
            const laterEvt =
              later && 'event' in later ? later.event : later && 'bundle' in later ? later.bundle?.parent : undefined;
            const laterTs = laterEvt?.timestamp;
            if (typeof laterTs === 'number' && laterTs > ts) {
              nextTs = laterTs;
              break;
            }
          }
        }
        if (typeof ts === 'number' && typeof nextTs === 'number' && nextTs > ts) {
          lines.push({ ...l, secondary: formatElapsedAtLeastOneSecond(nextTs - ts) });
          continue;
        }
        if (typeof ts === 'number' && isLastItem) {
          if (!block.completed) {
            // Live trailing thought: duration grows in real time. The row
            // component ticks it locally (LiveElapsed) — buildLines stays
            // static so text bodies are not rebuilt/reselected every second.
            lines.push({ ...l, running: true, elapsedStartMs: ts, secondary: '' });
            continue;
          }
          // Completed turn where the thought was the last item: freeze at the
          // block end (started + elapsed) so the duration does not vanish.
          const blockEnd =
            typeof block.started_ms === 'number' && typeof block.elapsed_ms === 'number'
              ? block.started_ms + block.elapsed_ms
              : undefined;
          if (typeof blockEnd === 'number' && blockEnd > ts) {
            lines.push({ ...l, secondary: formatElapsedAtLeastOneSecond(blockEnd - ts) });
            continue;
          }
        }
      }
      lines.push(l);
    }
  }

  return lines;
}

function frozenElapsedMs(block: WorkflowBlock, turnStartedMs?: number): number | null {
  if (typeof block.elapsed_ms === 'number') return Math.max(0, block.elapsed_ms);
  const start =
    (typeof turnStartedMs === 'number' ? turnStartedMs : undefined)
    ?? (typeof block.started_ms === 'number' ? block.started_ms : undefined)
    ?? (typeof block.events[0]?.timestamp === 'number' ? block.events[0].timestamp : undefined);
  if (typeof start !== 'number') return null;
  let end: number | undefined;
  for (let i = block.events.length - 1; i >= 0; i--) {
    const ts = block.events[i]?.timestamp;
    if (typeof ts === 'number' && ts >= start) {
      end = ts;
      break;
    }
  }
  // Completed turns must NEVER use Date.now() — that keeps "Worked for"
  // climbing after stop / no assistant reply (e.g. 11h ghosts).
  return Math.max(0, (end ?? start) - start);
}

/**
 * Work-mode tool categories — each maps a tool name to a user-facing action so
 * the outer fold can read like "已读取 3 个文件，搜索 2 次文件" in real time.
 */
type WorkToolCategory =
  | 'read' | 'search' | 'edit' | 'list' | 'terminal' | 'web' | 'skill' | 'task'
  | 'interaction' | 'collab' | 'memory' | 'goal' | 'media' | 'mcp' | 'system' | 'other';

/** Tool names arrive as `namespace__function` (e.g. websearch__search,
 *  filesystem__read_file, system__run_session_job, mcp__server__tool). */
function splitToolName(name: string): { ns: string; fn: string } {
  const n = String(name || '').toLowerCase();
  if (n.startsWith('mcp__')) {
    const parts = n.split('__');
    return { ns: 'mcp', fn: parts.slice(2).join('__') || (parts[1] || '') };
  }
  const idx = n.indexOf('__');
  if (idx > 0) return { ns: n.slice(0, idx), fn: n.slice(idx + 2) };
  const dot = n.indexOf('.');
  if (dot > 0) return { ns: n.slice(0, dot), fn: n.slice(dot + 1) };
  return { ns: n, fn: n };
}

function classifyWorkTool(name: string): WorkToolCategory {
  const { ns, fn } = splitToolName(name);
  // MCP 工具（mcp__server__tool）走专用的 Server 图标分类。
  if (ns === 'mcp') return 'mcp';
  if (ns.startsWith('skill')) return 'skill';
  if (ns === 'websearch' || ns === 'web' || ns === 'bocha') return 'web';
  // 用户交互：确认卡 / 追问建议 / 模式切换。
  if (isUiOnlyToolName(name)) return 'interaction';
  // 多智能体协作与消息：delegate_task 通常已被 delegation 折叠消费，兜底归协作。
  if (ns === 'collaboration' || ns === 'delegate_task' || ns === 'im' || ns === 'task_watch') return 'collab';
  if (ns === 'memory') return 'memory';
  if (ns === 'goal') return 'goal';
  // 系统控制：等待（system.wait）/ 定时提醒（reminder.set 等闹钟类）/ 状态切换。
  if (ns === 'reminder' || ns === 'scheduled_tasks') return 'system';
  // 多媒体（插件）：图像理解 / 媒体生成 / 语音转写。
  if (ns === 'vision' || ns === 'media' || ns.startsWith('whisper')) return 'media';
  if (ns === 'filesystem') {
    // '_' is a \w char so \b never matches inside snake_case — segment-match
    // on (^|_)…($|_) instead. Edit runs before list so create_directory
    // doesn't trip the directory-listing pattern. Only true strangers fall
    // through to 'other'.
    if (/(^|_)(read_file|read_multiple_files|view_file|read|cat|view)($|_)/.test(fn)) return 'read';
    if (/(^|_)(search_files|find_files|grep|search|glob)($|_)/.test(fn)) return 'search';
    if (/(^|_)(write_file|edit_file|replace_in_file|str_replace|apply_diff|patch|write|edit|create_file|create_directory|mkdir|delete_file|rename|move)($|_)/.test(fn)) return 'edit';
    if (/(^|_)(ls|tree)($|_)/.test(fn) || /(^|_)list(_|$)/.test(fn)) return 'list';
    return 'other';
  }
  if (ns === 'system') {
    if (/shell|session|job|(^|_)(run|bash|exec|command|terminal)($|_)/.test(fn)) return 'terminal';
    if (/(^|_)(write|binary)($|_)/.test(fn)) return 'edit';
    // 等待 / 睡眠 / 状态切换是系统控制，不是"其他工具"。
    if (/^(wait|sleep|set_state|set_wake_mode)$/.test(fn)) return 'system';
    return 'other';
  }
  // 裸名兜底（部分链路会剥掉 system. 前缀再上报）。
  if (/^(wait|sleep|set_state|set_wake_mode)$/.test(fn)) return 'system';
  if (/(^|_)(read_file|read_multiple_files|view_file|read|cat|view)($|_)/.test(fn)) return 'read';
  if (/(^|_)(grep|search_files|find_files|search|glob)($|_)/.test(fn)) return 'search';
  if (/(^|_)(write_file|edit_file|replace_in_file|str_replace|patch|apply_diff|write|edit|replace)($|_)/.test(fn)) return 'edit';
  if (/terminal|(^|_)(bash|run_command|run_session_job|start_job|job|shell|session|exec|command|run)($|_)/.test(fn)) return 'terminal';
  if (/(^|_)(todo|update_task_progress|batch_update_tasks|add_task|update_todo|task)($|_)/.test(fn)) return 'task';
  if (/(^|_)(ls|tree)($|_)/.test(fn) || /(^|_)list(_|$)/.test(fn)) return 'list';
  if (fn.includes('skill')) return 'skill';
  return 'other';
}

/**
 * 纯 UI 交互工具（追问建议 / 选项确认 / 模式切换）—— 不是「工具流」的一部分。
 *
 * 这三者都不产出任何用户可见的动作：真正的 UI 是各自的前端组件（追问 chips、
 * 选项确认卡、模式切换卡），工具行只是同一件事的第二遍陈述。所以它们既不进
 * 「工具流统计」的计数（否则标题里会多出「向用户确认 1 次」），也不在工具流
 * 明细里占一行。
 *
 * 只过滤**展示层**：`WorkflowBlock.events` 保持原样 —— 「是否还有未闭合工具」
 * 「本轮是否已交付」等判定都读原始事件，不能被这里影响。
 *
 * 判定本身住在 `aiChatTimeline.isUiOnlyToolName`：实时时间线要用同一份名单，
 * 才知道一个 tool_call 之后是否还有真正的活儿（见 `demoteTrailing`）。
 */
function isUiOnlyTool(name: string): boolean {
  return isUiOnlyToolName(name);
}

/** Short, user-facing label for a tool name (websearch__search → 网络搜索 / Web search).
 *  Labels live in i18n under aiChat.toolFlow.{ns,fn}; falls back to the raw fn. */
function friendlyToolName(name: string, t: TFunction): string {
  const { ns, fn } = splitToolName(name);
  if (ns.startsWith('skill')) return t('aiChat.toolFlow.skill');
  const nsLabel = t(`aiChat.toolFlow.ns.${ns}`, { defaultValue: '' });
  if (nsLabel) return nsLabel;
  const fnLabel = t(`aiChat.toolFlow.fn.${fn}`, { defaultValue: '' });
  if (fnLabel) return fnLabel;
  if (fn) return fn.replace(/_/g, ' ');
  return String(name || 'Tool');
}

/** Category-matched leading icon for a tool line (Globe for web search,
 *  magnifier for file search, etc.). */
function toolIcon(name: string): React.ReactNode {
  const cat = classifyWorkTool(name);
  const size = 13;
  const cls = 'shrink-0 opacity-70';
  switch (cat) {
    case 'web': return <Globe size={size} className={cls} />;
    case 'search': return <Search size={size} className={cls} />;
    case 'read': return <FileText size={size} className={cls} />;
    case 'edit': return <Pencil size={size} className={cls} />;
    case 'terminal': return <Terminal size={size} className={cls} />;
    case 'skill': return <Sparkles size={size} className={cls} />;
    case 'task': return <CheckSquare size={size} className={cls} />;
    case 'list': return <Folder size={size} className={cls} />;
    case 'mcp': return <Server size={size} className={cls} />;
    case 'interaction': return <MessageCircleQuestion size={size} className={cls} />;
    case 'collab': return <Users size={size} className={cls} />;
    case 'memory': return <Database size={size} className={cls} />;
    case 'goal': return <Target size={size} className={cls} />;
    case 'media': return <Image size={size} className={cls} />;
    case 'system': return <Clock size={size} className={cls} />;
    default: return <Wrench size={size} className={cls} />;
  }
}

/**
 * Count tool calls per category and render a localized summary for Work mode.
 * Counts every tool_call (finished or still running) so the headline updates
 * live while the agent works.
 */
function summarizeWorkTools(block: WorkflowBlock, t: TFunction): string {
  const counts = new Map<WorkToolCategory, number>();
  for (const e of block.events) {
    if (e.type !== 'tool_call') continue;
    if (e.subAgent) continue; // nested delegate tools stay inside the delegate fold
    const data = typeof e.content === 'object' && e.content ? e.content : {};
    const name = String(data.name || data.tool || 'Tool');
    // 纯 UI 交互工具不进统计 —— 它们是前端卡片，不是「做过的事」。
    if (isUiOnlyTool(name)) continue;
    const cat = classifyWorkTool(name);
    counts.set(cat, (counts.get(cat) || 0) + 1);
  }
  const parts: string[] = [];
  for (const cat of ['read', 'search', 'edit', 'list', 'terminal', 'web', 'skill', 'task', 'interaction', 'collab', 'memory', 'goal', 'media', 'mcp', 'system', 'other'] as WorkToolCategory[]) {
    const n = counts.get(cat);
    if (!n) continue;
    parts.push(t(`aiChat.toolFlow.summary.${cat}`, { n }));
  }
  return parts.join('，');
}

function outerSummary(
  block: WorkflowBlock,
  lines: ActivityLine[],
  turnStartedMs: number | undefined,
  uiMode: 'classic' | 'solo',
  t: TFunction,
): { primary: string; secondary: string } {
  const thoughts = lines.filter((l) => l.kind === 'thought').length;
  const tools = lines.filter((l) => l.kind === 'tool' || l.kind === 'delegation' || l.kind === 'shell_job').length;
  const plans = lines.filter((l) => l.kind === 'plan');
  const summaries = lines.filter((l) => l.kind === 'summary');
  const liveSummary = summaries.some((l) => l.running);
  const liveTool = lines.some((l) => l.kind === 'tool' && l.running);
  const livePlan = plans.some((l) => l.running);
  const hasLiveLine = lines.some((l) => l.running);
  const stillLive = !!(liveTool || livePlan || liveSummary || (hasLiveLine && !block.completed) || !block.completed);
  let elapsedMs: number | null = null;
  if (stillLive) {
    if (turnStartedMs != null) {
      elapsedMs = Math.max(0, Date.now() - turnStartedMs);
    } else if (typeof block.started_ms === 'number') {
      elapsedMs = Math.max(0, Date.now() - block.started_ms);
    } else if (typeof block.elapsed_ms === 'number') {
      elapsedMs = Math.max(0, block.elapsed_ms);
    }
  } else if (typeof block.elapsed_ms === 'number') {
    elapsedMs = Math.max(0, block.elapsed_ms);
  } else if (block.completed) {
    elapsedMs = frozenElapsedMs(block, turnStartedMs);
  }
  const elapsedLabel =
    elapsedMs != null ? formatElapsedAtLeastOneSecond(elapsedMs) : null;

  if (liveSummary) {
    if (elapsedLabel != null) return { primary: t('aiChat.toolFlow.outer.compressingFor', { time: elapsedLabel }), secondary: '' };
    return { primary: t('aiChat.toolFlow.outer.compressing'), secondary: '' };
  }
  // Summary finished (even if workflow block not yet marked completed).
  if (summaries.length > 0 && summaries.every((l) => !l.running) && block.completed) {
    return { primary: t('aiChat.toolFlow.outer.compressed'), secondary: '' };
  }
  // Work mode: live Chinese tool summary headline ("读取 3 个文件，搜索 2 次文件")
  // updates in real time while the agent works, with a live-ticking elapsed
  // ("… · 7s → · 8s") joined by "·" — the label freezes at the final elapsed
  // once the block completes.
  if (uiMode === 'classic' && tools > 0) {
    const summary = summarizeWorkTools(block, t);
    if (summary) {
      return {
        primary: elapsedLabel != null ? `${summary} · ${elapsedLabel}` : summary,
        secondary: '',
      };
    }
  }
  if (liveTool || livePlan || (hasLiveLine && !block.completed)) {
    if (elapsedLabel != null) return { primary: t('aiChat.toolFlow.outer.workingFor', { time: elapsedLabel }), secondary: '' };
    return { primary: t('aiChat.toolFlow.outer.working'), secondary: '' };
  }
  // Incomplete block = still working (even between tool rounds / without turnStartedMs).
  if (!block.completed) {
    if (elapsedLabel != null) return { primary: t('aiChat.toolFlow.outer.workingFor', { time: elapsedLabel }), secondary: '' };
    return { primary: t('aiChat.toolFlow.outer.working'), secondary: '' };
  }
  if (tools > 0 && elapsedLabel != null) return { primary: t('aiChat.toolFlow.outer.workedFor', { time: elapsedLabel }), secondary: '' };
  if (tools > 0) return { primary: t('aiChat.toolFlow.outer.worked'), secondary: '' };
  if (plans.length > 0) {
    const n = plans.reduce((sum, l) => sum + (l.planSteps?.length || 0), 0);
    if (n > 0) return { primary: t('aiChat.toolFlow.outer.plan'), secondary: String(n) };
    return { primary: t('aiChat.toolFlow.outer.planned'), secondary: '' };
  }
  if (thoughts > 0) {
    if (elapsedMs != null && elapsedMs >= 2000 && elapsedLabel != null) {
      return { primary: t('aiChat.toolFlow.outer.thoughtFor', { time: elapsedLabel }), secondary: '' };
    }
    return { primary: t('aiChat.toolFlow.line.thoughtDeep'), secondary: '' };
  }
  return { primary: t('aiChat.toolFlow.outer.activity'), secondary: '' };
}

/** Soft white-light sweep across live activity title text. */
const ShimmerLabel: React.FC<{
  children: React.ReactNode;
  color: string;
  className?: string;
}> = ({ children, color, className }) => (
  <span
    className={`solo-text-shimmer ${className || ''}`}
    style={{ ['--solo-shimmer-base' as string]: color }}
  >
    {children}
  </span>
);

/** Waiting buffer while the next thought / tool call is being prepared. */
const NextPlanningPlaceholder: React.FC<{
  depth?: 0 | 1;
  /** Classic Agent Web: Manus-style pulse dots instead of shimmer ellipsis */
  classic?: boolean;
  startedMs?: number;
}> = ({ depth = 1, classic = false, startedMs }) => {
  if (classic) {
    return (
      <div className="w-full select-none py-0.5">
        <PulseDotsStatus kind="preparing" startedMs={startedMs} />
      </div>
    );
  }
  const faint =
    depth === 0
      ? 'color-mix(in srgb, rgb(var(--color-text-muted)) 72%, transparent)'
      : 'color-mix(in srgb, rgb(var(--color-text-muted)) 55%, transparent)';
  return (
    <div className="w-full select-none py-0.5">
      <ShimmerLabel color={faint} className="text-[13px] leading-relaxed font-normal">
        next planning...
      </ShimmerLabel>
    </div>
  );
};

/** Self-ticking elapsed label for a live trailing thought ("深度思考 4s…"). */
const LiveElapsed: React.FC<{ fromMs: number }> = React.memo(({ fromMs }) => {
  const [, force] = useState(0);
  useEffect(() => {
    const iv = window.setInterval(() => force((n) => n + 1), 1000);
    return () => window.clearInterval(iv);
  }, []);
  return <>{formatElapsedAtLeastOneSecond(Math.max(0, Date.now() - fromMs))}</>;
});

/** Cursor-like faint activity chrome: outer slightly stronger, nested lighter. */
const TextChevronToggle: React.FC<{
  primary: string;
  secondary?: React.ReactNode;
  open: boolean;
  onToggle: () => void;
  running?: boolean;
  /** Soft light sweep on the title while this line is in progress */
  shimmer?: boolean;
  /** 0 = outer turn, 1 = event line */
  depth?: 0 | 1;
  addedLines?: number;
  removedLines?: number;
  /** Failed tool outcome — red title + fail cue */
  errored?: boolean;
  /** Filename substring in primary — dashed underline + click (does not toggle) */
  fileLabel?: string;
  onFileClick?: () => void;
  /** Dot-matrix orbit before the title (e.g. live “Working for”) */
  leadingPulse?: boolean;
  /** Category icon rendered before the title (tool lines only) */
  leadingIcon?: React.ReactNode;
}> = React.memo(({ primary, secondary, open, onToggle, running, shimmer, depth = 0, addedLines, removedLines, errored, fileLabel, onFileClick, leadingPulse, leadingIcon }) => {
  const { t } = useTranslation();
  // Inline color-mix: Tailwind opacity utilities were not reliably fading
  // primary labels (inherited theme muted stayed too strong).
  const faint = errored
    ? 'color-mix(in srgb, #dc2626 78%, transparent)'
    : depth === 0
      ? 'color-mix(in srgb, rgb(var(--color-text-muted)) 72%, transparent)'
      : 'color-mix(in srgb, rgb(var(--color-text-muted)) 70%, transparent)';

  const fileIdx = fileLabel && onFileClick ? primary.indexOf(fileLabel) : -1;
  const primaryNode =
    fileIdx >= 0 && fileLabel && onFileClick ? (
      <span className="font-normal">
        {primary.slice(0, fileIdx)}
        <span
          role="link"
          tabIndex={0}
          className="underline decoration-dashed decoration-white/30 underline-offset-2 hover:decoration-white/50"
          onClick={(e) => {
            e.stopPropagation();
            e.preventDefault();
            onFileClick();
          }}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ' ') {
              e.stopPropagation();
              e.preventDefault();
              onFileClick();
            }
          }}
        >
          {fileLabel}
        </span>
        {primary.slice(fileIdx + fileLabel.length)}
      </span>
    ) : (
      <span className="font-normal">{primary}</span>
    );

  const title = (
    <>
      {primaryNode}
      {secondary ? (
        <span className={errored && secondary === 'fail' ? 'font-medium' : undefined}>
          {' '}
          {secondary === 'fail' ? t('aiChat.toolFlow.line.fail') : secondary}
        </span>
      ) : null}
    </>
  );

  return (
  <button
    type="button"
    onClick={onToggle}
    style={{ color: faint, ['--solo-shimmer-base' as string]: faint }}
    className={`group inline-flex items-center gap-1.5 py-0.5 text-left max-w-full bg-transparent border-0 p-0 cursor-pointer${
      depth === 0 && (shimmer || leadingPulse) ? ' solo-activity-header' : ''
    }`}
  >
    {leadingPulse ? <PulseDotsOrbit size={depth === 0 ? 16 : 14} /> : null}
    {leadingIcon ? (
      <span className="shrink-0 flex items-center">{leadingIcon}</span>
    ) : null}
    <span className="text-[13px] leading-relaxed min-w-0" style={{ color: faint }}>
      {shimmer ? <ShimmerLabel color={faint}>{title}</ShimmerLabel> : title}
    </span>
    {!errored && (addedLines != null && addedLines > 0) && (
      <span className="text-[12px] font-mono font-semibold shrink-0" style={{ color: 'color-mix(in srgb, #059669 55%, transparent)' }}>
        +{addedLines}
      </span>
    )}
    {!errored && (removedLines != null && removedLines > 0) && (
      <span className="text-[12px] font-mono font-semibold shrink-0" style={{ color: 'color-mix(in srgb, #ef4444 55%, transparent)' }}>
        -{removedLines}
      </span>
    )}
    {errored && secondary !== 'fail' ? (
      <span className="text-[11px] font-medium shrink-0" style={{ color: faint }}>
        fail
      </span>
    ) : null}
    <span className="text-[13px] font-normal leading-relaxed shrink-0 inline-flex w-3.5 justify-center" style={{ color: faint }}>
      {(open ? '⌄' : '>')}
    </span>
  </button>
  );
});

const SoloToolExpandPanel: React.FC<{
  toolName: string;
  args: Record<string, unknown> | null | undefined;
  result: string;
  running?: boolean;
  /** Hide bulky html arg when the iframe is rendered below the assistant reply */
  hideHtmlArg?: boolean;
}> = ({ toolName, args, result, running, hideHtmlArg = false }) => {
  const hasArgs = !!(args && Object.keys(args).length > 0);
  const hasResult = !!(result && result.trim());
  const displayArgs = useMemo(() => {
    if (!args || !hideHtmlArg) return args;
    const next = { ...args };
    if ('html' in next) {
      const raw = next.html;
      const len = typeof raw === 'string' ? raw.length : JSON.stringify(raw ?? '').length;
      next.html = `[HTML ${len} chars — rendered below reply]`;
    }
    return next;
  }, [args, hideHtmlArg]);

  // Prefer short host message from html_embed result
  const displayResult = useMemo(() => {
    if (!hasResult) return '';
    try {
      const o = JSON.parse(result);
      if (o && typeof o === 'object' && o.kind === 'html_embed') {
        if (typeof o.text === 'string' && o.text.trim()) return o.text;
        if (Array.isArray(o.content) && o.content[0]?.text) return String(o.content[0].text);
        return `Interactive visualization "${o.filename || o.title || 'viz'}" was created.`;
      }
    } catch {
      /* fall through */
    }
    return result;
  }, [hasResult, result]);

  return (
    <div className="mt-1 mb-1.5 rounded-md border border-border/50 bg-bgLight overflow-hidden">
      <div className="px-2.5 py-1.5 border-b border-border/40 bg-bgLight">
        <span className="text-[11px] font-mono text-textMuted/80 truncate block">{toolName}</span>
      </div>
      {hasArgs && (
        <div className="px-2.5 py-2 border-b border-border/30">
          <div className="text-[10px] font-medium text-textMuted/60 mb-1 uppercase tracking-wide">Input</div>
          <div className="space-y-1 max-h-[220px] overflow-y-auto">
            {Object.entries(displayArgs || {}).map(([k, v]) => {
              const valStr = typeof v === 'string' ? v : prettyJson(v);
              const isLong = valStr.length > 120 || valStr.includes('\n');
              return (
                <div key={k} className={isLong ? 'space-y-0.5' : 'flex items-start gap-2'}>
                  <span className="text-[11px] font-mono font-semibold text-amber-700/80 dark:text-amber-400/80 flex-shrink-0">
                    {k}
                  </span>
                  {isLong ? (
                    <pre className="text-[11px] text-textMuted/70 whitespace-pre-wrap break-all font-mono m-0 leading-relaxed max-h-[140px] overflow-y-auto">
                      {valStr}
                    </pre>
                  ) : (
                    <span className="text-[11px] text-textMuted/70 font-mono break-all leading-relaxed">
                      {valStr}
                    </span>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}
      <div className="px-2.5 py-2">
        <div className="text-[10px] font-medium text-textMuted/60 mb-1 uppercase tracking-wide">
          {running && !hasResult ? 'Status' : 'Output'}
        </div>
        {running && !hasResult ? (
          <div className="text-[12px] text-textMuted/55">Running…</div>
        ) : hasResult ? (
          <pre className="text-[11px] text-textMuted/75 whitespace-pre-wrap break-words font-mono m-0 leading-relaxed max-h-[280px] overflow-y-auto">
            {prettyJson(displayResult)}
          </pre>
        ) : (
          <div className="text-[12px] text-textMuted/45">No result</div>
        )}
      </div>
    </div>
  );
};

/** Workflow-style Plan card inside Solo activity stream. */
const SoloPlanFold: React.FC<{
  steps: PlanStep[];
  running?: boolean;
  defaultOpen?: boolean;
}> = ({ steps, running, defaultOpen = true }) => (
  <div className="w-full select-text my-0.5">
    <PlanBlock
      steps={steps}
      defaultOpen={defaultOpen || !!running}
      className="mb-0 border border-border/55 rounded-lg overflow-hidden bg-bgLight"
    />
  </div>
);

const SoloEventLine = React.memo(function SoloEventLine({
  line,
  defaultOpen = false,
  shellStreamFor,
  onOpenFile,
  embedVisualizations: _embedVisualizations = false,
}: {
  line: ActivityLine;
  defaultOpen?: boolean;
  shellStreamFor?: (callId: string) => ShellStreamState | null | undefined;
  onOpenFile?: (path: string) => void;
  /** @deprecated Embeds render below the assistant reply; tool stream stays a normal tool row. */
  embedVisualizations?: boolean;
}) {
  void _embedVisualizations;
  const { t } = useTranslation();
  const isSummary = line.kind === 'summary';
  const isProgress = line.kind === 'progress';
  // Keep compression summary open while streaming so text is visible live.
  // useFold keeps the body mounted after the first expansion and animates
  // open/close via <Collapse> (same primitive as the sidebar groups).
  const {
    open,
    mounted,
    toggle: foldToggle,
    setOpen: foldSetOpen,
  } = useFold(defaultOpen || !!(isSummary && line.running));
  /** Once the user toggles this fold, preference changes must not fight them. */
  const userTouchedRef = useRef(false);
  const isThought = line.kind === 'thought';
  const isFileEdit = !!(line.fileEdit && (line.fileEdit.kind === 'edit' || line.fileEdit.kind === 'write'));
  const isFileRead = line.fileEdit?.kind === 'read';
  // Hide bulky html arg/result text in the tool expand panel; iframe renders below the reply.
  const hideVizHtml =
    line.kind === 'tool' &&
    (!!extractHtmlEmbed(line.toolName, line.toolArgs, line.toolResult) ||
      isVisualizationToolName(line.toolName));

  useEffect(() => {
    if (userTouchedRef.current) return;
    if (defaultOpen) {
      foldSetOpen(true);
      return;
    }
    // Keep compression summary open while streaming. Do NOT auto-open file
    // diffs — streaming Myers + highlight on every delta is the main jank source.
    if (isSummary && line.running) {
      foldSetOpen(true);
      return;
    }
    // Tools just landed in this stream: collapse thoughts so the tool row
    // is not pushed out of the 280px step box by an open thought body.
    if (isThought) foldSetOpen(false);
  }, [defaultOpen, isSummary, isThought, line.running, foldSetOpen]);

  const toggleOpen = () => {
    userTouchedRef.current = true;
    foldToggle();
  };

  const added = line.fileEdit?.addedLines;
  const removed = line.fileEdit?.removedLines;

  // Delegate: open Cursor-style sub-agent window (not an inline expand).
  if (line.kind === 'delegation' && line.delegation) {
    return <DelegateFold bundle={line.delegation} variant="solo" />;
  }

  // Shell job: CMD-style live panel
  if (line.kind === 'shell_job' && line.shellJob) {
    return (
      <ShellJobFold
        bundle={line.shellJob}
        stream={shellStreamFor?.(line.shellJob.id)}
        variant="solo"
      />
    );
  }

  // Plan / To-dos: Cursor-style bordered list fold.
  if (line.kind === 'plan' && line.planSteps && line.planSteps.length > 0) {
    return (
      <SoloPlanFold
        steps={line.planSteps}
        running={line.running}
        defaultOpen={defaultOpen || !!line.running}
      />
    );
  }

  // Progress lines are compact status rows (no nested fold needed).
  if (isProgress) {
    return (
      <div
        className="w-full select-text py-0.5 text-[12px] leading-relaxed"
        style={{ color: 'color-mix(in srgb, rgb(var(--color-text-muted)) 62%, transparent)' }}
      >
        {line.running ? <span className="opacity-80">… </span> : null}
        <span className="whitespace-pre-wrap break-words">{line.detail || line.primary}</span>
      </div>
    );
  }

  return (
    <div
      className="w-full select-text"
      data-tool-expanded={line.kind === 'tool' && open ? true : undefined}
    >
      <TextChevronToggle
        primary={line.primary}
        secondary={line.elapsedStartMs != null ? <LiveElapsed fromMs={line.elapsedStartMs} /> : line.secondary}
        open={open}
        onToggle={toggleOpen}
        running={line.running}
        shimmer={!!line.running && (line.kind === 'tool' || line.kind === 'thought' || line.kind === 'summary')}
        depth={1}
        addedLines={isFileEdit ? added : undefined}
        removedLines={isFileEdit ? removed : undefined}
        errored={line.kind === 'tool' && line.toolStatus === 'error'}
        fileLabel={line.fileEdit?.fileName}
        onFileClick={
          line.fileEdit && onOpenFile
            ? () => onOpenFile(line.fileEdit!.filePath)
            : undefined
        }
        leadingIcon={
          line.kind === 'tool'
            ? toolIcon(line.toolName || line.primary)
            : line.kind === 'thought'
              ? <Zap size={13} className="shrink-0 opacity-70" />
              : line.kind === 'process'
                ? <Lightbulb size={13} className="shrink-0 opacity-70" />
                : undefined
        }
      />

      {/* Bodies stay mounted after first expansion — <Collapse> animates the
          height (grid 1fr→0fr) instead of snapping via conditional unmount. */}
      {mounted ? (
        <Collapse open={open}>
          {/* Thought body only — title stays outside the faded panel (same as thought-only fold). */}
          {isThought && line.detail && (
            <div className="mt-0.5 pl-4 pr-1 rounded-sm bg-bgLight py-1">
              <MarkdownScrollBody
                text={line.detail}
                follow={!!line.running}
                softEdge={!!line.running}
                muted
                maxHeightClass="max-h-[320px]"
              />
            </div>
          )}

          {/* 过程输出 body — intermediate assistant summary inside the workflow fold. */}
          {line.kind === 'process' && line.detail && (
            <div className="mt-0.5 pl-4 pr-1 rounded-sm bg-bgLight py-1">
              <MarkdownScrollBody text={line.detail} muted maxHeightClass="max-h-[320px]" />
            </div>
          )}

          {/* Context compression summary — live streaming text (classic-parity). */}
          {isSummary && (line.detail || line.running) && (
            <div
              className={`mt-0.5 pl-4 pr-1 py-1.5 rounded-md border ${
                line.summaryDone
                  ? 'border-emerald-500/25 bg-emerald-500/[0.04]'
                  : 'border-indigo-500/25 bg-indigo-500/[0.04]'
              }`}
            >
              {line.summaryPending && !line.detail ? (
                <div
                  className="text-[12px] animate-pulse"
                  style={{ color: 'color-mix(in srgb, rgb(var(--color-text-muted)) 55%, transparent)' }}
                >
                  Waiting for context compression…
                </div>
              ) : (
                <FollowScrollBox
                  as="pre"
                  contentKey={(line.detail || '').length}
                  follow={!!line.running}
                  className="text-[12px] leading-relaxed whitespace-pre-wrap break-words font-sans m-0 bg-transparent border-0 p-0 max-h-[360px] overflow-y-auto"
                  style={{ color: 'color-mix(in srgb, rgb(var(--color-text-muted)) 70%, transparent)' }}
                >
                  {line.detail || 'Summarizing…'}
                  {line.running && !line.summaryPending ? (
                    <span className="inline-block w-1.5 h-3.5 bg-indigo-400/50 animate-pulse ml-0.5 align-middle" />
                  ) : null}
                </FollowScrollBox>
              )}
            </div>
          )}

          {isFileEdit && line.fileEdit && (
            <div className="mt-1 mb-1.5 pl-4">
              <FileDiffBlock
                info={line.fileEdit}
                status={line.toolStatus || 'success'}
                embedded
              />
            </div>
          )}

          {isFileRead && line.fileEdit && (
            <div className="mt-1 mb-1.5 pl-4">
              <FileDiffBlock
                info={line.fileEdit}
                status={line.toolStatus || 'success'}
                resultContent={line.toolResult}
                embedded
              />
            </div>
          )}

          {line.kind === 'tool' && !isFileEdit && !isFileRead && (
            <div className="pl-4">
              <SoloToolExpandPanel
                toolName={friendlyToolName(line.toolName || line.primary, t)}
                args={line.toolArgs}
                result={line.toolResult || ''}
                running={line.running}
                hideHtmlArg={hideVizHtml}
              />
            </div>
          )}

          {line.kind === 'info' && line.detail && (
            <div className="mt-1 mb-1.5 pl-4 rounded-md border border-border/40 bg-bgLight px-2.5 py-2">
              <pre
                className="text-[12px] whitespace-pre-wrap break-words font-sans m-0"
                style={{ color: 'color-mix(in srgb, rgb(var(--color-text-muted)) 42%, transparent)' }}
              >
                {line.detail}
              </pre>
            </div>
          )}
        </Collapse>
      ) : null}
    </div>
  );
}, (prev, next) => {
  if (prev.defaultOpen !== next.defaultOpen) return false;
  if (prev.onOpenFile !== next.onOpenFile) return false;
  if (prev.line !== next.line && !activityLineEqual(prev.line, next.line)) return false;
  if (next.line.kind === 'shell_job') {
    const id = next.line.shellJob?.id;
    if (id && prev.shellStreamFor?.(id) !== next.shellStreamFor?.(id)) return false;
  }
  return true;
});

export function mergeWorkflowBlocks(blocks: WorkflowBlock[]): WorkflowBlock {
  if (blocks.length === 1) return blocks[0];
  const events = blocks.flatMap((b) => b.events);
  const completed = blocks.every((b) => b.completed);
  const elapsed = blocks.reduce((sum, b) => sum + (typeof b.elapsed_ms === 'number' ? b.elapsed_ms : 0), 0);
  const startedNums = blocks
    .map((b) => b.started_ms)
    .filter((n): n is number => typeof n === 'number');
  const started = startedNums.length ? Math.min(...startedNums) : undefined;
  const status = completed
    ? null
    : (blocks.find((b) => !b.completed)?.status ?? 'working');
  return {
    events,
    status,
    completed,
    // Live merged groups must not inherit a frozen elapsed from earlier chunks
    // or the fold stops being a scroll box / clock and tools leak into the page.
    elapsed_ms: completed && elapsed > 0 ? elapsed : undefined,
    started_ms: started,
  };
}

export const SoloActivityRow = React.memo(function SoloActivityRow({
  block,
  turnDelivered = false,
  expandLevel = 'thoughts',
  turnStartedMs,
  shellStreams = {},
  onOpenFile,
  embedVisualizations = false,
  uiMode = 'solo',
}: SoloActivityRowProps) {
  const { t } = useTranslation();
  const expand = workflowExpandFlags(expandLevel);
  const [tick, setTick] = useState(0);
  // 任务已交付 → 视为已收尾：所有"执行中"判定（脉冲/流光/计时）一律停。
  const effBlock = turnDelivered && !block.completed ? { ...block, completed: true } : block;
  const hasOpenTools = effBlock.events.some(
    (e) => e.type === 'tool_call' && !e.result,
  );
  // Async delegate_task_submit keeps the sub-agent window live after the parent
  // turn seals — treat it as still running so the outer fold / panel stay mounted.
  const hasAsyncDelegate = !turnDelivered && hasOpenAsyncDelegate(effBlock.events);
  const hasLiveShell = !turnDelivered
    && Object.values(shellStreams).some((s) => s.state === 'running');
  const hasRunning = hasOpenTools || hasAsyncDelegate || hasLiveShell;
  const hasLiveCompression = effBlock.events.some((e) => {
    if (effBlock.completed && !hasAsyncDelegate) return false;
    if (e.type === 'summary_stream') {
      const data = typeof e.content === 'object' && e.content ? e.content : {};
      return !data.done;
    }
    if (e.type === 'compression_progress') {
      return !isFinalFlag(e.content);
    }
    return false;
  });
  // Parent only passes turnStartedMs for the active incomplete group.
  // Treat any incomplete block as live so gaps between tool rounds do not
  // flip the header to "Worked" and auto-collapse while the agent is still going.
  // After Stop, the fold is completed even if some tool_call never got a
  // result — do not keep "Working" / spinner from those open tools.
  const isLiveTurn =
    !effBlock.completed || hasLiveCompression || hasAsyncDelegate || hasLiveShell;
  const hasToolSteps = effBlock.events.some(
    (e) => e.type === 'tool_call' || e.type === 'tool_result',
  );

  // Outer fold state goes through useFold so the body mounts lazily and the
  // open/close animates via <Collapse> (same motion as the sidebar groups).
  const {
    open: outerOpen,
    mounted: outerMounted,
    toggle: toggleOuterFold,
    setOpen: setOuterOpen,
  } = useFold(isLiveTurn);
  const [stepVirt, setStepVirt] = useState({ start: 0, end: 24 });
  /** User pin: 'open' | 'closed' | null (follow auto open/collapse). */
  const userOverrideRef = useRef<'open' | 'closed' | null>(null);
  const stepsScrollRef = useRef<HTMLDivElement>(null);
  const stepsAtBottomRef = useRef(true);
  const rootRef = useRef<HTMLDivElement>(null);

  const toggleOuter = useCallback(() => {
    const next = toggleOuterFold();
    userOverrideRef.current = next ? 'open' : 'closed';
  }, [toggleOuterFold]);

  useEffect(() => {
    if (isLiveTurn) {
      // Keep the process visible while the agent is working, unless the user
      // hid it. Do not clear the pin — history rebuilds can flicker
      // completed↔live and would otherwise snap against a deliberate toggle.
      if (userOverrideRef.current !== 'closed') setOuterOpen(true);
      return;
    }
    if (userOverrideRef.current === 'open') return;
    // Delay so a brief completed↔live flicker does not hide the steps, and so
    // the fold tucks away right after the final reply lands.
    const t = window.setTimeout(() => {
      if (userOverrideRef.current === 'open') return;
      setOuterOpen(false);
    }, 480);
    return () => window.clearTimeout(t);
  }, [isLiveTurn]);

  useEffect(() => {
    if (!isLiveTurn) return;
    const t = setInterval(() => {
      // Elapsed-time header only — never rebuild line bodies on this tick.
      // Skip entirely while the user has a text selection (any pane).
      const sel = window.getSelection();
      if (sel && !sel.isCollapsed) return;
      setTick((n) => n + 1);
    }, 1000);
    return () => clearInterval(t);
  }, [isLiveTurn]);

  // Do NOT put `tick` in buildLines deps — that remounted thought/tool text every
  // 400ms and cleared mouse selections in scheduled-task / live panes.
  const prevLinesRef = useRef<ActivityLine[]>([]);
  const lineCacheRef = useRef<{
    completed: boolean;
    map: WeakMap<WorkflowEvent, ActivityLine[]>;
  }>({ completed: !!effBlock.completed, map: new WeakMap() });
  const lines = useMemo(() => {
    const cache = lineCacheRef.current;
    if (cache.completed !== !!effBlock.completed) {
      cache.completed = !!effBlock.completed;
      cache.map = new WeakMap();
    }
    const built = buildLines(effBlock, shellStreams, t, cache.map);
    const prevByKey = new Map(prevLinesRef.current.map((l) => [l.key, l]));
    const next = built.map((line) => {
      const old = prevByKey.get(line.key);
      return old && activityLineEqual(old, line) ? old : line;
    });
    prevLinesRef.current = next;
    return next;
  }, [effBlock, shellStreams, t]);
  const summary = useMemo(
    () => outerSummary(effBlock, lines, turnStartedMs, uiMode, t),
    [effBlock, lines, turnStartedMs, tick, uiMode, t],
  );

  // Active phase detection: while the latest step is still thought / plan /
  // compression / a running tool, do NOT show "next planning…".
  // That placeholder is only for the idle gap *after* real work has settled,
  // waiting on the agent's next move — never on an empty / lifecycle-only block
  // (new session, mode switch, Workflow started with no thoughts yet).
  const lastActivity = useMemo(() => {
    for (let i = lines.length - 1; i >= 0; i--) {
      const l = lines[i];
      if (l.kind === 'info') continue;
      return l;
    }
    return null;
  }, [lines]);

  const thinkingActive =
    isLiveTurn && !effBlock.completed && lastActivity?.kind === 'thought';
  const planningActive =
    isLiveTurn &&
    !effBlock.completed &&
    (lastActivity?.kind === 'plan' || lines.some((l) => l.kind === 'plan' && !!l.running));

  const displayLines = useMemo(() => {
    if (!thinkingActive) return lines;
    let lastThoughtIdx = -1;
    for (let i = lines.length - 1; i >= 0; i--) {
      if (lines[i].kind === 'thought') {
        lastThoughtIdx = i;
        break;
      }
    }
    if (lastThoughtIdx < 0) return lines;
    return lines.map((l, i) => (i === lastThoughtIdx ? { ...l, running: true } : l));
  }, [lines, thinkingActive]);

  const useStepsScrollBox = displayLines.length > SOLO_STEPS_SCROLL_THRESHOLD;
  const virtSteps = useStepsScrollBox && displayLines.length > STEP_VIRT_AFTER;

  const shellStreamsRef = useRef(shellStreams);
  shellStreamsRef.current = shellStreams;
  const shellStreamFor = useCallback(
    (id: string) => shellStreamsRef.current[id],
    [],
  );

  const virtRafRef = useRef<number | null>(null);
  const userScrollingRef = useRef(false);
  const userScrollIdleRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const markStepsUserScrolling = useCallback(() => {
    userScrollingRef.current = true;
    if (userScrollIdleRef.current) clearTimeout(userScrollIdleRef.current);
    userScrollIdleRef.current = setTimeout(() => {
      userScrollingRef.current = false;
    }, 180);
  }, []);
  const syncStepVirt = useCallback((el: HTMLDivElement) => {
    const n = displayLines.length;
    setStepVirt((p) => {
      const next = expandVirtWindow(n, el.scrollTop, el.clientHeight, p);
      return p.start === next.start && p.end === next.end ? p : next;
    });
  }, [displayLines.length]);

  useEffect(() => () => {
    if (virtRafRef.current != null) cancelAnimationFrame(virtRafRef.current);
    if (userScrollIdleRef.current) clearTimeout(userScrollIdleRef.current);
  }, []);

  // Keep the steps box pinned to the latest tools (thoughts sit above them).
  // Do this after the turn seals too — otherwise the 280px box stays on the
  // first thought lines and the tool headers never enter the viewport.
  // Window jumps to the tail only here (new rows while stuck to bottom), never
  // from the scroll handler — that fight with the thumb is the jitter source.
  useLayoutEffect(() => {
    if (!outerOpen || !useStepsScrollBox) return;
    const sel = window.getSelection();
    if (sel && !sel.isCollapsed) return;
    const el = stepsScrollRef.current;
    if (!el) return;
    const n = displayLines.length;
    if (userScrollingRef.current) {
      if (virtSteps) syncStepVirt(el);
      return;
    }
    if (!stepsAtBottomRef.current) {
      if (virtSteps) syncStepVirt(el);
      return;
    }
    if (virtSteps) setStepVirt(virtTailWindow(n, el.clientHeight));
    el.scrollTop = el.scrollHeight;
  }, [displayLines.length, outerOpen, useStepsScrollBox, virtSteps, syncStepVirt]);

  const hasSettledActivity = displayLines.some(
    (l) =>
      (l.kind === 'thought' ||
        l.kind === 'tool' ||
        l.kind === 'plan' ||
        l.kind === 'summary' ||
        l.kind === 'progress' ||
        l.kind === 'delegation' ||
        l.kind === 'shell_job') &&
      !l.running,
  );

  const showNextPlanning =
    isLiveTurn &&
    !hasRunning &&
    !hasLiveCompression &&
    !thinkingActive &&
    !planningActive &&
    !displayLines.some((l) => !!l.running) &&
    hasSettledActivity;

  const liveStartedMs =
    turnStartedMs ??
    (typeof block.started_ms === 'number' ? block.started_ms : undefined);

  const renderOuterToggle = (opts?: { shimmer?: boolean; running?: boolean }) => {
    const running = opts?.running ?? isLiveTurn;
    return (
      <TextChevronToggle
        primary={summary.primary}
        secondary={summary.secondary}
        open={outerOpen}
        onToggle={toggleOuter}
        running={running}
        shimmer={opts?.shimmer}
        depth={0}
        leadingPulse={!!running}
      />
    );
  };

  // Empty / lifecycle-only incomplete blocks: render nothing (matches classic
  // WorkflowBlockView). Never show a lone "next planning…" on a blank session.
  if (!lines.length) {
    return null;
  }

  // Thought-only (no tools / compression / delegate / plan): single fold → body text.
  const isThoughtOnly = !displayLines.some(
    (l) =>
      l.kind === 'tool' ||
      l.kind === 'summary' ||
      l.kind === 'progress' ||
      l.kind === 'delegation' ||
      l.kind === 'shell_job' ||
      l.kind === 'plan',
  );
  // Compression-only: one fold → summary body (avoid "Context compressed" + "Context summary done").
  const isCompressionOnly =
    !isThoughtOnly &&
    displayLines.every((l) => l.kind === 'summary' || l.kind === 'progress') &&
    displayLines.some((l) => l.kind === 'summary');
  // Plan-only (+ optional thoughts): outer fold opens to To-dos box.
  const isPlanOnly =
    !isThoughtOnly &&
    !isCompressionOnly &&
    displayLines.every((l) => l.kind === 'plan' || l.kind === 'thought') &&
    displayLines.some((l) => l.kind === 'plan');

  const thoughtBodies = displayLines
    .filter((l) => l.kind === 'thought' && l.detail.trim())
    .map((l) => l.detail);

  // Live turns always use the inner step stream so a later tool_call can
  // appear as a row. Thought-only document layout is for completed folds.
  if (isThoughtOnly && !isLiveTurn) {
    // Info-only / empty chrome used to render a bare "Activity" fold on new session.
    if (thoughtBodies.length === 0 && !showNextPlanning) return null;
    if (thoughtBodies.length === 0 && showNextPlanning) {
      return (
        <div ref={rootRef} className="my-1.5 w-full select-text">
          <NextPlanningPlaceholder
            depth={0}
            classic={embedVisualizations}
            startedMs={liveStartedMs}
          />
        </div>
      );
    }

    return (
      <div ref={rootRef} className="my-1.5 w-full select-text">
        {renderOuterToggle({ running: isLiveTurn, shimmer: thinkingActive })}
        {outerMounted && thoughtBodies.length > 0 ? (
          <Collapse open={outerOpen}>
            <div className="mt-0.5 pl-4 pr-1 rounded-sm bg-bgLight py-1">
              {thoughtBodies.map((text, i) => (
                <MarkdownScrollBody
                  key={i}
                  text={text}
                  follow={thinkingActive && i === thoughtBodies.length - 1}
                  muted
                  maxHeightClass="max-h-[320px]"
                />
              ))}
            </div>
          </Collapse>
        ) : null}
        {showNextPlanning ? (
          <div className={outerOpen ? 'pl-4' : undefined}>
            <NextPlanningPlaceholder
              depth={outerOpen ? 1 : 0}
              classic={embedVisualizations}
              startedMs={liveStartedMs}
            />
          </div>
        ) : null}
      </div>
    );
  }

  if (isCompressionOnly) {
    // Prefer the latest summary line (streaming updates merge into one event usually).
    const summaryLines = displayLines.filter((l) => l.kind === 'summary');
    const summaryLine = summaryLines[summaryLines.length - 1];
    const live = !!(summaryLine?.running || hasLiveCompression);

    return (
      <div ref={rootRef} className="my-1.5 w-full select-text">
        {renderOuterToggle({ running: live, shimmer: live })}
        {outerMounted && summaryLine && (summaryLine.detail || summaryLine.running) ? (
          <Collapse open={outerOpen}>
            <div
              className={`mt-0.5 pl-4 pr-1 py-1.5 rounded-md border ${
                summaryLine.summaryDone
                  ? 'border-emerald-500/25 bg-emerald-500/[0.04]'
                  : 'border-indigo-500/25 bg-indigo-500/[0.04]'
              }`}
            >
              {summaryLine.summaryPending && !summaryLine.detail ? (
                <div
                  className="text-[12px] animate-pulse"
                  style={{ color: 'color-mix(in srgb, rgb(var(--color-text-muted)) 55%, transparent)' }}
                >
                  Waiting for context compression…
                </div>
              ) : (
                <FollowScrollBox
                  as="pre"
                  contentKey={(summaryLine.detail || '').length}
                  follow={!!summaryLine.running}
                  className="text-[12px] leading-relaxed whitespace-pre-wrap break-words font-sans m-0 bg-transparent border-0 p-0 max-h-[360px] overflow-y-auto"
                  style={{ color: 'color-mix(in srgb, rgb(var(--color-text-muted)) 70%, transparent)' }}
                >
                  {summaryLine.detail || 'Summarizing…'}
                  {summaryLine.running && !summaryLine.summaryPending ? (
                    <span className="inline-block w-1.5 h-3.5 bg-indigo-400/50 animate-pulse ml-0.5 align-middle" />
                  ) : null}
                </FollowScrollBox>
              )}
            </div>
          </Collapse>
        ) : null}
        {showNextPlanning ? (
          <div className={outerOpen ? 'pl-4' : undefined}>
            <NextPlanningPlaceholder
              depth={outerOpen ? 1 : 0}
              classic={embedVisualizations}
              startedMs={liveStartedMs}
            />
          </div>
        ) : null}
      </div>
    );
  }

  // Plan-only: show optional thought body + Cursor-style To-dos fold (no extra outer wrapper).
  if (isPlanOnly) {
    const planLines = displayLines.filter((l) => l.kind === 'plan' && l.planSteps && l.planSteps.length > 0);
    return (
      <div ref={rootRef} className="my-1.5 w-full select-text space-y-0.5">
        {thoughtBodies.length > 0 && (
          <div className="mb-1">
            {renderOuterToggle({ running: thinkingActive, shimmer: thinkingActive })}
            {outerMounted ? (
              <Collapse open={outerOpen}>
                <div className="mt-0.5 pl-4 pr-1 rounded-sm bg-bgLight py-1">
                  {thoughtBodies.map((text, i) => (
                    <MarkdownScrollBody
                      key={i}
                      text={text}
                      follow={thinkingActive && i === thoughtBodies.length - 1}
                      softEdge={thinkingActive && i === thoughtBodies.length - 1}
                      muted
                      maxHeightClass="max-h-[320px]"
                    />
                  ))}
                </div>
              </Collapse>
            ) : null}
          </div>
        )}
        {planLines.map((line) => (
          <SoloPlanFold
            key={line.key}
            steps={line.planSteps!}
            running={line.running}
            defaultOpen
          />
        ))}
        {showNextPlanning ? (
          <NextPlanningPlaceholder classic={embedVisualizations} startedMs={liveStartedMs} />
        ) : null}
      </div>
    );
  }

  return (
    <div ref={rootRef} className="my-1.5 w-full select-text">
      {renderOuterToggle({
        running: isLiveTurn,
        shimmer: thinkingActive || isLiveTurn,
      })}
      {/* Depth 1: event lines indented under the outer fold.
          >10 steps → fixed-height scroll box so the page doesn't grow forever.
          Delegate folds stay mounted (hidden when collapsed) so an open
          SubAgentPanel keeps receiving live job_id updates after the turn seals.
          The whole body mounts lazily via useFold().mounted and animates
          through <Collapse> instead of display:none snapping. */}
      {outerMounted ? (
      <Collapse open={outerOpen}>
      <div
        ref={stepsScrollRef}
        onPointerDown={markStepsUserScrolling}
        onScroll={(e) => {
          const el = e.currentTarget;
          markStepsUserScrolling();
          stepsAtBottomRef.current =
            el.scrollHeight - el.scrollTop - el.clientHeight < 48;
          if (!virtSteps) return;
          if (virtRafRef.current != null) return;
          virtRafRef.current = requestAnimationFrame(() => {
            virtRafRef.current = null;
            syncStepVirt(el);
          });
        }}
        className={
          useStepsScrollBox
            ? `mt-0.5 pl-3 pr-1 py-1 ${SOLO_STEPS_SCROLL_MAX_CLASS} overflow-y-auto overflow-x-hidden overscroll-contain [scrollbar-gutter:stable] rounded-md border border-border/45 bg-bgLight ${
                virtSteps ? 'flex flex-col' : 'space-y-0.5'
              }`
            : 'mt-0.5 space-y-0.5 pl-4'
        }
        style={
          useStepsScrollBox
            ? { overflowAnchor: 'none', scrollBehavior: 'auto' }
            : undefined
        }
      >
        {(() => {
          const n = displayLines.length;
          const start = virtSteps ? Math.max(0, Math.min(stepVirt.start, n)) : 0;
          const end = virtSteps ? Math.max(start, Math.min(stepVirt.end, n)) : n;
          const slice = displayLines.slice(start, end);
          return (
            <>
              {virtSteps && start > 0 ? (
                <div style={{ height: start * STEP_EST_PX, flexShrink: 0 }} aria-hidden />
              ) : null}
              {slice.map((line) => (
                <SoloEventLine
                  key={line.key}
                  line={line}
                  shellStreamFor={shellStreamFor}
                  onOpenFile={onOpenFile}
                  embedVisualizations={embedVisualizations}
                  defaultOpen={
                    // Never auto-open tool panels just because they are running:
                    // each open panel pretty-prints args/results and used to stay
                    // open after the call finished, so fast bursts got slower
                    // with every extra tool. Expand-all remains an explicit pref.
                    (line.kind === 'thought' && expand.thoughts && !hasToolSteps) ||
                    (line.kind === 'plan' && expand.plan) ||
                    (line.kind === 'tool' && expand.tools) ||
                    !!(line.kind === 'summary' && line.running)
                  }
                />
              ))}
              {virtSteps && end < n ? (
                <div style={{ height: (n - end) * STEP_EST_PX, flexShrink: 0 }} aria-hidden />
              ) : null}
            </>
          );
        })()}
        {showNextPlanning ? (
          <NextPlanningPlaceholder classic={embedVisualizations} startedMs={liveStartedMs} />
        ) : null}
      </div>
      </Collapse>
      ) : null}
    </div>
  );
});
