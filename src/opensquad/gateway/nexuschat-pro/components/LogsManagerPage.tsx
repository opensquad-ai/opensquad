import React, { useState, useEffect, useRef, useCallback } from 'react';
import {
  FileText, RefreshCw, ChevronDown, Search, X,
  Activity, Server, Bot, AlertTriangle, AlertCircle, Info, Bug,
  Download, Filter, Menu, ArrowLeft
} from 'lucide-react';
import { adminAPI, logsAPI, AdminAgent, LogFileInfo } from '../services/api';
import {
  adminHeaderBar,
  adminHeaderGhostBtn,
  adminHeaderIcon,
  adminHeaderIconBox,
  adminHeaderNavBtn,
  adminHeaderSubtitle,
  adminHeaderTitle,
} from './admin/adminShellStyles';
import { OpenSquadLoader } from './OpenSquadLoader';

// ============================================================
// Types
// ============================================================

type LogLevel = 'all' | 'debug' | 'info' | 'warning' | 'error';

interface LogSource {
  id: string;           // unique key
  label: string;        // display name
  kind: 'system' | 'agent';
  fileKey?: string;     // for system logs
  agentName?: string;   // for agent logs
}

interface ParsedLine {
  raw: string;
  level: LogLevel;
}

// ============================================================
// Helpers
// ============================================================

const LEVEL_PATTERNS: Array<[RegExp, LogLevel]> = [
  [/\b(ERROR|CRITICAL|FATAL|EXCEPTION)\b/i,  'error'],
  // Python exception class names: AttributeError: / ValueError: / RuntimeException: etc.
  [/\b[A-Za-z]+Error:/,                       'error'],
  [/\b[A-Za-z]+Exception:/,                   'error'],
  // Python traceback header line
  [/Traceback \(most recent call last\)/,      'error'],
  [/\b(WARNING|WARN)\b/i,                      'warning'],
  [/\bDEBUG\b/i,                               'debug'],
  [/\bINFO\b/i,                                'info'],
];

function detectLevel(line: string): LogLevel {
  for (const [re, level] of LEVEL_PATTERNS) {
    if (re.test(line)) return level;
  }
  return 'info';
}

function parseLines(rawLines: string[]): ParsedLine[] {
  return rawLines.map(raw => ({ raw, level: detectLevel(raw) }));
}

const LEVEL_STYLES: Record<LogLevel, string> = {
  all:     'text-green-300',
  debug:   'text-gray-400',
  info:    'text-green-300',
  warning: 'text-amber-300',
  error:   'text-red-400',
};

const LEVEL_BG: Record<LogLevel, string> = {
  all:     '',
  debug:   '',
  info:    '',
  warning: 'bg-amber-900/20',
  error:   'bg-red-900/30',
};

const LEVEL_FILTER_STYLES: Record<Exclude<LogLevel, 'all'>, string> = {
  debug:   'bg-gray-500/20 text-gray-300 border-gray-500/30 hover:border-gray-400/60',
  info:    'bg-green-500/20 text-green-300 border-green-500/30 hover:border-green-400/60',
  warning: 'bg-amber-500/20 text-amber-300 border-amber-500/30 hover:border-amber-400/60',
  error:   'bg-red-500/20 text-red-300 border-red-500/30 hover:border-red-400/60',
};

function highlightLine(line: string): React.ReactNode {
  const level = detectLevel(line);
  return (
    <span className={LEVEL_STYLES[level]}>
      {line}
    </span>
  );
}

function countByLevel(lines: ParsedLine[]): Record<Exclude<LogLevel, 'all'>, number> {
  const counts = { debug: 0, info: 0, warning: 0, error: 0 };
  for (const l of lines) {
    if (l.level !== 'all') counts[l.level]++;
  }
  return counts;
}

// ============================================================
// Component
// ============================================================

interface LogsManagerPageProps {
  onBack: () => void;
}

const SYSTEM_SOURCE_LABELS: Record<string, string> = {
  backend: 'Backend',
  backend_startup: 'Startup',
  websocket: 'WebSocket',
  ws_auth: 'WS Auth',
};

