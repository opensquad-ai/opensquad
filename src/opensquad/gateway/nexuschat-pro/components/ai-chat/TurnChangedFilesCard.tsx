/**
 * TurnChangedFilesCard — after a turn completes, shows the files the
 * workflow created/modified (max 4) below the assistant reply.
 * Click a file → open it in the files panel; footer → open the panel's
 * "changed" tab for the full list.
 */
import React from 'react';
import { useTranslation } from 'react-i18next';
import { FileCode2, FilePlus2, ListTree, Pencil } from 'lucide-react';
import { extractFileEditInfo } from './FileDiffBlock';

export type TurnChangedFile = {
  /** Raw path as it appeared in tool args (rel or absolute). */
  path: string;
  name: string;
  /** write = created/new file, edit = modified existing file. */
  kind: 'write' | 'edit';
  /** 行数变动，来自工具参数（write=新增行，edit=新/旧文本行数）。 */
  additions: number;
  deletions: number;
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
      files.push({
        path,
        name: info.fileName || path,
        kind: info.kind === 'write' ? 'write' : 'edit',
        additions: Math.max(0, Number(info.addedLines) || 0),
        deletions: Math.max(0, Number(info.removedLines) || 0),
      });
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
  /**
   * Code(solo) 的「最终产出」要能看出改了哪个文件、动了多少行，所以走列表：
   * 完整相对路径 + `+N -M`。Work(classic) 保持原来的文件卡片（名字 + 语言），
   * 那边的产物语义是「交付了什么」，不是仓库变更。
   */
  uiMode?: 'classic' | 'solo';
}

const MAX_SHOWN = 4;

export const TurnChangedFilesCard: React.FC<TurnChangedFilesCardProps> = ({
  files,
  onOpenFile,
  onViewAll,
  viewAllLabel,
  uiMode = 'classic',
}) => {
  const { t } = useTranslation();
  if (!files.length) return null;

  if (uiMode === 'solo') {
    return (
      <div
        className="w-full mt-1.5 mb-3 rounded-xl border border-border/60 overflow-hidden"
        data-testid="changed-files-list"
      >
        <div className="px-3 py-1.5 text-[11px] font-medium text-textMuted border-b border-border/40 bg-black/[0.02] dark:bg-white/[0.04]">
          {t('aiChat.changes')}
        </div>
        {files.map((f) => (
          <button
            key={f.path}
            type="button"
            onClick={() => onOpenFile(f.path)}
            className="w-full flex items-center gap-2 px-3 py-[7px] text-left border-b border-border/40 last:border-b-0 hover:bg-primary/10 transition-colors min-w-0"
            title={f.path}
          >
            <FileCode2 size={14} className="shrink-0 text-textMuted/60" />
            <span className="min-w-0 flex-1 truncate font-mono text-[12px] text-textMain">
              {f.path}
            </span>
            <span className="shrink-0 flex items-center gap-1 text-[11px] font-mono tabular-nums">
              {f.additions > 0 ? <span className="text-emerald-600/80">+{f.additions}</span> : null}
              {f.deletions > 0 ? <span className="text-rose-500/80">-{f.deletions}</span> : null}
              {f.additions === 0 && f.deletions === 0 ? (
                <span className="text-textMuted/50">+0</span>
              ) : null}
            </span>
          </button>
        ))}
      </div>
    );
  }

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
