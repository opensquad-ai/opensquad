/**
 * ProjectFilesPanel — Cursor-like project file browser on the right of Agent Web.
 *
 * Enter-directory navigation + breadcrumb + search + highlighted code preview.
 * Root = agent project cwd. Width is drag-resizable (left edge).
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ChevronRight,
  File as FileIcon,
  FileCode2,
  FileJson,
  FileText,
  FileType2,
  Folder,
  Image as ImageIcon,
  Loader2,
  RefreshCw,
  Search,
  Settings2,
  Terminal,
  X,
} from 'lucide-react';
import { adminAPI } from '../../services/api';
import { getLangForFile, highlightLine, HLJS_THEME_CSS } from '../../utils/codeHighlight';
import { AI_MARKDOWN_CLASS, renderFencedMarkdown } from '../../utils/fencedMarkdown';

export type ProjectFileOpenRequest = {
  /** Path relative to project root, or absolute under root */
  path: string;
  nonce: number;
};

type FsEntry = { name: string; type: 'file' | 'dir'; size?: number | null };

interface ProjectFilesPanelProps {
  isOpen: boolean;
  onClose: () => void;
  agentId: string;
  /** Absolute project root (agentCwd || defaultCwd) */
  rootPath: string;
  openRequest?: ProjectFileOpenRequest | null;
  width: number;
  onWidthChange: (w: number) => void;
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
  md: { Icon: FileText, color: 'text-sky-400', label: 'MD' },
  mdx: { Icon: FileText, color: 'text-sky-400', label: 'MDX' },
  markdown: { Icon: FileText, color: 'text-sky-400', label: 'MD' },
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
    return <Folder size={12} className="text-amber-500/80 shrink-0" />;
  }
  const ext = fileExt(name);
  const style = EXT_STYLE[ext] || EXT_STYLE[name.toLowerCase()];
  if (!style) {
    return <FileIcon size={12} className="text-sky-500/70 shrink-0" />;
  }
  const { Icon, color, label } = style;
  // Prefer a compact extension badge when we have a short label (more "后缀" readable).
  if (label && label.length <= 4) {
    return (
      <span
        className={`shrink-0 inline-flex items-center justify-center min-w-[22px] h-[12px] px-0.5 rounded-[2px] text-[8px] font-bold leading-none tracking-tight ${color}`}
        style={{ backgroundColor: 'color-mix(in srgb, currentColor 14%, transparent)' }}
        title={ext || name}
      >
        {label}
      </span>
    );
  }
  return <Icon size={12} className={`${color} shrink-0`} />;
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
}) => {
  const [browsePath, setBrowsePath] = useState('');
  const [entries, setEntries] = useState<FsEntry[]>([]);
  const [listLoading, setListLoading] = useState(false);
  const [listError, setListError] = useState<string | null>(null);
  const [search, setSearch] = useState('');

  const [activeFile, setActiveFile] = useState<string | null>(null);
  const [fileContent, setFileContent] = useState<string>('');
  const [imageSrc, setImageSrc] = useState<string | null>(null);
  const [fileMeta, setFileMeta] = useState<{ truncated?: boolean; path?: string; size?: number; kind?: 'text' | 'image' } | null>(null);
  const [fileLoading, setFileLoading] = useState(false);
  const [fileError, setFileError] = useState<string | null>(null);
  /** For .md: rendered preview vs raw source */
  const [mdRaw, setMdRaw] = useState(false);

  const dragRef = useRef<{ startX: number; startWidth: number } | null>(null);
  const lastOpenNonce = useRef<number>(-1);

  const projectLabel = useMemo(() => {
    if (!rootPath) return '';
    const parts = rootPath.replace(/\\/g, '/').split('/').filter(Boolean);
    return parts[parts.length - 1] || rootPath;
  }, [rootPath]);

  const crumbs = useMemo(() => {
    const parts = browsePath ? browsePath.split('/').filter(Boolean) : [];
    const out: { label: string; path: string }[] = [{ label: projectLabel || 'Project', path: '' }];
    let acc = '';
    for (const part of parts) {
      acc = acc ? `${acc}/${part}` : part;
      out.push({ label: part, path: acc });
    }
    return out;
  }, [browsePath, projectLabel]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return entries;
    return entries.filter((e) => e.name.toLowerCase().includes(q));
  }, [entries, search]);

  const loadList = useCallback(
    async (dir: string) => {
      if (!agentId || !rootPath) return;
      setListLoading(true);
      setListError(null);
      try {
        const resp = await adminAPI.listProjectDir(agentId, dir, rootPath);
        setEntries(resp.entries || []);
        setBrowsePath((resp.path || '').replace(/\\/g, '/'));
      } catch (err: any) {
        setEntries([]);
        setListError(err?.message || 'Failed to list directory');
      } finally {
        setListLoading(false);
      }
    },
    [agentId, rootPath],
  );

  const openFile = useCallback(
    async (relPath: string) => {
      if (!agentId || !relPath || !rootPath) return;
      setActiveFile(relPath);
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
        setActiveFile(resp.path || relPath);
        // Keep list on the parent folder of this file
        const parent = parentRel(resp.path || relPath);
        if (parent !== browsePath) {
          void loadList(parent);
        }
      } catch (err: any) {
        setFileError(err?.message || 'Failed to read file');
      } finally {
        setFileLoading(false);
      }
    },
    [agentId, browsePath, loadList, rootPath],
  );

  // Reset when root / open changes
  useEffect(() => {
    if (!isOpen) return;
    setSearch('');
    setActiveFile(null);
    setFileContent('');
    setImageSrc(null);
    setFileError(null);
    setBrowsePath('');
    // If an external open is pending, skip root list — openFile will list the parent.
    const pendingOpen = !!(openRequest && openRequest.nonce !== lastOpenNonce.current);
    if (rootPath && !pendingOpen) void loadList('');
    else if (!rootPath) {
      setEntries([]);
      setListError(null);
    }
  }, [isOpen, rootPath, agentId]); // eslint-disable-line react-hooks/exhaustive-deps

  // External open request (tool-stream filename click)
  useEffect(() => {
    if (!isOpen || !openRequest || !rootPath) return;
    if (openRequest.nonce === lastOpenNonce.current) return;
    lastOpenNonce.current = openRequest.nonce;
    const rel = toProjectRelative(rootPath, openRequest.path);
    if (!rel) return;
    void openFile(rel);
  }, [isOpen, openRequest, rootPath, openFile]);

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

  if (!isOpen) return null;

  const listPane = (
    <div className="flex flex-col min-h-0 border-r border-border w-[42%] min-w-[120px] max-w-[220px] flex-shrink-0">
      <div className="px-2 py-1.5 border-b border-border flex-shrink-0">
        <div className="relative">
          <Search size={12} className="absolute left-2 top-1/2 -translate-y-1/2 text-textMuted" />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search…"
            className="w-full pl-7 pr-2 py-1 text-[11px] rounded-md bg-black/5 dark:bg-white/5 border border-border/60 text-textMain placeholder:text-textMuted/50 outline-none focus:border-primary/40"
          />
        </div>
      </div>
      <div className="flex-1 min-h-0 overflow-y-auto py-1">
        {!rootPath ? (
          <div className="px-3 py-4 text-[11px] text-textMuted leading-relaxed">
            Choose a project folder in the chat footer to browse files.
          </div>
        ) : listLoading ? (
          <div className="flex items-center gap-2 px-3 py-3 text-[11px] text-textMuted">
            <Loader2 size={12} className="animate-spin" /> Loading…
          </div>
        ) : listError ? (
          <div className="px-3 py-3 text-[11px] text-red-400">{listError}</div>
        ) : filtered.length === 0 ? (
          <div className="px-3 py-3 text-[11px] text-textMuted/60">Empty</div>
        ) : (
          filtered.map((e) => {
            const rel = joinRel(browsePath, e.name);
            const selected = e.type === 'file' && activeFile === rel;
            return (
              <button
                key={`${e.type}:${e.name}`}
                type="button"
                onClick={() => {
                  if (e.type === 'dir') {
                    setSearch('');
                    void loadList(rel);
                  } else {
                    void openFile(rel);
                  }
                }}
                className={`w-full flex items-center gap-1.5 px-2 py-1 text-left text-[11px] truncate hover:bg-primary/10 transition-colors ${
                  selected ? 'bg-primary/15 text-textMain' : 'text-textMuted'
                }`}
                title={e.name}
              >
                {e.type === 'dir' ? (
                  <Folder size={12} className="text-amber-500/80 shrink-0" />
                ) : (
                  <FileTypeIcon name={e.name} type="file" />
                )}
                <span className="truncate font-mono">{e.name}</span>
              </button>
            );
          })
        )}
      </div>
    </div>
  );

  return (
    <div
      className="relative h-full border-l border-border bg-bgLight flex flex-col flex-shrink-0"
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

      <div className="h-11 px-2 border-b border-border box-border flex items-center gap-1 flex-shrink-0">
        <div className="flex-1 min-w-0 flex items-center gap-0.5 overflow-x-auto text-[11px]">
          {crumbs.map((c, i) => (
            <React.Fragment key={c.path || 'root'}>
              {i > 0 ? <ChevronRight size={10} className="text-textMuted/50 shrink-0" /> : null}
              <button
                type="button"
                className={`shrink-0 px-1 py-0.5 rounded hover:bg-primary/10 truncate max-w-[100px] ${
                  i === crumbs.length - 1 ? 'text-textMain font-medium' : 'text-textMuted'
                }`}
                onClick={() => {
                  setSearch('');
                  void loadList(c.path);
                }}
                title={c.path || rootPath}
              >
                {c.label}
              </button>
            </React.Fragment>
          ))}
        </div>
        <button
          type="button"
          onClick={() => void loadList(browsePath)}
          disabled={listLoading || !rootPath}
          className="p-1.5 hover:bg-primary/10 rounded-md"
          title="Refresh"
        >
          <RefreshCw size={13} className={`text-textMuted ${listLoading ? 'animate-spin' : ''}`} />
        </button>
        <button type="button" onClick={onClose} className="p-1.5 hover:bg-primary/10 rounded-md" title="Close">
          <X size={13} className="text-textMuted" />
        </button>
      </div>

      <div className="flex-1 min-h-0 flex">
        {listPane}
        <div className="flex-1 min-w-0 flex flex-col">
          {activeFile ? (
            <>
              <div className="px-2 py-1.5 border-b border-border flex-shrink-0 flex items-start gap-2">
                <div className="min-w-0 flex-1">
                  <div className="text-[11px] font-medium text-textMain font-mono truncate flex items-center gap-1.5">
                    <FileTypeIcon name={basename(activeFile)} type="file" />
                    <span className="truncate">{basename(activeFile)}</span>
                  </div>
                  <div className="text-[10px] text-textMuted font-mono truncate" title={activeFile}>
                    {activeFile}
                    {fileMeta?.truncated ? ' · truncated' : ''}
                  </div>
                </div>
                {isMarkdownFile(activeFile) && !imageSrc && !fileLoading && !fileError ? (
                  <button
                    type="button"
                    onClick={() => setMdRaw((v) => !v)}
                    className="shrink-0 px-1.5 py-0.5 text-[10px] rounded border border-border/70 text-textMuted hover:bg-primary/10 hover:text-textMain"
                    title={mdRaw ? 'Rendered preview' : 'Raw source'}
                  >
                    {mdRaw ? 'Preview' : 'Raw'}
                  </button>
                ) : null}
              </div>
              {fileLoading ? (
                <div className="flex-1 flex items-center justify-center text-textMuted text-xs gap-2">
                  <Loader2 size={14} className="animate-spin" /> Loading…
                </div>
              ) : fileError ? (
                <div className="px-3 py-4 text-[11px] text-red-400">{fileError}</div>
              ) : imageSrc ? (
                <ImagePreview
                  src={imageSrc}
                  fileName={basename(activeFile)}
                  size={fileMeta?.size}
                />
              ) : isImageFile(activeFile) && !fileContent ? (
                <div className="px-3 py-4 text-[11px] text-textMuted">No image data</div>
              ) : isMarkdownFile(activeFile) && !mdRaw ? (
                <MarkdownPreview content={fileContent} />
              ) : (
                <CodePreview fileName={activeFile} content={fileContent} />
              )}
            </>
          ) : (
            <div className="flex-1 flex items-center justify-center px-4 text-center text-[11px] text-textMuted/60">
              Select a file to preview
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default ProjectFilesPanel;