export const LogsManagerPage: React.FC<LogsManagerPageProps> = ({ onBack }) => {
  // ---- sources ----
  const [systemFiles, setSystemFiles] = useState<LogFileInfo[]>([]);
  const [agents, setAgents] = useState<AdminAgent[]>([]);
  const [sourcesLoading, setSourcesLoading] = useState(true);

  // ---- selected source ----
  const [selectedSource, setSelectedSource] = useState<LogSource | null>(null);

  // ---- log data ----
  const [rawLines, setRawLines] = useState<string[]>([]);
  const [parsedLines, setParsedLines] = useState<ParsedLine[]>([]);
  const [logsLoading, setLogsLoading] = useState(false);

  // ---- filters ----
  const [levelFilter, setLevelFilter] = useState<LogLevel>('all');
  const [searchText, setSearchText] = useState('');
  const [lineCount, setLineCount] = useState(500);

  // ---- auto refresh ----
  const [autoRefresh, setAutoRefresh] = useState(false); // 默认关闭，避免在日志面板未打开时占用后端连接
  const autoRefreshRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const logEndRef = useRef<HTMLDivElement>(null);
  const logContainerRef = useRef<HTMLDivElement>(null);
  const [pinnedBottom, setPinnedBottom] = useState(true);

  // ============================================================
  // Load sources
  // ============================================================

  const loadSources = useCallback(async () => {
    setSourcesLoading(true);
    try {
      const [filesRes, agentsRes] = await Promise.all([
        logsAPI.listLogFiles(),
        adminAPI.getAgents(),
      ]);
      setSystemFiles(filesRes.files);
      setAgents(agentsRes.agents || []);

      // Auto-select first available source
      const firstFile = filesRes.files.find(f => f.exists);
      if (firstFile && !selectedSource) {
        setSelectedSource({
          id: `sys_${firstFile.key}`,
          label: SYSTEM_SOURCE_LABELS[firstFile.key] || firstFile.key,
          kind: 'system',
          fileKey: firstFile.key,
        });
      }
    } catch (e) {
      console.error('Failed to load log sources:', e);
    } finally {
      setSourcesLoading(false);
    }
  }, []);

  useEffect(() => { loadSources(); }, [loadSources]);

  // ============================================================
  // Fetch logs
  // ============================================================

  const fetchLogs = useCallback(async (src: LogSource | null = selectedSource, silent = false) => {
    if (!src) return;
    if (!silent) setLogsLoading(true);
    try {
      let lines: string[] = [];
      if (src.kind === 'system' && src.fileKey) {
        const res = await logsAPI.getSystemLogs(src.fileKey, lineCount);
        lines = res.logs || [];
      } else if (src.kind === 'agent' && src.agentName) {
        const res = await logsAPI.getAgentLogs(src.agentName, lineCount);
        lines = res.logs || [];
      }
      setRawLines(lines);
      setParsedLines(parseLines(lines));
      if (pinnedBottom) {
        setTimeout(() => logEndRef.current?.scrollIntoView({ behavior: 'smooth' }), 80);
      }
    } catch (e: any) {
      setRawLines([`[ERROR] Failed to load logs: ${e.message}`]);
      setParsedLines([{ raw: `[ERROR] Failed to load logs: ${e.message}`, level: 'error' }]);
    } finally {
      if (!silent) setLogsLoading(false);
    }
  }, [selectedSource, lineCount, pinnedBottom]);

  // Fetch when source changes
  useEffect(() => {
    if (selectedSource) {
      setRawLines([]);
      setParsedLines([]);
      setLevelFilter('all');
      setSearchText('');
      fetchLogs(selectedSource);
    }
  }, [selectedSource]);

  // Fetch when lineCount changes
  useEffect(() => {
    if (selectedSource) fetchLogs(selectedSource);
  }, [lineCount]);

  // ============================================================
  // Auto refresh
  // ============================================================

  useEffect(() => {
    if (autoRefresh && selectedSource) {
      autoRefreshRef.current = setInterval(() => fetchLogs(undefined, true), 30000);
    }
    return () => {
      if (autoRefreshRef.current) {
        clearInterval(autoRefreshRef.current);
        autoRefreshRef.current = null;
      }
    };
  }, [autoRefresh, selectedSource, fetchLogs]);

  // ============================================================
  // Filtered lines
  // ============================================================

  const filteredLines = React.useMemo(() => {
    let lines = parsedLines;
    if (levelFilter !== 'all') {
      lines = lines.filter(l => l.level === levelFilter);
    }
    if (searchText.trim()) {
      const q = searchText.toLowerCase();
      lines = lines.filter(l => l.raw.toLowerCase().includes(q));
    }
    return lines;
  }, [parsedLines, levelFilter, searchText]);

  const counts = React.useMemo(() => countByLevel(parsedLines), [parsedLines]);

  // ============================================================
  // Download logs
  // ============================================================

  const handleDownload = () => {
    const text = filteredLines.map(l => l.raw).join('\n');
    const blob = new Blob([text], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${selectedSource?.id || 'logs'}_${new Date().toISOString().slice(0, 19).replace(/:/g, '-')}.log`;
    a.click();
    URL.revokeObjectURL(url);
  };

  // ============================================================
  // Source select helper
  // ============================================================

  const selectSource = (src: LogSource) => {
    if (src.id === selectedSource?.id) return;
    setSelectedSource(src);
  };

  // ============================================================
  // Scroll handler
  // ============================================================

  const handleScroll = () => {
    const el = logContainerRef.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
    setPinnedBottom(atBottom);
  };

  // ============================================================
  // Render
  // ============================================================

  const LEVEL_ICONS: Record<Exclude<LogLevel, 'all'>, React.ReactNode> = {
    debug:   <Bug size={13} />,
    info:    <Info size={13} />,
    warning: <AlertTriangle size={13} />,
    error:   <AlertCircle size={13} />,
  };

  // Mobile source selector dropdown
  const [sourceDropdownOpen, setSourceDropdownOpen] = useState(false);
  const sourceBtnRef = useRef<HTMLButtonElement>(null);
  const [sourceDropdownPos, setSourceDropdownPos] = useState<{ top: number; left: number } | null>(null);

  return (
    <div className="flex h-full min-h-0 flex-1 flex-col overflow-hidden bg-bgLight">

      {/* ── Header ── */}
      <div className={`${adminHeaderBar} justify-between overflow-x-auto whitespace-nowrap no-scrollbar`}>
        <div className="flex items-center gap-2 md:gap-2.5 shrink-0">
          <button
            type="button"
            onClick={onBack}
            className={adminHeaderNavBtn}
            title="Back"
            aria-label="Back"
          >
            <ArrowLeft size={16} />
          </button>
          <button
            onClick={() => window.dispatchEvent(new CustomEvent('openMobileNav'))}
            className={`${adminHeaderNavBtn} md:hidden`}
            aria-label="Navigation menu"
          >
            <Menu size={16} />
          </button>
          <div className={adminHeaderIconBox}>
            <Activity size={14} className={adminHeaderIcon} />
          </div>
          <div className="flex items-baseline gap-1.5 shrink-0">
            <h2 className={adminHeaderTitle}>Logs</h2>
            <p className={adminHeaderSubtitle}>System & Agent Log Viewer</p>
          </div>

          {/* Mobile Source Selector Dropdown */}
          <div className="md:hidden relative shrink-0">
            <button
              ref={sourceBtnRef}
              onClick={() => {
                if (!sourceDropdownOpen && sourceBtnRef.current) {
                  const rect = sourceBtnRef.current.getBoundingClientRect();
                  setSourceDropdownPos({ top: rect.bottom + 4, left: rect.left });
                }
                setSourceDropdownOpen(v => !v);
              }}
              className="flex items-center gap-1.5 px-2 py-1 rounded-lg bg-bgLight border border-border text-[11px] font-medium text-textMain max-w-[120px]"
            >
              <span className="truncate">{selectedSource?.label || 'Source'}</span>
              <ChevronDown size={12} className="text-textMuted shrink-0" />
            </button>
          </div>
        </div>

        <div className="flex items-center gap-1.5 md:gap-2 shrink-0">
          <button
            onClick={() => fetchLogs()}
            disabled={logsLoading || !selectedSource}
            className="p-1.5 md:px-3 md:py-1.5 rounded-lg bg-primary/10 text-primary text-xs font-medium hover:bg-primary/20 transition-colors disabled:opacity-50"
          >
            {logsLoading ? <OpenSquadLoader size={16} /> : <RefreshCw size={13} />}
            <span className="hidden md:inline ml-1.5">Refresh</span>
          </button>
          <button
            onClick={() => setAutoRefresh(v => !v)}
            disabled={!selectedSource}
            className={`flex items-center gap-1.5 px-2 md:px-3 py-1.5 rounded-lg text-xs font-medium transition-colors disabled:opacity-50 ${
              autoRefresh
                ? 'bg-green-500/20 text-green-600 border border-green-500/30'
                : 'bg-bgLight text-textMuted border border-border hover:text-textMain'
            }`}
          >
            {autoRefresh ? <OpenSquadLoader size={14} /> : <RefreshCw size={13} />}
            <span className="hidden md:inline">{autoRefresh ? 'Auto ON' : 'Auto OFF'}</span>
          </button>
          <button
            onClick={handleDownload}
            disabled={filteredLines.length === 0}
            className="flex items-center gap-1.5 px-2 md:px-3 py-1.5 rounded-lg bg-bgLight text-textMuted border border-border text-xs font-medium hover:text-textMain transition-colors disabled:opacity-50"
          >
            <Download size={13} /> <span className="hidden md:inline">Export</span>
          </button>
        </div>
      </div>

      <div className="flex min-h-0 flex-1 overflow-hidden">

        {/* ── Left: Source selector ── */}
        <div className="w-48 bg-panel border-r border-border shrink-0 overflow-y-auto hidden md:flex flex-col">
          {sourcesLoading ? (
            <div className="flex items-center justify-center h-16 text-textMuted">
              <OpenSquadLoader size={20} />
            </div>
          ) : (
            <>
              {/* System logs section */}
              {systemFiles.length > 0 && (
                <div>
                  <div className="px-3 py-2 flex items-center gap-1.5">
                    <Server size={12} className="text-textMuted" />
                    <span className="text-[10px] font-bold text-textMuted uppercase tracking-widest">Gateway</span>
                  </div>
                  {systemFiles.map(f => {
                    const src: LogSource = {
                      id: `sys_${f.key}`,
                      label: SYSTEM_SOURCE_LABELS[f.key] || f.key,
                      kind: 'system',
                      fileKey: f.key,
                    };
                    const active = selectedSource?.id === src.id;
                    return (
                      <button
                        key={f.key}
                        onClick={() => selectSource(src)}
                        className={`w-full text-left px-3 py-2 text-sm flex items-center gap-2 transition-colors ${
                          active
                            ? 'bg-primary/10 text-primary font-medium'
                            : f.exists
                              ? 'text-textMain hover:bg-primary/5'
                              : 'text-textMuted opacity-50 cursor-default'
                        }`}
                        disabled={!f.exists}
                      >
                        <FileText size={13} className="shrink-0" />
                        <span className="truncate">{src.label}</span>
                        {!f.exists && (
                          <span className="ml-auto text-[9px] text-textMuted">N/A</span>
                        )}
                      </button>
                    );
                  })}
                </div>
              )}

              {/* Agent logs section */}
              {agents.length > 0 && (
                <div>
                  <div className="px-3 pt-3 pb-2 flex items-center gap-1.5">
                    <Bot size={12} className="text-textMuted" />
                    <span className="text-[10px] font-bold text-textMuted uppercase tracking-widest">Agents</span>
                  </div>
                  {agents.map(a => {
                    const name = a.dir_name || a.agent_id;
                    const src: LogSource = {
                      id: `agent_${name}`,
                      label: a.agent_name || name,
                      kind: 'agent',
                      agentName: name,
                    };
                    const active = selectedSource?.id === src.id;
                    const isRunning = a.process_status === 'running';
                    return (
                      <button
                        key={name}
                        onClick={() => selectSource(src)}
                        className={`w-full text-left px-3 py-2 text-sm flex items-center gap-2 transition-colors ${
                          active
                            ? 'bg-primary/10 text-primary font-medium'
                            : 'text-textMain hover:bg-primary/5'
                        }`}
                      >
                        <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${
                          isRunning ? 'bg-green-500' : 'bg-gray-400'
                        }`} />
                        <span className="truncate text-xs">{src.label}</span>
                      </button>
                    );
                  })}
                </div>
              )}
            </>
          )}
        </div>

        {/* ── Right: Log viewer ── */}
        <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">

          {!selectedSource ? (
            <div className="flex-1 flex flex-col items-center justify-center text-textMuted">
              <Activity size={40} className="mb-3 opacity-30" />
              <p className="text-sm">Select a log source from the left panel</p>
            </div>
          ) : (
            <>
              {/* ── Toolbar ── */}
              <div className="border-b border-border bg-panel shrink-0 flex flex-col">
                {/* Row 1: Level filters (Scrollable on mobile) */}
                <div className="px-3 md:px-4 py-1.5 border-b border-border/50 flex items-center gap-1.5 overflow-x-auto whitespace-nowrap scrollbar-hide">
                  <Filter size={13} className="text-textMuted shrink-0" />
                  {/* ALL button */}
                  <button
                    onClick={() => setLevelFilter('all')}
                    className={`shrink-0 px-2.5 py-1 rounded-md text-[10px] md:text-xs font-medium border transition-colors ${
                      levelFilter === 'all'
                        ? 'bg-primary/20 text-primary border-primary/40'
                        : 'bg-transparent text-textMuted border-border hover:text-textMain'
                    }`}
                  >
                    ALL {parsedLines.length}
                  </button>
                  {(['debug', 'info', 'warning', 'error'] as Exclude<LogLevel, 'all'>[]).map(lv => (
                    <button
                      key={lv}
                      onClick={() => setLevelFilter(levelFilter === lv ? 'all' : lv)}
                      className={`shrink-0 flex items-center gap-1 px-2.5 py-1 rounded-md text-[10px] md:text-xs font-medium border transition-colors ${
                        levelFilter === lv
                          ? LEVEL_FILTER_STYLES[lv] + ' border-opacity-80 shadow-sm'
                          : counts[lv] > 0
                            ? 'bg-transparent text-textMuted border-border hover:' + LEVEL_FILTER_STYLES[lv]
                            : 'bg-transparent text-textMuted/40 border-border/40 cursor-default'
                      }`}
                      disabled={counts[lv] === 0 && levelFilter !== lv}
                    >
                      {LEVEL_ICONS[lv]}
                      <span className="uppercase">{lv}</span>
                      <span className="opacity-70">{counts[lv]}</span>
                    </button>
                  ))}
                </div>

                {/* Row 2: Search + Limit + Stats */}
                <div className="px-3 md:px-4 py-1.5 flex items-center gap-2 overflow-x-auto whitespace-nowrap scrollbar-hide">
                  {/* Search */}
                  <div className="relative flex items-center flex-1 min-w-[100px] md:flex-none md:w-48">
                    <Search size={12} className="absolute left-2.5 text-textMuted pointer-events-none" />
                    <input
                      type="text"
                      value={searchText}
                      onChange={e => setSearchText(e.target.value)}
                      placeholder="Search..."
                      className="w-full pl-7 pr-7 py-1 bg-bgLight border border-border rounded-lg text-xs focus:outline-none focus:border-primary/50 text-textMain placeholder-textMuted"
                    />
                    {searchText && (
                      <button
                        onClick={() => setSearchText('')}
                        className="absolute right-2 text-textMuted hover:text-textMain"
                      >
                        <X size={12} />
                      </button>
                    )}
                  </div>

                  {/* Line count selector */}
                  <div className="relative flex items-center shrink-0">
                    <select
                      value={lineCount}
                      onChange={e => setLineCount(Number(e.target.value))}
                      className="appearance-none pl-2 pr-6 py-1 bg-bgLight border border-border rounded-lg text-xs focus:outline-none focus:border-primary/50 text-textMain cursor-pointer min-w-[70px]"
                    >
                      <option value={200}>200</option>
                      <option value={500}>500</option>
                      <option value={1000}>1k</option>
                      <option value={2000}>2k</option>
                      <option value={5000}>5k</option>
                    </select>
                    <ChevronDown size={11} className="absolute right-2 text-textMuted pointer-events-none" />
                  </div>

                  {/* Match count */}
                  <span className="text-[10px] md:text-xs text-textMuted shrink-0 font-medium bg-bgLight px-1.5 py-0.5 rounded border border-border/50">
                    {filteredLines.length}/{parsedLines.length}
                  </span>
                </div>
              </div>

              {/* ── Log output ── */}
              <div
                ref={logContainerRef}
                onScroll={handleScroll}
                className="min-h-0 flex-1 overflow-auto bg-gray-950 p-3 font-mono text-[11.5px] leading-relaxed"
              >
                {logsLoading && parsedLines.length === 0 ? (
                  <div className="flex items-center gap-2 p-4 text-gray-500">
                    <OpenSquadLoader size={20} />
                  </div>
                ) : filteredLines.length === 0 ? (
                  <div className="p-4 text-gray-500">
                    {searchText || levelFilter !== 'all'
                      ? 'No lines match the current filter.'
                      : 'No log entries found.'}
                  </div>
                ) : (
                  <div className="min-w-max">
                    {filteredLines.map((line, i) => (
                      <div
                        key={i}
                        className={`whitespace-pre py-px px-1.5 rounded ${LEVEL_BG[line.level]} hover:bg-white/5`}
                      >
                        {highlightLine(line.raw)}
                      </div>
                    ))}
                  </div>
                )}
                <div ref={logEndRef} />
              </div>

              {/* ── Scroll-to-bottom hint ── */}
              {!pinnedBottom && (
                <button
                  onClick={() => {
                    logEndRef.current?.scrollIntoView({ behavior: 'smooth' });
                    setPinnedBottom(true);
                  }}
                  className="absolute bottom-6 right-8 z-10 px-3 py-1.5 bg-primary text-white text-xs rounded-full shadow-lg hover:opacity-90 transition-opacity flex items-center gap-1"
                >
                  <ChevronDown size={13} /> Jump to bottom
                </button>
              )}
            </>
          )}
        </div>
      </div>
      {/* Mobile Source Selector Dropdown (Moved to avoid clipping & forced vertical) */}
      {sourceDropdownOpen && sourceDropdownPos && (
        <>
          <div className="fixed inset-0 z-[100]" onClick={() => setSourceDropdownOpen(false)} />
          <div
            className="fixed w-48 bg-panel border border-border rounded-xl shadow-2xl z-[101] py-1 max-h-[60vh] overflow-y-auto flex flex-col whitespace-normal"
            style={{ top: sourceDropdownPos.top, left: sourceDropdownPos.left }}
          >
            <div className="px-3 py-1 text-[10px] font-bold text-textMuted uppercase shrink-0">Gateway</div>
            {systemFiles.map(f => (
              <button
                key={f.key}
                onClick={() => {
                  selectSource({ id: `sys_${f.key}`, label: SYSTEM_SOURCE_LABELS[f.key] || f.key, kind: 'system', fileKey: f.key });
                  setSourceDropdownOpen(false);
                }}
                className={`w-full text-left px-4 py-2 text-xs transition-colors shrink-0 ${selectedSource?.id === `sys_${f.key}` ? 'bg-primary/10 text-primary font-medium' : 'text-textMain hover:bg-bgLight'}`}
              >
                {SYSTEM_SOURCE_LABELS[f.key] || f.key}
              </button>
            ))}
            <div className="px-3 py-1 mt-2 text-[10px] font-bold text-textMuted uppercase border-t border-border/50 pt-2 shrink-0">Agents</div>
            {agents.map(a => (
              <button
                key={a.dir_name}
                onClick={() => {
                  selectSource({ id: `agent_${a.dir_name}`, label: a.agent_name || a.dir_name, kind: 'agent', agentName: a.dir_name });
                  setSourceDropdownOpen(false);
                }}
                className={`w-full text-left px-4 py-2 text-xs transition-colors shrink-0 ${selectedSource?.id === `agent_${a.dir_name}` ? 'bg-primary/10 text-primary font-medium' : 'text-textMain hover:bg-bgLight'}`}
              >
                {a.agent_name || a.dir_name}
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  );
};

export default LogsManagerPage;
