import React, { useState, useEffect, useCallback } from 'react';
import { PluginViewProps } from '@opensquad/plugin-sdk';
import {
  ArrowLeft, RefreshCw, Loader2, AlertCircle,
  StickyNote, Trash2, Check, Plus, X, Edit2
} from 'lucide-react';
import { createRoot, Root } from 'react-dom/client';

// Mock translation or similar if needed, or just use strings
const t = (key: string) => {
    const keys: Record<string, string> = {
        'quickNote.title': '快速笔记',
        'quickNote.subtitle': '管理你的碎片化想法与任务',
        'quickNote.add': '添加笔记',
        'quickNote.totalNotes': '总计',
        'quickNote.done': '已完成',
        'quickNote.todo': '待办',
        'quickNote.tags': '标签',
        'quickNote.searchPlaceholder': '搜索笔记...',
        'quickNote.showDone': '显示已完成',
        'quickNote.showTodo': '显示待办',
        'quickNote.resetFilter': '重置',
        'quickNote.newNote': '新笔记',
        'quickNote.contentPlaceholder': '输入笔记内容...',
        'quickNote.tagsPlaceholder': '标签 (逗号分隔)',
        'quickNote.saving': '保存中...',
        'quickNote.save': '保存',
        'quickNote.noNotes': '还没有笔记',
        'quickNote.deleteConfirm': '确定要删除这条笔记吗？',
        'quickNote.cancel': '取消',
        'quickNote.editTitle': '编辑',
        'quickNote.deleteTitle': '删除'
    };
    return keys[key] || key;
};

// Simple plugin API proxy
const pluginAPI = {
    getPluginData: async (id: string, params: any) => {
        const query = new URLSearchParams(params).toString();
        const token = localStorage.getItem('chat_token');
        const headers: Record<string, string> = {};
        if (token) {
            headers['Authorization'] = `Bearer ${token}`;
        }

        // Correct path for Gateway proxy
        const resp = await fetch(`/api/ai-web/admin/plugins/${id}/data?${query}`, {
            headers
        });

        if (!resp.ok) {
            if (resp.status === 401) throw new Error('Unauthorized');
            const errData = await resp.json().catch(() => ({}));
            throw new Error(errData.error || `HTTP ${resp.status}`);
        }
        return resp.json();
    },
    pluginAction: async (id: string, action: string, body: any) => {
        const token = localStorage.getItem('chat_token');
        const headers: Record<string, string> = {
            'Content-Type': 'application/json'
        };
        if (token) {
            headers['Authorization'] = `Bearer ${token}`;
        }

        // Correct path for Gateway proxy
        const resp = await fetch(`/api/ai-web/admin/plugins/${id}/action`, {
            method: 'POST',
            headers,
            body: JSON.stringify({ action, data: body }) // Changed to {action, data: ...} to match launcher expectation
        });

        if (!resp.ok) {
            if (resp.status === 401) throw new Error('Unauthorized');
            const errData = await resp.json().catch(() => ({}));
            throw new Error(errData.error || `HTTP ${resp.status}`);
        }
        return resp.json();
    }
};

interface DashboardData {
  success: boolean;
  summary: {
    total: number;
    done: number;
    todo: number;
    tags_count: number;
  };
  notes: Array<{
    id: string;
    content: string;
    tags: string[] | string;
    created_at: string;
    updated_at: string;
    done: boolean;
  }>;
  tags: string[];
}

/** Coerce note.tags to string[] — stored data may be a comma-separated string. */
function asTagList(raw: unknown): string[] {
  if (Array.isArray(raw)) {
    return raw.map((t) => String(t).trim()).filter(Boolean);
  }
  if (typeof raw === 'string') {
    return raw
      .replace(/;/g, ',')
      .split(',')
      .map((t) => t.trim())
      .filter(Boolean);
  }
  return [];
}

