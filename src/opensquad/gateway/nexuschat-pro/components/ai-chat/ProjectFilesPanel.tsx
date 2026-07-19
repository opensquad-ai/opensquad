/**
 * ProjectFilesPanel — Cursor/Trae-like workspace file explorer on the right of Agent Web.
 *
 * Full project tree loads once (metadata only, ≤10000). Folder expand is local;
 * file contents load only when the user clicks a file.
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
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
  RefreshCw,
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
};

type ChangedEntry = {
  name: string;
  path: string;
  type: 'file' | 'dir';
  status?: string;
  additions?: number;
  deletions?: number;
  oversized?: boolean;
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
  /** Notify parent when session change list refreshes. */
  onSessionChanges?: (summary: {
    additions: number;
    deletions: number;
    count: number;
  }) => void;
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

/** Per-extension icon + color for the file list. */
type ExtStyle = { Icon: React.ComponentType<{ size?: number; className?: string }>; color: string; label?: string };

const EXT_STYLE: Record<string, ExtStyle> = {
  // Languages
  py: { Icon: FileCode2, color: 'text-yellow-500', label: 'PY' },
  pyw: { Icon: FileCode2, color: 'text-yellow-500', label: 'PY' },
  js: { Icon: FileCode2, color: 'text-amber-400', label: 'JS' },
  mjs: { Icon: FileCode2, color: 'text-amber-400', label: 'JS' },
  cjs: { Icon: FileCode2, color: 'text-amber-400', label: 'JS' },
  jsx: { Icon: FileCode2, color: 'text-sky-400', label: 'JSX' },
  ts: { Icon: FileCode2, color: 'text-blue-500', label: 'TS' },
  tsx: { Icon: FileCode2, color: 'text-blue-400', label: 'TSX' },
  vue: { Icon: FileCode2, color: 'text-emerald-500', label: 'VUE' },
  svelte: { Icon: FileCode2, color: 'text-orange-500', label: 'SV' },
  go: { Icon: FileCode2, color: 'text-cyan-500', label: 'GO' },
  rs: { Icon: FileCode2, color: 'text-orange-600', label: 'RS' },
  java: { Icon: FileCode2, color: 'text-red-500', label: 'JV' },
  kt: { Icon: FileCode2, color: 'text-purple-400', label: 'KT' },
  c: { Icon: FileCode2, color: 'text-blue-600', label: 'C' },
  h: { Icon: FileCode2, color: 'text-blue-600', label: 'H' },
  cpp: { Icon: FileCode2, color: 'text-blue-500', label: 'C++' },
  hpp: { Icon: FileCode2, color: 'text-blue-500', label: 'H++' },
  cs: { Icon: FileCode2, color: 'text-violet-500', label: 'CS' },
  rb: { Icon: FileCode2, color: 'text-red-400', label: 'RB' },
  php: { Icon: FileCode2, color: 'text-indigo-400', label: 'PHP' },
  swift: { Icon: FileCode2, color: 'text-orange-400', label: 'SW' },
  // Markup / style
  html: { Icon: FileType2, color: 'text-orange-500', label: 'HTML' },
  htm: { Icon: FileType2, color: 'text-orange-500', label: 'HTML' },
  css: { Icon: FileType2, color: 'text-sky-500', label: 'CSS' },
  scss: { Icon: FileType2, color: 'text-pink-400', label: 'SCSS' },
  less: { Icon: FileType2, color: 'text-indigo-400', label: 'LESS' },
  xml: { Icon: FileType2, color: 'text-orange-400', label: 'XML' },
  svg: { Icon: ImageIcon, color: 'text-pink-400', label: 'SVG' },
  // Data / config
  json: { Icon: FileJson, color: 'text-amber-500', label: 'JSON' },
  jsonc: { Icon: FileJson, color: 'text-amber-500', label: 'JSON' },
  yaml: { Icon: Settings2, color: 'text-rose-400', label: 'YML' },
  yml: { Icon: Settings2, color: 'text-rose-400', label: 'YML' },
  toml: { Icon: Settings2, color: 'text-slate-400', label: 'TOML' },
  ini: { Icon: Settings2, color: 'text-slate-400', label: 'INI' },
  cfg: { Icon: Settings2, color: 'text-slate-400', label: 'CFG' },
  conf: { Icon: Settings2, color: 'text-slate-400', label: 'CFG' },
  env: { Icon: Settings2, color: 'text-lime-500', label: 'ENV' },
  // Docs
  md: { Icon: FileText, color: 'text-sky-500', label: 'MD' },
  mdx: { Icon: FileText, color: 'text-sky-500', label: 'MDX' },
  markdown: { Icon: FileText, color: 'text-sky-500', label: 'MD' },
  txt: { Icon: FileText, color: 'text-textMuted', label: 'TXT' },
  csv: { Icon: FileText, color: 'text-emerald-400', label: 'CSV' },
  log: { Icon: FileText, color: 'text-textMuted', label: 'LOG' },
  // Shell
  sh: { Icon: Terminal, color: 'text-green-500', label: 'SH' },
  bash: { Icon: Terminal, color: 'text-green-500', label: 'SH' },
  zsh: { Icon: Terminal, color: 'text-green-500', label: 'SH' },
  ps1: { Icon: Terminal, color: 'text-blue-400', label: 'PS' },
  bat: { Icon: Terminal, color: 'text-textMuted', label: 'BAT' },
  cmd: { Icon: Terminal, color: 'text-textMuted', label: 'CMD' },
  // Images
  png: { Icon: ImageIcon, color: 'text-fuchsia-400', label: 'PNG' },
  jpg: { Icon: ImageIcon, color: 'text-fuchsia-400', label: 'JPG' },
  jpeg: { Icon: ImageIcon, color: 'text-fuchsia-400', label: 'JPG' },
  gif: { Icon: ImageIcon, color: 'text-fuchsia-400', label: 'GIF' },
  webp: { Icon: ImageIcon, color: 'text-fuchsia-400', label: 'WEBP' },
  bmp: { Icon: ImageIcon, color: 'text-fuchsia-400', label: 'BMP' },
  ico: { Icon: ImageIcon, color: 'text-fuchsia-400', label: 'ICO' },
  // Dotfiles
  gitignore: { Icon: Settings2, color: 'text-orange-400', label: 'GIT' },
  dockerfile: { Icon: Settings2, color: 'text-blue-400', label: 'DKR' },
  editorconfig: { Icon: Settings2, color: 'text-textMuted', label: 'EDC' },
};

