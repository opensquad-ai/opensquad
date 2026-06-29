import React, { useState, useEffect, useCallback } from 'react';
import {
  ArrowLeft, RefreshCw, Loader2, AlertCircle, Database,
  Trash2, Search, ChevronDown,
  BrainCircuit, BookOpen, Zap, FileText, Info,
  X, ChevronUp, Clock,
} from 'lucide-react';
import { pluginAPI } from '../../../services/api';

// ─── Types ───

interface AgentItem {
  agent_id: string;
  agent_name: string;
  dir_name: string;
}

interface MemoryEntry {
  id: string;
  entry_type: string;
  topic: string | null;
  summary: string | null;
  body: string | null;
  source: string | null;
  category: string | null;
  importance: number;
  timestamp: number;
  date_str: string | null;
  created_at: string;
  keywords: string[];
  access_count: number;
}

interface QueryResponse {
  agents: AgentItem[];
  memories: MemoryEntry[];
  total: number;
  meta: { error?: string; db_path?: string; query_time_ms: number; limit: number; offset: number };
}

// ─── Helpers ───

const TYPE_ICONS: Record<string, React.ReactNode> = {
  knowledge: <BookOpen size={14} />,
  experience: <Zap size={14} />,
  log: <FileText size={14} />,
};

const TYPE_COLORS: Record<string, string> = {
  knowledge: 'text-blue-400 bg-blue-500/10',
  experience: 'text-amber-400 bg-amber-500/10',
  log: 'text-gray-400 bg-gray-500/10',
};

function importanceLabel(n: number): { label: string; color: string } {
  if (n >= 5) return { label: 'Critical', color: 'text-red-400 bg-red-500/10' };
  if (n >= 4) return { label: 'High', color: 'text-orange-400 bg-orange-500/10' };
  if (n >= 3) return { label: 'Medium', color: 'text-yellow-400 bg-yellow-500/10' };
  if (n >= 2) return { label: 'Low', color: 'text-blue-400 bg-blue-500/10' };
  return { label: 'Trivial', color: 'text-gray-400 bg-gray-500/10' };
}

function typeLabel(t: string): string {
  const map: Record<string, string> = {
    knowledge: 'Knowledge',
    experience: 'Experience',
    log: 'Log',
  };
  return map[t] || t;
}

function stripHtml(s: string): string {
  return s?.replace(/<[^>]*>/g, '') || '';
}

// ─── Component ───

