import React, { useState, useEffect, useMemo, useRef, useCallback } from 'react';
import {
  ArrowLeft, RefreshCw, BookOpen, Search, Tag, Code, Terminal,
  Loader2, AlertCircle, Package, Plus, Upload, X, FolderOpen, Trash2, Menu,
  FileText, ChevronRight, File as FileIcon, Eye, LayoutGrid, List,
} from 'lucide-react';
import { skillAPI, SkillInfo, SkillSourceResponse, adminAPI, AdminAgent } from '../services/api';
import { useTranslation } from 'react-i18next';
import { marked } from 'marked';

interface SkillManagerPageProps {
  onBack: () => void;
}

const LAYOUT_KEY = 'skill_manager_layout';
type SkillLayoutMode = 'grid' | 'list';

function loadLayoutMode(): SkillLayoutMode {
  try {
    const raw = localStorage.getItem(LAYOUT_KEY);
    return raw === 'list' ? 'list' : 'grid';
  } catch {
    return 'grid';
  }
}

// ---- Skill Card ----

const SkillCard: React.FC<{
  skill: SkillInfo;
  layout?: SkillLayoutMode;
  onDelete: (name: string) => void;
  onView: (skill: SkillInfo) => void;
  skillLevel?: 'full' | 'summary' | 'hidden';
  onSkillLevelChange?: (name: string, level: 'full' | 'summary' | 'hidden') => void;
}> = ({ skill, layout = 'grid', onDelete, onView, skillLevel = 'summary', onSkillLevelChange }) => {
  const { t } = useTranslation();
  const hasBins = skill.requires?.bins && skill.requires.bins.length > 0;
  const hasEnv  = skill.requires?.env  && skill.requires.env.length  > 0;
  const isList = layout === 'list';

  const levelToggle = onSkillLevelChange && (
    <div
      className="flex items-center rounded-md border border-border overflow-hidden text-[11px] font-medium shrink-0"
      onClick={(e) => e.stopPropagation()}
    >
      <button
        onClick={(e) => { e.stopPropagation(); onSkillLevelChange(skill.name, 'full'); }}
        className="px-1.5 py-0.5 transition-colors"
        style={skillLevel === 'full'
          ? { background: 'rgba(34,197,94,0.15)', color: '#22c55e' }
          : { background: 'transparent', color: 'var(--tw-text-opacity,#9ca3af)' }}
        title={t('skillManager.fullTooltip')}
      >
        full
      </button>
      <button
        onClick={(e) => { e.stopPropagation(); onSkillLevelChange(skill.name, 'summary'); }}
        className="px-1.5 py-0.5 transition-colors border-l border-border"
        style={skillLevel === 'summary'
          ? { background: 'rgba(99,102,241,0.15)', color: '#818cf8' }
          : { background: 'transparent', color: 'var(--tw-text-opacity,#9ca3af)' }}
        title={t('skillManager.summaryTooltip')}
      >
        summary
      </button>
      <button
        onClick={(e) => { e.stopPropagation(); onSkillLevelChange(skill.name, 'hidden'); }}
        className="px-1.5 py-0.5 transition-colors border-l border-border"
        style={skillLevel === 'hidden'
          ? { background: 'rgba(245,158,11,0.15)', color: '#f59e0b' }
          : { background: 'transparent', color: 'var(--tw-text-opacity,#9ca3af)' }}
        title={t('skillManager.hiddenTooltip')}
      >
        hidden
      </button>
    </div>
  );

  // ── Compact list row ──
  if (isList) {
    return (
      <div
        onClick={() => onView(skill)}
        className="bg-panel border border-border rounded-lg px-3 py-2 flex items-center gap-3 hover:border-primary/30 transition-colors cursor-pointer group"
      >
        <div className="w-8 h-8 rounded-lg bg-primary/10 flex items-center justify-center shrink-0">
          <BookOpen size={16} className="text-primary" />
        </div>
        <div
          className="flex-1 min-w-0 select-text cursor-text"
          onClick={(e) => e.stopPropagation()}
        >
          <h3 className="text-[13px] font-semibold text-textMain truncate leading-tight">
            {skill.display_name || skill.name}
          </h3>
          {skill.description && (
            <p className="text-[11px] text-textMuted truncate leading-tight mt-0.5">{skill.description}</p>
          )}
        </div>
        {levelToggle}
        <button
          onClick={(e) => { e.stopPropagation(); onDelete(skill.name); }}
          className="p-1 rounded text-textMuted hover:bg-red-500/10 hover:text-red-400 transition-colors shrink-0"
          title={t('common.delete')}
        >
          <Trash2 size={13} />
        </button>
        <ChevronRight size={14} className="text-textMuted opacity-0 group-hover:opacity-100 transition-opacity shrink-0" />
      </div>
    );
  }

  // ── Grid card ──
  return (
    <div
      onClick={() => onView(skill)}
      className="bg-panel border border-border rounded-xl p-5 flex flex-col gap-3 hover:border-primary/30 transition-colors cursor-pointer group"
    >
      {/* Header */}
      <div className="flex items-start gap-3">
        <div className="w-10 h-10 rounded-xl bg-primary/10 flex items-center justify-center shrink-0">
          <BookOpen size={20} className="text-primary" />
        </div>
        <div
          className="flex-1 min-w-0 select-text cursor-text"
          onClick={(e) => e.stopPropagation()}
        >
          <div className="flex items-center gap-2 flex-wrap">
            <h3 className="font-bold text-textMain text-sm">{skill.display_name || skill.name}</h3>
            {skill.version && (
              <span className="text-xs text-textMuted bg-bgLight border border-border px-1.5 py-0.5 rounded">
                v{skill.version}
              </span>
            )}
          </div>
          {skill.description && (
            <p className="text-xs text-textMuted mt-1 line-clamp-2">{skill.description}</p>
          )}
        </div>
        <ChevronRight size={16} className="text-textMuted opacity-0 group-hover:opacity-100 transition-opacity shrink-0 mt-1" />
      </div>

      {/* Keywords */}
      {skill.keywords && skill.keywords.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {skill.keywords.map(kw => (
            <span
              key={kw}
              className="flex items-center gap-1 text-xs bg-primary/10 text-primary border border-primary/20 px-2 py-0.5 rounded-full"
            >
              <Tag size={10} />
              {kw}
            </span>
          ))}
        </div>
      )}

      {/* Requirements */}
      {(hasBins || hasEnv) && (
        <div className="space-y-1.5">
          {hasBins && (
            <div className="flex items-center gap-1.5 text-xs text-textMuted">
              <Terminal size={13} className="shrink-0" />
              <span className="font-semibold text-textMain">{t('skillManager.requires')}:</span>
              <span className="font-mono">{skill.requires.bins!.join(', ')}</span>
            </div>
          )}
          {hasEnv && (
            <div className="flex items-center gap-1.5 text-xs text-textMuted">
              <Code size={13} className="shrink-0" />
              <span className="font-semibold text-textMain">{t('skillManager.envVars')}:</span>
              <span className="font-mono">{skill.requires.env!.join(', ')}</span>
            </div>
          )}
        </div>
      )}

      {/* Install steps */}
      {skill.install && skill.install.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {skill.install.map(step => (
            <span
              key={step.id}
              className="text-xs bg-bgLight border border-border px-2 py-0.5 rounded font-mono text-textMuted"
              title={`${step.kind}: ${(step.packages || []).join(' ')}`}
            >
              {step.kind}{step.packages && step.packages.length > 0 ? ` (${step.packages.length})` : ''}
            </span>
          ))}
        </div>
      )}

      {/* Footer: author, dir, delete */}
      <div className="text-xs text-textMuted pt-1 border-t border-border/50 space-y-2">
        {onSkillLevelChange && (
          <div>
            <div className="mb-1 text-[10px] text-textMuted">{t('skillManager.injectLevel')}</div>
            {levelToggle}
          </div>
        )}

        <div className="flex items-center justify-between">
          <span>{skill.author ? `by ${skill.author}` : skill.dir}</span>
          <div className="flex items-center gap-2">
            {skill.license && <span>{skill.license}</span>}
            <button
              onClick={(e) => { e.stopPropagation(); onDelete(skill.name); }}
              className="p-1 rounded hover:bg-red-500/10 hover:text-red-400 transition-colors"
              title={t('common.delete')}
            >
              <Trash2 size={14} />
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};

