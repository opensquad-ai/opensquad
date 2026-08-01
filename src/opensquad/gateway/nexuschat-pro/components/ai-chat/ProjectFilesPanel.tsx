/**
 * ProjectFilesPanel — Cursor/Trae-like workspace file explorer on the right of Agent Web.
 *
 * Full project tree loads once (metadata only, ≤10000). Folder expand is local;
 * file contents load only when the user clicks a file.
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Check,
  ChevronDown,
  ChevronRight,
  Copy,
  Eye,
  EyeOff,
  File as FileIcon,
  FileCode2,
  FileJson,
  FileText,
  FileType2,
  Folder,
  FolderPlus,
  Image as ImageIcon,
  Loader2,
  MoreHorizontal,
  Plus,
  RotateCcw,
  Search,
  Settings2,
  Terminal,
  X,
} from 'lucide-react';
import { adminAPI } from '../../services/api';
import { getLangForFile, highlightLine, HLJS_THEME_CSS } from '../../utils/codeHighlight';
import { AI_MARKDOWN_CLASS, renderFencedMarkdown } from '../../utils/fencedMarkdown';
import { UnifiedDiffView, type DiffLine } from './UnifiedDiffView';
import { fillDiffCollapseHidden, flattenDiffCollapses } from './fillDiffCollapseHidden';
import { SOFT_PRESENCE_MS, useSoftPresence } from '../../utils/useSoftPresence';
import {
  getWorkspaceFileCache,
  putWorkspaceFileCache,
} from './WorkspaceFileEditor';

export type ProjectFileOpenRequest = {
  /** Path relative to project root, or absolute under root */
  path: string;
  nonce: number;
};

type TreeEntry = {
  path: string;
  name: string;
  type: 'file' | 'dir';
  size?: number | null;
  skipped?: boolean;
  /** Present in Changes as deleted / withdrawn — not on disk */
  missing?: boolean;
};

type ChangedEntry = {
  name: string;
  path: string;
  type: 'file' | 'dir';
  status?: string;
  additions?: number;
  deletions?: number;
  oversized?: boolean;
  mtime?: number;
  size?: number;
  created?: boolean;
  missing?: boolean;
};

type ListTab = 'changed' | 'all';

type CtxTarget = {
  /** Relative path; empty string = browse root */
  path: string;
  type: 'file' | 'dir' | 'root';
  name: string;
};

type CtxMenuState = {
  x: number;
  y: number;
  target: CtxTarget;
};

type InlineCreate = { kind: 'file' | 'dir' };

type VisibleRow = TreeEntry & { depth: number };

/** Module-level tree cache — survives remounts / session switches with same root. */
const TREE_CACHE_MAX = 8;
type ModuleTreeCache = {
  entries: TreeEntry[];
  truncated: boolean;
  count: number;
  at: number;
};
const moduleTreeCache = new Map<string, ModuleTreeCache>();

type ModuleFileCacheEntry = {
  content: string;
  imageSrc: string | null;
  meta: { truncated?: boolean; path?: string; size?: number; kind?: 'text' | 'image' };
  at: number;
};
const MODULE_FILE_CACHE_MAX = 64;
const moduleFileContentCache = new Map<string, ModuleFileCacheEntry>();

function projectCacheKey(agentId: string, rootPath: string): string {
  return `${agentId}::${rootPath.replace(/\\/g, '/').replace(/\/+$/, '')}`;
}

function fileCacheKey(agentId: string, rootPath: string, relPath: string): string {
  return `${projectCacheKey(agentId, rootPath)}::${relPath.replace(/\\/g, '/')}`;
}

function putModuleTreeCache(
  agentId: string,
  rootPath: string,
  entries: TreeEntry[],
  truncated: boolean,
  count: number,
): void {
  const key = projectCacheKey(agentId, rootPath);
  moduleTreeCache.set(key, { entries, truncated, count, at: Date.now() });
  if (moduleTreeCache.size <= TREE_CACHE_MAX) return;
  const oldest = [...moduleTreeCache.entries()].sort((a, b) => a[1].at - b[1].at);
  moduleTreeCache.delete(oldest[0][0]);
}

function getModuleTreeCache(agentId: string, rootPath: string): ModuleTreeCache | null {
  return moduleTreeCache.get(projectCacheKey(agentId, rootPath)) || null;
}

function putModuleFileCache(
  agentId: string,
  rootPath: string,
  relPath: string,
  entry: ModuleFileCacheEntry,
): void {
  const key = fileCacheKey(agentId, rootPath, relPath);
  moduleFileContentCache.set(key, { ...entry, at: Date.now() });
  // Mirror into WorkspaceFileEditor cache — hover prefetch must warm the
  // center-pane editor or every open flashes「加载中」.
  putWorkspaceFileCache(agentId, rootPath, relPath, {
    content: entry.content,
    imageSrc: entry.imageSrc,
    meta: { truncated: entry.meta?.truncated, size: entry.meta?.size },
  });
  if (moduleFileContentCache.size <= MODULE_FILE_CACHE_MAX) return;
  const oldest = [...moduleFileContentCache.entries()].sort((a, b) => a[1].at - b[1].at);
  const drop = moduleFileContentCache.size - MODULE_FILE_CACHE_MAX;
  for (let i = 0; i < drop; i++) moduleFileContentCache.delete(oldest[i][0]);
}

function getModuleFileCache(
  agentId: string,
  rootPath: string,
  relPath: string,
): ModuleFileCacheEntry | null {
  const hit = moduleFileContentCache.get(fileCacheKey(agentId, rootPath, relPath));
  if (hit) return hit;
  const shared = getWorkspaceFileCache(agentId, rootPath, relPath);
  if (!shared) return null;
  return {
    content: shared.content,
    imageSrc: shared.imageSrc,
    meta: { truncated: shared.meta?.truncated, size: shared.meta?.size },
    at: shared.at,
  };
}

function buildChildrenMap(entries: TreeEntry[]): Map<string, TreeEntry[]> {
  const map = new Map<string, TreeEntry[]>();
  for (const e of entries) {
    const parent = parentRel(e.path);
    const list = map.get(parent);
    if (list) list.push(e);
    else map.set(parent, [e]);
  }
  for (const [, list] of map) {
    list.sort((a, b) => {
      if (a.type !== b.type) return a.type === 'dir' ? -1 : 1;
      return a.name.localeCompare(b.name, undefined, { sensitivity: 'base' });
    });
  }
  return map;
}

function collectVisibleRows(
  childrenMap: Map<string, TreeEntry[]>,
  expanded: Set<string>,
  searchQ: string,
): VisibleRow[] {
  const q = searchQ.trim().toLowerCase();
  if (q) {
    // Flat search hits with depth from path segments
    const out: VisibleRow[] = [];
    for (const list of childrenMap.values()) {
      for (const e of list) {
        if (
          e.name.toLowerCase().includes(q) ||
          e.path.toLowerCase().includes(q)
        ) {
          out.push({ ...e, depth: e.path.split('/').filter(Boolean).length - 1 });
        }
      }
    }
    out.sort((a, b) => a.path.localeCompare(b.path));
    return out;
  }

  const out: VisibleRow[] = [];
  const walk = (parent: string, depth: number) => {
    const kids = childrenMap.get(parent) || [];
    for (const e of kids) {
      out.push({ ...e, depth });
      if (e.type === 'dir' && !e.skipped && expanded.has(e.path)) {
        walk(e.path, depth + 1);
      }
    }
  };
  walk('', 0);
  return out;
}

function ancestorPaths(relPath: string): string[] {
  const parts = relPath.replace(/\\/g, '/').split('/').filter(Boolean);
  const out: string[] = [];
  let acc = '';
  for (let i = 0; i < parts.length - 1; i++) {
    acc = acc ? `${acc}/${parts[i]}` : parts[i];
    out.push(acc);
  }
  return out;
}

interface ProjectFilesPanelProps {
  isOpen: boolean;
  onClose: () => void;
  agentId: string;
  /** Absolute project root (agentCwd || defaultCwd) */
  rootPath: string;
  openRequest?: ProjectFileOpenRequest | null;
  width: number;
  onWidthChange: (w: number) => void;
  /** Force switch to changed tab + refresh (from Changes bar). */
  focusChangedNonce?: number;
  /**
   * Live snapshot from parent (tool/turn events). Applied in-place without
   * full-panel loading spinners so the chat/files UI does not flash-reload.
   */
  liveChanges?: {
    nonce: number;
    additions: number;
    deletions: number;
    count: number;
    files: ChangedEntry[];
  } | null;
  /** Notify parent when session change list refreshes. */
  onSessionChanges?: (summary: {
    additions: number;
    deletions: number;
    count: number;
  }) => void;
  /** Tree-only mode: no inline preview; open files via onOpenFile. */
  treeOnly?: boolean;
  /** Called when user opens a file (treeOnly or when provided). */
  onOpenFile?: (relPath: string) => void;
}

const WIDTH_MIN = 320;
const WIDTH_MAX = 720;

function joinRel(dir: string, name: string): string {
  const d = (dir || '').replace(/\\/g, '/').replace(/\/+$/, '');
  if (!d) return name;
  return `${d}/${name}`;
}

function parentRel(path: string): string {
  const p = path.replace(/\\/g, '/').replace(/\/+$/, '');
  const i = p.lastIndexOf('/');
  return i <= 0 ? '' : p.slice(0, i);
}

function basename(path: string): string {
  const p = path.replace(/\\/g, '/');
  const i = p.lastIndexOf('/');
  return i < 0 ? p : p.slice(i + 1);
}

function fileExt(fileName: string): string {
  const base = fileName.split(/[/\\]/).pop() || fileName;
  if (!base.includes('.') || base.startsWith('.')) {
    // dotfiles like .gitignore — use full name without leading dot as "ext"
    if (base.startsWith('.') && base.length > 1) return base.slice(1).toLowerCase();
    return '';
  }
  return (base.split('.').pop() || '').toLowerCase();
}

