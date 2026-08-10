import React, { useState, useEffect } from 'react';
import { GitBranch, User, Clock, CheckCircle, AlertCircle, ChevronDown, ChevronUp, Github, Filter, RefreshCw, Search, ArrowLeft } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { getAuthToken } from '../services/api';
import { OpenSquadLoader } from './OpenSquadLoader';

interface AuditLog {
  timestamp: string;
  agent_id: string;
  repo_name: string;
  action: string;
  arguments: any;
  output: string;
  status: 'success' | 'error';
}

export const VCSAuditTimeline: React.FC<{ onBack?: () => void }> = ({ onBack }) => {
  const { t } = useTranslation();
  const [repos, setRepos] = useState<string[]>([]);
  const [selectedRepo, setSelectedRepo] = useState<string>('');
  const [logs, setLogs] = useState<AuditLog[]>([]);
  const [loading, setLoading] = useState(false);
  const [expandedLog, setExpandedLog] = useState<number | null>(null);
  const [searchQuery, setSearchQuery] = useState('');

  const fetchRepos = async () => {
    try {
      const resp = await fetch('/api/ai-web/audit/repos', {
        headers: { 'Authorization': `Bearer ${getAuthToken()}` }
      });
      const data = await resp.json();
      setRepos(data.repos || []);
      if (data.repos?.length > 0 && !selectedRepo) {
        setSelectedRepo(data.repos[0]);
      }
    } catch (error) {
      console.error('Failed to fetch repos:', error);
    }
  };

  const fetchLogs = async (repo: string) => {
    if (!repo) return;
    setLoading(true);
    try {
      const resp = await fetch(`/api/ai-web/audit/logs?repo=${encodeURIComponent(repo)}`, {
        headers: { 'Authorization': `Bearer ${getAuthToken()}` }
      });
      const data = await resp.json();
      setLogs(data.logs || []);
    } catch (error) {
      console.error('Failed to fetch logs:', error);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchRepos();
  }, []);

  useEffect(() => {
    if (selectedRepo) {
      fetchLogs(selectedRepo);
    }
  }, [selectedRepo]);

  const filteredLogs = logs.filter(log =>
    log.action.toLowerCase().includes(searchQuery.toLowerCase()) ||
    log.agent_id.toLowerCase().includes(searchQuery.toLowerCase()) ||
    JSON.stringify(log.arguments).toLowerCase().includes(searchQuery.toLowerCase())
  );

  return (
    <div className="flex h-full bg-bgMain animate-in fade-in duration-300">
      {/* Sidebar: Repo List */}
      <div className="w-64 border-r border-border bg-bgLight flex flex-col">
        <div className="p-4 border-b border-border flex items-center justify-between">
          <div className="flex items-center gap-2">
            {onBack && (
              <button
                onClick={onBack}
                className="p-1 rounded-lg text-textMuted hover:bg-primary/10 hover:text-primary transition-colors"
                title={t('common.back', 'Back')}
              >
                <ArrowLeft size={16} />
              </button>
            )}
            <h2 className="font-bold text-textMain flex items-center gap-2">
              <Github size={18} />
              {t('vcsAudit.projects', 'Projects')}
            </h2>
          </div>
          <button onClick={fetchRepos} className="p-1 hover:bg-primary/10 rounded-md text-textMuted hover:text-primary transition-colors">
            <RefreshCw size={14} />
          </button>
        </div>
        <div className="flex-1 overflow-y-auto p-2 space-y-1">
          {repos.length === 0 ? (
            <div className="text-xs text-textMuted text-center mt-8 px-4">
              {t('vcsAudit.noRepos', 'No GitHub projects tracked yet.')}
            </div>
          ) : (
            repos.map(repo => (
              <button
                key={repo}
                onClick={() => setSelectedRepo(repo)}
                className={`w-full text-left px-3 py-2 rounded-lg text-sm transition-all flex items-center gap-2 ${
                  selectedRepo === repo
                    ? 'bg-primary text-white shadow-md'
                    : 'text-textMain hover:bg-primary/10'
                }`}
              >
                <GitBranch size={14} className={selectedRepo === repo ? 'text-white' : 'text-primary'} />
                <span className="truncate">{repo}</span>
              </button>
            ))
          )}
        </div>
      </div>

      {/* Main Content: Timeline */}
      <div className="flex-1 flex flex-col min-w-0">
        <div className="p-4 border-b border-border bg-panel flex items-center justify-between sticky top-0 z-10">
          <div className="flex items-center gap-4">
            <h1 className="text-lg font-bold text-textMain truncate max-w-[300px]">
              {selectedRepo || t('vcsAudit.selectProject', 'Select a Project')}
            </h1>
            <div className="relative group">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-textMuted group-focus-within:text-primary transition-colors" size={14} />
              <input
                type="text"
                placeholder={t('vcsAudit.search', 'Search footprints...')}
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="pl-9 pr-4 py-1.5 bg-bgMain border border-border rounded-full text-sm focus:outline-none focus:ring-2 focus:ring-primary/30 w-64 transition-all"
              />
            </div>
          </div>
          <div className="flex items-center gap-2">
             <div className="text-xs text-textMuted flex items-center gap-1 bg-bgLight px-2 py-1 rounded-md border border-border">
                <Clock size={12} />
                {logs.length} Operations
             </div>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto p-6 bg-bgMain relative">
          {loading && (
            <div className="absolute inset-0 bg-bgMain/50 backdrop-blur-[1px] flex items-center justify-center z-20">
               <OpenSquadLoader size={24} />
            </div>
          )}

          <div className="max-w-4xl mx-auto">
            {filteredLogs.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-20 text-textMuted">
                <Filter size={48} className="opacity-20 mb-4" />
                <p>{t('vcsAudit.noLogs', 'No activity found for this filter.')}</p>
              </div>
            ) : (
              <div className="space-y-6">
                {filteredLogs.map((log, index) => (
                  <div key={index} className="relative pl-8 before:absolute before:left-3 before:top-8 before:bottom-[-24px] before:w-px before:bg-border last:before:hidden">
                    {/* Timeline Dot */}
                    <div className={`absolute left-0 top-1 w-6 h-6 rounded-full flex items-center justify-center border-2 border-bgMain z-10 ${
                      log.status === 'success' ? 'bg-green-100 text-green-600' : 'bg-red-100 text-red-600'
                    }`}>
                      {log.status === 'success' ? <CheckCircle size={14} /> : <AlertCircle size={14} />}
                    </div>

                    {/* Card */}
                    <div className={`bg-panel border border-border rounded-xl shadow-sm overflow-hidden transition-all hover:shadow-md ${
                      expandedLog === index ? 'ring-1 ring-primary/30' : ''
                    }`}>
                      <div
                        className="p-4 cursor-pointer flex items-center justify-between"
                        onClick={() => setExpandedLog(expandedLog === index ? null : index)}
                      >
                        <div className="flex items-center gap-4 min-w-0">
                          <div className="bg-primary/10 px-2 py-1 rounded text-[10px] font-bold text-primary uppercase tracking-wider shrink-0">
                            {log.action}
                          </div>
                          <div className="flex items-center gap-1.5 text-sm text-textMain font-medium truncate">
                            <User size={14} className="text-textMuted" />
                            {log.agent_id}
                          </div>
                          <div className="text-xs text-textMuted flex items-center gap-1 shrink-0">
                            <Clock size={12} />
                            {new Date(log.timestamp).toLocaleString()}
                          </div>
                        </div>
                        <div className="text-textMuted">
                          {expandedLog === index ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
                        </div>
                      </div>

                      {expandedLog === index && (
                        <div className="px-4 pb-4 border-t border-border bg-bgLight/30 animate-in slide-in-from-top duration-200">
                          <div className="mt-4 space-y-4">
                            <div>
                              <h4 className="text-[10px] font-bold text-textMuted uppercase mb-1.5 tracking-widest">{t('vcsAudit.arguments', 'Arguments')}</h4>
                              <pre className="p-3 bg-bgMain rounded-lg text-xs font-mono text-textMain overflow-x-auto border border-border">
                                {JSON.stringify(log.arguments, null, 2)}
                              </pre>
                            </div>
                            <div>
                              <h4 className="text-[10px] font-bold text-textMuted uppercase mb-1.5 tracking-widest">{t('vcsAudit.output', 'Raw Output')}</h4>
                              <pre className="p-3 bg-bgMain rounded-lg text-xs font-mono text-textMain whitespace-pre-wrap max-h-60 overflow-y-auto border border-border">
                                {log.output || '(No output)'}
                              </pre>
                            </div>
                          </div>
                        </div>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};