export const QuickNoteDashboard: React.FC<PluginViewProps> = ({ onBack }) => {
  const [data, setData] = useState<DashboardData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedTag, setSelectedTag] = useState('');
  const [searchQuery, setSearchQuery] = useState('');
  const [showDone, setShowDone] = useState(false);

  const [editingId, setEditingId] = useState<string | null>(null);
  const [editContent, setEditContent] = useState('');
  const [editTags, setEditTags] = useState('');
  const [showAddForm, setShowAddForm] = useState(false);
  const [newContent, setNewContent] = useState('');
  const [newTags, setNewTags] = useState('');
  const [saving, setSaving] = useState(false);

  const fetchData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params: Record<string, string> = {};
      if (selectedTag) params.tag = selectedTag;
      if (searchQuery) params.search = searchQuery;
      if (showDone) params.done = 'true';
      const result = await pluginAPI.getPluginData('quick_note', params);
      setData(result);
    } catch (err: any) {
      setError(err.message || 'Failed to load notes');
    } finally {
      setLoading(false);
    }
  }, [selectedTag, searchQuery, showDone]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const formatDate = (isoStr: string) => {
    try {
      const d = new Date(isoStr);
      return d.toLocaleString('zh-CN', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
    } catch {
      return isoStr;
    }
  };

  const handleAdd = async () => {
    if (!newContent.trim()) return;
    setSaving(true);
    try {
      const tags = newTags.split(',').map(t => t.trim()).filter(Boolean);
      await pluginAPI.pluginAction('quick_note', 'add', { content: newContent.trim(), tags });
      setNewContent('');
      setNewTags('');
      setShowAddForm(false);
      fetchData();
    } catch (err: any) {
      setError(err.message || 'Failed to add note');
    } finally {
      setSaving(false);
    }
  };

  const startEdit = (note: { id: string; content: string; tags: string[] | string }) => {
    setEditingId(note.id);
    setEditContent(note.content);
    setEditTags(asTagList(note.tags).join(', '));
  };

  const handleSaveEdit = async () => {
    if (!editingId || !editContent.trim()) return;
    setSaving(true);
    try {
      const tags = editTags.split(',').map(t => t.trim()).filter(Boolean);
      await pluginAPI.pluginAction('quick_note', 'update', { id: editingId, content: editContent.trim(), tags });
      setEditingId(null);
      setEditContent('');
      setEditTags('');
      fetchData();
    } catch (err: any) {
      setError(err.message || 'Failed to update note');
    } finally {
      setSaving(false);
    }
  };

  const handleToggle = async (noteId: string) => {
    try {
      await pluginAPI.pluginAction('quick_note', 'toggle', { id: noteId });
      fetchData();
    } catch (err: any) {
      setError(err.message || 'Failed to toggle note');
    }
  };

  const handleDelete = async (noteId: string) => {
    if (!confirm(t('quickNote.deleteConfirm'))) return;
    try {
      await pluginAPI.pluginAction('quick_note', 'delete', { id: noteId });
      fetchData();
    } catch (err: any) {
      setError(err.message || 'Failed to delete note');
    }
  };

  return (
    <div className="flex-1 h-full bg-slate-50 flex flex-col overflow-hidden rounded-2xl">
      {/* Header */}
      <div className="px-6 py-4 border-b border-slate-200 bg-white flex items-center gap-4">
        <button onClick={onBack} className="p-2 hover:bg-slate-100 rounded-lg transition-colors">
          <ArrowLeft size={20} />
        </button>
        <div className="flex flex-col gap-1 flex-1">
          <h1 className="text-lg font-semibold text-slate-800">{t('quickNote.title')}</h1>
          <p className="text-sm text-slate-500">{t('quickNote.subtitle')}</p>
        </div>
        <button
          onClick={() => setShowAddForm(true)}
          className="px-4 py-2 bg-indigo-600 hover:bg-indigo-700 text-white rounded-lg flex items-center gap-2 transition-colors"
        >
          <Plus size={18} />
          {t('quickNote.add')}
        </button>
        <button
          onClick={fetchData}
          disabled={loading}
          className="p-2 hover:bg-slate-100 rounded-lg transition-colors"
        >
          {loading ? <Loader2 className="animate-spin" size={20} /> : <RefreshCw size={20} />}
        </button>
      </div>

      {/* Summary */}
      {data?.summary && (
        <div className="px-6 py-3 bg-white border-b border-slate-200 grid grid-cols-4 gap-3">
          <div className="bg-slate-900 p-3 rounded-xl text-center">
            <div className="text-xl font-bold text-white">{data.summary.total}</div>
            <div className="text-[10px] text-slate-400 uppercase font-bold">{t('quickNote.totalNotes')}</div>
          </div>
          <div className="bg-emerald-500 p-3 rounded-xl text-center">
            <div className="text-xl font-bold text-white">{data.summary.done}</div>
            <div className="text-[10px] text-emerald-100 uppercase font-bold">{t('quickNote.done')}</div>
          </div>
          <div className="bg-amber-500 p-3 rounded-xl text-center">
            <div className="text-xl font-bold text-white">{data.summary.todo}</div>
            <div className="text-[10px] text-amber-100 uppercase font-bold">{t('quickNote.todo')}</div>
          </div>
          <div className="bg-violet-500 p-3 rounded-xl text-center">
            <div className="text-xl font-bold text-white">{data.summary.tags_count}</div>
            <div className="text-[10px] text-violet-100 uppercase font-bold">{t('quickNote.tags')}</div>
          </div>
        </div>
      )}

      {/* Filters */}
      <div className="px-6 py-3 border-b border-slate-200 bg-white">
        <div className="flex flex-wrap gap-2">
          <input
            type="text"
            placeholder={t('quickNote.searchPlaceholder')}
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="flex-1 p-2 rounded-lg border border-slate-200 bg-slate-50 focus:outline-none focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-500"
          />
          <button
            onClick={() => setShowDone(!showDone)}
            className={`py-2 px-4 rounded-lg font-medium transition-colors ${showDone ? 'bg-emerald-500 text-white' : 'bg-slate-100 text-slate-600 hover:bg-slate-200'}`}
          >
            {showDone ? t('quickNote.showDone') : t('quickNote.showTodo')}
          </button>
          <button
            onClick={() => { setSelectedTag(''); setSearchQuery(''); setShowDone(false); }}
            className="py-2 px-4 rounded-lg bg-slate-100 text-slate-600 hover:bg-slate-200 transition-colors"
          >
            {t('quickNote.resetFilter')}
          </button>
        </div>
        {data && asTagList(data.tags).length > 0 && (
          <div className="flex flex-wrap gap-2 mt-3">
            {asTagList(data.tags).map((tag) => (
              <button
                key={tag}
                onClick={() => setSelectedTag(selectedTag === tag ? '' : tag)}
                className={`px-3 py-1 rounded-full text-xs font-medium transition-colors ${selectedTag === tag ? 'bg-indigo-600 text-white' : 'bg-indigo-50 text-indigo-600 hover:bg-indigo-100'}`}
              >
                #{tag}
              </button>
            ))}
          </div>
        )}
      </div>

      {/* List */}
      <div className="flex-1 overflow-y-auto p-6">
        {loading ? (
          <div className="flex items-center justify-center h-full">
            <Loader2 className="animate-spin text-indigo-600" size={40} />
          </div>
        ) : data && data.notes.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full text-slate-400">
            <StickyNote size={64} strokeWidth={1.5} />
            <p className="mt-4 font-medium">{t('quickNote.noNotes')}</p>
          </div>
        ) : data ? (
          <div className="space-y-4">
            {data.notes.map((note) => (
              <div
                key={note.id}
                className={`p-4 rounded-2xl border transition-all ${note.done ? 'border-emerald-100 bg-emerald-50/30' : 'border-slate-100 bg-white shadow-sm hover:shadow-md'}`}
              >
                {editingId === note.id ? (
                  <div className="space-y-3">
                    <textarea
                      value={editContent}
                      onChange={(e) => setEditContent(e.target.value)}
                      className="w-full p-3 rounded-xl border border-slate-200 bg-slate-50 focus:outline-none focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-500 resize-none"
                      rows={3}
                    />
                    <input
                      type="text"
                      value={editTags}
                      onChange={(e) => setEditTags(e.target.value)}
                      placeholder={t('quickNote.tagsPlaceholder')}
                      className="w-full p-2 rounded-lg border border-slate-200 bg-slate-50 focus:outline-none focus:border-indigo-500"
                    />
                    <div className="flex gap-2">
                      <button
                        onClick={handleSaveEdit}
                        disabled={saving}
                        className="px-4 py-2 bg-indigo-600 text-white rounded-lg text-sm font-medium hover:bg-indigo-700 disabled:opacity-50"
                      >
                        {saving ? t('quickNote.saving') : t('quickNote.save')}
                      </button>
                      <button
                        onClick={() => { setEditingId(null); setEditContent(''); setEditTags(''); }}
                        className="px-4 py-2 bg-slate-100 text-slate-600 rounded-lg text-sm font-medium hover:bg-slate-200"
                      >
                        {t('quickNote.cancel')}
                      </button>
                    </div>
                  </div>
                ) : (
                  <div>
                    <div className="flex items-start gap-4">
                      <button
                        onClick={() => handleToggle(note.id)}
                        className={`mt-1 p-1 rounded-lg transition-colors ${note.done ? 'bg-emerald-500 text-white' : 'bg-slate-100 text-slate-300 hover:text-slate-400 hover:bg-slate-200'}`}
                      >
                        <Check size={16} strokeWidth={3} />
                      </button>
                      <div className="flex-1 min-w-0">
                        <p className={`text-slate-700 whitespace-pre-wrap leading-relaxed ${note.done ? 'line-through text-slate-400' : ''}`}>
                          {note.content}
                        </p>
                      </div>
                      <div className="flex gap-1">
                        <button
                          onClick={() => startEdit(note)}
                          className="p-2 text-slate-400 hover:text-indigo-600 hover:bg-indigo-50 rounded-lg transition-colors"
                        >
                          <Edit2 size={16} />
                        </button>
                        <button
                          onClick={() => handleDelete(note.id)}
                          className="p-2 text-slate-400 hover:text-rose-600 hover:bg-rose-50 rounded-lg transition-colors"
                        >
                          <Trash2 size={16} />
                        </button>
                      </div>
                    </div>
                    {(() => {
                      const tags = asTagList(note.tags);
                      if (tags.length === 0 && !note.created_at) return null;
                      return (
                      <div className="mt-3 flex items-center justify-between ml-10">
                        <div className="flex flex-wrap gap-1">
                          {tags.map((tag) => (
                            <span key={tag} className="text-[10px] font-bold text-slate-400 uppercase tracking-wider">
                              #{tag}
                            </span>
                          ))}
                        </div>
                        <span className="text-[10px] text-slate-300 font-medium">
                          {formatDate(note.created_at)}
                        </span>
                      </div>
                      );
                    })()}
                  </div>
                )}
              </div>
            ))}
          </div>
        ) : null}
      </div>
    </div>
  );
};

// Plugin System Interface
const _roots = new WeakMap<HTMLElement, Root>();
export function mount(container: HTMLElement, props: PluginViewProps): void {
  const root = createRoot(container);
  _roots.set(container, root);
  root.render(<QuickNoteDashboard {...props} />);
}
export function unmount(container: HTMLElement): void {
  const root = _roots.get(container);
  if (root) {
    root.unmount();
    _roots.delete(container);
  }
}
export default QuickNoteDashboard;