const LongMemoryPanel: React.FC<{ onBack: () => void }> = ({ onBack }) => {
  const [agents, setAgents] = useState<AgentItem[]>([]);
  const [selectedAgent, setSelectedAgent] = useState<string>('');
  const [memories, setMemories] = useState<MemoryEntry[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [sort, setSort] = useState('date_desc');
  const [typeFilter, setTypeFilter] = useState('');
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [initialLoading, setInitialLoading] = useState(true);

  // ── Fetch memories ──
  const loadMemories = useCallback(async (agentId: string, searchTerm?: string, sortBy?: string, typeF?: string) => {
    setLoading(true);
    setError(null);
    try {
      const params: Record<string, string> = { agent_id: agentId };
      if (searchTerm) params.search = searchTerm;
      if (sortBy) params.sort = sortBy;
      if (typeF) params.type = typeF;
      const result: QueryResponse = await pluginAPI.getPluginData('long_memory', params);
      setAgents(result.agents || []);
      setMemories(result.memories || []);
      setTotal(result.total || 0);
    } catch (e: any) {
      setError(e.message || 'Failed to load data');
    } finally {
      setLoading(false);
      setInitialLoading(false);
    }
  }, []);

  // Initial load (agent list only)
  useEffect(() => {
    let cancelled = false;
    (async () => {
      setInitialLoading(true);
      try {
        const result: QueryResponse = await pluginAPI.getPluginData('long_memory', {});
        if (!cancelled) {
          setAgents(result.agents || []);
          setMemories([]);
          setTotal(0);
        }
      } catch (e: any) {
        if (!cancelled) setError(e.message || 'Failed to load data');
      } finally {
        if (!cancelled) setInitialLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  // Fetch memories when agent, search, sort, or type filter changes
  useEffect(() => {
    if (selectedAgent) {
      loadMemories(selectedAgent, search || undefined, sort, typeFilter || undefined);
    }
  }, [selectedAgent, search, sort, typeFilter, loadMemories]);

  // Refresh handler
  const handleRefresh = useCallback(() => {
    setSelectedIds(new Set());
    if (selectedAgent) {
      loadMemories(selectedAgent, search || undefined, sort, typeFilter || undefined);
    } else {
      (async () => {
        try {
          const result: QueryResponse = await pluginAPI.getPluginData('long_memory', {});
          setAgents(result.agents || []);
        } catch (_) {}
      })();
    }
  }, [selectedAgent, search, sort, typeFilter, loadMemories]);

  // ── Delete handler ──
  const handleDelete = useCallback(async (id: string) => {
    if (!selectedAgent) return;
    setDeleting(true);
    try {
      await pluginAPI.pluginAction('long_memory', 'delete', { agent_id: selectedAgent, id });
      setConfirmDelete(null);
      setSelectedIds(prev => { const s = new Set(prev); s.delete(id); return s; });
      loadMemories(selectedAgent, search || undefined, sort, typeFilter || undefined);
    } catch (e: any) {
      setError(e.message || 'Delete failed');
    } finally {
      setDeleting(false);
    }
  }, [selectedAgent, search, sort, typeFilter, loadMemories]);

  // ── Bulk delete ──
  const handleBulkDelete = useCallback(async () => {
    if (!selectedAgent || selectedIds.size === 0) return;
    setDeleting(true);
    try {
      await pluginAPI.pluginAction('long_memory', 'delete_multi', {
        agent_id: selectedAgent,
        ids: Array.from(selectedIds),
      });
      setSelectedIds(new Set());
      setConfirmDelete(null);
      loadMemories(selectedAgent, search || undefined, sort, typeFilter || undefined);
    } catch (e: any) {
      setError(e.message || 'Bulk delete failed');
    } finally {
      setDeleting(false);
    }
  }, [selectedAgent, selectedIds, search, sort, typeFilter, loadMemories]);

  // ── Toggle select all on current page ──
  const toggleSelectAll = useCallback(() => {
    if (selectedIds.size === memories.length && memories.every(m => selectedIds.has(m.id))) {
      setSelectedIds(new Set());
    } else {
      setSelectedIds(new Set(memories.map(m => m.id)));
    }
  }, [memories, selectedIds]);

  // ── Toggle single selection ──
  const toggleSelect = useCallback((id: string) => {
    setSelectedIds(prev => {
      const s = new Set(prev);
      if (s.has(id)) s.delete(id);
      else s.add(id);
      return s;
    });
  }, []);

  // Empty state (no agent selected or initial)
  const showEmpty = !loading && !error && memories.length === 0 && selectedAgent;

  return (
    <div className="flex-1 h-full bg-bgLight flex flex-col overflow-hidden">
      {/* ── Header ── */}
      <div className="px-4 py-3 border-b border-border bg-panel flex items-center gap-3 shrink-0">
        <button
          onClick={onBack}
          className="p-2 rounded-lg text-textMuted hover:bg-primary/10 hover:text-primary transition-colors"
        >
          <ArrowLeft size={20} />
        </button>
        <div className="flex items-center gap-3 flex-1">
          <div className="w-9 h-9 bg-violet-500/15 rounded-xl flex items-center justify-center">
            <BrainCircuit className="text-violet-400" size={20} />
          </div>
          <div>
            <h1 className="text-base font-bold text-textMain">Memory Management</h1>
            <p className="text-xs text-textMuted">{total > 0 ? `${total} memories` : 'Long-term memory manager'}</p>
          </div>
        </div>

        {/* Agent selector */}
        <div className="flex items-center gap-2">
          <span className="text-xs text-textMuted">Agent:</span>
          <select
            value={selectedAgent}
            onChange={e => { setSelectedAgent(e.target.value); setSelectedIds(new Set()); }}
            className="px-2 py-1.5 text-sm bg-bgLight border border-border rounded-lg text-textMain focus:outline-none focus:border-primary max-w-[160px]"
          >
            <option value="">-- Select --</option>
            {agents.map(a => (
              <option key={a.agent_id} value={a.agent_id}>{a.agent_name}</option>
            ))}
          </select>
        </div>

        <button
          onClick={handleRefresh}
          className="p-2 rounded-lg text-textMuted hover:bg-primary/10 hover:text-primary transition-colors"
          title="Refresh"
        >
          <RefreshCw size={18} className={loading ? 'animate-spin' : ''} />
        </button>
      </div>

      {/* ── Toolbar ── */}
      {selectedAgent && (
        <div className="px-4 py-2 border-b border-border bg-panel/50 flex items-center gap-3 shrink-0 flex-wrap">
          {/* Search */}
          <div className="relative flex-1 min-w-[160px] max-w-xs">
            <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-textMuted" />
            <input
              type="text"
              value={search}
              onChange={e => setSearch(e.target.value)}
              placeholder="Search memories..."
              className="w-full pl-8 pr-3 py-1.5 text-sm bg-bgLight border border-border rounded-lg text-textMain placeholder:text-textMuted/50 focus:outline-none focus:border-primary"
            />
            {search && (
              <button
                onClick={() => setSearch('')}
                className="absolute right-2 top-1/2 -translate-y-1/2 text-textMuted hover:text-textMain"
              >
                <X size={14} />
              </button>
            )}
          </div>

          {/* Type filter */}
          <select
            value={typeFilter}
            onChange={e => setTypeFilter(e.target.value)}
            className="px-2 py-1.5 text-sm bg-bgLight border border-border rounded-lg text-textMain focus:outline-none focus:border-primary"
          >
            <option value="">All Types</option>
            <option value="knowledge">Knowledge</option>
            <option value="experience">Experience</option>
            <option value="log">Log</option>
          </select>

          {/* Sort */}
          <select
            value={sort}
            onChange={e => setSort(e.target.value)}
            className="px-2 py-1.5 text-sm bg-bgLight border border-border rounded-lg text-textMain focus:outline-none focus:border-primary"
          >
            <option value="date_desc">Newest First</option>
            <option value="date_asc">Oldest First</option>
            <option value="importance">Importance</option>
          </select>

          {/* Bulk delete */}
          {selectedIds.size > 0 && (
            <button
              onClick={() => setConfirmDelete('__bulk__')}
              className="px-3 py-1.5 text-sm bg-red-500/10 text-red-400 rounded-lg hover:bg-red-500/20 transition-colors flex items-center gap-1.5"
            >
              <Trash2 size={14} />
              Delete {selectedIds.size}
            </button>
          )}
        </div>
      )}

      {/* ── Content ── */}
      <div className="flex-1 overflow-y-auto">
        {initialLoading ? (
          <div className="flex flex-col items-center justify-center h-64 gap-3">
            <Loader2 className="animate-spin text-primary" size={32} />
            <p className="text-textMuted text-sm">Loading...</p>
          </div>
        ) : error && memories.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-64 gap-3">
            <AlertCircle className="text-red-400" size={32} />
            <p className="text-red-400 text-sm">{error}</p>
            <button onClick={handleRefresh} className="text-xs text-primary hover:underline">Retry</button>
          </div>
        ) : !selectedAgent ? (
          <div className="flex flex-col items-center justify-center h-64 gap-3">
            <Database className="text-textMuted/30" size={48} />
            <p className="text-textMuted text-sm">Select an agent to view memories</p>
          </div>
        ) : showEmpty ? (
          <div className="flex flex-col items-center justify-center h-64 gap-3">
            <BrainCircuit className="text-textMuted/30" size={48} />
            <p className="text-textMuted text-sm">No memories found</p>
            {search && <p className="text-textMuted/60 text-xs">Try a different search term</p>}
          </div>
        ) : memories.length > 0 ? (
          <table className="w-full text-sm">
            <thead className="sticky top-0 bg-bgLight z-10">
              <tr className="border-b border-border text-textMuted text-xs uppercase tracking-wider">
                <th className="w-10 px-3 py-2 text-left">
                  <input
                    type="checkbox"
                    checked={memories.length > 0 && memories.every(m => selectedIds.has(m.id))}
                    onChange={toggleSelectAll}
                    className="rounded border-border accent-primary"
                  />
                </th>
                <th className="w-8 px-1 py-2" />
                <th className="px-2 py-2 text-left">Topic</th>
                <th className="px-2 py-2 text-left hidden sm:table-cell">Summary</th>
                <th className="w-20 px-2 py-2 text-left hidden md:table-cell">Category</th>
                <th className="w-16 px-2 py-2 text-center hidden lg:table-cell">Imp</th>
                <th className="w-28 px-2 py-2 text-left hidden md:table-cell">Date</th>
                <th className="w-12 px-2 py-2" />
              </tr>
            </thead>
            <tbody>
              {memories.map(m => {
                const isExpanded = expandedId === m.id;
                const isSelected = selectedIds.has(m.id);
                return (
                  <React.Fragment key={m.id}>
                    <tr
                      className={`border-b border-border/50 hover:bg-panel/50 transition-colors cursor-pointer ${
                        isSelected ? 'bg-primary/5' : ''
                      }`}
                      onClick={() => setExpandedId(isExpanded ? null : m.id)}
                    >
                      <td className="px-3 py-2.5" onClick={e => e.stopPropagation()}>
                        <input
                          type="checkbox"
                          checked={isSelected}
                          onChange={() => toggleSelect(m.id)}
                          className="rounded border-border accent-primary"
                        />
                      </td>
                      <td className="px-1 py-2.5">
                        <span className={`inline-flex items-center justify-center w-7 h-7 rounded-lg ${TYPE_COLORS[m.entry_type] || 'text-gray-400'}`}>
                          {TYPE_ICONS[m.entry_type] || <Info size={14} />}
                        </span>
                      </td>
                      <td className="px-2 py-2.5 max-w-[200px]">
                        <div className="flex items-center gap-1.5">
                          <span className="text-textMain font-medium truncate">{m.topic || '(untitled)'}</span>
                          <span className={`text-[10px] px-1.5 py-0.5 rounded-full ${TYPE_COLORS[m.entry_type] || ''} shrink-0`}>
                            {typeLabel(m.entry_type)}
                          </span>
                        </div>
                      </td>
                      <td className="px-2 py-2.5 max-w-[300px] hidden sm:table-cell">
                        <span className="text-textMuted truncate block">
                          {stripHtml(m.summary || m.body || '').substring(0, 80)}
                          {(m.summary || '').length > 80 || (!m.summary && (m.body || '').length > 80) ? '...' : ''}
                        </span>
                      </td>
                      <td className="px-2 py-2.5 hidden md:table-cell">
                        {m.category ? (
                          <span className="text-xs text-textMuted bg-bgLight px-2 py-0.5 rounded-full">
                            {m.category}
                          </span>
                        ) : (
                          <span className="text-textMuted/40 text-xs">-</span>
                        )}
                      </td>
                      <td className="px-2 py-2.5 text-center hidden lg:table-cell">
                        <span className={`text-[10px] px-1.5 py-0.5 rounded-full font-medium ${importanceLabel(m.importance).color}`}>
                          {importanceLabel(m.importance).label}
                        </span>
                      </td>
                      <td className="px-2 py-2.5 hidden md:table-cell">
                        <span className="text-textMuted text-xs whitespace-nowrap">{m.created_at}</span>
                      </td>
                      <td className="px-2 py-2.5 text-right" onClick={e => e.stopPropagation()}>
                        <div className="flex items-center gap-0.5">
                          <button
                            onClick={e => { e.stopPropagation(); setConfirmDelete(m.id); }}
                            className="p-1.5 rounded-lg text-textMuted hover:bg-red-500/10 hover:text-red-400 transition-colors"
                            title="Delete"
                          >
                            <Trash2 size={14} />
                          </button>
                          {isExpanded ? <ChevronUp size={14} className="text-textMuted" /> : <ChevronDown size={14} className="text-textMuted" />}
                        </div>
                      </td>
                    </tr>
                    {/* Expanded detail row */}
                    {isExpanded && (
                      <tr key={`${m.id}-detail`}>
                        <td colSpan={8} className="bg-panel/30 px-4 py-4 border-b border-border/50">
                          <div className="max-w-3xl space-y-3">
                            {/* Keywords */}
                            {m.keywords && m.keywords.length > 0 && (
                              <div className="flex items-start gap-2">
                                <span className="text-xs text-textMuted shrink-0 mt-0.5">Keywords:</span>
                                <div className="flex flex-wrap gap-1">
                                  {m.keywords.map(kw => (
                                    <span key={kw} className="text-xs px-2 py-0.5 bg-primary/10 text-primary rounded-full">
                                      {kw}
                                    </span>
                                  ))}
                                </div>
                              </div>
                            )}
                            {/* Full body */}
                            {m.body && (
                              <div>
                                <span className="text-xs text-textMuted block mb-1">Content:</span>
                                <div className="text-sm text-textMain bg-bgLight rounded-lg p-3 max-h-48 overflow-y-auto whitespace-pre-wrap font-mono text-xs leading-relaxed">
                                  {m.body}
                                </div>
                              </div>
                            )}
                            {!m.body && m.summary && (
                              <div>
                                <span className="text-xs text-textMuted block mb-1">Summary:</span>
                                <p className="text-sm text-textMain">{m.summary}</p>
                              </div>
                            )}
                            {/* Stats */}
                            <div className="flex items-center gap-4 text-xs text-textMuted">
                              <span className="flex items-center gap-1">
                                <Clock size={12} /> {m.created_at}
                              </span>
                              <span>ID: {m.id}</span>
                              {m.access_count > 0 && <span>Accessed: {m.access_count}x</span>}
                              {m.source && <span>Source: {m.source}</span>}
                            </div>
                          </div>
                        </td>
                      </tr>
                    )}
                  </React.Fragment>
                );
              })}
            </tbody>
          </table>
        ) : null}
      </div>

      {/* ── Delete confirmation modal ── */}
      {confirmDelete && (
        <div className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4">
          <div className="bg-panel rounded-xl border border-border p-6 max-w-sm w-full shadow-xl">
            <div className="flex items-center gap-3 mb-4">
              <div className="w-10 h-10 rounded-full bg-red-500/15 flex items-center justify-center">
                <Trash2 className="text-red-400" size={20} />
              </div>
              <div>
                <h3 className="text-base font-bold text-textMain">Delete Memory</h3>
                <p className="text-xs text-textMuted">
                  {confirmDelete === '__bulk__'
                    ? `Delete ${selectedIds.size} selected memories?`
                    : 'This will permanently delete this memory entry.'}
                </p>
              </div>
            </div>
            <p className="text-sm text-textMuted mb-5">
              {confirmDelete === '__bulk__'
                ? 'These memories will be removed from the database and will no longer surface during conversations.'
                : `Memory "${memories.find(m => m.id === confirmDelete)?.topic || confirmDelete}" will be permanently removed.`}
            </p>
            <div className="flex justify-end gap-2">
              <button
                onClick={() => setConfirmDelete(null)}
                disabled={deleting}
                className="px-4 py-2 text-sm text-textMain bg-bgLight rounded-lg hover:bg-border transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={() => confirmDelete === '__bulk__' ? handleBulkDelete() : handleDelete(confirmDelete)}
                disabled={deleting}
                className="px-4 py-2 text-sm bg-red-500 text-white rounded-lg hover:bg-red-600 transition-colors flex items-center gap-1.5"
              >
                {deleting ? <Loader2 size={14} className="animate-spin" /> : <Trash2 size={14} />}
                Delete
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

// ─── React adapter for PluginViewContainer ───

import { createRoot } from 'react-dom/client';

let root: ReturnType<typeof createRoot> | null = null;

export function mount(el: HTMLElement, props: { onBack: () => void }) {
  root = createRoot(el);
  root.render(<LongMemoryPanel onBack={props.onBack} />);
}

export function unmount(_el: HTMLElement) {
  if (root) {
    root.unmount();
    root = null;
  }
}