const IMAGE_EXTS = new Set(['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'svg', 'ico']);

function isImageFile(fileName: string): boolean {
  return IMAGE_EXTS.has(fileExt(fileName));
}

function isMarkdownFile(fileName: string): boolean {
  const ext = fileExt(fileName);
  return ext === 'md' || ext === 'mdx' || ext === 'markdown';
}

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

function toProjectRelative(rootPath: string, filePath: string): string {
  const root = rootPath.replace(/\\/g, '/').replace(/\/+$/, '');
  let p = filePath.replace(/\\/g, '/');
  if (/^[A-Za-z]:\//.test(p)) {
    // Absolute Windows path
    const rootNorm = root.replace(/^([A-Za-z]):/, (_, d) => d.toUpperCase() + ':');
    const pNorm = p.replace(/^([A-Za-z]):/, (_, d) => d.toUpperCase() + ':');
    if (pNorm.toLowerCase().startsWith(rootNorm.toLowerCase() + '/')) {
      return pNorm.slice(rootNorm.length + 1);
    }
    if (pNorm.toLowerCase() === rootNorm.toLowerCase()) return '';
  } else if (p.startsWith('/')) {
    if (p.startsWith(root + '/')) return p.slice(root.length + 1);
    if (p === root) return '';
  }
  // Already relative or unmatched — strip leading ./
  return p.replace(/^\.\//, '');
}

/** Per-extension icon + muted color for the file list (fig-2 soft grey hierarchy). */
type ExtStyle = { Icon: React.ComponentType<{ size?: number; className?: string; strokeWidth?: number }>; color: string; label?: string };

const EXT_STYLE: Record<string, ExtStyle> = {
  // Languages — soft / low-chroma
  py: { Icon: FileCode2, color: 'text-yellow-700/45 dark:text-yellow-400/40', label: 'PY' },
  pyw: { Icon: FileCode2, color: 'text-yellow-700/45 dark:text-yellow-400/40', label: 'PY' },
  js: { Icon: FileCode2, color: 'text-amber-700/45 dark:text-amber-400/40', label: 'JS' },
  mjs: { Icon: FileCode2, color: 'text-amber-700/45 dark:text-amber-400/40', label: 'JS' },
  cjs: { Icon: FileCode2, color: 'text-amber-700/45 dark:text-amber-400/40', label: 'JS' },
  jsx: { Icon: FileCode2, color: 'text-sky-700/45 dark:text-sky-400/40', label: 'JSX' },
  ts: { Icon: FileCode2, color: 'text-blue-700/45 dark:text-blue-400/40', label: 'TS' },
  tsx: { Icon: FileCode2, color: 'text-blue-700/40 dark:text-blue-400/40', label: 'TSX' },
  vue: { Icon: FileCode2, color: 'text-emerald-700/45 dark:text-emerald-400/40', label: 'VUE' },
  svelte: { Icon: FileCode2, color: 'text-orange-700/40 dark:text-orange-400/35', label: 'SV' },
  go: { Icon: FileCode2, color: 'text-cyan-700/45 dark:text-cyan-400/40', label: 'GO' },
  rs: { Icon: FileCode2, color: 'text-orange-800/40 dark:text-orange-400/35', label: 'RS' },
  java: { Icon: FileCode2, color: 'text-red-700/40 dark:text-red-400/35', label: 'JV' },
  kt: { Icon: FileCode2, color: 'text-purple-700/40 dark:text-purple-400/35', label: 'KT' },
  c: { Icon: FileCode2, color: 'text-blue-800/40 dark:text-blue-400/40', label: 'C' },
  h: { Icon: FileCode2, color: 'text-blue-800/40 dark:text-blue-400/40', label: 'H' },
  cpp: { Icon: FileCode2, color: 'text-blue-700/40 dark:text-blue-400/40', label: 'C++' },
  hpp: { Icon: FileCode2, color: 'text-blue-700/40 dark:text-blue-400/40', label: 'H++' },
  cs: { Icon: FileCode2, color: 'text-violet-700/40 dark:text-violet-400/35', label: 'CS' },
  rb: { Icon: FileCode2, color: 'text-red-700/40 dark:text-red-400/35', label: 'RB' },
  php: { Icon: FileCode2, color: 'text-indigo-700/40 dark:text-indigo-400/35', label: 'PHP' },
  swift: { Icon: FileCode2, color: 'text-orange-700/40 dark:text-orange-400/35', label: 'SW' },
  // Markup / style
  html: { Icon: FileType2, color: 'text-orange-700/40 dark:text-orange-400/35', label: 'HTML' },
  htm: { Icon: FileType2, color: 'text-orange-700/40 dark:text-orange-400/35', label: 'HTML' },
  css: { Icon: FileType2, color: 'text-sky-700/45 dark:text-sky-400/40', label: 'CSS' },
  scss: { Icon: FileType2, color: 'text-pink-700/40 dark:text-pink-400/35', label: 'SCSS' },
  less: { Icon: FileType2, color: 'text-indigo-700/40 dark:text-indigo-400/35', label: 'LESS' },
  xml: { Icon: FileType2, color: 'text-orange-700/40 dark:text-orange-400/35', label: 'XML' },
  svg: { Icon: ImageIcon, color: 'text-pink-700/40 dark:text-pink-400/35', label: 'SVG' },
  // Data / config
  json: { Icon: FileJson, color: 'text-amber-700/45 dark:text-amber-400/40', label: 'JSON' },
  jsonc: { Icon: FileJson, color: 'text-amber-700/45 dark:text-amber-400/40', label: 'JSON' },
  yaml: { Icon: Settings2, color: 'text-rose-700/40 dark:text-rose-400/35', label: 'YML' },
  yml: { Icon: Settings2, color: 'text-rose-700/40 dark:text-rose-400/35', label: 'YML' },
  toml: { Icon: Settings2, color: 'text-neutral-400 dark:text-neutral-500', label: 'TOML' },
  ini: { Icon: Settings2, color: 'text-neutral-400 dark:text-neutral-500', label: 'INI' },
  cfg: { Icon: Settings2, color: 'text-neutral-400 dark:text-neutral-500', label: 'CFG' },
  conf: { Icon: Settings2, color: 'text-neutral-400 dark:text-neutral-500', label: 'CFG' },
  env: { Icon: Settings2, color: 'text-lime-700/40 dark:text-lime-400/35', label: 'ENV' },
  // Docs
  md: { Icon: FileText, color: 'text-sky-700/45 dark:text-sky-400/40', label: 'MD' },
  mdx: { Icon: FileText, color: 'text-sky-700/45 dark:text-sky-400/40', label: 'MDX' },
  markdown: { Icon: FileText, color: 'text-sky-700/45 dark:text-sky-400/40', label: 'MD' },
  txt: { Icon: FileText, color: 'text-neutral-400 dark:text-neutral-500', label: 'TXT' },
  csv: { Icon: FileText, color: 'text-emerald-700/40 dark:text-emerald-400/35', label: 'CSV' },
  log: { Icon: FileText, color: 'text-neutral-400 dark:text-neutral-500', label: 'LOG' },
  // Shell
  sh: { Icon: Terminal, color: 'text-green-700/40 dark:text-green-400/35', label: 'SH' },
  bash: { Icon: Terminal, color: 'text-green-700/40 dark:text-green-400/35', label: 'SH' },
  zsh: { Icon: Terminal, color: 'text-green-700/40 dark:text-green-400/35', label: 'SH' },
  ps1: { Icon: Terminal, color: 'text-blue-700/40 dark:text-blue-400/35', label: 'PS' },
  bat: { Icon: Terminal, color: 'text-neutral-400 dark:text-neutral-500', label: 'BAT' },
  cmd: { Icon: Terminal, color: 'text-neutral-400 dark:text-neutral-500', label: 'CMD' },
  // Images
  png: { Icon: ImageIcon, color: 'text-fuchsia-700/35 dark:text-fuchsia-400/30', label: 'PNG' },
  jpg: { Icon: ImageIcon, color: 'text-fuchsia-700/35 dark:text-fuchsia-400/30', label: 'JPG' },
  jpeg: { Icon: ImageIcon, color: 'text-fuchsia-700/35 dark:text-fuchsia-400/30', label: 'JPG' },
  gif: { Icon: ImageIcon, color: 'text-fuchsia-700/35 dark:text-fuchsia-400/30', label: 'GIF' },
  webp: { Icon: ImageIcon, color: 'text-fuchsia-700/35 dark:text-fuchsia-400/30', label: 'WEBP' },
  bmp: { Icon: ImageIcon, color: 'text-fuchsia-700/35 dark:text-fuchsia-400/30', label: 'BMP' },
  ico: { Icon: ImageIcon, color: 'text-fuchsia-700/35 dark:text-fuchsia-400/30', label: 'ICO' },
  // Dotfiles
  gitignore: { Icon: Settings2, color: 'text-neutral-400 dark:text-neutral-500', label: 'GIT' },
  dockerfile: { Icon: Settings2, color: 'text-blue-700/40 dark:text-blue-400/35', label: 'DKR' },
  editorconfig: { Icon: Settings2, color: 'text-neutral-400 dark:text-neutral-500', label: 'EDC' },
};

const FileTypeIcon: React.FC<{ name: string; type: 'file' | 'dir' }> = ({ name, type }) => {
  if (type === 'dir') {
    return <Folder size={14} className="text-neutral-400 dark:text-neutral-500 shrink-0" strokeWidth={1.5} />;
  }
  const ext = fileExt(name);
  if (ext === 'html' || ext === 'htm') {
    return (
      <span
        className="shrink-0 inline-flex items-center justify-center min-w-[26px] h-[14px] px-0.5 rounded-[3px] text-[8px] font-semibold leading-none tracking-tight text-orange-700/45 dark:text-orange-400/40"
        style={{ backgroundColor: 'color-mix(in srgb, #c2410c 8%, transparent)' }}
        title={ext}
      >
        HTML
      </span>
    );
  }
  if (ext === 'md' || ext === 'mdx' || ext === 'markdown') {
    return <FileText size={14} className="text-sky-700/45 dark:text-sky-400/40 shrink-0" strokeWidth={1.5} />;
  }
  const style = EXT_STYLE[ext] || EXT_STYLE[name.toLowerCase()];
  if (!style) {
    return <FileIcon size={14} className="text-neutral-400 dark:text-neutral-500 shrink-0" strokeWidth={1.5} />;
  }
  const { Icon, color, label } = style;
  if (label && label.length <= 4) {
    return (
      <span
        className={`shrink-0 inline-flex items-center justify-center min-w-[22px] h-[13px] px-0.5 rounded-[2px] text-[8px] font-semibold leading-none tracking-tight ${color}`}
        style={{ backgroundColor: 'color-mix(in srgb, currentColor 8%, transparent)' }}
        title={ext || name}
      >
        {label}
      </span>
    );
  }
  return <Icon size={14} className={`${color} shrink-0`} strokeWidth={1.5} />;
};

const CodePreview: React.FC<{ fileName: string; content: string }> = ({ fileName, content }) => {
  const lang = useMemo(() => getLangForFile(fileName), [fileName]);
  const lines = useMemo(() => content.split('\n'), [content]);
  return (
    <div className="flex-1 min-h-0 overflow-auto bg-[#0d1117] font-mono text-[11px] leading-5">
      <style>{HLJS_THEME_CSS}</style>
      <div className="min-w-full inline-block">
        {lines.map((line, i) => (
          <div key={i} className="flex items-start hover:bg-primary/10">
            <span className="select-none w-10 shrink-0 text-right pr-2 text-gray-600 tabular-nums text-[10px] border-r border-gray-800">
              {i + 1}
            </span>
            <span
              className="flex-1 min-w-0 whitespace-pre-wrap break-words pl-2 text-gray-200"
              dangerouslySetInnerHTML={{ __html: highlightLine(line, lang) }}
            />
          </div>
        ))}
      </div>
    </div>
  );
};

const MarkdownPreview: React.FC<{ content: string }> = ({ content }) => {
  const html = useMemo(() => renderFencedMarkdown(content || ''), [content]);
  return (
    <div className="flex-1 min-h-0 overflow-auto bg-[#0d1117]">
      <style>{HLJS_THEME_CSS}</style>
      <div
        className={`${AI_MARKDOWN_CLASS} prose prose-sm prose-invert max-w-none break-words px-3.5 py-3 text-[12.5px] leading-relaxed`}
        dangerouslySetInnerHTML={{ __html: html }}
      />
    </div>
  );
};

const ImagePreview: React.FC<{ src: string; fileName: string; size?: number }> = ({ src, fileName, size }) => (
  <div className="flex-1 min-h-0 overflow-auto bg-[#0d1117] flex flex-col items-center justify-center p-3 gap-2">
    <img
      src={src}
      alt={fileName}
      className="max-w-full max-h-full object-contain rounded-sm shadow-lg"
      draggable={false}
    />
    {typeof size === 'number' ? (
      <div className="text-[10px] text-gray-500 font-mono">{formatBytes(size)}</div>
    ) : null}
  </div>
);

export const ProjectFilesPanel: React.FC<ProjectFilesPanelProps> = ({
  isOpen,
  onClose,
  agentId,
  rootPath,
  openRequest,
  width,
  onWidthChange,
  focusChangedNonce,
  liveChanges,
  onSessionChanges,
  treeOnly = false,
  onOpenFile,
}) => {
  const [browsePath, setBrowsePath] = useState('');
  const [treeEntries, setTreeEntries] = useState<TreeEntry[]>([]);
  const [treeTruncated, setTreeTruncated] = useState(false);
  const [treeCount, setTreeCount] = useState(0);
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());
  const [listLoading, setListLoading] = useState(false);
  const [listError, setListError] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [showSearch, setShowSearch] = useState(false);

  const [tab, setTab] = useState<ListTab>('all');
  const [changedEntries, setChangedEntries] = useState<ChangedEntry[]>([]);
  const [changedLoading, setChangedLoading] = useState(false);
  const [changedError, setChangedError] = useState<string | null>(null);
  /** Accordion: which changed files have inline diff expanded */
  const [expandedChanged, setExpandedChanged] = useState<Set<string>>(() => new Set());
  const [inlineDiffByPath, setInlineDiffByPath] = useState<
    Record<string, { lines: DiffLine[]; additions: number; deletions: number; oversized?: boolean }>
  >({});
  const [inlineDiffLoading, setInlineDiffLoading] = useState<string | null>(null);
  const [revertingPath, setRevertingPath] = useState<string | null>(null);
  const [keepingPath, setKeepingPath] = useState<string | null>(null);
  const onSessionChangesRef = useRef(onSessionChanges);
  useEffect(() => {
    onSessionChangesRef.current = onSessionChanges;
  }, [onSessionChanges]);
  const inlineDiffByPathRef = useRef(inlineDiffByPath);
  useEffect(() => {
    inlineDiffByPathRef.current = inlineDiffByPath;
  }, [inlineDiffByPath]);
  /** Uncollapsed diffs for All Files preview — click hits this first (no loading flash). */
  const fullDiffByPathRef = useRef<
    Record<string, { lines: DiffLine[]; additions: number; deletions: number; oversized?: boolean }>
  >({});
  const expandedChangedRef = useRef(expandedChanged);
  useEffect(() => {
    expandedChangedRef.current = expandedChanged;
  }, [expandedChanged]);
  const treeEntriesRef = useRef(treeEntries);
  useEffect(() => {
    treeEntriesRef.current = treeEntries;
  }, [treeEntries]);
  const changedEntriesRef = useRef(changedEntries);
  useEffect(() => {
    changedEntriesRef.current = changedEntries;
  }, [changedEntries]);
  const lastLiveNonceRef = useRef(0);
  const treeLoadedOnceRef = useRef(false);
  const prefetchGenRef = useRef(0);
  const fileContentCacheRef = useRef<
    Map<
      string,
      {
        content: string;
        imageSrc: string | null;
        meta: { truncated?: boolean; path?: string; size?: number; kind?: 'text' | 'image' };
        at: number;
      }
    >
  >(new Map());
  const filePrefetchInflightRef = useRef<Set<string>>(new Set());

  const [activeFile, setActiveFile] = useState<string | null>(null);
  const activeFileRef = useRef<string | null>(null);
  useEffect(() => {
    activeFileRef.current = activeFile;
  }, [activeFile]);
  const [fileContent, setFileContent] = useState<string>('');
  const [imageSrc, setImageSrc] = useState<string | null>(null);
  const [fileMeta, setFileMeta] = useState<{ truncated?: boolean; path?: string; size?: number; kind?: 'text' | 'image' } | null>(null);
  const [fileLoading, setFileLoading] = useState(false);
  const [fileError, setFileError] = useState<string | null>(null);
  /** For .md: rendered preview vs raw source */
  const [mdRaw, setMdRaw] = useState(false);
  const [showPreview, setShowPreview] = useState(true);
  const [diffLines, setDiffLines] = useState<DiffLine[] | null>(null);
  const [diffMeta, setDiffMeta] = useState<{
    additions: number;
    deletions: number;
    oversized?: boolean;
    status?: string;
  } | null>(null);

  const [ctxMenu, setCtxMenu] = useState<CtxMenuState | null>(null);
  const [newMenuOpen, setNewMenuOpen] = useState(false);
  const [inlineCreate, setInlineCreate] = useState<InlineCreate | null>(null);
  /** Directory (rel) where inline create commits; defaults to browsePath */
  const [createUnder, setCreateUnder] = useState<string>('');
  const [createName, setCreateName] = useState('');
  const [renamingPath, setRenamingPath] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState('');
  const [actionBusy, setActionBusy] = useState(false);

  const dragRef = useRef<{ startX: number; startWidth: number } | null>(null);
  const lastOpenNonce = useRef<number>(-1);
  const createInputRef = useRef<HTMLInputElement | null>(null);
  const renameInputRef = useRef<HTMLInputElement | null>(null);
  const skipCreateBlurRef = useRef(false);
  const skipRenameBlurRef = useRef(false);
  const newMenuRef = useRef<HTMLDivElement | null>(null);
  const ctxMenuRef = useRef<HTMLDivElement | null>(null);
  const panelRef = useRef<HTMLDivElement | null>(null);

  const projectLabel = useMemo(() => {
    if (!rootPath) return '默认工作区';
    const parts = rootPath.replace(/\\/g, '/').split('/').filter(Boolean);
    return parts[parts.length - 1] || rootPath;
  }, [rootPath]);

  const childrenMap = useMemo(() => {
    // Inject ghost rows for deleted/withdrawn files so 所有文件 still shows them (red)
    const byPath = new Map<string, TreeEntry>();
    for (const e of treeEntries) {
      byPath.set((e.path || '').replace(/\\/g, '/'), { ...e, path: (e.path || '').replace(/\\/g, '/') });
    }
    for (const ch of changedEntries) {
      const p = (ch.path || '').replace(/\\/g, '/');
      if (!p) continue;
      const gone = ch.missing || ch.status === 'D';
      if (!gone) continue;
      if (byPath.has(p)) {
        byPath.set(p, { ...byPath.get(p)!, missing: true });
      } else {
        byPath.set(p, {
          path: p,
          name: ch.name || p.split('/').pop() || p,
          type: 'file',
          missing: true,
          size: 0,
        });
      }
    }
    return buildChildrenMap([...byPath.values()]);
  }, [treeEntries, changedEntries]);

  const visibleRows = useMemo(
    () => collectVisibleRows(childrenMap, expanded, search),
    [childrenMap, expanded, search],
  );

  const filteredChanged = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return changedEntries;
    return changedEntries.filter(
      (e) => e.name.toLowerCase().includes(q) || e.path.toLowerCase().includes(q),
    );
  }, [changedEntries, search]);

  /** path → session change stats (for +/- badges on 所有文件 tree) */
  const changedByPath = useMemo(() => {
    const m = new Map<string, ChangedEntry>();
    for (const e of changedEntries) {
      const p = (e.path || '').replace(/\\/g, '/');
      if (p) m.set(p, e);
    }
    return m;
  }, [changedEntries]);

  /** dirs that contain at least one changed file (subtle name tint) */
  const dirsWithChanges = useMemo(() => {
    const s = new Set<string>();
    for (const p of changedByPath.keys()) {
      const parts = p.split('/');
      for (let i = 1; i < parts.length; i++) {
        s.add(parts.slice(0, i).join('/'));
      }
    }
    return s;
  }, [changedByPath]);
  const closeMenus = useCallback(() => {
    setCtxMenu(null);
    setNewMenuOpen(false);
  }, []);

  const enrichDiffLines = useCallback(
    async (relPath: string, lines: DiffLine[]): Promise<DiffLine[]> => {
      const needsFill = lines.some(
        (l) => l.type === 'collapse' && !(Array.isArray(l.hidden) && l.hidden.length > 0),
      );
      if (!needsFill) return lines;
      let content = fileContentCacheRef.current.get(relPath)?.content;
      if (content == null && agentId && rootPath) {
        try {
          const resp = await adminAPI.readProjectFile(agentId, relPath, rootPath);
          content = resp.content ?? '';
          const entry = {
            content,
            imageSrc: null as string | null,
            meta: {
              truncated: resp.truncated,
              path: resp.path || relPath,
              size: resp.size,
              kind: 'text' as const,
            },
            at: Date.now(),
          };
          fileContentCacheRef.current.set(relPath, entry);
          if (resp.path && resp.path !== relPath) {
            fileContentCacheRef.current.set(resp.path, entry);
          }
        } catch {
          return lines;
        }
      }
      return fillDiffCollapseHidden(lines, content) as DiffLine[];
    },
    [agentId, rootPath],
  );

  const prefetchDiffs = useCallback(
    async (paths: string[], opts?: { showLoadingFor?: string[] }) => {
      if (!agentId || !rootPath || paths.length === 0) return;
      const unique = [...new Set(paths.filter(Boolean))];
      if (unique.length === 0) return;
      const loadingSet = new Set(opts?.showLoadingFor || []);
      for (const p of loadingSet) setInlineDiffLoading(p);
      const gen = ++prefetchGenRef.current;
      try {
        const resp = await adminAPI.getSessionDiffsBatch(agentId, unique, rootPath);
        if (gen !== prefetchGenRef.current) return;
        const batch = resp.files || {};
        const nextEntries: Record<
          string,
          { lines: DiffLine[]; additions: number; deletions: number; oversized?: boolean }
        > = {};
        for (const [p, d] of Object.entries(batch)) {
          const lines = await enrichDiffLines(p, (d.lines || []) as DiffLine[]);
          if (gen !== prefetchGenRef.current) return;
          nextEntries[p] = {
            lines,
            additions: d.additions || 0,
            deletions: d.deletions || 0,
            oversized: d.oversized,
          };
          // Pre-build All Files full preview so click paints instantly.
          fullDiffByPathRef.current[p] = {
            lines: flattenDiffCollapses(lines) as DiffLine[],
            additions: d.additions || 0,
            deletions: d.deletions || 0,
            oversized: d.oversized,
          };
        }
        setInlineDiffByPath((prev) => ({ ...prev, ...nextEntries }));
      } catch {
        // Fallback: fetch individually for paths that still need UI (expanded)
        for (const p of loadingSet) {
          try {
            const d = await adminAPI.getSessionDiff(agentId, p, rootPath);
            if (gen !== prefetchGenRef.current) return;
            const lines = await enrichDiffLines(p, (d.lines || []) as DiffLine[]);
            const entry = {
              lines,
              additions: d.additions || 0,
              deletions: d.deletions || 0,
              oversized: d.oversized,
            };
            fullDiffByPathRef.current[p] = {
              ...entry,
              lines: flattenDiffCollapses(lines) as DiffLine[],
            };
            setInlineDiffByPath((prev) => ({
              ...prev,
              [p]: entry,
            }));
          } catch {
            /* ignore */
          }
        }
      } finally {
        if (loadingSet.size) {
          setInlineDiffLoading((cur) => (cur && loadingSet.has(cur) ? null : cur));
        }
      }
    },
    [agentId, rootPath, enrichDiffLines],
  );

  const prefetchFileContent = useCallback(
    async (relPath: string, opts?: { force?: boolean }) => {
      if (!agentId || !rootPath || !relPath) return;
      if (!opts?.force) {
        if (fileContentCacheRef.current.has(relPath)) return;
        const mod = getModuleFileCache(agentId, rootPath, relPath);
        if (mod) {
          fileContentCacheRef.current.set(relPath, mod);
          return;
        }
      }
      if (filePrefetchInflightRef.current.has(relPath)) return;
      // Skip images for prefetch budget (open still loads them on demand)
      if (isImageFile(relPath)) return;
      filePrefetchInflightRef.current.add(relPath);
      try {
        const resp = await adminAPI.readProjectFile(agentId, relPath, rootPath);
        const kind =
          resp.kind === 'image' || (resp.content_base64 && resp.mime?.startsWith('image/'))
            ? 'image'
            : 'text';
        const imageSrc =
          kind === 'image' && resp.content_base64 && resp.mime
            ? `data:${resp.mime};base64,${resp.content_base64}`
            : null;
        const entry = {
          content: kind === 'image' ? '' : (resp.content ?? ''),
          imageSrc,
          meta: {
            truncated: resp.truncated,
            path: resp.path || relPath,
            size: resp.size,
            kind: kind as 'text' | 'image',
          },
          at: Date.now(),
        };
        fileContentCacheRef.current.set(relPath, entry);
        putModuleFileCache(agentId, rootPath, relPath, entry);
        if (resp.path && resp.path !== relPath) {
          fileContentCacheRef.current.set(resp.path, entry);
          putModuleFileCache(agentId, rootPath, resp.path, entry);
        }
        if (fileContentCacheRef.current.size > 80) {
          const oldest = [...fileContentCacheRef.current.entries()].sort((a, b) => a[1].at - b[1].at);
          for (let i = 0; i < oldest.length - 60; i++) {
            fileContentCacheRef.current.delete(oldest[i][0]);
          }
        }
      } catch {
        /* ignore prefetch errors */
      } finally {
        filePrefetchInflightRef.current.delete(relPath);
      }
    },
    [agentId, rootPath],
  );

  /** Merge changed-file list in place; invalidate + refetch diffs when stats/mtime change. */
  const applyChangedFiles = useCallback(
    (
      files: ChangedEntry[],
      summary: { additions: number; deletions: number; count: number },
      opts?: { notifyParent?: boolean; forceDiffRefresh?: boolean },
    ) => {
      const normalized = files.map((f) => ({
        ...f,
        path: (f.path || '').replace(/\\/g, '/'),
      }));
      const prev = changedEntriesRef.current;
      const prevByPath = new Map(
        prev.map((e) => [(e.path || '').replace(/\\/g, '/'), e] as const),
      );
      const nextPaths = new Set(normalized.map((f) => f.path));
      const removedPaths: string[] = [];
      for (const p of prevByPath.keys()) {
        if (!nextPaths.has(p)) removedPaths.push(p);
      }
      const staleDiffPaths: string[] = [];
      for (const f of normalized) {
        const old = prevByPath.get(f.path);
        if (
          opts?.forceDiffRefresh ||
          !old ||
          old.additions !== f.additions ||
          old.deletions !== f.deletions ||
          old.status !== f.status ||
          old.oversized !== f.oversized ||
          old.mtime !== f.mtime ||
          old.size !== f.size
        ) {
          staleDiffPaths.push(f.path);
        }
      }
      // Keep prior diffs on screen until prefetch replaces them — dropping cache
      // early causes accordion to flash「无 diff」then reappear.
      setInlineDiffByPath((cache) => {
        const next: typeof cache = {};
        for (const [key, val] of Object.entries(cache)) {
          const k = key.replace(/\\/g, '/');
          if (!nextPaths.has(k)) continue;
          next[k] = val;
        }
        return next;
      });
      // Disk content may have changed for stale + removed (withdraw/revert) paths
      for (const p of [...staleDiffPaths, ...removedPaths]) {
        fileContentCacheRef.current.delete(p);
        delete fullDiffByPathRef.current[p];
      }
      if (opts?.forceDiffRefresh) {
        for (const p of nextPaths) {
          fileContentCacheRef.current.delete(p);
          delete fullDiffByPathRef.current[p];
        }
      }
      setChangedEntries(normalized);
      setChangedError(null);
      if (opts?.notifyParent !== false) {
        onSessionChangesRef.current?.(summary);
      }
      const toPrefetch = opts?.forceDiffRefresh
        ? normalized.map((f) => f.path)
        : staleDiffPaths;
      if (toPrefetch.length > 0) {
        void prefetchDiffs(toPrefetch);
      }
      for (const f of normalized.slice(0, 40)) {
        if (f.type === 'file' && !f.oversized && staleDiffPaths.includes(f.path)) {
          void prefetchFileContent(f.path, { force: true });
        }
      }
      return { staleDiffPaths, removedPaths, nextPaths };
    },
    [prefetchDiffs, prefetchFileContent],
  );

  const loadTree = useCallback(async (opts?: { silent?: boolean }) => {
    if (!agentId || !rootPath) return;
    const cached = getModuleTreeCache(agentId, rootPath);
    const firstPaint = !treeLoadedOnceRef.current;
    // Instant paint from module cache (session switch / remount with same root).
    if (cached && cached.entries.length > 0 && firstPaint) {
      setTreeEntries(cached.entries);
      setTreeTruncated(!!cached.truncated);
      setTreeCount(cached.count);
      treeLoadedOnceRef.current = true;
      const topDirs = cached.entries
        .filter((e) => e.type === 'dir' && !e.path.includes('/'))
        .map((e) => e.path);
      setExpanded(new Set(topDirs.slice(0, 12)));
      const warmDirs = new Set(topDirs.slice(0, 12));
      const warmFiles = cached.entries
        .filter((e) => {
          if (e.type !== 'file') return false;
          if (!e.path.includes('/')) return true;
          const parent = e.path.slice(0, e.path.lastIndexOf('/'));
          return warmDirs.has(parent);
        })
        .slice(0, 50);
      for (const f of warmFiles) {
        void prefetchFileContent(f.path);
      }
    }
    const hasTree =
      treeEntriesRef.current.length > 0 || treeLoadedOnceRef.current || !!(cached && cached.entries.length);
    // Soft-refresh by default once a tree is on screen (avoid full-panel flash)
    const silent = hasTree && opts?.silent !== false;
    if (!silent) setListLoading(true);
    setListError(null);
    try {
      const resp = await adminAPI.listProjectTree(agentId, rootPath, 10000);
      const entries = (resp.entries || []).map((e) => ({
        path: (e.path || '').replace(/\\/g, '/'),
        name: e.name,
        type: e.type,
        size: e.size,
        skipped: e.skipped,
      }));
      setTreeEntries(entries);
      setTreeTruncated(!!resp.truncated);
      setTreeCount(resp.count ?? entries.length);
      putModuleTreeCache(agentId, rootPath, entries, !!resp.truncated, resp.count ?? entries.length);
      // Preserve fold state on soft refresh; only seed defaults on first load
      if (!treeLoadedOnceRef.current) {
        const topDirs = entries
          .filter((e) => e.type === 'dir' && !e.path.includes('/'))
          .map((e) => e.path);
        setExpanded(new Set(topDirs.slice(0, 12)));
        // Warm root + first-level files so All Files clicks paint from cache.
        const warmDirs = new Set(topDirs.slice(0, 12));
        const warmFiles = entries
          .filter((e) => {
            if (e.type !== 'file') return false;
            if (!e.path.includes('/')) return true;
            const parent = e.path.slice(0, e.path.lastIndexOf('/'));
            return warmDirs.has(parent);
          })
          .slice(0, 50);
        for (const f of warmFiles) {
          void prefetchFileContent(f.path);
        }
      }
      treeLoadedOnceRef.current = true;
    } catch (err: any) {
      if (!silent) {
        setTreeEntries([]);
        setTreeTruncated(false);
        setTreeCount(0);
      }
      setListError(err?.message || '无法加载项目文件树');
    } finally {
      if (!silent) setListLoading(false);
    }
  }, [agentId, rootPath, prefetchFileContent]);

  const toggleExpand = useCallback((dirPath: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(dirPath)) next.delete(dirPath);
      else {
        next.add(dirPath);
        // Prefetch children when opening a folder
        const kids = treeEntriesRef.current.filter(
          (e) =>
            e.type === 'file' &&
            e.path.startsWith(dirPath ? `${dirPath}/` : '') &&
            !e.path.slice(dirPath.length + (dirPath ? 1 : 0)).includes('/'),
        );
        for (const f of kids.slice(0, 40)) {
          void prefetchFileContent(f.path);
        }
      }
      return next;
    });
  }, [prefetchFileContent]);

  const expandToPath = useCallback((relPath: string) => {
    const ancestors = ancestorPaths(relPath);
    if (ancestors.length === 0) return;
    setExpanded((prev) => {
      const next = new Set(prev);
      for (const a of ancestors) next.add(a);
      return next;
    });
  }, []);

  const loadChanged = useCallback(async (opts?: { silent?: boolean }) => {
    if (!agentId || !rootPath) return;
    const hasList = changedEntriesRef.current.length > 0;
    // Soft-refresh once we already show rows; first paint may still use full loading
    const silent = hasList ? opts?.silent !== false : !!opts?.silent;
    if (!silent) {
      setChangedLoading(true);
      setChangedError(null);
    }
    try {
      const resp = await adminAPI.listSessionChanges(agentId, rootPath);
      const files = (resp.files || resp.entries || []).map((e) => ({
        name: e.name,
        path: (e.path || '').replace(/\\/g, '/'),
        type: e.type,
        status: e.status,
        additions: e.additions,
        deletions: e.deletions,
        oversized: e.oversized,
        mtime: e.mtime,
        size: e.size,
        created: e.created,
        missing: !!(e as { missing?: boolean }).missing || e.status === 'D',
      }));
      applyChangedFiles(
        files,
        {
          additions: resp.additions || 0,
          deletions: resp.deletions || 0,
          count: resp.count ?? files.length,
        },
        { notifyParent: true },
      );
    } catch (err: any) {
      if (!silent) {
        setChangedEntries([]);
        setChangedError(err?.message || '无法加载变动文件');
        onSessionChangesRef.current?.({ additions: 0, deletions: 0, count: 0 });
      } else {
        setChangedError(err?.message || '无法加载变动文件');
      }
    } finally {
      if (!silent) setChangedLoading(false);
    }
  }, [agentId, rootPath, applyChangedFiles]);

  const refreshCurrent = useCallback(() => {
    if (tab === 'changed') {
      void loadChanged({ silent: true });
    } else {
      void loadTree({ silent: true });
      void loadChanged({ silent: true });
    }
  }, [tab, loadChanged, loadTree]);

  // Silent keep-alive — tree/changed stay fresh without a manual refresh control.
  // Separate intervals: the project tree payload can be up to 10k entries (heavy),
  // while per-turn file changes are already pushed by AIChatPage's session-changes
  // path — these are only slow fallbacks (was 5s for both → tree 30s / changed 10s).
  // Ticks skip entirely while the document is hidden.
  useEffect(() => {
    if (!isOpen || !rootPath || !agentId) return;
    const treeTick = () => {
      if (document.visibilityState !== 'visible') return;
      void loadTree({ silent: true });
    };
    const changedTick = () => {
      if (document.visibilityState !== 'visible') return;
      void loadChanged({ silent: true });
    };
    const onFocus = () => {
      if (tab === 'changed') void loadChanged({ silent: true });
      else {
        void loadTree({ silent: true });
        void loadChanged({ silent: true });
      }
    };
    const onVis = () => {
      if (document.visibilityState === 'visible') onFocus();
    };
    const treeId = window.setInterval(treeTick, 30000);
    const changedId = window.setInterval(changedTick, 10000);
    window.addEventListener('focus', onFocus);
    document.addEventListener('visibilitychange', onVis);
    return () => {
      window.clearInterval(treeId);
      window.clearInterval(changedId);
      window.removeEventListener('focus', onFocus);
      document.removeEventListener('visibilitychange', onVis);
    };
  }, [isOpen, rootPath, agentId, tab, loadTree, loadChanged]);

  const openDiff = useCallback(
    async (relPath: string) => {
      if (!agentId || !relPath || !rootPath) return;
      const norm = relPath.replace(/\\/g, '/');
      activeFileRef.current = relPath;
      setActiveFile(relPath);
      setBrowsePath(parentRel(relPath));
      expandToPath(relPath);
      setShowPreview(true);
      setFileError(null);
      setMdRaw(false);
      setImageSrc(null);
      setFileMeta(null);

      const paintFull = (
        lines: DiffLine[],
        meta: { additions: number; deletions: number; oversized?: boolean; status?: string },
      ) => {
        setFileLoading(false);
        setFileContent('');
        setDiffLines(lines);
        setDiffMeta(meta);
      };

      // 1) Instant: pre-flattened All Files cache
      const fullCached =
        fullDiffByPathRef.current[relPath] ||
        fullDiffByPathRef.current[norm];
      // 2) Instant: collapsed prefetch → enrich+flatten from file content
      const inlineCached =
        inlineDiffByPathRef.current[relPath] ||
        inlineDiffByPathRef.current[norm];
      let painted = false;
      if (fullCached?.lines?.length) {
        paintFull(fullCached.lines, {
          additions: fullCached.additions,
          deletions: fullCached.deletions,
          oversized: fullCached.oversized,
        });
        painted = true;
      } else if (inlineCached?.lines?.length) {
        const content =
          fileContentCacheRef.current.get(relPath)?.content ??
          fileContentCacheRef.current.get(norm)?.content;
        const enriched = (
          content
            ? fillDiffCollapseHidden(inlineCached.lines, content)
            : inlineCached.lines
        ) as DiffLine[];
        const flat = flattenDiffCollapses(enriched) as DiffLine[];
        if (flat.length) {
          fullDiffByPathRef.current[relPath] = {
            lines: flat,
            additions: inlineCached.additions,
            deletions: inlineCached.deletions,
            oversized: inlineCached.oversized,
          };
          paintFull(flat, {
            additions: inlineCached.additions,
            deletions: inlineCached.deletions,
            oversized: inlineCached.oversized,
          });
          painted = true;
        }
      }
      if (!painted) {
        // Instant: plain file content while full diff loads
        const fileCached =
          fileContentCacheRef.current.get(relPath) ||
          fileContentCacheRef.current.get(norm);
        if (fileCached) {
          setFileLoading(false);
          setDiffLines(null);
          setDiffMeta(null);
          setFileContent(fileCached.content);
          setImageSrc(fileCached.imageSrc);
          setFileMeta(fileCached.meta);
          setActiveFile(fileCached.meta.path || relPath);
          painted = true;
        }
      }

      if (!painted) {
        setFileLoading(true);
        setDiffLines(null);
        setDiffMeta(null);
        setFileContent('');
      }

      // Soft refresh: uncollapsed server diff (upgrade preview without flash)
      try {
        const resp = await adminAPI.getSessionDiff(agentId, relPath, rootPath, {
          collapse: false,
        });
        let lines = await enrichDiffLines(relPath, (resp.lines || []) as DiffLine[]);
        lines = flattenDiffCollapses(lines) as DiffLine[];
        const entry = {
          lines,
          additions: resp.additions || 0,
          deletions: resp.deletions || 0,
          oversized: resp.oversized,
        };
        fullDiffByPathRef.current[relPath] = entry;
        if (resp.path && resp.path !== relPath) {
          fullDiffByPathRef.current[resp.path] = entry;
        }
        const cur = (activeFileRef.current || '').replace(/\\/g, '/');
        if (cur === norm || cur === relPath || cur === resp.path) {
          setActiveFile(resp.path || relPath);
          paintFull(entry.lines, {
            additions: entry.additions,
            deletions: entry.deletions,
            oversized: entry.oversized,
            status: resp.status,
          });
        }
      } catch (err: any) {
        if (!painted) {
          setFileError(err?.message || '无法加载 diff');
        }
      } finally {
        if (!painted || (activeFileRef.current || '').replace(/\\/g, '/') === norm) {
          setFileLoading(false);
        }
      }
    },
    [agentId, rootPath, expandToPath, enrichDiffLines],
  );

  const expandInlineDiffFull = useCallback(
    async (relPath: string) => {
      if (!agentId || !rootPath || !relPath) return;
      try {
        const d = await adminAPI.getSessionDiff(agentId, relPath, rootPath, {
          collapse: false,
        });
        let lines = await enrichDiffLines(relPath, (d.lines || []) as DiffLine[]);
        lines = flattenDiffCollapses(lines) as DiffLine[];
        const entry = {
          lines,
          additions: d.additions || 0,
          deletions: d.deletions || 0,
          oversized: d.oversized,
        };
        fullDiffByPathRef.current[relPath] = entry;
        setInlineDiffByPath((prev) => ({ ...prev, [relPath]: entry }));
        if (activeFile === relPath || activeFile === d.path) {
          setDiffLines(entry.lines);
          setDiffMeta({
            additions: entry.additions,
            deletions: entry.deletions,
            oversized: entry.oversized,
            status: d.status,
          });
        }
      } catch {
        /* ignore */
      }
    },
    [agentId, rootPath, activeFile, enrichDiffLines],
  );

  const openFile = useCallback(
    async (relPath: string, opts?: { force?: boolean }) => {
      if (!agentId || !relPath || !rootPath) return;
      if (treeOnly && onOpenFile) {
        setActiveFile(relPath);
        setBrowsePath(parentRel(relPath));
        expandToPath(relPath);
        onOpenFile(relPath);
        return;
      }
      setShowPreview(true);
      setDiffLines(null);
      setDiffMeta(null);
      activeFileRef.current = relPath;
      setActiveFile(relPath);
      setBrowsePath(parentRel(relPath));
      expandToPath(relPath);
      setFileError(null);
      setMdRaw(false);

      const cached = !opts?.force
        ? fileContentCacheRef.current.get(relPath) ||
          (() => {
            const mod = getModuleFileCache(agentId, rootPath, relPath);
            if (mod) fileContentCacheRef.current.set(relPath, mod);
            return mod || undefined;
          })()
        : undefined;
      if (cached) {
        // Instant paint from cache — no loading flash
        setFileLoading(false);
        setFileContent(cached.content);
        setImageSrc(cached.imageSrc);
        setFileMeta(cached.meta);
        setActiveFile(cached.meta.path || relPath);
        // Soft revalidate in background
        void (async () => {
          try {
            await prefetchFileContent(relPath, { force: true });
            const fresh = fileContentCacheRef.current.get(relPath);
            if (!fresh) return;
            setActiveFile((cur) => {
              if (cur !== relPath && cur !== fresh.meta.path) return cur;
              setFileContent(fresh.content);
              setImageSrc(fresh.imageSrc);
              setFileMeta(fresh.meta);
              return fresh.meta.path || relPath;
            });
          } catch {
            /* keep cached */
          }
        })();
        return;
      }

      setFileLoading(true);
      setFileContent('');
      setImageSrc(null);
      setFileMeta(null);
      try {
        const resp = await adminAPI.readProjectFile(agentId, relPath, rootPath);
        const kind = resp.kind === 'image' || (resp.content_base64 && resp.mime?.startsWith('image/'))
          ? 'image'
          : 'text';
        const imageSrc =
          kind === 'image' && resp.content_base64 && resp.mime
            ? `data:${resp.mime};base64,${resp.content_base64}`
            : null;
        const content = kind === 'image' ? '' : (resp.content ?? '');
        const meta = {
          truncated: resp.truncated,
          path: resp.path,
          size: resp.size,
          kind: kind as 'text' | 'image',
        };
        const entry = {
          content,
          imageSrc,
          meta: { ...meta, path: resp.path || relPath },
          at: Date.now(),
        };
        fileContentCacheRef.current.set(relPath, entry);
        putModuleFileCache(agentId, rootPath, relPath, entry);
        if (imageSrc) {
          setImageSrc(imageSrc);
          setFileContent('');
        } else {
          setImageSrc(null);
          setFileContent(content);
        }
        setFileMeta(meta);
        const finalPath = resp.path || relPath;
        setActiveFile(finalPath);
        setBrowsePath(parentRel(finalPath));
        expandToPath(finalPath);
      } catch (err: any) {
        setFileError(err?.message || '无法读取文件');
      } finally {
        setFileLoading(false);
      }
    },
    [agentId, rootPath, expandToPath, prefetchFileContent, treeOnly, onOpenFile],
  );

  /** Show red tombstone preview for files deleted by withdraw/revert. */
  const openUnavailable = useCallback(
    (relPath: string) => {
      const norm = (relPath || '').replace(/\\/g, '/');
      setShowPreview(true);
      setActiveFile(norm);
      setBrowsePath(parentRel(norm));
      expandToPath(norm);
      setDiffLines(null);
      setDiffMeta(null);
      setFileContent('');
      setImageSrc(null);
      setFileMeta(null);
      setFileLoading(false);
      setMdRaw(false);
      setFileError('该文件已因撤回被删除，无法查看内容');
    },
    [expandToPath],
  );

  /** All-files: dirty (session-changed, not kept) → same red/green diff as 变动文件. */
  const openFileOrDiff = useCallback(
    async (relPath: string) => {
      if (treeOnly && onOpenFile) {
        const norm = (relPath || '').replace(/\\/g, '/');
        setActiveFile(norm);
        expandToPath(norm);
        onOpenFile(norm);
        return;
      }
      const norm = (relPath || '').replace(/\\/g, '/');
      const ch = changedByPath.get(norm) || changedByPath.get(relPath);
      if (ch && (ch.missing || ch.status === 'D')) {
        openUnavailable(norm);
        return;
      }
      if (ch) {
        await openDiff(relPath);
        return;
      }
      await openFile(relPath, { force: true });
    },
    [changedByPath, openDiff, openFile, openUnavailable, treeOnly, onOpenFile, expandToPath],
  );

  // Reset when root / open changes — load full tree once (module cache paints instantly)
  useEffect(() => {
    if (!isOpen) return;
    setSearch('');
    setShowSearch(false);
    setActiveFile(null);
    setFileContent('');
    setImageSrc(null);
    setFileError(null);
    setDiffLines(null);
    setDiffMeta(null);
    setBrowsePath('');
    setInlineCreate(null);
    setRenamingPath(null);
    closeMenus();
    treeLoadedOnceRef.current = false;
    if (rootPath) {
      const cached = getModuleTreeCache(agentId, rootPath);
      // Same root revisited: silent refresh (no spinner). Cache miss: full load.
      void loadTree({ silent: !!cached });
      // Silent warm of change stats (both tabs) — no full-panel flash
      void loadChanged({ silent: true });
    } else {
      setTreeEntries([]);
      setListError(null);
      setChangedEntries([]);
    }
  }, [isOpen, rootPath, agentId]); // eslint-disable-line react-hooks/exhaustive-deps

  // External open request (tool-stream filename click)
  useEffect(() => {
    if (!isOpen || !openRequest || !rootPath) return;
    if (openRequest.nonce === lastOpenNonce.current) return;
    lastOpenNonce.current = openRequest.nonce;
    const rel = toProjectRelative(rootPath, openRequest.path);
    if (!rel) return;
    setTab('all');
    void openFileOrDiff(rel);
  }, [isOpen, openRequest, rootPath, openFileOrDiff]);

  // Keep session-change map fresh on either tab (always silent once open)
  useEffect(() => {
    if (!isOpen || !rootPath) return;
    void loadChanged({ silent: true });
  }, [isOpen, rootPath, tab, loadChanged]);
  // Changes bar → switch to changed tab; soft refresh (no full-panel flash)
  useEffect(() => {
    if (!focusChangedNonce || !isOpen) return;
    setTab('changed');
    setShowPreview(false);
    void loadChanged({ silent: true });
  }, [focusChangedNonce]); // eslint-disable-line react-hooks/exhaustive-deps

  // Parent live snapshot after tool/turn/withdraw — apply in place, soft-refresh tree
  useEffect(() => {
    if (!liveChanges || !isOpen) return;
    if (liveChanges.nonce === lastLiveNonceRef.current) return;
    lastLiveNonceRef.current = liveChanges.nonce;
    const files = (liveChanges.files || []).map((e) => ({
      ...e,
      path: (e.path || '').replace(/\\/g, '/'),
      missing: !!(e as ChangedEntry).missing || e.status === 'D',
    }));
    const result = applyChangedFiles(
      files,
      {
        additions: liveChanges.additions || 0,
        deletions: liveChanges.deletions || 0,
        count: liveChanges.count ?? files.length,
      },
      // Soft merge: only refetch diffs whose stats/mtime changed (no full-list flash)
      { notifyParent: false, forceDiffRefresh: false },
    );
    // Keep file tree in sync without wiping fold state / spinner flash
    void loadTree({ silent: true });
    // Expand parents of deleted ghosts so they stay visible in 所有文件
    for (const f of files) {
      if (f.missing || f.status === 'D') {
        for (const a of ancestorPaths(f.path)) {
          setExpanded((prev) => {
            if (prev.has(a)) return prev;
            const next = new Set(prev);
            next.add(a);
            return next;
          });
        }
      }
    }
    // Refresh open preview silently when disk/diff changed
    const active = (activeFile || '').replace(/\\/g, '/');
    if (active && showPreview && tab === 'all') {
      const ch = files.find((f) => f.path === active);
      if (ch && (ch.missing || ch.status === 'D')) {
        openUnavailable(active);
      } else if (result?.nextPaths.has(active) || result?.staleDiffPaths.includes(active)) {
        void openDiff(active);
      } else if (result?.removedPaths.includes(active)) {
        void openFile(active, { force: true });
      }
    }
  }, [
    liveChanges,
    isOpen,
    applyChangedFiles,
    loadTree,
    activeFile,
    showPreview,
    tab,
    openUnavailable,
    openDiff,
    openFile,
  ]);

  // 所有文件右侧预览：未 Keep 的改动文件同步为红绿 diff（与变动文件同源缓存）
  useEffect(() => {
    if (!isOpen || tab !== 'all' || !activeFile || !showPreview) return;
    const norm = activeFile.replace(/\\/g, '/');
    const ch = changedByPath.get(norm) || changedByPath.get(activeFile);
    if (ch && (ch.missing || ch.status === 'D')) {
      setDiffLines(null);
      setDiffMeta(null);
      setFileContent('');
      setImageSrc(null);
      setFileError('该文件已因撤回被删除，无法查看内容');
      return;
    }
    const dirty = !!ch;
    if (!dirty) return;
    const cached = inlineDiffByPath[activeFile] || inlineDiffByPath[norm];
    if (!cached) return;
    setFileError(null);
    setDiffLines(cached.lines);
    setDiffMeta({
      additions: cached.additions,
      deletions: cached.deletions,
      oversized: cached.oversized,
    });
  }, [isOpen, tab, activeFile, showPreview, changedByPath, inlineDiffByPath]);

  const toggleChangedExpand = useCallback(
    async (relPath: string) => {
      const wasOpen = expandedChangedRef.current.has(relPath);
      setExpandedChanged((prev) => {
        const next = new Set(prev);
        if (wasOpen) next.delete(relPath);
        else next.add(relPath);
        return next;
      });
      if (wasOpen || !agentId || !rootPath) return;
      // Cache hit → instant expand, no loading UI
      if (inlineDiffByPathRef.current[relPath]) return;
      // Rare miss (prefetch still in flight): show spinner only for this row
      await prefetchDiffs([relPath], { showLoadingFor: [relPath] });
    },
    [agentId, rootPath, prefetchDiffs],
  );

  const revertChangedFile = useCallback(
    async (relPath: string) => {
      if (!agentId || !rootPath || !relPath) return;
      setRevertingPath(relPath);
      try {
        const resp = await adminAPI.revertSessionFile(agentId, relPath, rootPath);
        setExpandedChanged((prev) => {
          const next = new Set(prev);
          next.delete(relPath);
          return next;
        });
        setInlineDiffByPath((prev) => {
          const next = { ...prev };
          delete next[relPath];
          return next;
        });
        if (activeFile === relPath) {
          setActiveFile(null);
          setDiffLines(null);
          setDiffMeta(null);
        }
        onSessionChangesRef.current?.({
          additions: resp.additions || 0,
          deletions: resp.deletions || 0,
          count: resp.count ?? 0,
        });
        await loadChanged({ silent: true });
      } catch (err: any) {
        setChangedError(err?.message || '撤回失败');
      } finally {
        setRevertingPath(null);
      }
    },
    [agentId, rootPath, activeFile, loadChanged],
  );

  /** Keep/save current disk for one file — drop from Changes; withdraw still rolls back. */
  const keepChangedFile = useCallback(
    async (relPath: string) => {
      if (!agentId || !rootPath || !relPath) return;
      setKeepingPath(relPath);
      try {
        const resp = await adminAPI.keepSessionFile(agentId, relPath, rootPath);
        setExpandedChanged((prev) => {
          const next = new Set(prev);
          next.delete(relPath);
          return next;
        });
        setInlineDiffByPath((prev) => {
          const next = { ...prev };
          delete next[relPath];
          return next;
        });
        if (activeFile === relPath) {
          setDiffLines(null);
          setDiffMeta(null);
          // Keep/save → show plain content again (no longer in Changes)
          void openFile(relPath);
        }
        onSessionChangesRef.current?.({
          additions: resp.additions || 0,
          deletions: resp.deletions || 0,
          count: resp.count ?? 0,
        });
        await loadChanged({ silent: true });
        void loadTree({ silent: true });
      } catch (err: any) {
        setChangedError(err?.message || '保留变动失败');
      } finally {
        setKeepingPath(null);
      }
    },
    [agentId, rootPath, activeFile, loadChanged, loadTree, openFile],
  );

  // Focus inline create / rename inputs
  useEffect(() => {
    if (inlineCreate) {
      createInputRef.current?.focus();
      createInputRef.current?.select();
    }
  }, [inlineCreate]);

  useEffect(() => {
    if (renamingPath) {
      renameInputRef.current?.focus();
      renameInputRef.current?.select();
    }
  }, [renamingPath]);

  // Close menus on outside click / Escape
  useEffect(() => {
    if (!ctxMenu && !newMenuOpen) return;
    const onPointerDown = (e: MouseEvent) => {
      const t = e.target as Node;
      if (ctxMenuRef.current?.contains(t)) return;
      if (newMenuRef.current?.contains(t)) return;
      closeMenus();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        closeMenus();
        setInlineCreate(null);
        setRenamingPath(null);
      }
    };
    document.addEventListener('mousedown', onPointerDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onPointerDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [ctxMenu, newMenuOpen, closeMenus]);

  const onResizePointerDown = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      e.preventDefault();
      dragRef.current = { startX: e.clientX, startWidth: width };
      try {
        e.currentTarget.setPointerCapture(e.pointerId);
      } catch {
        /* ignore */
      }
      document.body.style.cursor = 'col-resize';
      document.body.style.userSelect = 'none';
    },
    [width],
  );

  const onResizePointerMove = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      if (!dragRef.current) return;
      // Dragging left edge: moving mouse left increases width
      const next = Math.min(
        WIDTH_MAX,
        Math.max(WIDTH_MIN, Math.round(dragRef.current.startWidth - (e.clientX - dragRef.current.startX))),
      );
      onWidthChange(next);
    },
    [onWidthChange],
  );

  const onResizePointerUp = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    if (!dragRef.current) return;
    dragRef.current = null;
    try {
      e.currentTarget.releasePointerCapture(e.pointerId);
    } catch {
      /* ignore */
    }
    document.body.style.cursor = '';
    document.body.style.userSelect = '';
  }, []);

  useEffect(() => {
    return () => {
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    };
  }, []);

  const openContextMenu = useCallback((e: React.MouseEvent, target: CtxTarget) => {
    e.preventDefault();
    e.stopPropagation();
    setNewMenuOpen(false);
    const panel = panelRef.current;
    const rect = panel?.getBoundingClientRect();
    let x = e.clientX;
    let y = e.clientY;
    // Keep menu inside panel roughly
    if (rect) {
      x = Math.min(x, rect.right - 180);
      y = Math.min(y, rect.bottom - 220);
      x = Math.max(x, rect.left + 4);
      y = Math.max(y, rect.top + 4);
    }
    setCtxMenu({ x, y, target });
  }, []);

  const startCreate = useCallback(async (kind: 'file' | 'dir', underDir?: string) => {
    closeMenus();
    setTab('all');
    skipCreateBlurRef.current = false;
    setRenamingPath(null);
    const parent = underDir != null ? underDir : browsePath;
    setCreateUnder(parent);
    if (parent) {
      setExpanded((prev) => {
        const next = new Set(prev);
        next.add(parent);
        for (const a of ancestorPaths(parent)) next.add(a);
        return next;
      });
    }
    setInlineCreate({ kind });
    setCreateName(kind === 'file' ? '未命名文档.md' : '新建文件夹');
  }, [closeMenus, browsePath]);

  const commitCreate = useCallback(async () => {
    if (!inlineCreate || !agentId || !rootPath || actionBusy) return;
    const name = createName.trim();
    if (!name || /[/\\]/.test(name)) {
      setInlineCreate(null);
      return;
    }
    const parent = createUnder || browsePath;
    const rel = joinRel(parent, name);
    setActionBusy(true);
    try {
      if (inlineCreate.kind === 'file') {
        await adminAPI.writeProjectFile(agentId, rel, '', rootPath);
        setInlineCreate(null);
        await loadTree();
        void openFile(rel);
      } else {
        await adminAPI.mkdirProject(agentId, rel, rootPath);
        setInlineCreate(null);
        await loadTree();
        setExpanded((prev) => new Set(prev).add(parent || rel));
      }
    } catch (err: any) {
      setListError(err?.message || '创建失败');
      setInlineCreate(null);
    } finally {
      setActionBusy(false);
    }
  }, [inlineCreate, agentId, rootPath, actionBusy, createName, createUnder, browsePath, loadTree, openFile]);

  const startRename = useCallback((relPath: string) => {
    closeMenus();
    skipRenameBlurRef.current = false;
    setRenamingPath(relPath);
    setRenameValue(basename(relPath));
    setInlineCreate(null);
  }, [closeMenus]);

  const commitRename = useCallback(async () => {
    if (!renamingPath || !agentId || !rootPath || actionBusy) return;
    const newName = renameValue.trim();
    if (!newName || /[/\\]/.test(newName)) {
      setRenamingPath(null);
      return;
    }
    const to = joinRel(parentRel(renamingPath), newName);
    if (to === renamingPath) {
      setRenamingPath(null);
      return;
    }
    setActionBusy(true);
    try {
      await adminAPI.renameProjectPath(agentId, renamingPath, to, rootPath);
      if (activeFile === renamingPath) {
        setActiveFile(to);
      }
      setRenamingPath(null);
      if (tab === 'changed') await loadChanged();
      else await loadTree();
    } catch (err: any) {
      setListError(err?.message || '重命名失败');
      setRenamingPath(null);
    } finally {
      setActionBusy(false);
    }
  }, [
    renamingPath,
    agentId,
    rootPath,
    actionBusy,
    renameValue,
    activeFile,
    tab,
    loadChanged,
    loadTree,
  ]);

  const doDelete = useCallback(async (relPath: string) => {
    closeMenus();
    if (!agentId || !rootPath || !relPath) return;
    const label = basename(relPath);
    if (!window.confirm(`确定删除「${label}」？此操作不可撤销。`)) return;
    setActionBusy(true);
    try {
      await adminAPI.deleteProjectPath(agentId, relPath, rootPath);
      if (activeFile === relPath) {
        setActiveFile(null);
        setFileContent('');
        setImageSrc(null);
      }
      if (tab === 'changed') await loadChanged();
      else await loadTree();
    } catch (err: any) {
      setListError(err?.message || '删除失败');
    } finally {
      setActionBusy(false);
    }
  }, [closeMenus, agentId, rootPath, activeFile, tab, loadChanged, loadTree]);

  const doReveal = useCallback(async (relPath: string) => {
    closeMenus();
    if (!agentId || !rootPath) return;
    try {
      await adminAPI.revealProjectPath(agentId, relPath, rootPath);
    } catch (err: any) {
      setListError(err?.message || '无法打开所在目录');
    }
  }, [closeMenus, agentId, rootPath]);

  const doTerminal = useCallback(async (target: CtxTarget) => {
    closeMenus();
    if (!agentId || !rootPath) return;
    const dir =
      target.type === 'file' ? parentRel(target.path) : target.path;
    try {
      await adminAPI.openProjectTerminal(agentId, dir, rootPath);
    } catch (err: any) {
      setListError(err?.message || '无法在终端中打开');
    }
  }, [closeMenus, agentId, rootPath]);

  const doCopyPath = useCallback(async (relPath: string) => {
    closeMenus();
    try {
      await navigator.clipboard.writeText(relPath || '.');
    } catch {
      /* ignore */
    }
  }, [closeMenus]);

  const { mounted: softMounted, visible: softVisible } = useSoftPresence(isOpen, SOFT_PRESENCE_MS);
  const [railToggling, setRailToggling] = useState(false);

  useEffect(() => {
    setRailToggling(true);
    const t = window.setTimeout(() => setRailToggling(false), SOFT_PRESENCE_MS);
    return () => window.clearTimeout(t);
  }, [isOpen]);

  if (!softMounted) return null;

  const canCreateHere = tab === 'all';

  const renderCtxMenu = () => {
    if (!ctxMenu) return null;
    const { target } = ctxMenu;
    const isRoot = target.type === 'root';
    const showNew = isRoot || target.type === 'dir';
    return (
      <div
        ref={ctxMenuRef}
        className="fixed z-[80] min-w-[168px] py-1 rounded-lg bg-white dark:bg-[#252526] border border-black/8 dark:border-white/10 shadow-lg text-[12px] text-textMain os-soft-pop is-open"
        style={{ left: ctxMenu.x, top: ctxMenu.y }}
        onContextMenu={(e) => e.preventDefault()}
      >
        {showNew ? (
          <>
            <button
              type="button"
              className="w-full px-3 py-1.5 text-left hover:bg-primary/10"
              onClick={() => void startCreate('file', isRoot ? browsePath : target.path)}
            >
              新建文档
            </button>
            <button
              type="button"
              className="w-full px-3 py-1.5 text-left hover:bg-primary/10"
              onClick={() => void startCreate('dir', isRoot ? browsePath : target.path)}
            >
              新建文件夹
            </button>
            <div className="my-1 h-px bg-black/8 dark:bg-white/10" />
          </>
        ) : null}
        <button
          type="button"
          className="w-full px-3 py-1.5 text-left hover:bg-primary/10"
          onClick={() => void doReveal(target.path)}
        >
          打开所在目录
        </button>
        <button
          type="button"
          className="w-full px-3 py-1.5 text-left hover:bg-primary/10"
          onClick={() => void doTerminal(target)}
        >
          在终端中打开
        </button>
        <button
          type="button"
          className="w-full px-3 py-1.5 text-left hover:bg-primary/10"
          onClick={() => void doCopyPath(target.path)}
        >
          复制路径
        </button>
        {!isRoot ? (
          <>
            <div className="my-1 h-px bg-black/8 dark:bg-white/10" />
            <button
              type="button"
              className="w-full px-3 py-1.5 text-left hover:bg-primary/10"
              onClick={() => startRename(target.path)}
            >
              重命名
            </button>
            <button
              type="button"
              className="w-full px-3 py-1.5 text-left hover:bg-primary/10 text-red-500"
              onClick={() => void doDelete(target.path)}
            >
              删除
            </button>
          </>
        ) : null}
      </div>
    );
  };

  const useSplitPreview = !treeOnly && showPreview && tab !== 'changed';

  const listPane = (
    <div
      className={`flex flex-col min-h-0 border-r border-border flex-shrink-0 ${
        useSplitPreview ? 'w-[42%] min-w-[140px] max-w-[240px]' : 'flex-1 min-w-0'
      }`}
      onContextMenu={(e) => {
        // Empty area / background → root context
        if (e.target === e.currentTarget || (e.target as HTMLElement).dataset?.fsEmpty === '1') {
          openContextMenu(e, { path: browsePath, type: 'root', name: '' });
        }
      }}
    >
      {showSearch ? (
        <div className="px-2 py-1.5 border-b border-border flex-shrink-0">
          <div className="relative">
            <Search size={12} className="absolute left-2 top-1/2 -translate-y-1/2 text-textMuted" />
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="搜索文件…"
              autoFocus
              className="w-full pl-7 pr-2 py-1 text-[11px] rounded-md bg-black/[0.03] dark:bg-white/5 border border-border/60 text-textMuted placeholder:text-textMuted/40 outline-none focus:border-primary/40"
            />
          </div>
        </div>
      ) : null}

      {/* Tabs */}
      <div className="flex items-center gap-0 px-1.5 pt-1.5 pb-0 border-b border-border flex-shrink-0">
        {([
          { id: 'changed' as const, label: '变动文件' },
          { id: 'all' as const, label: '所有文件' },
        ]).map((t) => (
          <button
            key={t.id}
            type="button"
            onClick={() => {
              setTab(t.id);
              setInlineCreate(null);
              setRenamingPath(null);
              closeMenus();
              if (t.id === 'changed') setShowPreview(false);
              else setShowPreview(true);
            }}
            className={`px-2.5 py-1.5 text-[11px] relative transition-colors ${
              tab === t.id
                ? 'text-textMain font-medium'
                : 'text-textMuted/55 hover:text-textMuted'
            }`}
          >
            {t.label}
            {tab === t.id ? (
              <span className="absolute left-2 right-2 bottom-0 h-[1.5px] rounded-full bg-textMain/50" />
            ) : null}
          </button>
        ))}
      </div>

      {/* Tree status — all-files only */}
      {tab === 'all' && rootPath && !listLoading ? (
        <div className="px-2.5 py-1 border-b border-border/40 text-[10px] text-textMuted/50 flex-shrink-0 flex items-center gap-2">
          <span>
            已加载 {treeCount.toLocaleString()} 项
            {treeTruncated ? '（已达上限 10000）' : ''}
          </span>
          {search.trim() ? (
            <span className="truncate">· 搜索 {visibleRows.length} 条</span>
          ) : null}
        </div>
      ) : null}

      <div
        className="flex-1 min-h-0 overflow-y-auto py-1 os-depth-nest os-depth-nest--flush"
        data-fs-empty="1"
        onContextMenu={(e) => {
          if ((e.target as HTMLElement).dataset?.fsEmpty === '1') {
            openContextMenu(e, {
              path: '',
              type: 'root',
              name: '',
            });
          }
        }}
      >
        {!rootPath ? (
          <div className="px-3 py-4 text-[11px] text-textMuted leading-relaxed" data-fs-empty="1">
            请在聊天栏底部选择项目文件夹后再浏览文件。
          </div>
        ) : tab === 'changed' ? (
          changedLoading && filteredChanged.length === 0 ? (
            <div className="flex items-center gap-2 px-3 py-3 text-[11px] text-textMuted">
              <Loader2 size={12} className="animate-spin" /> 加载中…
            </div>
          ) : changedError && filteredChanged.length === 0 ? (
            <div className="px-3 py-3 text-[11px] text-textMuted">{changedError}</div>
          ) : filteredChanged.length === 0 ? (
            <div className="px-3 py-3 text-[11px] text-textMuted/60" data-fs-empty="1">
              暂无变动文件
            </div>
          ) : (
            <div className="flex flex-col min-h-0">
              {filteredChanged.map((e) => {
                const isOpenRow = expandedChanged.has(e.path);
                const inline = inlineDiffByPath[e.path];
                const isDiffLoading = inlineDiffLoading === e.path;
                const isReverting = revertingPath === e.path;
                const isKeeping = keepingPath === e.path;
                return (
                  <div key={`ch:${e.path}`} className="border-b border-border/40 last:border-b-0">
                    <div
                      className={`group os-interactive flex items-center gap-1 px-2 py-[5px] text-[11px] rounded-none ${
                        isOpenRow
                          ? 'is-active text-textMuted'
                          : 'text-textMuted/70'
                      }`}
                      onMouseEnter={() => {
                        if (!inlineDiffByPathRef.current[e.path]) {
                          void prefetchDiffs([e.path]);
                        }
                        void prefetchFileContent(e.path);
                      }}
                      onContextMenu={(ev) =>
                        openContextMenu(ev, { path: e.path, type: e.type, name: e.name })
                      }
                    >
                      <button
                        type="button"
                        className="p-0.5 rounded shrink-0 text-textMuted/50 hover:text-textMuted"
                        title={isOpenRow ? '折叠' : '展开 diff'}
                        onClick={() => void toggleChangedExpand(e.path)}
                      >
                        {isOpenRow ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
                      </button>
                      <FileTypeIcon name={e.name} type={e.type === 'dir' ? 'dir' : 'file'} />
                      <button
                        type="button"
                        className="flex-1 min-w-0 text-left truncate font-mono text-[11px]"
                        title={e.path}
                        onClick={() => void toggleChangedExpand(e.path)}
                      >
                        <span
                          className={
                            e.status === 'A' || e.status === 'U'
                              ? 'text-emerald-700/70 dark:text-emerald-400/65'
                              : e.status === 'D'
                                ? 'text-rose-500/70'
                                : 'text-textMuted/80'
                          }
                        >
                          {e.path}
                        </span>
                      </button>
                      <span className="flex items-center gap-1 shrink-0 text-[10px] tabular-nums font-mono">
                        {(e.additions || 0) > 0 ? (
                          <span className="text-emerald-600/70">+{e.additions}</span>
                        ) : null}
                        {(e.deletions || 0) > 0 ? (
                          <span className="text-rose-500/60">-{e.deletions}</span>
                        ) : (
                          (e.additions || 0) === 0 ? (
                            <span className="text-textMuted/50">+0</span>
                          ) : null
                        )}
                      </span>
                      <button
                        type="button"
                        className="opacity-0 group-hover:opacity-100 p-0.5 rounded hover:bg-emerald-500/15 shrink-0 disabled:opacity-40"
                        title="保留全部变动（从 Changes 移除，磁盘内容不变；消息撤回仍可回滚）"
                        disabled={isKeeping || isReverting}
                        onClick={(ev) => {
                          ev.stopPropagation();
                          void keepChangedFile(e.path);
                        }}
                      >
                        {isKeeping ? (
                          <Loader2 size={12} className="animate-spin text-emerald-500" />
                        ) : (
                          <Check size={12} className="text-emerald-600 dark:text-emerald-400" />
                        )}
                      </button>
                      <button
                        type="button"
                        className="opacity-0 group-hover:opacity-100 p-0.5 rounded hover:bg-primary/15 shrink-0 disabled:opacity-40"
                        title="撤回此文件"
                        disabled={isReverting || isKeeping}
                        onClick={(ev) => {
                          ev.stopPropagation();
                          void revertChangedFile(e.path);
                        }}
                      >
                        {isReverting ? (
                          <Loader2 size={12} className="animate-spin text-textMuted" />
                        ) : (
                          <RotateCcw size={12} className="text-textMuted" />
                        )}
                      </button>
                      <button
                        type="button"
                        className="opacity-0 group-hover:opacity-100 p-0.5 rounded hover:bg-primary/15 shrink-0"
                        title="Copy Path"
                        onClick={(ev) => {
                          ev.stopPropagation();
                          void doCopyPath(e.path);
                        }}
                      >
                        <Copy size={12} className="text-textMuted" />
                      </button>
                    </div>
                    {isOpenRow ? (
                      <div className="max-h-[280px] overflow-auto border-t border-border/30 bg-bgLight/80">
                        {inline ? (
                          <UnifiedDiffView
                            fileName={e.name}
                            lines={inline.lines}
                            additions={inline.additions}
                            deletions={inline.deletions}
                            oversized={inline.oversized}
                            onExpandWithoutHidden={() => {
                              void expandInlineDiffFull(e.path);
                            }}
                          />
                        ) : isDiffLoading ? (
                          <div className="flex items-center gap-2 px-3 py-2 text-[10px] text-textMuted/70">
                            <Loader2 size={11} className="animate-spin" /> 准备中…
                          </div>
                        ) : (
                          <div className="px-3 py-2 text-[11px] text-textMuted">无 diff</div>
                        )}
                      </div>
                    ) : null}
                  </div>
                );
              })}
            </div>
          )
        ) : listLoading && treeEntries.length === 0 ? (
          <div className="flex items-center gap-2 px-3 py-3 text-[11px] text-textMuted">
            <Loader2 size={12} className="animate-spin" /> 正在加载文件树…
          </div>
        ) : listError && treeEntries.length === 0 ? (
          <div className="px-3 py-3 text-[11px] text-red-400">{listError}</div>
        ) : (
          <>
            {inlineCreate && canCreateHere ? (
              <div
                className="flex items-center gap-1.5 px-2 py-[5px]"
                style={{ paddingLeft: 8 + ((createUnder || '').split('/').filter(Boolean).length) * 12 }}
              >
                {inlineCreate.kind === 'dir' ? (
                  <Folder size={14} className="text-neutral-400 dark:text-neutral-500 shrink-0" strokeWidth={1.5} />
                ) : (
                  <FileText size={14} className="text-neutral-400 dark:text-neutral-500 shrink-0" strokeWidth={1.5} />
                )}
                <input
                  ref={createInputRef}
                  value={createName}
                  onChange={(e) => setCreateName(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') {
                      e.preventDefault();
                      void commitCreate();
                    } else if (e.key === 'Escape') {
                      skipCreateBlurRef.current = true;
                      setInlineCreate(null);
                    }
                  }}
                  onBlur={() => {
                    if (skipCreateBlurRef.current) {
                      skipCreateBlurRef.current = false;
                      return;
                    }
                    if (createName.trim()) void commitCreate();
                    else setInlineCreate(null);
                  }}
                  className="flex-1 min-w-0 px-1 py-0.5 text-[11px] rounded border border-primary/40 bg-bgLight outline-none font-mono"
                  placeholder={inlineCreate.kind === 'dir' ? '文件夹名称' : '文件名'}
                />
              </div>
            ) : null}
            {visibleRows.length === 0 && !inlineCreate ? (
              <div className="px-3 py-3 text-[11px] text-textMuted/60" data-fs-empty="1">
                {search.trim() ? '无匹配文件' : '空工作区'}
              </div>
            ) : (
              visibleRows.map((e) => {
                const selected = e.type === 'file' && activeFile === e.path;
                const isRenaming = renamingPath === e.path;
                const isOpenDir = e.type === 'dir' && expanded.has(e.path);
                const hasKids =
                  e.type === 'dir' &&
                  !e.skipped &&
                  (childrenMap.get(e.path)?.length ?? 0) > 0;
                const ch = e.type === 'file' ? changedByPath.get(e.path) : undefined;
                const dirDirty = e.type === 'dir' && dirsWithChanges.has(e.path);
                const isMissing = !!(e.missing || ch?.missing || ch?.status === 'D');
                const nameClass = isMissing
                  ? 'text-rose-500/70 line-through decoration-rose-500/40'
                  : ch
                    ? ch.status === 'A' || ch.status === 'U'
                      ? 'text-emerald-700/70 dark:text-emerald-400/65'
                      : ch.status === 'D'
                        ? 'text-rose-500/70'
                        : 'text-amber-800/55 dark:text-amber-400/55'
                    : dirDirty
                      ? 'text-amber-800/50 dark:text-amber-400/50'
                      : 'text-inherit';
                return (
                  <div
                    key={`${e.type}:${e.path}`}
                    className={`group os-interactive relative flex items-center gap-0.5 pr-2 py-[4px] text-[11px] rounded-none ${
                      selected
                        ? 'is-active text-textMuted'
                        : 'text-textMuted/70'
                    } ${isMissing ? 'opacity-80' : ''}`}
                    style={{ paddingLeft: 8 + e.depth * 12 }}
                    onMouseEnter={() => {
                      if (e.type !== 'file' || isMissing) return;
                      void prefetchFileContent(e.path);
                      if (ch) void prefetchDiffs([e.path]);
                    }}
                    onContextMenu={(ev) =>
                      openContextMenu(ev, { path: e.path, type: e.type, name: e.name })
                    }
                  >
                    {e.type === 'dir' ? (
                      <button
                        type="button"
                        className="w-4 h-4 flex items-center justify-center shrink-0 border-0 bg-transparent p-0 cursor-pointer text-textMuted/45"
                        onClick={() => {
                          if (e.skipped) return;
                          toggleExpand(e.path);
                          setBrowsePath(e.path);
                        }}
                        title={e.skipped ? '已跳过深层内容' : isOpenDir ? '折叠' : '展开'}
                      >
                        {e.skipped || !hasKids ? (
                          <span className="w-3" />
                        ) : isOpenDir ? (
                          <ChevronDown size={12} />
                        ) : (
                          <ChevronRight size={12} />
                        )}
                      </button>
                    ) : (
                      <span className="w-4 shrink-0" />
                    )}
                    <FileTypeIcon name={e.name} type={e.type} />
                    {isRenaming ? (
                      <input
                        ref={renameInputRef}
                        value={renameValue}
                        onChange={(ev) => setRenameValue(ev.target.value)}
                        onKeyDown={(ev) => {
                          if (ev.key === 'Enter') {
                            ev.preventDefault();
                            void commitRename();
                          } else if (ev.key === 'Escape') {
                            setRenamingPath(null);
                          }
                        }}
                        onBlur={() => void commitRename()}
                        className="flex-1 min-w-0 ml-1 px-1 py-0.5 text-[11px] rounded border border-primary/40 bg-bgLight outline-none font-mono"
                      />
                    ) : (
                      <button
                        type="button"
                        className={`flex-1 min-w-0 ml-1 text-left truncate font-mono border-0 bg-transparent p-0 cursor-pointer ${nameClass}`}
                        title={
                          isMissing
                            ? `${e.path}（已因撤回删除，无法查看）`
                            : ch
                              ? `${e.path}  (+${ch.additions || 0} -${ch.deletions || 0})`
                              : e.path + (e.skipped ? '（未展开深层）' : '')
                        }
                        onClick={() => {
                          if (e.type === 'dir') {
                            setBrowsePath(e.path);
                            if (!e.skipped) toggleExpand(e.path);
                          } else {
                            void openFileOrDiff(e.path);
                          }
                        }}
                      >
                        {e.name}
                        {isMissing ? (
                          <span className="ml-1 text-[9px] text-rose-400/80 no-underline">已删除</span>
                        ) : e.skipped ? (
                          <span className="ml-1 text-[9px] opacity-50">…</span>
                        ) : null}
                      </button>
                    )}
                    {ch && !isRenaming ? (
                      <span className="flex items-center gap-1 shrink-0 text-[10px] tabular-nums font-mono mr-0.5">
                        {(ch.additions || 0) > 0 ? (
                          <span className="text-emerald-600/70">+{ch.additions}</span>
                        ) : null}
                        {(ch.deletions || 0) > 0 ? (
                          <span className="text-rose-500/60">-{ch.deletions}</span>
                        ) : (ch.additions || 0) === 0 ? (
                          <span className="text-textMuted/50">+0</span>
                        ) : null}
                      </span>
                    ) : null}
                    {!isRenaming ? (
                      <button
                        type="button"
                        className="opacity-0 group-hover:opacity-100 p-0.5 rounded hover:bg-primary/15 shrink-0 border-0 bg-transparent cursor-pointer"
                        title="更多"
                        onClick={(ev) =>
                          openContextMenu(ev, { path: e.path, type: e.type, name: e.name })
                        }
                      >
                        <MoreHorizontal size={13} className="text-textMuted" />
                      </button>
                    ) : null}
                  </div>
                );
              })
            )}
          </>
        )}
      </div>
    </div>
  );

  return (
    <div
      className={`os-soft-rail os-soft-rail--right ${softVisible ? 'is-open' : ''} ${
        railToggling ? 'is-toggling' : ''
      }`}
      style={{ width: softVisible ? width : 0 }}
      aria-hidden={!softVisible}
    >
    <div
      ref={panelRef}
      className="relative h-full os-depth-card flex flex-col os-soft-rail-inner"
      style={{ width }}
    >
      {/* Left drag handle (widen by dragging left) */}
      <div
        role="separator"
        aria-orientation="vertical"
        onPointerDown={onResizePointerDown}
        onPointerMove={onResizePointerMove}
        onPointerUp={onResizePointerUp}
        className="absolute left-0 top-0 bottom-0 w-1.5 -ml-0.5 cursor-col-resize z-10 hover:bg-primary/30"
      />

      {/* Header — same h-11 band as L1 / session sidebar */}
      <div className="h-11 px-2.5 border-b border-border box-border flex-shrink-0 flex items-center gap-1">
          <div
            className="flex-1 min-w-0 text-[13px] font-medium leading-none text-textMuted truncate"
            title={rootPath || projectLabel || undefined}
          >
            工作区文件
          </div>
          <div className="flex items-center gap-0.5 shrink-0">
            <button
              type="button"
              onClick={() => setShowSearch((v) => !v)}
              className={`p-1.5 rounded-md hover:bg-primary/10 ${
                showSearch ? 'bg-black/[0.06] dark:bg-white/10' : ''
              }`}
              title="搜索"
            >
              <Search size={13} className="text-textMuted" />
            </button>
            <div className="relative" ref={newMenuRef}>
              <button
                type="button"
                onClick={() => {
                  setCtxMenu(null);
                  setNewMenuOpen((v) => !v);
                }}
                disabled={!rootPath}
                className="p-1.5 rounded-md hover:bg-primary/10 disabled:opacity-40"
                title="新建"
              >
                <Plus size={13} className="text-textMuted" />
              </button>
              {newMenuOpen ? (
                <div className="absolute right-0 top-full mt-0.5 z-[70] min-w-[140px] py-1 rounded-lg bg-white dark:bg-[#252526] border border-black/8 dark:border-white/10 shadow-lg text-[12px] os-soft-pop is-open">
                  <button
                    type="button"
                    className="w-full flex items-center gap-2 px-3 py-1.5 text-left hover:bg-primary/10"
                    onClick={() => void startCreate('file')}
                  >
                    <FileText size={12} className="text-neutral-400" />
                    新建文档
                  </button>
                  <button
                    type="button"
                    className="w-full flex items-center gap-2 px-3 py-1.5 text-left hover:bg-primary/10"
                    onClick={() => void startCreate('dir')}
                  >
                    <FolderPlus size={12} className="text-neutral-400" />
                    新建文件夹
                  </button>
                </div>
              ) : null}
            </div>
            <button
              type="button"
              onClick={() => setShowPreview((v) => !v)}
              className={`p-1.5 rounded-md hover:bg-primary/10 ${
                treeOnly ? 'hidden' : ''
              }`}
              title={showPreview ? '隐藏预览' : '显示预览'}
            >
              {showPreview ? (
                <Eye size={13} className="text-textMuted" />
              ) : (
                <EyeOff size={13} className="text-textMuted" />
              )}
            </button>
            <button
              type="button"
              onClick={onClose}
              className="p-1.5 rounded-md hover:bg-primary/10"
              title="关闭"
            >
              <X size={13} className="text-textMuted" />
            </button>
          </div>
      </div>

      <div className="flex-1 min-h-0 flex">
        {listPane}
        {useSplitPreview ? (
          <div className="flex-1 min-w-0 flex flex-col bg-bgLight">
            {activeFile ? (
              <>
                <div className="px-2 py-1.5 border-b border-border flex-shrink-0 flex items-start gap-2">
                  <div className="min-w-0 flex-1">
                    <div className="text-[11px] font-medium text-textMuted font-mono truncate flex items-center gap-1.5">
                      <FileTypeIcon name={basename(activeFile)} type="file" />
                      <span className="truncate">{basename(activeFile)}</span>
                      {diffMeta ? (
                        <span className="text-[10px] font-normal tabular-nums shrink-0">
                          <span className="text-emerald-600/70">+{diffMeta.additions}</span>{' '}
                          <span className="text-rose-500/60">-{diffMeta.deletions}</span>
                        </span>
                      ) : null}
                    </div>
                    <div className="text-[10px] text-textMuted font-mono truncate" title={activeFile}>
                      {activeFile}
                      {fileMeta?.truncated ? ' · 已截断' : ''}
                    </div>
                  </div>
                  {isMarkdownFile(activeFile) && !imageSrc && !fileLoading && !fileError && !diffLines ? (
                    <button
                      type="button"
                      onClick={() => setMdRaw((v) => !v)}
                      className="shrink-0 px-1.5 py-0.5 text-[10px] rounded border border-border/70 text-textMuted hover:bg-primary/10 hover:text-textMain"
                      title={mdRaw ? '渲染预览' : '原始源码'}
                    >
                      {mdRaw ? '预览' : '源码'}
                    </button>
                  ) : null}
                </div>
                {fileLoading ? (
                  <div className="flex-1 flex items-center justify-center text-textMuted text-xs gap-2">
                    <Loader2 size={14} className="animate-spin" /> 加载中…
                  </div>
                ) : fileError ? (
                  <div className="px-3 py-4 text-[11px] text-red-400">{fileError}</div>
                ) : diffLines ? (
                  <UnifiedDiffView
                    fileName={basename(activeFile)}
                    lines={diffLines}
                    additions={diffMeta?.additions}
                    deletions={diffMeta?.deletions}
                    oversized={diffMeta?.oversized}
                    collapseUnmodified={false}
                  />
                ) : imageSrc ? (
                  <ImagePreview
                    src={imageSrc}
                    fileName={basename(activeFile)}
                    size={fileMeta?.size}
                  />
                ) : isImageFile(activeFile) && !fileContent ? (
                  <div className="px-3 py-4 text-[11px] text-textMuted">无图片数据</div>
                ) : isMarkdownFile(activeFile) && !mdRaw ? (
                  <MarkdownPreview content={fileContent} />
                ) : (
                  <CodePreview fileName={activeFile} content={fileContent} />
                )}
              </>
            ) : (
              <div className="flex-1 flex items-center justify-center px-4 text-center text-[11px] text-textMuted/60">
                选择文件以预览
              </div>
            )}
          </div>
        ) : null}
      </div>

      {renderCtxMenu()}
    </div>
    </div>
  );
};

export default ProjectFilesPanel;
