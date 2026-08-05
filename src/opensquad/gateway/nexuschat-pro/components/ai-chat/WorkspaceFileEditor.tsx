/**
 * WorkspaceFileEditor — center-pane editor for an L2 file tab.
 * Cache-first paint + soft revalidate (no loading flash on switch).
 */
import React, { lazy, Suspense, useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Eye,
  FileCode2,
  FileText,
  Loader2,
  Pencil,
  Save,
} from 'lucide-react';
import { adminAPI } from '../../services/api';
import i18n from '../../i18n';
import type { FileDocMode } from './FileDocumentEditor';
import { UnifiedDiffView, type DiffLine } from './UnifiedDiffView';
import { fillDiffCollapseHidden, flattenDiffCollapses } from './fillDiffCollapseHidden';

// TipTap/ProseMirror is heavy. The editor is only needed when a file tab is
// actually opened — loading it lazily keeps it out of the first-paint bundle
// (refresh / chat hydrate must not wait on the editor chunk). FileDocumentEditor
// is a named export, so map it onto .default for React.lazy.
const FileDocumentEditor = lazy(() =>
  import('./FileDocumentEditor').then((m) => ({ default: m.FileDocumentEditor })),
);

type FileViewMode = FileDocMode | 'diff';

type CacheEntry = {
  content: string;
  imageSrc: string | null;
  meta: { truncated?: boolean; size?: number };
  at: number;
};

/** Shared across tab switches so revisiting a file paints instantly. */
const fileCache = new Map<string, CacheEntry>();
const CACHE_MAX = 80;

function cacheKey(agentId: string, rootPath: string, relPath: string): string {
  return `${agentId}\0${rootPath.replace(/\\/g, '/')}\0${relPath.replace(/\\/g, '/')}`;
}

function putCache(key: string, entry: CacheEntry) {
  fileCache.set(key, entry);
  if (fileCache.size <= CACHE_MAX) return;
  const oldest = [...fileCache.entries()].sort((a, b) => a[1].at - b[1].at);
  const drop = fileCache.size - CACHE_MAX;
  for (let i = 0; i < drop; i++) fileCache.delete(oldest[i][0]);
}

/** Read shared editor cache (used by ProjectFilesPanel hover prefetch). */
export function getWorkspaceFileCache(
  agentId: string,
  rootPath: string,
  relPath: string,
): CacheEntry | null {
  const p = (relPath || '').replace(/\\/g, '/');
  if (!agentId || !rootPath || !p) return null;
  return fileCache.get(cacheKey(agentId, rootPath, p)) || null;
}

/** Write shared editor cache so center-pane open can paint without a spinner. */
export function putWorkspaceFileCache(
  agentId: string,
  rootPath: string,
  relPath: string,
  entry: {
    content: string;
    imageSrc?: string | null;
    meta?: { truncated?: boolean; size?: number };
  },
): void {
  const p = (relPath || '').replace(/\\/g, '/');
  if (!agentId || !rootPath || !p) return;
  putCache(cacheKey(agentId, rootPath, p), {
    content: entry.content ?? '',
    imageSrc: entry.imageSrc ?? null,
    meta: entry.meta ?? {},
    at: Date.now(),
  });
}

function basename(path: string): string {
  const p = path.replace(/\\/g, '/');
  const i = p.lastIndexOf('/');
  return i < 0 ? p : p.slice(i + 1);
}

function isMarkdownFile(name: string): boolean {
  return /\.(md|markdown|mdx)$/i.test(name);
}

function isImageFile(name: string): boolean {
  return /\.(png|jpe?g|gif|webp|bmp|svg|ico)$/i.test(name);
}

function defaultViewMode(relPath: string): FileViewMode {
  return isMarkdownFile(relPath) ? 'preview' : 'source';
}

export interface WorkspaceFileEditorProps {
  agentId: string;
  rootPath: string;
  relPath: string;
  onDirtyChange?: (dirty: boolean) => void;
}

