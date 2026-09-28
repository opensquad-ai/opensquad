/**
 * TurnChangedFilesCard — after a turn completes, shows the files the
 * workflow created/modified (max 4) below the assistant reply.
 * Click a file → open it in the files panel; footer → open the panel's
 * "changed" tab for the full list.
 */
import React from 'react';
import { FileCode2, FilePlus2, ListTree, Pencil } from 'lucide-react';
import { extractFileEditInfo } from './FileDiffBlock';

export type TurnChangedFile = {
  /** Raw path as it appeared in tool args (rel or absolute). */
  path: string;
  name: string;
  /** write = created/new file, edit = modified existing file. */
  kind: 'write' | 'edit';
};

const LANG_BY_EXT: Record<string, string> = {
  js: 'JavaScript', jsx: 'JavaScript', mjs: 'JavaScript', cjs: 'JavaScript',
  ts: 'TypeScript', tsx: 'TypeScript',
  py: 'Python', json: 'JSON', md: 'Markdown', css: 'CSS', scss: 'SCSS',
  html: 'HTML', vue: 'Vue', go: 'Go', rs: 'Rust', java: 'Java',
  sh: 'Shell', bat: 'Batch', ps1: 'PowerShell', yml: 'YAML', yaml: 'YAML',
  toml: 'TOML', sql: 'SQL', c: 'C', cpp: 'C++', h: 'Header', cs: 'C#',
  rb: 'Ruby', php: 'PHP', swift: 'Swift', kt: 'Kotlin', txt: 'Text',
};

function langLabel(name: string): string {
  const dot = name.lastIndexOf('.');
  if (dot <= 0) return 'File';
  const ext = name.slice(dot + 1).toLowerCase();
  return LANG_BY_EXT[ext] || ext.toUpperCase();
}

function parseMaybeJsonArgs(raw: unknown): Record<string, unknown> | null {
  if (raw && typeof raw === 'object') return raw as Record<string, unknown>;
  if (typeof raw === 'string') {
    try {
      const v = JSON.parse(raw);
      return v && typeof v === 'object' ? (v as Record<string, unknown>) : null;
    } catch {
      return null;
    }
  }
  return null;
}

/** Workflow block shape (structural — avoids import cycles). */
type TurnWorkflowEvent = {
  type?: string;
  subAgent?: boolean;
  content?: unknown;
};
type TurnWorkflowBlock = {
  completed?: boolean;
  events?: TurnWorkflowEvent[];
};

/**
 * Same file, two spellings. The agent writes the same path both ways inside one
 * turn (`C:\...\model_cards\commandcode_space-bunny-alpha.json` and
 * `commandcode_space-bunny-alpha.json` — verified in session
 * 20260928_070309_n8ls), so keying the card by the raw path painted the SAME
 * file twice, side by side, which reads as a duplicated card.
 */
function pathKey(p: string): string {
  return p.replace(/\\/g, '/').replace(/\/+$/, '');
}

const isAbsolutePath = (p: string): boolean => /^[a-zA-Z]:\//.test(p) || p.startsWith('/');

function isSameFile(a: string, b: string): boolean {
  const na = pathKey(a);
  const nb = pathKey(b);
  if (na === nb) return true;
  // A relative spelling is the same file as an absolute one that ends with it —
  // but two relative paths never collapse into each other (`x.json` and
  // `sub/x.json` are different files).
  const abs = isAbsolutePath(na) ? na : isAbsolutePath(nb) ? nb : null;
  const rel = abs === na ? nb : abs === nb ? na : null;
  if (!abs || !rel || isAbsolutePath(rel)) return false;
  return abs.endsWith(`/${rel}`);
}

/** Collect unique created/modified file paths from workflow tool_call events. */
export function collectTurnChangedFiles(blocks: TurnWorkflowBlock[]): TurnChangedFile[] {
  const files: TurnChangedFile[] = [];
  for (const block of blocks) {
    for (const evt of block.events || []) {
      if (evt.type !== 'tool_call' || evt.subAgent) continue;
      const content = typeof evt.content === 'object' && evt.content ? evt.content as Record<string, unknown> : {};
      const name = String(content.name || content.tool || '');
      const args = parseMaybeJsonArgs(content.arguments ?? content.args ?? content.input);
      const info = extractFileEditInfo(name, args || {});
      if (!info || (info.kind !== 'write' && info.kind !== 'edit')) continue;
      const path = info.filePath;
      if (!path) continue;
      if (files.some((f) => isSameFile(f.path, path))) continue;
      files.push({ path, name: info.fileName || path, kind: info.kind === 'write' ? 'write' : 'edit' });
    }
  }
  return files;
}

/** Scan backwards from a timeline index for the completed workflow run that produced this reply. */
export function collectTurnChangedFilesBefore(
  timeline: Array<{ kind?: string; data?: unknown }>,
  replyIndex: number,
): TurnChangedFile[] {
  const blocks: TurnWorkflowBlock[] = [];
  for (let i = replyIndex - 1; i >= 0; i--) {
    const entry = timeline[i];
    if (!entry) break;
    if (entry.kind === 'workflow') {
      const block = (entry as { data?: TurnWorkflowBlock }).data;
      if (block) {
        if (!block.completed) return []; // still running — no card yet
        blocks.push(block);
      }
      continue;
    }
    if (entry.kind === 'status_hint') continue;
    break; // user message / fold boundary — stop
  }
  if (!blocks.length) return [];
  return collectTurnChangedFiles(blocks.reverse());
}

interface TurnChangedFilesCardProps {
  files: TurnChangedFile[];
  onOpenFile: (path: string) => void;
  onViewAll: () => void;
  viewAllLabel: string;
}

const MAX_SHOWN = 4;

export const TurnChangedFilesCard: React.FC<TurnChangedFilesCardProps> = ({
  files,
  onOpenFile,
  onViewAll,
  viewAllLabel,
}) => {
  if (!files.length) return null;
  const shown = files.slice(0, MAX_SHOWN);
  return (
    <div className="w-full mt-1.5 mb-3 rounded-xl border border-border/60 bg-black/[0.03] dark:bg-white/[0.05] overflow-hidden">
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-px bg-border/40">
        {shown.map((f) => (
          <button
            key={f.path}
            type="button"
            onClick={() => onOpenFile(f.path)}
            className="flex items-center gap-2.5 px-3 py-2.5 bg-black/[0.02] dark:bg-white/[0.04] hover:bg-primary/10 transition-colors text-left min-w-0"
            title={f.path}
          >
            <span className="shrink-0 w-8 h-8 rounded-lg bg-amber-400/15 flex items-center justify-center">
              <FileCode2 size={15} className="text-amber-500" />
            </span>
            <span className="min-w-0 flex-1">
              <span className="block text-[13px] font-medium text-textMain truncate">{f.name}</span>
              <span className="block text-[11px] text-textMuted truncate">{langLabel(f.name)}</span>
            </span>
            {f.kind === 'write' ? (
              <FilePlus2 size={13} className="shrink-0 text-textMuted/50" />
            ) : (
              <Pencil size={13} className="shrink-0 text-textMuted/50" />
            )}
          </button>
        ))}
      </div>
      <button
        type="button"
        onClick={onViewAll}
        className="w-full flex items-center justify-center gap-1.5 py-2 text-[12px] text-textMuted hover:text-textMain border-t border-border/40 bg-transparent transition-colors"
      >
        <ListTree size={13} />
        {viewAllLabel}
      </button>
    </div>
  );
};

export default TurnChangedFilesCard;
