/**
 * FileDiffBlock - renders a GitHub-style unified diff view for filesystem edit tool calls.
 *
 * Features:
 *   - True unified diff (Myers/LCS algorithm): shows context lines + changed lines interleaved
 *   - Syntax highlighting via highlight.js (language detected from file extension)
 *   - Tool result note displayed in expanded header
 *   - Collapsed sections between hunks (fold unchanged regions)
 *
 * Detects tool calls like:
 *   - filesystem.replace_in_file / filesystem.edit_file
 *   - mcp__filesystem__edit_file
 *   - Any tool with old_str/new_str or oldString/newString args
 *
 * Shows:
 *   - Collapsed header: "编辑 routes.py  +68  -10"
 *   - Expanded: unified diff with syntax highlighted lines
 */
import React, { useState, useMemo, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { ChevronDown, ChevronRight, CheckCircle, XCircle, Loader2, FilePen, FilePlus, FileText, MessageSquare, ChevronsUpDown } from 'lucide-react';
import { marked } from 'marked';
import { FollowScrollBox } from './FollowScrollBox';
import { getLangForFile, highlightLine, escapeHtml, HLJS_THEME_CSS } from '../../utils/codeHighlight';

/**
 * Extract a quoted string field (JSON or Python-repr style) from a tool payload.
 * Handles escapes so `'content': '1: {\\n2: …'` becomes real multiline text.
 * Also works on incomplete streaming JSON (unterminated string → returns so-far).
 */
export function extractQuotedField(raw: string, field: string): string | null {
  const keyRe = new RegExp(`['"]${field}['"]\\s*:\\s*`);
  const km = keyRe.exec(raw);
  if (!km) return null;
  let i = km.index + km[0].length;
  while (i < raw.length && /\s/.test(raw[i])) i++;
  const quote = raw[i];
  if (quote !== "'" && quote !== '"') return null;
  i += 1;
  let out = '';
  while (i < raw.length) {
    const c = raw[i];
    if (c === '\\' && i + 1 < raw.length) {
      const n = raw[i + 1];
      if (n === 'n') { out += '\n'; i += 2; continue; }
      if (n === 'r') { out += '\r'; i += 2; continue; }
      if (n === 't') { out += '\t'; i += 2; continue; }
      if (n === '\\' || n === "'" || n === '"') { out += n; i += 2; continue; }
      if (n === 'u' && i + 5 < raw.length) {
        const hex = raw.slice(i + 2, i + 6);
        if (/^[0-9a-fA-F]{4}$/.test(hex)) {
          out += String.fromCharCode(parseInt(hex, 16));
          i += 6;
          continue;
        }
      }
      out += n;
      i += 2;
      continue;
    }
    if (c === quote) break;
    out += c;
    i += 1;
  }
  return out;
}

/**
 * Parse complete or partial Native-FC tool arguments JSON for file write/edit preview.
 */
export function parsePartialFileToolArgs(raw: string | null | undefined): Record<string, unknown> | null {
  if (!raw || typeof raw !== 'string') return null;
  const trimmed = raw.trim();
  if (!trimmed) return null;

  try {
    const parsed = JSON.parse(trimmed);
    if (parsed && typeof parsed === 'object') return parsed as Record<string, unknown>;
  } catch {
    // Incomplete streaming JSON — fall through to field extraction.
  }

  const path =
    extractQuotedField(trimmed, 'path') ??
    extractQuotedField(trimmed, 'file_path') ??
    extractQuotedField(trimmed, 'filepath') ??
    extractQuotedField(trimmed, 'filename') ??
    extractQuotedField(trimmed, 'file');
  const content = extractQuotedField(trimmed, 'content');
  const newStr =
    extractQuotedField(trimmed, 'new_str') ??
    extractQuotedField(trimmed, 'new_string') ??
    extractQuotedField(trimmed, 'newString') ??
    extractQuotedField(trimmed, 'newStr');
  const oldStr =
    extractQuotedField(trimmed, 'old_str') ??
    extractQuotedField(trimmed, 'old_string') ??
    extractQuotedField(trimmed, 'oldString') ??
    extractQuotedField(trimmed, 'oldStr');

  if (path == null && content == null && newStr == null && oldStr == null) return null;

  const out: Record<string, unknown> = {};
  if (path != null) out.path = path;
  if (content != null) out.content = content;
  if (newStr != null) {
    out.new_str = newStr;
    out.new_string = newStr;
    out.newString = newStr;
  }
  if (oldStr != null) {
    out.old_str = oldStr;
    out.old_string = oldStr;
    out.oldString = oldStr;
  }
  return out;
}

/**
 * Unwrap filesystem.read_file tool payloads into plain file text.
 * Only the `content` field is shown — status / meta are discarded.
 * Supports JSON and Python-repr style dict strings from the tool runtime.
 */
export function normalizeReadFileDisplayContent(raw: string): {
  text: string;
  startLine: number;
} {
  if (!raw) return { text: '', startLine: 1 };
  let text = raw.trim();
  if (!text) return { text: '', startLine: 1 };

  for (let i = 0; i < 3; i++) {
    const looksWrapped = text.startsWith('{') || text.startsWith('"');
    if (!looksWrapped) break;

    try {
      const parsed = JSON.parse(text);
      if (typeof parsed === 'string') {
        text = parsed.trim();
        continue;
      }
      if (parsed && typeof parsed === 'object') {
        const o = parsed as Record<string, unknown>;
        if (typeof o.content === 'string') {
          text = o.content;
          break;
        }
        if (typeof o.result === 'string') {
          text = o.result.trim();
          continue;
        }
        if (typeof o.output === 'string') {
          text = o.output.trim();
          continue;
        }
      }
    } catch {
      // Python-repr / single-quoted payloads fail JSON.parse — fall through.
    }

    const fromContent = extractQuotedField(text, 'content');
    if (fromContent != null) {
      text = fromContent;
      break;
    }
    const fromResult = extractQuotedField(text, 'result');
    if (fromResult != null) {
      text = fromResult.trim();
      continue;
    }
    break;
  }

  if (text.charCodeAt(0) === 0xfeff) text = text.slice(1);
  text = text.replace(/^(\d+:\s*)\uFEFF/, '$1');

  const lines = text.split('\n');
  const numberedCount = lines.filter((l) => /^\d+: ?/.test(l)).length;
  let startLine = 1;
  if (lines.length > 0 && numberedCount >= Math.ceil(lines.length * 0.8)) {
    const firstNum = lines.find((l) => /^\d+:/.test(l));
    if (firstNum) {
      const n = parseInt(firstNum, 10);
      if (Number.isFinite(n) && n > 0) startLine = n;
    }
    text = lines.map((l) => l.replace(/^\d+: ?/, '')).join('\n');
  }

  return { text, startLine };
}

// ============================================================
// Types
// ============================================================

export type FileOpKind = 'read' | 'edit' | 'write' | 'other';

export interface FileEditInfo {
  kind: FileOpKind;
  filePath: string;
  fileName: string;
  oldStr: string | null;
  newStr: string;
  /** Optional — a failed/partial edit may drop the diff counts. */
  addedLines?: number;
  removedLines?: number;
  lineRange?: string;
  /** 1-based line number of the first line in oldStr/newStr (for contextual snippets). */
  startLine?: number;
}

// ============================================================
// Arg extraction
// ============================================================

function getArg(args: Record<string, any>, ...keys: string[]): string | undefined {
  for (const k of keys) {
    if (args[k] !== undefined && args[k] !== null) return String(args[k]);
  }
  return undefined;
}

export function extractFileEditInfo(toolName: string, args: any): FileEditInfo | null {
  if (!args || typeof args !== 'object') return null;

  const nameLower = toolName.toLowerCase();

  const filePath = getArg(args, 'path', 'file_path', 'filepath', 'filename', 'file');
  if (!filePath) return null;

  const fileName = filePath.split(/[/\\]/).pop() || filePath;

  const isEditTool =
    nameLower.includes('edit_file') ||
    nameLower.includes('replace_in_file') ||
    nameLower.includes('replace_file') ||
    (nameLower.includes('edit') && (args.old_str !== undefined || args.oldString !== undefined || args.old_string !== undefined));

  if (isEditTool || args.old_str !== undefined || args.old_string !== undefined || args.oldString !== undefined) {
    const oldStr = getArg(args, 'old_str', 'old_string', 'oldString', 'oldStr') ?? '';
    const newStr = getArg(args, 'new_str', 'new_string', 'newString', 'newStr') ?? '';

    const oldLines = oldStr ? oldStr.split('\n') : [];
    const newLines = newStr ? newStr.split('\n') : [];

    return {
      kind: 'edit',
      filePath,
      fileName,
      oldStr,
      newStr,
      addedLines: newLines.length,
      removedLines: oldLines.length,
    };
  }

  const isReadTool =
    nameLower.includes('read_file') ||
    nameLower.includes('view_file') ||
    nameLower.includes('open_file') ||
    nameLower === 'read' ||
    nameLower.endsWith('.read') ||
    nameLower.endsWith('__read');

  // Read: only if no edit/write args present
  if (
    isReadTool &&
    args.content === undefined &&
    args.old_str === undefined &&
    args.oldString === undefined &&
    args.old_string === undefined
  ) {
    const startLine = parseInt(args.start_line ?? args.startLine ?? 1, 10);
    const endLine = parseInt(args.end_line ?? args.endLine ?? -1, 10);
    const maxLines = parseInt(args.max_lines ?? args.maxLines ?? 200, 10);
    let lineRange: string;
    if (endLine > 0) {
      lineRange = `L${startLine}-L${endLine}`;
    } else if (maxLines > 0) {
      lineRange = `L${startLine}-L${startLine + maxLines - 1}`;
    } else {
      lineRange = `L${startLine}..`;
    }
    return { kind: 'read', filePath, fileName, oldStr: null, newStr: '', addedLines: 0, removedLines: 0, lineRange };
  }

  const isWriteTool =
    nameLower.includes('write_file') ||
    nameLower.includes('create_file') ||
    nameLower.includes('write_to_file') ||
    (nameLower.includes('write') && args.content !== undefined);

  // Allow streaming preview: write_file with path only (content still arriving).
  if (isWriteTool && (args.content !== undefined || nameLower.includes('write_file') || nameLower.includes('create_file'))) {
    const content = args.content !== undefined ? String(args.content) : '';
    const lines = content.split('\n');
    return {
      kind: 'write',
      filePath,
      fileName,
      oldStr: null,
      newStr: content,
      addedLines: content ? lines.length : 0,
      removedLines: 0,
    };
  }

  return null;
}

/**
 * Prefer server-expanded snippets (±unchanged context) from replace_in_file /
 * write_file over the raw tool args when available.
 */
export function applyEditDiffContext(
  info: FileEditInfo | null,
  ctx?: { diffOld?: string; diffNew?: string; diffStartLine?: number } | null,
): FileEditInfo | null {
  if (!info || !ctx) return info;
  if (ctx.diffOld == null || ctx.diffNew == null) return info;
  if (info.kind !== 'edit' && info.kind !== 'write') return info;
  const oldLines = ctx.diffOld.split('\n');
  const newLines = ctx.diffNew.split('\n');
  // Count real +/- so Solo "+N -M" stays about the edit, not the whole context window.
  const dp: number[][] = Array.from({ length: oldLines.length + 1 }, () =>
    new Array(newLines.length + 1).fill(0),
  );
  for (let i = 1; i <= oldLines.length; i++) {
    for (let j = 1; j <= newLines.length; j++) {
      dp[i][j] =
        oldLines[i - 1] === newLines[j - 1]
          ? dp[i - 1][j - 1] + 1
          : Math.max(dp[i - 1][j], dp[i][j - 1]);
    }
  }
  const lcs = dp[oldLines.length][newLines.length];
  return {
    ...info,
    // Promote write→edit semantics when we have a real before/after pair
    kind: info.kind === 'write' && ctx.diffOld.length > 0 ? 'edit' : info.kind,
    oldStr: ctx.diffOld,
    newStr: ctx.diffNew,
    addedLines: Math.max(0, newLines.length - lcs),
    removedLines: Math.max(0, oldLines.length - lcs),
    startLine: typeof ctx.diffStartLine === 'number' ? ctx.diffStartLine : info.startLine,
  };
}

// ============================================================
// LCS-based diff algorithm (Myers-inspired)
// ============================================================

type LineKind = 'unchanged' | 'removed' | 'added';

interface RawDiffLine {
  kind: LineKind;
  /** Line number in old file (1-based), or null for added lines */
  oldNo: number | null;
  /** Line number in new file (1-based), or null for removed lines */
  newNo: number | null;
  content: string;
}

/** Compute LCS length table */
function lcsTable(a: string[], b: string[]): number[][] {
  const m = a.length, n = b.length;
  const dp: number[][] = Array.from({ length: m + 1 }, () => new Array(n + 1).fill(0));
  for (let i = 1; i <= m; i++) {
    for (let j = 1; j <= n; j++) {
      if (a[i - 1] === b[j - 1]) {
        dp[i][j] = dp[i - 1][j - 1] + 1;
      } else {
        dp[i][j] = Math.max(dp[i - 1][j], dp[i][j - 1]);
      }
    }
  }
  return dp;
}

/** Build diff from LCS table */
function buildDiff(oldLines: string[], newLines: string[]): RawDiffLine[] {
  // For very large inputs, fall back to simple before/after to avoid O(n*m) memory
  if (oldLines.length * newLines.length > 200_000) {
    const result: RawDiffLine[] = [];
    oldLines.forEach((c, i) => result.push({ kind: 'removed', oldNo: i + 1, newNo: null, content: c }));
    newLines.forEach((c, i) => result.push({ kind: 'added', oldNo: null, newNo: i + 1, content: c }));
    return result;
  }

  const dp = lcsTable(oldLines, newLines);

  // Iterative backtracking to avoid stack overflow on large diffs
  let i = oldLines.length, j = newLines.length;
  const ops: Array<{ kind: LineKind; oldNo: number | null; newNo: number | null; content: string }> = [];

  while (i > 0 || j > 0) {
    if (i > 0 && j > 0 && oldLines[i - 1] === newLines[j - 1]) {
      ops.push({ kind: 'unchanged', oldNo: i, newNo: j, content: oldLines[i - 1] });
      i--; j--;
    } else if (j > 0 && (i === 0 || dp[i][j - 1] >= dp[i - 1][j])) {
      ops.push({ kind: 'added', oldNo: null, newNo: j, content: newLines[j - 1] });
      j--;
    } else {
      ops.push({ kind: 'removed', oldNo: i, newNo: null, content: oldLines[i - 1] });
      i--;
    }
  }

  ops.reverse();
  return ops;
}

// ============================================================
// Unified diff hunk generation (with context)
// ============================================================

const CONTEXT_LINES = 3;

interface DiffHunk {
  lines: RawDiffLine[];
}

interface FoldedSection {
  count: number;
  startOld: number;
  startNew: number;
}

type HunkEntry =
  | { type: 'hunk'; hunk: DiffHunk }
  | { type: 'fold'; fold: FoldedSection };

/** Group raw diff lines into hunks separated by folded unchanged sections */
function buildHunks(rawLines: RawDiffLine[]): HunkEntry[] {
  if (rawLines.length === 0) return [];

  // Find which lines are "changed" (for hunk boundary detection)
  const changed = rawLines.map(l => l.kind !== 'unchanged');

  // For each unchanged line, check if it's within CONTEXT_LINES of any changed line
  const keep = rawLines.map((_, idx) => {
    if (changed[idx]) return true;
    for (let d = -CONTEXT_LINES; d <= CONTEXT_LINES; d++) {
      const ni = idx + d;
      if (ni >= 0 && ni < changed.length && changed[ni]) return true;
    }
    return false;
  });

  const entries: HunkEntry[] = [];
  let i = 0;

  while (i < rawLines.length) {
    if (keep[i]) {
      // Collect consecutive "kept" lines into a hunk
      const hunkLines: RawDiffLine[] = [];
      while (i < rawLines.length && keep[i]) {
        hunkLines.push(rawLines[i]);
        i++;
      }
      entries.push({ type: 'hunk', hunk: { lines: hunkLines } });
    } else {
      // Collect consecutive "folded" lines
      const startOld = rawLines[i].oldNo ?? 0;
      const startNew = rawLines[i].newNo ?? 0;
      let count = 0;
      while (i < rawLines.length && !keep[i]) {
        count++;
        i++;
      }
      entries.push({ type: 'fold', fold: { count, startOld, startNew } });
    }
  }

  return entries;
}

// ============================================================
// Highlighted line cache (per component render)
// ============================================================

// ============================================================
// Sub-components
// ============================================================

interface DiffLineRowProps {
  line: RawDiffLine;
  lang: string;
}

const DiffLineRow: React.FC<DiffLineRowProps> = ({ line, lang }) => {
  const isRemoved = line.kind === 'removed';
  const isAdded = line.kind === 'added';
  const isUnchanged = line.kind === 'unchanged';

  const highlightedHtml = useMemo(() => highlightLine(line.content, lang), [line.content, lang]);

  const rowBg = isRemoved
    ? 'bg-red-500/10'
    : isAdded
    ? 'bg-green-500/10'
    : '';

  const lineNumStyle = isRemoved
    ? 'text-red-400/50 bg-red-500/15 border-red-500/20'
    : isAdded
    ? 'text-green-400/50 bg-green-500/15 border-green-500/20'
    : 'text-gray-600 bg-transparent border-gray-700/30';

  const marker = isRemoved ? '-' : isAdded ? '+' : ' ';
  const markerColor = isRemoved ? 'text-red-400 font-bold' : isAdded ? 'text-green-400 font-bold' : 'text-transparent';

  const contentColor = isRemoved ? 'text-red-200' : isAdded ? 'text-green-200' : 'text-gray-300';

  return (
    <div className={`flex items-start font-mono text-[11px] leading-5 min-w-0 ${rowBg}`}>
      {/* Old line number */}
      <span className={`select-none w-10 shrink-0 text-right pr-2 leading-5 tabular-nums text-[10px] border-r ${lineNumStyle}`}>
        {line.oldNo ?? ''}
      </span>
      {/* New line number */}
      <span className={`select-none w-10 shrink-0 text-right pr-2 leading-5 tabular-nums text-[10px] border-r ${lineNumStyle}`}>
        {line.newNo ?? ''}
      </span>
      {/* +/- marker */}
      <span className={`w-5 shrink-0 text-center select-none leading-5 ${markerColor}`}>
        {marker}
      </span>
      {/* Content with syntax highlighting — wrap so long lines stay readable */}
      <span
        className={`flex-1 min-w-0 whitespace-pre-wrap break-words pl-0.5 ${contentColor}`}
        dangerouslySetInnerHTML={{ __html: highlightedHtml }}
      />
    </div>
  );
};

interface FoldRowProps {
  fold: FoldedSection;
  onExpand: () => void;
}

const FoldRow: React.FC<FoldRowProps> = ({ fold, onExpand }) => {
  const { t } = useTranslation();
  return (
  <div
    className="flex items-center gap-2 px-2 py-0.5 bg-blue-900/10 border-y border-blue-500/10 cursor-pointer hover:bg-blue-900/20 transition-colors select-none"
    onClick={onExpand}
    title={t('aiChat.expandFoldedLines')}
  >
    <ChevronsUpDown size={11} className="text-blue-400/60 flex-shrink-0" />
    <span className="text-[10px] text-blue-400/60 font-mono">
      ... {fold.count} {t('aiChat.unchangedLines')}
    </span>
  </div>
  );
};

// ============================================================
// Shared hljs theme (Material Palenight) — from codeHighlight util
// ============================================================

const HLJS_STYLE = HLJS_THEME_CSS;

// ============================================================
// ReadContentPane — show only file content (like edit/write), never status/meta
// ============================================================

function renderReadMarkdown(text: string): string {
  try {
    return marked.parse(text, { breaks: true, async: false }) as string;
  } catch {
    return escapeHtml(text);
  }
}

const ReadContentPane: React.FC<{ content: string; lang: string }> = ({ content, lang }) => {
  const { text, startLine } = useMemo(
    () => normalizeReadFileDisplayContent(content),
    [content],
  );

  // Markdown files → prose (same chrome as edit/write body, no toggle).
  // Everything else → syntax-highlighted lines like write_file.
  if (lang === 'markdown') {
    const mdHtml = renderReadMarkdown(text);
    return (
      <div
        className="prose prose-sm prose-invert max-w-none break-words overflow-x-auto ai-markdown
                   text-[12.5px] leading-relaxed
                   max-h-[500px] overflow-y-auto
                   bg-gray-950 px-3.5 py-3"
        dangerouslySetInnerHTML={{ __html: mdHtml }}
      />
    );
  }

  const lines = text.length ? text.split('\n') : [''];
  return (
    <div className="max-h-[500px] overflow-y-auto overflow-x-hidden bg-gray-950">
      <style>{HLJS_STYLE}</style>
      {lines.map((lineContent, i) => {
        const html = highlightLine(lineContent, lang);
        return (
          <div
            key={i}
            className="flex items-start font-mono text-[11px] leading-5 min-w-0 hover:bg-primary/10"
          >
            <span className="select-none w-10 shrink-0 text-right pr-2 leading-5 tabular-nums text-[10px] text-gray-600 border-r border-gray-700/30 bg-gray-900/30">
              {startLine + i}
            </span>
            <span className="w-5 shrink-0" />
            <span
              className="flex-1 min-w-0 whitespace-pre-wrap break-words pl-0.5 text-gray-300"
              dangerouslySetInnerHTML={{ __html: html }}
            />
          </div>
        );
      })}
    </div>
  );
};

// ============================================================
// Main component
// ============================================================

interface FileDiffBlockProps {
  info: FileEditInfo;
  status: 'running' | 'success' | 'error';
  /** Short one-liner note from tool result (edit/write) */
  note?: string;
  /** Full read_file result — only inner `content` is shown (status/meta discarded) */
  resultContent?: string;
  /**
   * Solo mode: parent owns the fold header. When true, always render the
   * diff/body chrome without the FileDiffBlock header toggle.
   */
  embedded?: boolean;
  /** Open this file in the project files panel */
  onFileClick?: (path: string) => void;
}

export const FileDiffBlock: React.FC<FileDiffBlockProps> = ({ info, status, note, resultContent, embedded = false, onFileClick }) => {
  const { t } = useTranslation();
  const [isOpen, setIsOpen] = useState(embedded);
  const [expandedFolds, setExpandedFolds] = useState<Set<number>>(new Set());

  useEffect(() => {
    if (embedded) setIsOpen(true);
  }, [embedded]);

  const lang = useMemo(() => getLangForFile(info.fileName), [info.fileName]);

  const statusIcon = status === 'success'
    ? <CheckCircle size={12} className="text-emerald-500 flex-shrink-0" />
    : status === 'error'
    ? <XCircle size={12} className="text-red-500 flex-shrink-0" />
    : <Loader2 size={12} className="text-amber-500 animate-spin flex-shrink-0" />;

  // Build raw diff lines — always call hooks unconditionally (Rules of Hooks)
  const rawDiffLines = useMemo<RawDiffLine[]>(() => {
    let lines: RawDiffLine[];
    // write with a real before/after (from server diff_old) → unified red/green
    if (info.kind === 'write' && (info.oldStr == null || info.oldStr === '')) {
      lines = info.newStr.split('\n').map((content, i): RawDiffLine => ({
        kind: 'added',
        oldNo: null,
        newNo: i + 1,
        content,
      }));
    } else if (info.kind === 'write' || info.kind === 'edit') {
      const oldLines = (info.oldStr ?? '').split('\n');
      const newLines = info.newStr.split('\n');
      // Empty old file: treat as all adds (avoid a fake blank delete line)
      if (info.oldStr == null || (info.oldStr === '' && info.newStr !== '')) {
        lines = newLines.map((content, i): RawDiffLine => ({
          kind: 'added',
          oldNo: null,
          newNo: i + 1,
          content,
        }));
      } else {
        lines = buildDiff(oldLines, newLines);
      }
    } else if (!info.oldStr) {
      lines = [];
    } else {
      const oldLines = info.oldStr.split('\n');
      const newLines = info.newStr.split('\n');
      lines = buildDiff(oldLines, newLines);
    }
    const offset = typeof info.startLine === 'number' && info.startLine > 1 ? info.startLine - 1 : 0;
    if (!offset) return lines;
    return lines.map((l) => ({
      ...l,
      oldNo: l.oldNo != null ? l.oldNo + offset : null,
      newNo: l.newNo != null ? l.newNo + offset : null,
    }));
  }, [info]);

  // Build hunk entries
  const hunkEntries = useMemo(() => buildHunks(rawDiffLines), [rawDiffLines]);

  // When fold is expanded, we inline all its lines
  const expandFold = (foldIndex: number) => {
    setExpandedFolds(prev => {
      const next = new Set(prev);
      next.add(foldIndex);
      return next;
    });
  };

  // Count actual added/removed from diff
  const actualAdded = useMemo(() => rawDiffLines.filter(l => l.kind === 'added').length, [rawDiffLines]);
  const actualRemoved = useMemo(() => rawDiffLines.filter(l => l.kind === 'removed').length, [rawDiffLines]);

  const OpIcon = info.kind === 'write' ? FilePlus : FilePen;
  const opLabel = info.kind === 'write' ? t('aiChat.writeFile') : t('aiChat.editFile');

  // ── Read operation: simple viewer ──────────────────────────
  if (info.kind === 'read') {
    const canExpand = !!resultContent;
    const showBody = embedded ? !!resultContent : isOpen && !!resultContent;
    return (
      <div className="rounded-md border border-sky-500/20 bg-sky-500/5 overflow-hidden">
        {!embedded && (
          <div
            className={`flex items-center gap-1.5 px-2 py-1.5 transition-colors select-none ${canExpand ? 'cursor-pointer hover:bg-sky-500/10' : ''}`}
            onClick={() => canExpand && setIsOpen(!isOpen)}
          >
            {statusIcon}
            <FileText size={11} className="text-textMuted flex-shrink-0" />
            <span className="text-[11px] text-textMuted font-mono">{t('aiChat.readFile')}</span>
            <span
              className={`text-[11px] text-textMuted font-mono truncate flex-1 ${
                onFileClick ? 'underline decoration-dashed decoration-white/30 underline-offset-2 hover:decoration-white/50 cursor-pointer' : ''
              }`}
              onClick={(e) => {
                if (!onFileClick) return;
                e.stopPropagation();
                onFileClick(info.filePath);
              }}
              title={onFileClick ? info.filePath : undefined}
            >
              {info.fileName}
            </span>
            {info.lineRange && (
              <span className="text-[10px] text-sky-500 dark:text-sky-400 font-mono font-semibold flex-shrink-0">
                {info.lineRange}
              </span>
            )}
            {canExpand && (
              isOpen
                ? <ChevronDown size={12} className="text-textMuted flex-shrink-0 ml-1" />
                : <ChevronRight size={12} className="text-textMuted flex-shrink-0 ml-1" />
            )}
          </div>
        )}
        {showBody && resultContent && (
          <div className={embedded ? '' : 'border-t border-sky-500/10'}>
            <div className="px-2 py-1 bg-black/20 border-b border-sky-500/10">
              <span className="text-[10px] text-textMuted font-mono">{info.filePath}</span>
              {info.lineRange && (
                <span className="text-[10px] text-sky-500 dark:text-sky-400 font-mono ml-2">({info.lineRange})</span>
              )}
            </div>
            <ReadContentPane content={resultContent} lang={lang} />
          </div>
        )}
      </div>
    );
  }

  // Build hunk header label (e.g. @@ -10,4 +10,6 @@)
  function hunkHeader(lines: RawDiffLine[]): string {
    const oldStart = lines.find(l => l.oldNo !== null)?.oldNo ?? 1;
    const newStart = lines.find(l => l.newNo !== null)?.newNo ?? 1;
    const oldCount = lines.filter(l => l.kind !== 'added').length;
    const newCount = lines.filter(l => l.kind !== 'removed').length;
    return `@@ -${oldStart},${oldCount} +${newStart},${newCount} @@`;
  }

  return (
    <div className="rounded-md border border-amber-500/20 bg-amber-500/5 overflow-hidden">
      {/* ── Header ── */}
      {!embedded && (
        <div
          className="flex items-center gap-1.5 px-2 py-1.5 cursor-pointer hover:bg-amber-500/10 transition-colors select-none"
          onClick={() => setIsOpen(!isOpen)}
        >
          {statusIcon}
          <OpIcon size={11} className="text-textMuted flex-shrink-0" />
          <span className="text-[11px] text-textMuted font-mono">{opLabel}</span>
          <span
            className={`text-[11px] text-gray-700 dark:text-gray-200 font-mono font-semibold truncate flex-1 ${
              onFileClick ? 'underline decoration-dashed decoration-white/30 underline-offset-2 hover:decoration-white/50 cursor-pointer' : ''
            }`}
            onClick={(e) => {
              if (!onFileClick) return;
              e.stopPropagation();
              onFileClick(info.filePath);
            }}
            title={onFileClick ? info.filePath : undefined}
          >
            {info.fileName}
          </span>
          {actualAdded > 0 && (
            <span className="text-[11px] font-mono font-bold text-green-600 dark:text-green-400 flex-shrink-0">
              +{actualAdded}
            </span>
          )}
          {actualRemoved > 0 && (
            <span className="text-[11px] font-mono font-bold text-red-500 dark:text-red-400 flex-shrink-0 ml-0.5">
              -{actualRemoved}
            </span>
          )}
          {isOpen
            ? <ChevronDown size={12} className="text-textMuted flex-shrink-0 ml-1" />
            : <ChevronRight size={12} className="text-textMuted flex-shrink-0 ml-1" />
          }
        </div>
      )}

      {/* ── Expanded diff ── */}
      {(embedded || isOpen) && (
        <div className={embedded ? '' : 'border-t border-amber-500/10'}>
          {/* File path + note */}
          <div className="px-2 py-1.5 bg-black/20 border-b border-amber-500/10 space-y-0.5">
            <span className="block text-[10px] text-textMuted font-mono">{info.filePath}</span>
            {note && (
              <div className="flex items-start gap-1 pt-0.5">
                <MessageSquare size={10} className="text-gray-500 flex-shrink-0 mt-0.5" />
                <span className="text-[10px] text-gray-400 font-mono break-all">{note}</span>
              </div>
            )}
          </div>

          {/* Diff content */}
          <FollowScrollBox
            contentKey={`${info.filePath}:${info.newStr.length}:${info.oldStr?.length ?? 0}`}
            follow={status === 'running'}
            className="max-h-[500px] overflow-y-auto overflow-x-hidden bg-gray-950"
          >
            <style>{HLJS_STYLE}</style>

            {hunkEntries.length === 0 ? (
              <div className="p-3 text-[11px] text-gray-500 font-mono">
                {status === 'running' ? 'Streaming…' : 'No diff content'}
              </div>
            ) : (
              hunkEntries.map((entry, entryIdx) => {
                if (entry.type === 'fold') {
                  if (expandedFolds.has(entryIdx)) {
                    let skipStart = 0;
                    for (let ei = 0; ei < entryIdx; ei++) {
                      const e = hunkEntries[ei];
                      if (e.type === 'hunk') skipStart += e.hunk.lines.length;
                      else skipStart += e.fold.count;
                    }
                    const foldLines = rawDiffLines.slice(skipStart, skipStart + entry.fold.count);
                    return (
                      <div key={entryIdx}>
                        {foldLines.map((line, li) => (
                          <DiffLineRow key={`fold-${entryIdx}-${li}`} line={line} lang={lang} />
                        ))}
                      </div>
                    );
                  }
                  return (
                    <FoldRow
                      key={entryIdx}
                      fold={entry.fold}
                      onExpand={() => expandFold(entryIdx)}
                    />
                  );
                }

                // Hunk
                const { lines } = entry.hunk;
                return (
                  <div key={entryIdx}>
                    {/* Hunk header */}
                    <div className="px-2 py-0.5 text-[10px] text-blue-400/60 font-mono bg-blue-900/10 border-b border-blue-500/10 select-none">
                      {hunkHeader(lines)}
                    </div>
                    {lines.map((line, li) => (
                      <DiffLineRow key={`${entryIdx}-${li}`} line={line} lang={lang} />
                    ))}
                  </div>
                );
              })
            )}
          </FollowScrollBox>
        </div>
      )}
    </div>
  );
};
