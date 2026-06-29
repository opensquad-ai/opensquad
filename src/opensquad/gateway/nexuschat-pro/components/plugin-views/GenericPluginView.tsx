import React, { useState, useEffect, useCallback } from 'react';
import {
  ArrowLeft, RefreshCw, Loader2, AlertCircle, Database,
  Clock, ChevronDown, ChevronRight
} from 'lucide-react';
import { pluginAPI } from '../../services/api';

/**
 * GenericPluginView - Fallback view for plugins that have a data endpoint
 * but no custom dashboard component registered in the view registry.
 *
 * Renders the raw API response as an interactive, collapsible JSON tree
 * with a time range selector and auto-refresh.
 */

interface GenericPluginViewProps {
  pluginName: string;
  viewTitle: string;
  onBack: () => void;
}

const TIME_RANGES = [
  { value: '1h', label: '1H' },
  { value: '6h', label: '6H' },
  { value: '24h', label: '24H' },
  { value: '7d', label: '7D' },
  { value: '30d', label: '30D' },
  { value: 'all', label: 'All' },
];

function formatNumber(n: number): string {
  if (typeof n !== 'number') return String(n);
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M';
  if (n >= 1_000) return (n / 1_000).toFixed(1) + 'K';
  return String(n);
}

export const GenericPluginView: React.FC<GenericPluginViewProps> = ({
  pluginName, viewTitle, onBack
}) => {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [range, setRange] = useState('24h');

  const fetchData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await pluginAPI.getPluginData(pluginName, { range });
      if (result.error) {
        setError(result.error);
      } else {
        setData(result);
      }
    } catch (e: any) {
      setError(e.message || 'Failed to load data');
    } finally {
      setLoading(false);
    }
  }, [pluginName, range]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  return (
    <div className="flex-1 h-full bg-bgLight flex flex-col overflow-hidden">
      {/* Header */}
      <div className="px-6 py-4 border-b border-border bg-panel flex items-center gap-4 shrink-0">
        <button
          onClick={onBack}
          className="p-2 rounded-lg text-textMuted hover:bg-primary/10 hover:text-primary transition-colors"
        >
          <ArrowLeft size={20} />
        </button>
        <div className="flex items-center gap-3 flex-1">
          <div className="w-10 h-10 bg-cyan-500/15 rounded-xl flex items-center justify-center">
            <Database className="text-cyan-400" size={22} />
          </div>
          <div>
            <h1 className="text-lg font-bold text-textMain">{viewTitle}</h1>
            <p className="text-xs text-textMuted">Plugin: {pluginName}</p>
          </div>
        </div>

        {/* Time range */}
        <div className="flex gap-1 bg-bgLight rounded-lg p-1">
          {TIME_RANGES.map(tr => (
            <button
              key={tr.value}
              onClick={() => setRange(tr.value)}
              className={`px-2 py-1 rounded text-xs font-medium transition-colors ${
                range === tr.value
                  ? 'bg-primary/15 text-primary'
                  : 'text-textMuted hover:text-textMain'
              }`}
            >
              {tr.label}
            </button>
          ))}
        </div>

        <button
          onClick={fetchData}
          className="p-2 rounded-lg text-textMuted hover:bg-primary/10 hover:text-primary transition-colors"
          title="Refresh"
        >
          <RefreshCw size={18} className={loading ? 'animate-spin' : ''} />
        </button>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-6">
        {loading && !data ? (
          <div className="flex flex-col items-center justify-center h-64 gap-3">
            <Loader2 className="animate-spin text-primary" size={32} />
            <p className="text-textMuted text-sm">Loading data...</p>
          </div>
        ) : error && !data ? (
          <div className="flex flex-col items-center justify-center h-64 gap-3">
            <AlertCircle className="text-red-400" size={32} />
            <p className="text-red-400 text-sm">{error}</p>
            <button onClick={fetchData} className="text-xs text-primary hover:underline">Retry</button>
          </div>
        ) : data ? (
          <div className="space-y-4">
            {/* Summary cards for top-level numeric values */}
            <TopLevelSummary data={data} />

            {/* Collapsible JSON tree for full data */}
            <div className="bg-panel rounded-xl border border-border p-5">
              <h2 className="text-sm font-bold text-textMain mb-3">Raw Data</h2>
              <JsonTree data={data} depth={0} />
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
};

/** Extract top-level numeric/string fields as summary cards */
const TopLevelSummary: React.FC<{ data: any }> = ({ data }) => {
  if (!data || typeof data !== 'object') return null;

  // If there's a "summary" object, display its fields
  const summary = data.summary || {};
  const entries = Object.entries(summary).filter(
    ([, v]) => typeof v === 'number' || typeof v === 'string'
  );

  if (entries.length === 0) return null;

  return (
    <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-3">
      {entries.map(([key, value]) => (
        <div key={key} className="bg-panel rounded-xl border border-border p-4">
          <p className="text-lg font-bold text-textMain">
            {typeof value === 'number' ? formatNumber(value) : String(value)}
          </p>
          <p className="text-xs text-textMuted">{key.replace(/_/g, ' ')}</p>
        </div>
      ))}
    </div>
  );
};

/** Collapsible JSON tree renderer */
const JsonTree: React.FC<{ data: any; depth: number }> = ({ data, depth }) => {
  const [collapsed, setCollapsed] = useState(depth > 1);

  if (data === null || data === undefined) {
    return <span className="text-textMuted text-xs">null</span>;
  }

  if (typeof data === 'string') {
    return <span className="text-emerald-400 text-xs">"{data}"</span>;
  }

  if (typeof data === 'number' || typeof data === 'boolean') {
    return <span className="text-blue-400 text-xs">{String(data)}</span>;
  }

  if (Array.isArray(data)) {
    if (data.length === 0) {
      return <span className="text-textMuted text-xs">[]</span>;
    }
    return (
      <div className="ml-3">
        <button
          onClick={() => setCollapsed(!collapsed)}
          className="text-textMuted hover:text-textMain text-xs flex items-center gap-1"
        >
          {collapsed ? <ChevronRight size={12} /> : <ChevronDown size={12} />}
          Array [{data.length}]
        </button>
        {!collapsed && (
          <div className="ml-2 border-l border-border/50 pl-3 space-y-1 mt-1">
            {data.map((item, i) => (
              <div key={i} className="flex gap-2">
                <span className="text-textMuted text-[10px] shrink-0 w-4 text-right">{i}</span>
                <JsonTree data={item} depth={depth + 1} />
              </div>
            ))}
          </div>
        )}
      </div>
    );
  }

  if (typeof data === 'object') {
    const entries = Object.entries(data);
    if (entries.length === 0) {
      return <span className="text-textMuted text-xs">{'{}'}</span>;
    }
    return (
      <div className="ml-3">
        <button
          onClick={() => setCollapsed(!collapsed)}
          className="text-textMuted hover:text-textMain text-xs flex items-center gap-1"
        >
          {collapsed ? <ChevronRight size={12} /> : <ChevronDown size={12} />}
          Object {'{'}{entries.length}{'}'}
        </button>
        {!collapsed && (
          <div className="ml-2 border-l border-border/50 pl-3 space-y-1 mt-1">
            {entries.map(([key, value]) => (
              <div key={key} className="flex gap-2">
                <span className="text-amber-400 text-xs shrink-0">{key}:</span>
                <JsonTree data={value} depth={depth + 1} />
              </div>
            ))}
          </div>
        )}
      </div>
    );
  }

  return <span className="text-textMuted text-xs">{String(data)}</span>;
};