export const WorkspaceFileEditor: React.FC<WorkspaceFileEditorProps> = ({
  agentId,
  rootPath,
  relPath,
  onDirtyChange,
}) => {
  const { t } = useTranslation();
  const ck = cacheKey(agentId, rootPath, relPath);
  const initial = fileCache.get(ck);

  const [fileContent, setFileContent] = useState(initial?.content ?? '');
  const [draftContent, setDraftContent] = useState(initial?.content ?? '');
  const [imageSrc, setImageSrc] = useState<string | null>(initial?.imageSrc ?? null);
  const [fileMeta, setFileMeta] = useState<{ truncated?: boolean; size?: number } | null>(
    initial?.meta ?? null,
  );
  /** Only true when we have nothing to show yet (no cache). */
  const [loading, setLoading] = useState(!initial);
  const [error, setError] = useState<string | null>(null);
  const [viewMode, setViewMode] = useState<FileViewMode>(defaultViewMode(relPath));
  const [saving, setSaving] = useState(false);
  const [diffLines, setDiffLines] = useState<DiffLine[] | null>(null);
  const [diffMeta, setDiffMeta] = useState<{ additions: number; deletions: number } | null>(null);
  const loadGen = useRef(0);
  const dirtyRef = useRef(false);
  /** Delay spinner so fast cache/network switches never flash「加载中」. */
  const [showSpinner, setShowSpinner] = useState(false);

  useEffect(() => {
    if (!loading) {
      setShowSpinner(false);
      return;
    }
    // Prefer silent wait: hover-prefetch / open race often finishes <400ms.
    const t = window.setTimeout(() => setShowSpinner(true), 450);
    return () => window.clearTimeout(t);
  }, [loading]);

  const dirty = !imageSrc && draftContent !== fileContent;
  dirtyRef.current = dirty;
  useEffect(() => {
    onDirtyChange?.(dirty);
  }, [dirty, onDirtyChange]);

  const applyEntry = useCallback((entry: CacheEntry, opts?: { keepDirtyDraft?: boolean }) => {
    if (entry.imageSrc) {
      setImageSrc(entry.imageSrc);
      setFileContent('');
      setDraftContent('');
    } else {
      setImageSrc(null);
      setFileContent(entry.content);
      if (!opts?.keepDirtyDraft || !dirtyRef.current) {
        setDraftContent(entry.content);
      }
    }
    setFileMeta(entry.meta);
  }, []);

  const fetchAndCache = useCallback(async (): Promise<CacheEntry | null> => {
    const resp = await adminAPI.readProjectFile(agentId, relPath, rootPath);
    const kind =
      resp.kind === 'image' || (resp.content_base64 && resp.mime?.startsWith('image/'))
        ? 'image'
        : 'text';
    const nextImageSrc =
      kind === 'image' && resp.content_base64 && resp.mime
        ? `data:${resp.mime};base64,${resp.content_base64}`
        : null;
    const content = kind === 'image' ? '' : (resp.content ?? '');
    const entry: CacheEntry = {
      content,
      imageSrc: nextImageSrc,
      meta: { truncated: resp.truncated, size: resp.size },
      at: Date.now(),
    };
    putCache(ck, entry);
    const alt = resp.path && resp.path !== relPath ? cacheKey(agentId, rootPath, resp.path) : null;
    if (alt) putCache(alt, entry);
    return entry;
  }, [agentId, rootPath, relPath, ck]);

  useLayoutEffect(() => {
    if (!agentId || !rootPath || !relPath) return;
    setError(null);
    setDiffLines(null);
    setDiffMeta(null);
    setViewMode(defaultViewMode(relPath));
    const cached = fileCache.get(ck);
    if (cached) {
      setLoading(false);
      applyEntry(cached);
    } else {
      setLoading(true);
      setImageSrc(null);
      setFileContent('');
      setDraftContent('');
      setFileMeta(null);
    }
  }, [agentId, rootPath, relPath, ck, applyEntry]);

  useEffect(() => {
    if (!agentId || !rootPath || !relPath) return;
    const gen = ++loadGen.current;
    const cached = fileCache.get(ck);

    if (cached) {
      // Soft revalidate in background — no loading flash
      void (async () => {
        try {
          const fresh = await fetchAndCache();
          if (gen !== loadGen.current || !fresh) return;
          applyEntry(fresh, { keepDirtyDraft: true });
        } catch {
          /* keep cached */
        }
      })();
      return;
    }

    void (async () => {
      try {
        const entry = await fetchAndCache();
        if (gen !== loadGen.current || !entry) return;
        applyEntry(entry);
      } catch (err: any) {
        if (gen !== loadGen.current) return;
        setError(err?.message || t('workspaceEditor.loadFailed'));
      } finally {
        if (gen === loadGen.current) setLoading(false);
      }
    })();
  }, [agentId, rootPath, relPath, ck, applyEntry, fetchAndCache]);

  const loadDiff = useCallback(async () => {
    if (!agentId || !rootPath || !relPath) return;
    try {
      const d = await adminAPI.getSessionDiff(agentId, relPath, rootPath, { collapse: false });
      const raw = (d.lines || []) as DiffLine[];
      const filled = fillDiffCollapseHidden(raw, draftContent || fileContent) as DiffLine[];
      const lines = flattenDiffCollapses(filled) as DiffLine[];
      setDiffLines(lines);
      setDiffMeta({ additions: d.additions || 0, deletions: d.deletions || 0 });
      setViewMode('diff');
    } catch {
      setDiffLines([]);
      setDiffMeta({ additions: 0, deletions: 0 });
      setViewMode('diff');
    }
  }, [agentId, rootPath, relPath, draftContent, fileContent]);

  const save = useCallback(async () => {
    if (!agentId || !rootPath || !relPath || !dirty || fileMeta?.truncated) return;
    setSaving(true);
    try {
      await adminAPI.writeProjectFile(agentId, relPath, draftContent, rootPath);
      setFileContent(draftContent);
      putCache(ck, {
        content: draftContent,
        imageSrc: null,
        meta: { truncated: fileMeta?.truncated, size: fileMeta?.size },
        at: Date.now(),
      });
    } catch (err: any) {
      alert(err?.message || t('workspaceEditor.saveFailed'));
    } finally {
      setSaving(false);
    }
  }, [agentId, rootPath, relPath, dirty, draftContent, fileMeta, ck, t]);

  useEffect(() => {
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      if (!dirty) return;
      e.preventDefault();
      e.returnValue = '';
    };
    window.addEventListener('beforeunload', onBeforeUnload);
    return () => window.removeEventListener('beforeunload', onBeforeUnload);
  }, [dirty]);

  return (
    <div className="flex-1 min-w-0 min-h-0 flex flex-col bg-bgLight" data-dirty={dirty ? '1' : '0'}>
      <div className="px-3 py-2 border-b border-border flex-shrink-0 flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <div className="text-[12px] font-medium text-textMain font-mono truncate flex items-center gap-1.5">
            <span className="truncate">{basename(relPath)}</span>
            {diffMeta ? (
              <span className="text-[10px] font-normal tabular-nums shrink-0">
                <span className="text-emerald-500">+{diffMeta.additions}</span>{' '}
                <span className="text-rose-400">-{diffMeta.deletions}</span>
              </span>
            ) : null}
            {dirty ? (
              <span className="text-[9px] text-amber-600 dark:text-amber-400 shrink-0">{t('workspaceEditor.unsaved')}</span>
            ) : null}
          </div>
          <div className="text-[10px] text-textMuted font-mono truncate" title={relPath}>
            {relPath}
            {fileMeta?.truncated ? t('aiChat.truncated') : ''}
          </div>
        </div>
        {!imageSrc && !showSpinner && !error && !isImageFile(relPath) ? (
          <div className="flex items-center gap-1 shrink-0">
            <div className="inline-flex rounded-md border border-border/80 overflow-hidden text-[10px]">
              {(
                [
                  { id: 'rich' as const, label: t('workspaceEditor.richText'), Icon: Pencil },
                  { id: 'source' as const, label: t('workspaceEditor.source'), Icon: FileCode2 },
                  { id: 'preview' as const, label: t('workspaceEditor.preview'), Icon: Eye },
                  { id: 'diff' as const, label: 'Diff', Icon: FileText },
                ] as Array<{ id: FileViewMode; label: string; Icon: typeof Pencil }>
              ).map(({ id, label, Icon }) => (
                <button
                  key={id}
                  type="button"
                  title={label}
                  onClick={() => {
                    if (id === 'diff') {
                      void loadDiff();
                      return;
                    }
                    setViewMode(id);
                  }}
                  className={`px-1.5 py-0.5 flex items-center gap-0.5 border-0 ${
                    viewMode === id
                      ? 'bg-black/[0.08] dark:bg-white/15 text-textMain'
                      : 'bg-transparent text-textMuted hover:bg-primary/10'
                  }`}
                >
                  <Icon size={11} />
                  <span className="hidden sm:inline">{label}</span>
                </button>
              ))}
            </div>
            {dirty && !fileMeta?.truncated ? (
              <button
                type="button"
                disabled={saving}
                onClick={() => void save()}
                className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded-md text-[10px] font-medium bg-emerald-700 text-white hover:bg-emerald-600 disabled:opacity-50"
              >
                {saving ? <Loader2 size={11} className="animate-spin" /> : <Save size={11} />}
                {t('workspaceEditor.save')}
              </button>
            ) : null}
          </div>
        ) : null}
      </div>
      {showSpinner ? (
        <div className="flex-1 flex items-center justify-center text-textMuted text-xs gap-2">
          <Loader2 size={14} className="animate-spin" /> {t('common.loading')}
        </div>
      ) : error ? (
        <div className="px-3 py-4 text-[12px] text-red-400">{error}</div>
      ) : imageSrc ? (
        <div className="flex-1 min-h-0 overflow-auto flex items-center justify-center p-4 bg-[#0d1117]">
          <img src={imageSrc} alt={basename(relPath)} className="max-w-full max-h-full object-contain" />
        </div>
      ) : viewMode === 'diff' ? (
        <div className="flex-1 min-h-0 overflow-auto">
          {diffLines ? (
            <UnifiedDiffView
              lines={diffLines}
              fileName={basename(relPath)}
              collapseUnmodified={false}
            />
          ) : null}
        </div>
      ) : (
        <Suspense
          fallback={
            <div className="flex-1 flex items-center justify-center text-textMuted text-xs gap-2">
              <Loader2 size={14} className="animate-spin" /> {t('common.loading')}
            </div>
          }
        >
          <FileDocumentEditor
            fileName={basename(relPath)}
            value={draftContent}
            onChange={setDraftContent}
            mode={viewMode}
            isMarkdown={isMarkdownFile(relPath)}
          />
        </Suspense>
      )}
    </div>
  );
};

/** Ask before discarding dirty file edits (for tab close). */
export function confirmDiscardFileDirty(dirty: boolean): boolean {
  if (!dirty) return true;
  const lang = i18n?.language || 'zh';
  const msg =
    i18n?.getFixedT(lang)?.('workspaceEditor.unsavedConfirm') ||
    (lang === 'en'
      ? 'This file has unsaved changes. Close anyway?'
      : '文件有未保存的更改，确定关闭？');
  return window.confirm(msg);
}

/** Prefetch into shared cache so first open can paint instantly. */
export async function prefetchWorkspaceFile(
  agentId: string,
  rootPath: string,
  relPath: string,
): Promise<void> {
  const p = (relPath || '').replace(/\\/g, '/');
  if (!agentId || !rootPath || !p) return;
  if (isImageFile(p)) return;
  const ck = cacheKey(agentId, rootPath, p);
  if (fileCache.has(ck)) return;
  try {
    const resp = await adminAPI.readProjectFile(agentId, p, rootPath);
    const content = resp.content ?? '';
    putCache(ck, {
      content,
      imageSrc: null,
      meta: { truncated: resp.truncated, size: resp.size },
      at: Date.now(),
    });
  } catch {
    /* ignore prefetch errors */
  }
}