// ---- Skill Detail View ----

const SkillDetailView: React.FC<{
  skill: SkillInfo;
  onBack: () => void;
  onDelete: (name: string) => void;
}> = ({ skill, onBack, onDelete }) => {
  const { t } = useTranslation();
  const [source, setSource] = useState<SkillSourceResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<string>('skill_md');

  useEffect(() => {
    (async () => {
      setLoading(true);
      setError(null);
      try {
        const data = await skillAPI.getSkillSource(skill.dir);
        setSource(data);
        // Auto-select first tab
        if (data.skill_md) {
          setActiveTab('skill_md');
        } else if (data.skill_json) {
          setActiveTab('skill_json');
        } else if (Object.keys(data.py_sources).length > 0) {
          setActiveTab(Object.keys(data.py_sources)[0]);
        }
      } catch (e: any) {
        setError(e.message || 'Failed to load skill source');
      } finally {
        setLoading(false);
      }
    })();
  }, [skill.dir]);

  const renderedMd = useMemo(() => {
    if (!source?.skill_md) return '';
    try {
      return marked.parse(source.skill_md) as string;
    } catch {
      return source.skill_md;
    }
  }, [source?.skill_md]);

  const tabs = useMemo(() => {
    if (!source) return [];
    const list: { key: string; label: string; icon: React.ReactNode }[] = [];
    if (source.skill_md) {
      list.push({ key: 'skill_md', label: 'SKILL.md', icon: <FileText size={13} /> });
    }
    if (source.skill_json) {
      list.push({ key: 'skill_json', label: 'skill.json', icon: <FileIcon size={13} /> });
    }
    for (const fname of Object.keys(source.py_sources)) {
      list.push({ key: fname, label: fname, icon: <Code size={13} /> });
    }
    for (const fname of Object.keys(source.other_sources || {})) {
      list.push({ key: fname, label: fname, icon: <FileText size={13} /> });
    }
    return list;
  }, [source]);

  const formatBytes = (bytes: number) => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  };

  return (
    <div className="flex flex-col h-full">
      {/* Detail header */}
      <div className="flex items-center gap-3 px-4 py-3 md:px-6 border-b border-border bg-panel shrink-0">
        <button
          onClick={onBack}
          className="p-1.5 rounded-lg text-textMuted hover:text-textMain hover:bg-primary/10 transition-colors shrink-0"
        >
          <ArrowLeft size={18} />
        </button>
        <div className="w-9 h-9 rounded-xl bg-primary/10 flex items-center justify-center shrink-0">
          <BookOpen size={18} className="text-primary" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <h2 className="font-bold text-textMain text-base">{skill.display_name || skill.name}</h2>
            {skill.version && (
              <span className="text-xs text-textMuted bg-bgLight border border-border px-1.5 py-0.5 rounded">
                v{skill.version}
              </span>
            )}
          </div>
          {skill.description && (
            <p className="text-xs text-textMuted mt-0.5 line-clamp-1">{skill.description}</p>
          )}
        </div>
        <button
          onClick={() => onDelete(skill.name)}
          className="p-1.5 rounded-lg text-textMuted hover:text-red-400 hover:bg-red-500/10 transition-colors shrink-0"
          title={t('common.delete')}
        >
          <Trash2 size={16} />
        </button>
      </div>

      {/* Meta info bar */}
      <div className="flex items-center gap-3 px-4 py-2 md:px-6 border-b border-border bg-panel/50 shrink-0 flex-wrap text-xs text-textMuted">
        {skill.author && <span>by <span className="text-textMain font-medium">{skill.author}</span></span>}
        {skill.license && <span className="px-1.5 py-0.5 bg-bgLight border border-border rounded">{skill.license}</span>}
        {skill.keywords && skill.keywords.length > 0 && (
          <div className="flex flex-wrap gap-1">
            {skill.keywords.map(kw => (
              <span key={kw} className="flex items-center gap-0.5 bg-primary/10 text-primary border border-primary/20 px-1.5 py-0.5 rounded-full text-[10px]">
                <Tag size={8} />{kw}
              </span>
            ))}
          </div>
        )}
        {source && (
          <span className="ml-auto">{source.files.length} files</span>
        )}
      </div>

      {loading ? (
        <div className="flex-1 flex items-center justify-center text-textMuted">
          <Loader2 size={24} className="animate-spin mr-2" />
          {t('common.loading')}
        </div>
      ) : error ? (
        <div className="flex-1 p-6">
          <div className="flex items-center gap-2 p-3 bg-red-500/10 border border-red-500/20 rounded-xl text-red-400 text-sm">
            <AlertCircle size={16} />
            {error}
          </div>
        </div>
      ) : source ? (
        <div className="flex flex-1 overflow-hidden">
          {/* File tabs sidebar */}
          <div className="w-48 shrink-0 border-r border-border bg-panel overflow-y-auto hidden md:block">
            <div className="px-3 py-2 text-[10px] font-bold text-textMuted uppercase tracking-wider">Files</div>
            {source.files.map(f => {
              const tabKey = f.name === 'SKILL.md' ? 'skill_md'
                           : f.name === 'skill.json' ? 'skill_json'
                           : f.name;
              const isActive = activeTab === tabKey;
              const hasContent = f.name === 'SKILL.md' || f.name === 'skill.json' || f.name.endsWith('.py') || !!((source?.other_sources || {})[f.name]);
              return (
                <button
                  key={f.name}
                  onClick={() => hasContent && setActiveTab(tabKey)}
                  className={`w-full text-left px-3 py-2 text-xs flex items-center gap-2 transition-colors border-b border-border/30 ${
                    isActive ? 'bg-primary/10 text-primary font-medium' :
                    hasContent ? 'text-textMain hover:bg-primary/5 cursor-pointer' :
                    'text-textMuted/60 cursor-default'
                  }`}
                >
                  {f.name.endsWith('.md') ? <FileText size={12} className="shrink-0" /> :
                   f.name.endsWith('.py') ? <Code size={12} className="shrink-0" /> :
                   f.name.endsWith('.json') ? <FileIcon size={12} className="shrink-0" /> :
                   <FileIcon size={12} className="shrink-0" />}
                  <span className="truncate flex-1">{f.name}</span>
                  <span className="text-[10px] text-textMuted/50 shrink-0">{formatBytes(f.size)}</span>
                </button>
              );
            })}
          </div>

          {/* Mobile tab bar */}
          <div className="md:hidden shrink-0 border-b border-border bg-panel overflow-x-auto flex">
            {tabs.map(tab => (
              <button
                key={tab.key}
                onClick={() => setActiveTab(tab.key)}
                className={`flex items-center gap-1 px-3 py-2 text-xs whitespace-nowrap border-b-2 transition-colors ${
                  activeTab === tab.key
                    ? 'border-primary text-primary font-medium'
                    : 'border-transparent text-textMuted hover:text-textMain'
                }`}
              >
                {tab.icon}
                {tab.label}
              </button>
            ))}
          </div>

          {/* Content area */}
          <div className="flex-1 overflow-y-auto">
            {activeTab === 'skill_md' && source.skill_md ? (
              <div className="p-4 md:p-6 max-w-4xl">
                <div
                  className="prose prose-sm prose-invert max-w-none
                    prose-headings:text-textMain prose-p:text-textMuted prose-li:text-textMuted
                    prose-a:text-primary prose-code:text-primary prose-code:bg-primary/10 prose-code:px-1 prose-code:py-0.5 prose-code:rounded
                    prose-pre:bg-[#1e1e2e] prose-pre:border prose-pre:border-border prose-pre:rounded-xl
                    prose-strong:text-textMain prose-em:text-textMuted
                    prose-h1:text-xl prose-h2:text-lg prose-h3:text-base
                    prose-h1:border-b prose-h1:border-border prose-h1:pb-2
                    prose-ul:list-disc prose-ol:list-decimal"
                  dangerouslySetInnerHTML={{ __html: renderedMd }}
                />
              </div>
            ) : activeTab === 'skill_json' && source.skill_json ? (
              <div className="p-4 md:p-6">
                <pre className="bg-[#1e1e2e] border border-border rounded-xl p-4 text-sm font-mono text-textMuted overflow-x-auto whitespace-pre-wrap">
                  {JSON.stringify(source.skill_json, null, 2)}
                </pre>
              </div>
            ) : source.py_sources[activeTab] ? (
              <div className="p-4 md:p-6">
                <div className="flex items-center gap-2 mb-3">
                  <Code size={14} className="text-primary" />
                  <span className="text-sm font-mono font-medium text-textMain">{activeTab}</span>
                  <span className="text-xs text-textMuted">
                    ({source.py_sources[activeTab].split('\n').length} lines)
                  </span>
                </div>
                <pre className="bg-[#1e1e2e] border border-border rounded-xl p-4 text-sm font-mono text-textMuted overflow-x-auto whitespace-pre-wrap leading-relaxed">
                  {source.py_sources[activeTab]}
                </pre>
              </div>
            ) : (source.other_sources || {})[activeTab] ? (
              <div className="p-4 md:p-6">
                <div className="flex items-center gap-2 mb-3">
                  <FileText size={14} className="text-primary" />
                  <span className="text-sm font-mono font-medium text-textMain">{activeTab}</span>
                </div>
                {activeTab.endsWith('.md') ? (
                  <div
                    className="prose prose-sm prose-invert max-w-none
                      prose-headings:text-textMain prose-p:text-textMuted prose-li:text-textMuted
                      prose-a:text-primary prose-code:text-primary prose-code:bg-primary/10 prose-code:px-1 prose-code:py-0.5 prose-code:rounded
                      prose-pre:bg-[#1e1e2e] prose-pre:border prose-pre:border-border prose-pre:rounded-xl
                      prose-strong:text-textMain prose-em:text-textMuted
                      prose-h1:text-xl prose-h2:text-lg prose-h3:text-base
                      prose-h1:border-b prose-h1:border-border prose-h1:pb-2
                      prose-ul:list-disc prose-ol:list-decimal"
                    dangerouslySetInnerHTML={{ __html: marked.parse((source.other_sources || {})[activeTab]) as string }}
                  />
                ) : (
                  <pre className="bg-[#1e1e2e] border border-border rounded-xl p-4 text-sm font-mono text-textMuted overflow-x-auto whitespace-pre-wrap leading-relaxed">
                    {(source.other_sources || {})[activeTab]}
                  </pre>
                )}
              </div>
            ) : (
              <div className="flex-1 flex items-center justify-center text-textMuted p-8">
                <Eye size={20} className="mr-2 opacity-30" />
                {t('skillManager.selectFileToView')}
              </div>
            )}
          </div>
        </div>
      ) : null}
    </div>
  );
};

// ---- Categories ----

interface SkillCategoryDef { value: string; label: string; }

const SKILL_CATEGORIES: SkillCategoryDef[] = [
  { value: '',       label: 'skillManager.catAll' },
  { value: 'dev',    label: 'skillManager.catDev' },
  { value: 'search', label: 'skillManager.catSearch' },
  { value: 'file',   label: 'skillManager.catFile' },
  { value: 'data',   label: 'skillManager.catData' },
  { value: 'ai',     label: 'skillManager.catAi' },
  { value: 'system', label: 'skillManager.catSystem' },
];

// ---- Main Component ----

export const SkillManagerPage: React.FC<SkillManagerPageProps> = ({ onBack }) => {
  const { t } = useTranslation();
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [selectedCategory, setSelectedCategory] = useState<string>('');
  const [layoutMode, setLayoutMode] = useState<SkillLayoutMode>(loadLayoutMode);
  const setLayout = useCallback((mode: SkillLayoutMode) => {
    setLayoutMode(mode);
    try { localStorage.setItem(LAYOUT_KEY, mode); } catch { /* ignore */ }
  }, []);
  const [showUploadModal, setShowUploadModal] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [selectedFiles, setSelectedFiles] = useState<{ file: File; path: string }[]>([]);
  const [deletingSkill, setDeletingSkill] = useState<string | null>(null);
  const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null);
  const folderInputRef = useRef<HTMLInputElement>(null);

  // Per-agent skill level settings
  const [agents, setAgents] = useState<AdminAgent[]>([]);
  const [selectedAgent, setSelectedAgent] = useState<string>('');
  const [agentConfig, setAgentConfig] = useState<Record<string, any> | null>(null);
  const [skillLevelsDirty, setSkillLevelsDirty] = useState(false);
  const [savingSkillLevels, setSavingSkillLevels] = useState(false);

  // Detail view state
  const [viewingSkill, setViewingSkill] = useState<SkillInfo | null>(null);

  const loadSkills = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await skillAPI.getSkills();
      setSkills(data.skills);
    } catch (e: any) {
      setError(e.message || t('skillManager.loadFailed'));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { loadSkills(); }, []);

  // 监听市场安装事件（skills），自动刷新技能列表
  useEffect(() => {
    const handler = (e: Event) => {
      const detail = (e as CustomEvent).detail;
      if (detail?.kind?.includes('skill')) loadSkills();
    };
    window.addEventListener('opensquad:market-install', handler);
    return () => window.removeEventListener('opensquad:market-install', handler);
  }, []);

  useEffect(() => {
    (async () => {
      try {
        const data = await adminAPI.getAgents();
        const list = data.agents || [];
        setAgents(list);
        if (list.length > 0) setSelectedAgent(list[0].dir_name);
      } catch {
        setAgents([]);
      }
    })();
  }, []);

  useEffect(() => {
    if (!selectedAgent) return;
    (async () => {
      try {
        const data = await adminAPI.getConfig(selectedAgent);
        setAgentConfig(data.config || {});
        setSkillLevelsDirty(false);
      } catch {
        setAgentConfig(null);
      }
    })();
  }, [selectedAgent]);

  const handleSelectFolder = () => {
    folderInputRef.current?.click();
  };

  const handleFolderChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (!files || files.length === 0) return;

    const fileList: { file: File; path: string }[] = [];
    for (let i = 0; i < files.length; i++) {
      const file = files[i];
      const path = file.webkitRelativePath || file.name;
      fileList.push({ file, path });
    }
    setSelectedFiles(fileList);
    setUploadError(null);
  };

  const handleUpload = async () => {
    if (selectedFiles.length === 0) return;

    setUploading(true);
    setUploadError(null);
    try {
      await skillAPI.uploadSkill(selectedFiles);
      setShowUploadModal(false);
      setSelectedFiles([]);
      loadSkills();
    } catch (e: any) {
      setUploadError(e.message || t('skillManager.uploadFailed'));
    } finally {
      setUploading(false);
    }
  };

  const clearSelectedFiles = () => {
    setSelectedFiles([]);
    if (folderInputRef.current) {
      folderInputRef.current.value = '';
    }
  };

  const handleDeleteClick = (skillName: string) => {
    setDeleteConfirm(skillName);
  };

  const handleDeleteConfirm = async () => {
    if (!deleteConfirm) return;

    setDeletingSkill(deleteConfirm);
    try {
      await skillAPI.deleteSkill(deleteConfirm);
      setDeleteConfirm(null);
      // If we were viewing this skill, go back to list
      if (viewingSkill && viewingSkill.name === deleteConfirm) {
        setViewingSkill(null);
      }
      loadSkills();
    } catch (e: any) {
      setError(e.message || t('skillManager.deleteFailed'));
    } finally {
      setDeletingSkill(null);
    }
  };

  const handleViewSkill = useCallback((skill: SkillInfo) => {
    setViewingSkill(skill);
  }, []);

  const resolveSkillLevel = useCallback((skillName: string): 'full' | 'summary' | 'hidden' => {
    const pp = (agentConfig?.prompt_preload || {}) as Record<string, any>;
    const full = new Set<string>((pp.full_skills as string[]) || []);
    const hidden = new Set<string>((pp.hidden_skills as string[]) || []);
    if (hidden.has(skillName)) return 'hidden';
    if (full.has(skillName)) return 'full';
    return 'summary';
  }, [agentConfig]);

  const setSkillLevel = useCallback((skillName: string, level: 'full' | 'summary' | 'hidden') => {
    setAgentConfig((prev: any) => {
      if (!prev) return prev;
      const clone = JSON.parse(JSON.stringify(prev));
      const pp = clone.prompt_preload && typeof clone.prompt_preload === 'object' ? clone.prompt_preload : {};
      const full = new Set<string>((pp.full_skills as string[]) || []);
      const hidden = new Set<string>((pp.hidden_skills as string[]) || []);

      full.delete(skillName);
      hidden.delete(skillName);
      if (level === 'full') full.add(skillName);
      else if (level === 'hidden') hidden.add(skillName);

      pp.full_skills = [...full];
      pp.hidden_skills = [...hidden];
      clone.prompt_preload = pp;
      return clone;
    });
    setSkillLevelsDirty(true);
  }, []);

  const saveSkillLevels = useCallback(async () => {
    if (!selectedAgent || !agentConfig) return;
    setSavingSkillLevels(true);
    try {
      await adminAPI.updateConfig(selectedAgent, agentConfig);
      setSkillLevelsDirty(false);
    } catch (e: any) {
      setError(e.message || 'Save failed');
    } finally {
      setSavingSkillLevels(false);
    }
  }, [selectedAgent, agentConfig]);

  // category counts
  const categoryCounts = useMemo(() => {
    const valid = skills.filter(s => s.has_skill_json !== false);
    const counts: Record<string, number> = { '': valid.length };
    for (const cat of SKILL_CATEGORIES) {
      if (cat.value === '') continue;
      counts[cat.value] = valid.filter(s => (s.keywords || []).includes(cat.value)).length;
    }
    return counts;
  }, [skills]);

  // filter
  const filtered = useMemo(() => {
    let list = skills.filter(s => s.has_skill_json !== false);
    const q = search.trim().toLowerCase();
    if (q) {
      list = list.filter(s =>
        s.name.toLowerCase().includes(q) ||
        s.description.toLowerCase().includes(q) ||
        (s.keywords || []).some(k => k.toLowerCase().includes(q))
      );
    }
    if (selectedCategory) {
      list = list.filter(s => (s.keywords || []).includes(selectedCategory));
    }
    return list;
  }, [skills, search, selectedCategory]);

  // If viewing a skill detail, show the detail view
  if (viewingSkill) {
    return (
      <div className="flex flex-col h-full w-full bg-bgLight">
        <SkillDetailView
          skill={viewingSkill}
          onBack={() => setViewingSkill(null)}
          onDelete={handleDeleteClick}
        />

        {/* Delete Confirm Modal — also visible from detail view */}
        {deleteConfirm && (
          <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
            <div className="bg-panel border border-border rounded-2xl w-full max-w-sm mx-4 shadow-2xl">
              <div className="p-6">
                <div className="flex items-center gap-3 mb-4">
                  <div className="w-10 h-10 rounded-full bg-red-500/10 flex items-center justify-center">
                    <AlertCircle size={20} className="text-red-400" />
                  </div>
                  <div>
                    <h3 className="font-bold text-textMain">{t('skillManager.deleteConfirmTitle')}</h3>
                    <p className="text-sm text-textMuted">{t('skillManager.deleteConfirmDesc')}</p>
                  </div>
                </div>
                <div className="flex items-center justify-end gap-3">
                  <button
                    onClick={() => setDeleteConfirm(null)}
                    className="px-4 py-2 rounded-lg text-textMuted hover:text-textMain hover:bg-border transition-colors"
                  >
                    {t('common.cancel')}
                  </button>
                  <button
                    onClick={handleDeleteConfirm}
                    disabled={deletingSkill !== null}
                    className="px-4 py-2 rounded-lg bg-red-500 text-white hover:bg-red-600 transition-colors disabled:opacity-50 flex items-center gap-2"
                  >
                    {deletingSkill && <Loader2 size={16} className="animate-spin" />}
                    {t('common.delete')}
                  </button>
                </div>
              </div>
            </div>
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full w-full bg-bgLight">
      {/* Top bar */}
      <div className="flex items-center gap-2 md:gap-3 px-4 md:px-6 py-2 border-b border-border bg-panel shrink-0">
        <button
          onClick={onBack}
          className="p-1.5 md:p-2 rounded-lg text-textMuted hover:text-textMain hover:bg-primary/10 transition-colors shrink-0"
        >
          <ArrowLeft size={18} className="md:w-5 md:h-5" />
        </button>
        <button
          onClick={() => window.dispatchEvent(new CustomEvent('openMobileNav'))}
          className="p-1.5 rounded-lg text-textMuted hover:bg-primary/10 hover:text-primary transition-colors md:hidden shrink-0"
          aria-label="Navigation menu"
        >
          <Menu size={18} />
        </button>
        <BookOpen size={18} className="text-primary shrink-0 md:w-[22px] md:h-[22px]" />
        <h1 className="text-base md:text-lg font-bold text-textMain shrink-0 whitespace-nowrap">{t('skillManager.title')}</h1>

        {/* Mobile inline search */}
        <div className="relative flex-1 min-w-0 max-w-[110px] md:hidden">
          <Search size={12} className="absolute left-2 top-1/2 -translate-y-1/2 text-textMuted pointer-events-none" />
          <input
            type="text"
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder={t('skillManager.searchPlaceholder')}
            className="w-full pl-6 pr-2 py-1 rounded-lg text-[11px] bg-bgLight border border-border text-textMain placeholder-textMuted focus:outline-none focus:border-primary/50 transition-colors"
          />
        </div>

        <div className="ml-auto flex items-center gap-1 md:gap-2 shrink-0">
          <span className="text-[10px] md:text-xs text-textMuted hidden sm:inline">{skills.filter(s => s.has_skill_json !== false).length} {t('nav.skills')}</span>
          <div className="flex items-center rounded-lg border border-border bg-bgLight p-0.5 shrink-0">
            <button
              onClick={() => setLayout('grid')}
              title={t('skillManager.layoutGrid')}
              className={`p-1 rounded-md transition-colors ${
                layoutMode === 'grid' ? 'bg-primary/15 text-primary' : 'text-textMuted hover:text-textMain'
              }`}
            >
              <LayoutGrid size={13} />
            </button>
            <button
              onClick={() => setLayout('list')}
              title={t('skillManager.layoutList')}
              className={`p-1 rounded-md transition-colors ${
                layoutMode === 'list' ? 'bg-primary/15 text-primary' : 'text-textMuted hover:text-textMain'
              }`}
            >
              <List size={13} />
            </button>
          </div>
          <button
            onClick={() => setShowUploadModal(true)}
            className="p-1.5 md:p-2 rounded-lg text-textMuted hover:text-primary hover:bg-primary/10 transition-colors"
            title={t('skillManager.addSkill')}
          >
            <Plus size={14} className="md:w-4 md:h-4" />
          </button>
          <button
            onClick={loadSkills}
            disabled={loading}
            className="p-1.5 md:p-2 rounded-lg text-textMuted hover:text-primary hover:bg-primary/10 transition-colors disabled:opacity-40"
            title={t('common.refresh')}
          >
            <RefreshCw size={14} className={`md:w-4 md:h-4 ${loading ? 'animate-spin' : ''}`} />
          </button>
        </div>
      </div>

      {/* Search & category filter */}
      <div className="px-4 md:px-6 py-2 md:py-3 border-b border-border bg-panel shrink-0 space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs text-textMuted">{t('skillManager.targetAgent')}</span>
          <select
            value={selectedAgent}
            onChange={e => setSelectedAgent(e.target.value)}
            className="px-2 py-1 bg-bgLight border border-border rounded-lg text-xs text-textMain"
          >
            {agents.map(a => (
              <option key={a.dir_name} value={a.dir_name}>{a.agent_name || a.dir_name}</option>
            ))}
          </select>
          <button
            onClick={saveSkillLevels}
            disabled={!skillLevelsDirty || savingSkillLevels || !selectedAgent}
            className="px-2.5 py-1 rounded-lg text-xs bg-primary text-white disabled:opacity-50"
          >
            {savingSkillLevels ? t('skillManager.savingSkillLevels') : t('skillManager.saveSkillLevels')}
          </button>
        </div>
        {/* Desktop search */}
        <div className="relative hidden md:block">
          <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-textMuted" />
          <input
            type="text"
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder={t('skillManager.searchPlaceholder')}
            className="w-full pl-9 pr-4 py-2 bg-bgLight border border-border rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-primary/30"
          />
        </div>
        <div className="flex flex-wrap gap-1.5">
          {SKILL_CATEGORIES.map(cat => (
            <button
              key={cat.value}
              onClick={() => setSelectedCategory(cat.value)}
              className={`flex items-center gap-1 text-xs px-2.5 py-1 rounded-full border transition-colors ${
                selectedCategory === cat.value
                  ? 'bg-primary text-white border-primary'
                  : 'bg-bgLight text-textMuted border-border hover:border-primary/50 hover:text-primary'
              }`}
            >
              {t(cat.label)}
              <span className={`text-[10px] ${selectedCategory === cat.value ? 'text-white/70' : 'text-textMuted/50'}`}>
                {categoryCounts[cat.value] ?? 0}
              </span>
            </button>
          ))}
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-6">
        {error && (
          <div className="flex items-center gap-2 p-3 bg-red-500/10 border border-red-500/20 rounded-xl text-red-400 text-sm mb-4">
            <AlertCircle size={16} />
            {error}
          </div>
        )}

        {loading ? (
          <div className="flex items-center justify-center py-20 text-textMuted">
            <Loader2 size={24} className="animate-spin mr-2" />
            {t('common.loading')}
          </div>
        ) : filtered.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-20 text-textMuted">
            <Package size={40} className="mb-3 opacity-30" />
            <p>{search || selectedCategory ? t('skillManager.noSkills') : t('skillManager.noSkills')}</p>
          </div>
        ) : (
          <div className={
            layoutMode === 'list'
              ? 'grid grid-cols-1 xl:grid-cols-2 gap-1.5'
              : 'grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4'
          }>
            {filtered.map(skill => (
              <SkillCard
                key={skill.dir}
                skill={skill}
                layout={layoutMode}
                onDelete={handleDeleteClick}
                onView={handleViewSkill}
                skillLevel={resolveSkillLevel(skill.name)}
                onSkillLevelChange={selectedAgent ? setSkillLevel : undefined}
              />
            ))}
          </div>
        )}
      </div>

      {/* Upload Modal */}
      {showUploadModal && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-panel border border-border rounded-2xl w-full max-w-md mx-4 shadow-2xl">
            {/* Header */}
            <div className="flex items-center justify-between px-6 py-4 border-b border-border">
              <h2 className="text-lg font-bold text-textMain">{t('skillManager.addSkill')}</h2>
              <button
                onClick={() => { setShowUploadModal(false); clearSelectedFiles(); }}
                className="p-1 rounded-lg text-textMuted hover:text-textMain hover:bg-border"
              >
                <X size={20} />
              </button>
            </div>

            {/* Content */}
            <div className="p-6 space-y-4">
              {selectedFiles.length === 0 ? (
                <div
                  onClick={handleSelectFolder}
                  className="border-2 border-dashed border-border rounded-xl p-8 flex flex-col items-center gap-3 cursor-pointer hover:border-primary/50 hover:bg-primary/5 transition-colors"
                >
                  <FolderOpen size={40} className="text-textMuted" />
                  <p className="text-sm text-textMuted text-center">
                    {t('skillManager.selectFolder')}
                  </p>
                  <input
                    ref={folderInputRef}
                    type="file"
                    webkitdirectory=""
                    directory=""
                    multiple
                    className="hidden"
                    onChange={handleFolderChange}
                  />
                </div>
              ) : (
                <div className="space-y-3">
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-medium text-textMain">
                      {selectedFiles.length} {t('skillManager.filesSelected')}
                    </span>
                    <button
                      onClick={clearSelectedFiles}
                      className="text-xs text-red-400 hover:text-red-300"
                    >
                      {t('common.clear')}
                    </button>
                  </div>
                  <div className="bg-bgLight border border-border rounded-lg max-h-40 overflow-y-auto p-2">
                    {selectedFiles.slice(0, 10).map((item, idx) => (
                      <div key={idx} className="text-xs text-textMuted py-1 truncate">
                        {item.path}
                      </div>
                    ))}
                    {selectedFiles.length > 10 && (
                      <div className="text-xs text-textMuted italic">
                        ...and {selectedFiles.length - 10} more
                      </div>
                    )}
                  </div>
                </div>
              )}

              {uploadError && (
                <div className="flex items-center gap-2 p-3 bg-red-500/10 border border-red-500/20 rounded-xl text-red-400 text-sm">
                  <AlertCircle size={16} />
                  {uploadError}
                </div>
              )}
            </div>

            {/* Footer */}
            <div className="flex items-center justify-end gap-3 px-6 py-4 border-t border-border">
              <button
                onClick={() => { setShowUploadModal(false); clearSelectedFiles(); }}
                className="px-4 py-2 rounded-lg text-textMuted hover:text-textMain hover:bg-border transition-colors"
              >
                {t('common.cancel')}
              </button>
              <button
                onClick={handleUpload}
                disabled={selectedFiles.length === 0 || uploading}
                className="px-4 py-2 rounded-lg bg-primary text-white hover:bg-primary/90 transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
              >
                {uploading ? (
                  <>
                    <Loader2 size={16} className="animate-spin" />
                    {t('common.uploading')}
                  </>
                ) : (
                  <>
                    <Upload size={16} />
                    {t('skillManager.upload')}
                  </>
                )}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Delete Confirm Modal */}
      {deleteConfirm && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-panel border border-border rounded-2xl w-full max-w-sm mx-4 shadow-2xl">
            <div className="p-6">
              <div className="flex items-center gap-3 mb-4">
                <div className="w-10 h-10 rounded-full bg-red-500/10 flex items-center justify-center">
                  <AlertCircle size={20} className="text-red-400" />
                </div>
                <div>
                  <h3 className="font-bold text-textMain">{t('skillManager.deleteConfirmTitle')}</h3>
                  <p className="text-sm text-textMuted">{t('skillManager.deleteConfirmDesc')}</p>
                </div>
              </div>
              <div className="flex items-center justify-end gap-3">
                <button
                  onClick={() => setDeleteConfirm(null)}
                  className="px-4 py-2 rounded-lg text-textMuted hover:text-textMain hover:bg-border transition-colors"
                >
                  {t('common.cancel')}
                </button>
                <button
                  onClick={handleDeleteConfirm}
                  disabled={deletingSkill !== null}
                  className="px-4 py-2 rounded-lg bg-red-500 text-white hover:bg-red-600 transition-colors disabled:opacity-50 flex items-center gap-2"
                >
                  {deletingSkill && <Loader2 size={16} className="animate-spin" />}
                  {t('common.delete')}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