const FileTypeIcon: React.FC<{ name: string; type: 'file' | 'dir' }> = ({ name, type }) => {
  if (type === 'dir') {
    return <Folder size={14} className="text-amber-500 shrink-0" strokeWidth={1.75} />;
  }
  const ext = fileExt(name);
  // HTML: orange badge (reference style)
  if (ext === 'html' || ext === 'htm') {
    return (
      <span
        className="shrink-0 inline-flex items-center justify-center min-w-[26px] h-[14px] px-0.5 rounded-[3px] text-[8px] font-bold leading-none tracking-tight text-orange-500"
        style={{ backgroundColor: 'color-mix(in srgb, #f97316 16%, transparent)' }}
        title={ext}
      >
        HTML
      </span>
    );
  }
  // MD: blue FileText
  if (ext === 'md' || ext === 'mdx' || ext === 'markdown') {
    return <FileText size={14} className="text-sky-500 shrink-0" strokeWidth={1.75} />;
  }
  const style = EXT_STYLE[ext] || EXT_STYLE[name.toLowerCase()];
  if (!style) {
    return <FileIcon size={14} className="text-sky-500/70 shrink-0" strokeWidth={1.75} />;
  }
  const { Icon, color, label } = style;
  if (label && label.length <= 4) {
    return (
      <span
        className={`shrink-0 inline-flex items-center justify-center min-w-[22px] h-[13px] px-0.5 rounded-[2px] text-[8px] font-bold leading-none tracking-tight ${color}`}
        style={{ backgroundColor: 'color-mix(in srgb, currentColor 14%, transparent)' }}
        title={ext || name}
      >
        {label}
      </span>
    );
  }
  return <Icon size={14} className={`${color} shrink-0`} />;
};

const CodePreview: React.FC<{ fileName: string; content: string }> = ({ fileName, content }) => {
  const lang = useMemo(() => getLangForFile(fileName), [fileName]);
  const lines = useMemo(() => content.split('\n'), [content]);
  return (
    <div className="flex-1 min-h-0 overflow-auto bg-[#0d1117] font-mono text-[11px] leading-5">
      <style>{HLJS_THEME_CSS}</style>
      <div className="min-w-full inline-block">
        {lines.map((line, i) => (
          <div key={i} className="flex items-start hover:bg-white/[0.03]">
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
  onSessionChanges,
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
  const onSessionChangesRef = useRef(onSessionChanges);
  useEffect(() => {
    onSessionChangesRef.current = onSessionChanges;
  }, [onSessionChanges]);
  const inlineDiffByPathRef = useRef(inlineDiffByPath);
  useEffect(() => {
    inlineDiffByPathRef.current = inlineDiffByPath;
  }, [inlineDiffByPath]);
  const expandedChangedRef = useRef(expandedChanged);
  useEffect(() => {
    expandedChangedRef.current = expandedChanged;
  }, [expandedChanged]);

  const [activeFile, setActiveFile] = useState<string | null>(null);
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

  const childrenMap = useMemo(() => buildChildrenMap(treeEntries), [treeEntries]);

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

  const closeMenus = useCallback(() => {
    setCtxMenu(null);
    setNewMenuOpen(false);
  }, []);

  const loadTree = useCallback(async () => {
    if (!agentId || !rootPath) return;
    setListLoading(true);
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
      // Expand top-level dirs by default for a useful first glance
      const topDirs = entries.filter((e) => e.type === 'dir' && !e.path.includes('/')).map((e) => e.path);
      setExpanded(new Set(topDirs.slice(0, 12)));
    } catch (err: any) {
      setTreeEntries([]);
      setTreeTruncated(false);
      setTreeCount(0);
      setListError(err?.message || '无法加载项目文件树');
    } finally {
      setListLoading(false);
    }
  }, [agentId, rootPath]);

  const toggleExpand = useCallback((dirPath: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(dirPath)) next.delete(dirPath);
      else next.add(dirPath);
      return next;
    });
  }, []);

  const expandToPath = useCallback((relPath: string) => {
    const ancestors = ancestorPaths(relPath);
    if (ancestors.length === 0) return;
    setExpanded((prev) => {
      const next = new Set(prev);
      for (const a of ancestors) next.add(a);
      return next;
    });
  }, []);

  const loadChanged = useCallback(async () => {
    if (!agentId || !rootPath) return;
    setChangedLoading(true);
    setChangedError(null);
    // Drop cached diffs so refresh always reflects disk (e.g. after CMD edits)
    setInlineDiffByPath({});
    inlineDiffByPathRef.current = {};
    try {
      const resp = await adminAPI.listSessionChanges(agentId, rootPath);
      const files = (resp.files || resp.entries || []).map((e) => ({
        name: e.name,
        path: e.path,
        type: e.type,
        status: e.status,
        additions: e.additions,
        deletions: e.deletions,
        oversized: e.oversized,
      }));
      setChangedEntries(files);
      onSessionChangesRef.current?.({
        additions: resp.additions || 0,
        deletions: resp.deletions || 0,
        count: resp.count ?? files.length,
      });
      if (files.length === 0) {
        setChangedError(null);
      }
      // Re-fetch diffs for rows that are still expanded
      const stillExpanded = [...expandedChangedRef.current].filter((p) =>
        files.some((f) => f.path === p),
      );
      for (const p of stillExpanded) {
        void (async () => {
          setInlineDiffLoading(p);
          try {
            const d = await adminAPI.getSessionDiff(agentId, p, rootPath);
            setInlineDiffByPath((prev) => ({
              ...prev,
              [p]: {
                lines: d.lines || [],
                additions: d.additions || 0,
                deletions: d.deletions || 0,
                oversized: d.oversized,
              },
            }));
          } catch {
            /* keep empty until user re-expands */
          } finally {
            setInlineDiffLoading((cur) => (cur === p ? null : cur));
          }
        })();
      }
    } catch (err: any) {
      setChangedEntries([]);
      setChangedError(err?.message || '无法加载变动文件');
      onSessionChangesRef.current?.({ additions: 0, deletions: 0, count: 0 });
    } finally {
      setChangedLoading(false);
    }
  }, [agentId, rootPath]);

  const refreshCurrent = useCallback(() => {
    if (tab === 'changed') void loadChanged();
    else void loadTree();
  }, [tab, loadChanged, loadTree]);

  const openDiff = useCallback(
    async (relPath: string) => {
      if (!agentId || !relPath || !rootPath) return;
      setActiveFile(relPath);
      setShowPreview(true);
      setFileLoading(true);
      setFileError(null);
      setFileContent('');
      setImageSrc(null);
      setFileMeta(null);
      setDiffLines(null);
      setDiffMeta(null);
      setMdRaw(false);
      try {
        const resp = await adminAPI.getSessionDiff(agentId, relPath, rootPath);
        setDiffLines(resp.lines || []);
        setDiffMeta({
          additions: resp.additions || 0,
          deletions: resp.deletions || 0,
          oversized: resp.oversized,
          status: resp.status,
        });
        setActiveFile(resp.path || relPath);
      } catch (err: any) {
        setFileError(err?.message || '无法加载 diff');
      } finally {
        setFileLoading(false);
      }
    },
    [agentId, rootPath],
  );

  const openFile = useCallback(
    async (relPath: string) => {
      if (!agentId || !relPath || !rootPath) return;
      setShowPreview(true);
      setDiffLines(null);
      setDiffMeta(null);
      setActiveFile(relPath);
      setBrowsePath(parentRel(relPath));
      expandToPath(relPath);
      setFileLoading(true);
      setFileError(null);
      setFileContent('');
      setImageSrc(null);
      setFileMeta(null);
      setMdRaw(false);
      try {
        const resp = await adminAPI.readProjectFile(agentId, relPath, rootPath);
        const kind = resp.kind === 'image' || (resp.content_base64 && resp.mime?.startsWith('image/'))
          ? 'image'
          : 'text';
        if (kind === 'image' && resp.content_base64 && resp.mime) {
          setImageSrc(`data:${resp.mime};base64,${resp.content_base64}`);
          setFileContent('');
        } else {
          setImageSrc(null);
          setFileContent(resp.content ?? '');
        }
        setFileMeta({
          truncated: resp.truncated,
          path: resp.path,
          size: resp.size,
          kind,
        });
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
    [agentId, rootPath, expandToPath],
  );

  // Reset when root / open changes — load full tree once
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
    if (rootPath) {
      void loadTree();
      if (tab === 'changed') void loadChanged();
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
    void openFile(rel);
  }, [isOpen, openRequest, rootPath, openFile]);

  // Load changed when switching to that tab
  useEffect(() => {
    if (!isOpen || !rootPath || tab !== 'changed') return;
    void loadChanged();
  }, [isOpen, rootPath, tab, loadChanged]);

  // Changes bar → force changed tab + refresh
  useEffect(() => {
    if (!focusChangedNonce || !isOpen) return;
    setTab('changed');
    setShowPreview(false);
    void loadChanged();
  }, [focusChangedNonce]); // eslint-disable-line react-hooks/exhaustive-deps

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
      // Always fetch fresh disk diff on expand (CMD/external edits)
      setInlineDiffLoading(relPath);
      try {
        const resp = await adminAPI.getSessionDiff(agentId, relPath, rootPath);
        setInlineDiffByPath((prev) => ({
          ...prev,
          [relPath]: {
            lines: resp.lines || [],
            additions: resp.additions || 0,
            deletions: resp.deletions || 0,
            oversized: resp.oversized,
          },
        }));
      } catch {
        setInlineDiffByPath((prev) => ({
          ...prev,
          [relPath]: { lines: [], additions: 0, deletions: 0 },
        }));
      } finally {
        setInlineDiffLoading((cur) => (cur === relPath ? null : cur));
      }
    },
    [agentId, rootPath],
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
        await loadChanged();
      } catch (err: any) {
        setChangedError(err?.message || '撤回失败');
      } finally {
        setRevertingPath(null);
      }
    },
    [agentId, rootPath, activeFile, loadChanged],
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

  if (!isOpen) return null;

  const canCreateHere = tab === 'all';

  const renderCtxMenu = () => {
    if (!ctxMenu) return null;
    const { target } = ctxMenu;
    const isRoot = target.type === 'root';
    const showNew = isRoot || target.type === 'dir';
    return (
      <div
        ref={ctxMenuRef}
        className="fixed z-[80] min-w-[168px] py-1 rounded-lg bg-white dark:bg-[#252526] border border-black/8 dark:border-white/10 shadow-lg text-[12px] text-textMain"
        style={{ left: ctxMenu.x, top: ctxMenu.y }}
        onContextMenu={(e) => e.preventDefault()}
      >
        {showNew ? (
          <>
            <button
              type="button"
              className="w-full px-3 py-1.5 text-left hover:bg-black/[0.04] dark:hover:bg-white/10"
              onClick={() => void startCreate('file', isRoot ? browsePath : target.path)}
            >
              新建文档
            </button>
            <button
              type="button"
              className="w-full px-3 py-1.5 text-left hover:bg-black/[0.04] dark:hover:bg-white/10"
              onClick={() => void startCreate('dir', isRoot ? browsePath : target.path)}
            >
              新建文件夹
            </button>
            <div className="my-1 h-px bg-black/8 dark:bg-white/10" />
          </>
        ) : null}
        <button
          type="button"
          className="w-full px-3 py-1.5 text-left hover:bg-black/[0.04] dark:hover:bg-white/10"
          onClick={() => void doReveal(target.path)}
        >
          打开所在目录
        </button>
        <button
          type="button"
          className="w-full px-3 py-1.5 text-left hover:bg-black/[0.04] dark:hover:bg-white/10"
          onClick={() => void doTerminal(target)}
        >
          在终端中打开
        </button>
        <button
          type="button"
          className="w-full px-3 py-1.5 text-left hover:bg-black/[0.04] dark:hover:bg-white/10"
          onClick={() => void doCopyPath(target.path)}
        >
          复制路径
        </button>
        {!isRoot ? (
          <>
            <div className="my-1 h-px bg-black/8 dark:bg-white/10" />
            <button
              type="button"
              className="w-full px-3 py-1.5 text-left hover:bg-black/[0.04] dark:hover:bg-white/10"
              onClick={() => startRename(target.path)}
            >
              重命名
            </button>
            <button
              type="button"
              className="w-full px-3 py-1.5 text-left hover:bg-black/[0.04] dark:hover:bg-white/10 text-red-500"
              onClick={() => void doDelete(target.path)}
            >
              删除
            </button>
          </>
        ) : null}
      </div>
    );
  };

  const useSplitPreview = showPreview && tab !== 'changed';

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
              className="w-full pl-7 pr-2 py-1 text-[11px] rounded-md bg-black/[0.03] dark:bg-white/5 border border-border/60 text-textMain placeholder:text-textMuted/50 outline-none focus:border-primary/40"
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
                : 'text-textMuted hover:text-textMain'
            }`}
          >
            {t.label}
            {tab === t.id ? (
              <span className="absolute left-2 right-2 bottom-0 h-[2px] rounded-full bg-primary" />
            ) : null}
          </button>
        ))}
      </div>

      {/* Tree status — all-files only */}
      {tab === 'all' && rootPath && !listLoading ? (
        <div className="px-2.5 py-1 border-b border-border/50 text-[10px] text-textMuted/70 flex-shrink-0 flex items-center gap-2">
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
        className="flex-1 min-h-0 overflow-y-auto py-0.5"
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
              {changedLoading ? (
                <div className="flex items-center gap-1.5 px-2 py-1 text-[10px] text-textMuted border-b border-border/50">
                  <Loader2 size={10} className="animate-spin" /> 刷新中…
                </div>
              ) : null}
              {filteredChanged.map((e) => {
                const isOpenRow = expandedChanged.has(e.path);
                const inline = inlineDiffByPath[e.path];
                const isDiffLoading = inlineDiffLoading === e.path;
                const isReverting = revertingPath === e.path;
                return (
                  <div key={`ch:${e.path}`} className="border-b border-border/40 last:border-b-0">
                    <div
                      className={`group flex items-center gap-1 px-1.5 py-[5px] text-[11px] ${
                        isOpenRow
                          ? 'bg-black/[0.05] dark:bg-white/[0.08] text-textMain'
                          : 'text-textMuted hover:bg-black/[0.04] dark:hover:bg-white/[0.06] hover:text-textMain'
                      }`}
                      onContextMenu={(ev) =>
                        openContextMenu(ev, { path: e.path, type: e.type, name: e.name })
                      }
                    >
                      <button
                        type="button"
                        className="p-0.5 rounded shrink-0 text-textMuted hover:text-textMain"
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
                              ? 'text-emerald-600 dark:text-emerald-400'
                              : e.status === 'D'
                                ? 'text-rose-500/90'
                                : 'text-textMain'
                          }
                        >
                          {e.path}
                        </span>
                      </button>
                      <span className="flex items-center gap-1 shrink-0 text-[10px] tabular-nums font-mono">
                        {(e.additions || 0) > 0 ? (
                          <span className="text-emerald-500">+{e.additions}</span>
                        ) : null}
                        {(e.deletions || 0) > 0 ? (
                          <span className="text-rose-400">-{e.deletions}</span>
                        ) : (
                          (e.additions || 0) === 0 ? (
                            <span className="text-textMuted/50">+0</span>
                          ) : null
                        )}
                      </span>
                      <button
                        type="button"
                        className="opacity-0 group-hover:opacity-100 p-0.5 rounded hover:bg-black/10 dark:hover:bg-white/10 shrink-0 disabled:opacity-40"
                        title="撤回此文件"
                        disabled={isReverting}
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
                        className="opacity-0 group-hover:opacity-100 p-0.5 rounded hover:bg-black/10 dark:hover:bg-white/10 shrink-0"
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
                        {isDiffLoading ? (
                          <div className="flex items-center gap-2 px-3 py-3 text-[11px] text-textMuted">
                            <Loader2 size={12} className="animate-spin" /> 加载 diff…
                          </div>
                        ) : inline ? (
                          <UnifiedDiffView
                            fileName={e.name}
                            lines={inline.lines}
                            additions={inline.additions}
                            deletions={inline.deletions}
                            oversized={inline.oversized}
                          />
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
        ) : listLoading ? (
          <div className="flex items-center gap-2 px-3 py-3 text-[11px] text-textMuted">
            <Loader2 size={12} className="animate-spin" /> 正在加载文件树…
          </div>
        ) : listError ? (
          <div className="px-3 py-3 text-[11px] text-red-400">{listError}</div>
        ) : (
          <>
            {inlineCreate && canCreateHere ? (
              <div
                className="flex items-center gap-1.5 px-2 py-[5px]"
                style={{ paddingLeft: 8 + ((createUnder || '').split('/').filter(Boolean).length) * 12 }}
              >
                {inlineCreate.kind === 'dir' ? (
                  <Folder size={14} className="text-amber-500 shrink-0" strokeWidth={1.75} />
                ) : (
                  <FileText size={14} className="text-sky-500 shrink-0" strokeWidth={1.75} />
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
                return (
                  <div
                    key={`${e.type}:${e.path}`}
                    className={`group relative flex items-center gap-0.5 pr-1 py-[4px] text-[11px] ${
                      selected
                        ? 'bg-black/[0.06] dark:bg-white/10 text-textMain'
                        : 'text-textMuted hover:bg-black/[0.04] dark:hover:bg-white/[0.06] hover:text-textMain'
                    }`}
                    style={{ paddingLeft: 6 + e.depth * 12 }}
                    onContextMenu={(ev) =>
                      openContextMenu(ev, { path: e.path, type: e.type, name: e.name })
                    }
                  >
                    {e.type === 'dir' ? (
                      <button
                        type="button"
                        className="w-4 h-4 flex items-center justify-center shrink-0 border-0 bg-transparent p-0 cursor-pointer text-textMuted/70"
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
                        className="flex-1 min-w-0 ml-1 text-left truncate font-mono border-0 bg-transparent p-0 cursor-pointer text-inherit"
                        title={e.path + (e.skipped ? '（未展开深层）' : '')}
                        onClick={() => {
                          if (e.type === 'dir') {
                            setBrowsePath(e.path);
                            if (!e.skipped) toggleExpand(e.path);
                          } else {
                            void openFile(e.path);
                          }
                        }}
                      >
                        {e.name}
                        {e.skipped ? (
                          <span className="ml-1 text-[9px] opacity-50">…</span>
                        ) : null}
                      </button>
                    )}
                    {!isRenaming ? (
                      <button
                        type="button"
                        className="opacity-0 group-hover:opacity-100 p-0.5 rounded hover:bg-black/10 dark:hover:bg-white/10 shrink-0 border-0 bg-transparent cursor-pointer"
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
      ref={panelRef}
      className="relative h-full border-l border-border bg-[#f8f8f8] dark:bg-bgLight flex flex-col flex-shrink-0"
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

      {/* Header */}
      <div className="px-2.5 pt-2.5 pb-1.5 border-b border-border box-border flex-shrink-0">
        <div className="flex items-start gap-1">
          <div className="flex-1 min-w-0">
            <div className="text-[13px] font-semibold text-textMain leading-tight">工作区文件</div>
            <div className="text-[10px] text-textMuted truncate mt-0.5" title={rootPath || undefined}>
              {projectLabel}
            </div>
          </div>
          <div className="flex items-center gap-0.5 shrink-0">
            <button
              type="button"
              onClick={() => setShowSearch((v) => !v)}
              className={`p-1.5 rounded-md hover:bg-black/[0.05] dark:hover:bg-white/10 ${
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
                className="p-1.5 rounded-md hover:bg-black/[0.05] dark:hover:bg-white/10 disabled:opacity-40"
                title="新建"
              >
                <Plus size={13} className="text-textMuted" />
              </button>
              {newMenuOpen ? (
                <div className="absolute right-0 top-full mt-0.5 z-[70] min-w-[140px] py-1 rounded-lg bg-white dark:bg-[#252526] border border-black/8 dark:border-white/10 shadow-lg text-[12px]">
                  <button
                    type="button"
                    className="w-full flex items-center gap-2 px-3 py-1.5 text-left hover:bg-black/[0.04] dark:hover:bg-white/10"
                    onClick={() => void startCreate('file')}
                  >
                    <FileText size={12} className="text-sky-500" />
                    新建文档
                  </button>
                  <button
                    type="button"
                    className="w-full flex items-center gap-2 px-3 py-1.5 text-left hover:bg-black/[0.04] dark:hover:bg-white/10"
                    onClick={() => void startCreate('dir')}
                  >
                    <FolderPlus size={12} className="text-amber-500" />
                    新建文件夹
                  </button>
                </div>
              ) : null}
            </div>
            <button
              type="button"
              onClick={() => setShowPreview((v) => !v)}
              className="p-1.5 rounded-md hover:bg-black/[0.05] dark:hover:bg-white/10"
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
              onClick={() => refreshCurrent()}
              disabled={listLoading || changedLoading || !rootPath}
              className="p-1.5 rounded-md hover:bg-black/[0.05] dark:hover:bg-white/10 disabled:opacity-40"
              title="刷新"
            >
              <RefreshCw
                size={13}
                className={`text-textMuted ${listLoading || changedLoading ? 'animate-spin' : ''}`}
              />
            </button>
            <button
              type="button"
              onClick={onClose}
              className="p-1.5 rounded-md hover:bg-black/[0.05] dark:hover:bg-white/10"
              title="关闭"
            >
              <X size={13} className="text-textMuted" />
            </button>
          </div>
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
                    <div className="text-[11px] font-medium text-textMain font-mono truncate flex items-center gap-1.5">
                      <FileTypeIcon name={basename(activeFile)} type="file" />
                      <span className="truncate">{basename(activeFile)}</span>
                      {diffMeta ? (
                        <span className="text-[10px] font-normal tabular-nums shrink-0">
                          <span className="text-emerald-500">+{diffMeta.additions}</span>{' '}
                          <span className="text-rose-400">-{diffMeta.deletions}</span>
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
                      className="shrink-0 px-1.5 py-0.5 text-[10px] rounded border border-border/70 text-textMuted hover:bg-black/[0.04] dark:hover:bg-white/10 hover:text-textMain"
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
  );
};

export default ProjectFilesPanel;
