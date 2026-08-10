import React, { useState, useEffect, useCallback } from 'react';
import {
  Server, Plus, Trash2, Save, ChevronDown, ChevronUp,
  ToggleLeft, ToggleRight, AlertCircle, Check, Menu, ArrowLeft,
} from 'lucide-react';
import { mcpAPI, McpServerConfig } from '../services/api';
import { useTranslation } from 'react-i18next';
import {
  adminHeaderBar,
  adminHeaderCta,
  adminHeaderGhostBtn,
  adminHeaderIcon,
  adminHeaderIconBox,
  adminHeaderNavBtn,
  adminHeaderTitle,
} from './admin/adminShellStyles';
import { OpenSquadLoader } from './OpenSquadLoader';

interface McpManagerPageProps {
  onBack: () => void;
}

// 空白服务器模板
const EMPTY_SERVER: McpServerConfig = {
  enabled: true,
  command: 'npx',
  args: [],
  timeout: 30,
  env: {},
  autoApprove: [],
};

// 将 env 对象转为 "KEY=VALUE\nKEY=VALUE" 字符串
function envToText(env?: Record<string, string>): string {
  if (!env) return '';
  return Object.entries(env).map(([k, v]) => `${k}=${v}`).join('\n');
}

// 将 "KEY=VALUE\n..." 字符串解析为对象
function textToEnv(text: string): Record<string, string> {
  const result: Record<string, string> = {};
  for (const line of text.split('\n')) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    const idx = trimmed.indexOf('=');
    if (idx > 0) {
      result[trimmed.slice(0, idx).trim()] = trimmed.slice(idx + 1);
    }
  }
  return result;
}

// 单个 MCP 服务器编辑卡片
const McpServerCard: React.FC<{
  serverName: string;
  config: McpServerConfig;
  onChange: (name: string, config: McpServerConfig) => void;
  onDelete: (name: string) => void;
  onRename: (oldName: string, newName: string) => void;
  saving: boolean;
}> = ({ serverName, config, onChange, onDelete, onRename, saving }) => {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(false);
  const [editName, setEditName] = useState(serverName);
  const [argsText, setArgsText] = useState(config.args.join('\n'));
  const [envText, setEnvText] = useState(envToText(config.env));
  const [autoApproveText, setAutoApproveText] = useState((config.autoApprove || []).join('\n'));

  // 同步外部 name 变化（如重命名）
  useEffect(() => { setEditName(serverName); }, [serverName]);

  const handleBlurName = () => {
    const trimmed = editName.trim();
    if (trimmed && trimmed !== serverName) {
      onRename(serverName, trimmed);
    } else {
      setEditName(serverName);
    }
  };

  const applyChanges = useCallback(() => {
    const args = argsText.split('\n').map(s => s.trim()).filter(Boolean);
    const env = textToEnv(envText);
    const autoApprove = autoApproveText.split('\n').map(s => s.trim()).filter(Boolean);
    onChange(serverName, {
      ...config,
      args,
      env: Object.keys(env).length > 0 ? env : undefined,
      autoApprove: autoApprove.length > 0 ? autoApprove : undefined,
    });
  }, [serverName, config, argsText, envText, autoApproveText, onChange]);

  const toggleEnabled = () => {
    onChange(serverName, { ...config, enabled: !config.enabled });
  };

  return (
    <div className={`bg-panel border rounded-xl overflow-hidden transition-colors ${config.enabled ? 'border-border' : 'border-border/40 opacity-60'}`}>
      {/* Header */}
      <div className="flex items-center gap-3 px-4 py-3">
        <Server size={18} className="text-primary shrink-0" />
        <input
          value={editName}
          onChange={e => setEditName(e.target.value)}
          onBlur={handleBlurName}
          className="flex-1 bg-transparent text-sm font-semibold text-textMain focus:outline-none border-b border-transparent focus:border-primary/50 transition-colors"
          placeholder="server-name"
        />

        {/* Server toggle (single switch — same pattern as plugins) */}
        <button
          onClick={toggleEnabled}
          className={`shrink-0 transition-colors ${config.enabled ? 'text-primary' : 'text-textMuted'}`}
          title={config.enabled ? t('common.disable') : t('common.enable')}
        >
          {config.enabled ? <ToggleRight size={22} /> : <ToggleLeft size={22} />}
        </button>
        <button
          onClick={() => setExpanded(p => !p)}
          className="shrink-0 text-textMuted hover:text-textMain transition-colors"
        >
          {expanded ? <ChevronUp size={18} /> : <ChevronDown size={18} />}
        </button>
        <button
          onClick={() => onDelete(serverName)}
          className="shrink-0 text-textMuted hover:text-red-400 transition-colors"
          title={t('common.delete')}
        >
          <Trash2 size={16} />
        </button>
      </div>

      {/* Collapsed: show command preview + global-off warning */}
      {!expanded && (
        <div className="px-4 pb-3 flex items-center gap-2">
          <span className="text-xs text-textMuted font-mono truncate flex-1">
            {config.command} {config.args.slice(0, 3).join(' ')}{config.args.length > 3 ? ' …' : ''}
          </span>
        </div>
      )}

      {/* Expanded: full editor */}
      {expanded && (
        <div className="px-4 pb-4 space-y-3 border-t border-border/50 pt-3">
          <div>
            <label className="block text-xs font-bold text-textMuted uppercase mb-1">Command</label>
            <input
              value={config.command}
              onChange={e => onChange(serverName, { ...config, command: e.target.value })}
              className="w-full px-3 py-1.5 bg-bgLight border border-border rounded-lg text-sm font-mono focus:outline-none focus:ring-1 focus:ring-primary/50"
              placeholder="npx"
            />
          </div>

          <div>
            <label className="block text-xs font-bold text-textMuted uppercase mb-1">{t('mcpManager.args')}（{t('mcpManager.command')}）</label>
            <textarea
              value={argsText}
              onChange={e => setArgsText(e.target.value)}
              onBlur={applyChanges}
              rows={3}
              className="w-full px-3 py-1.5 bg-bgLight border border-border rounded-lg text-sm font-mono focus:outline-none focus:ring-1 focus:ring-primary/50 resize-y"
              placeholder={"-y\n@modelcontextprotocol/server-filesystem\n."}
            />
          </div>

          <div>
            <label className="block text-xs font-bold text-textMuted uppercase mb-1">{t('mcpManager.timeout')}</label>
            <input
              type="number"
              value={config.timeout ?? 30}
              onChange={e => onChange(serverName, { ...config, timeout: Number(e.target.value) })}
              className="w-32 px-3 py-1.5 bg-bgLight border border-border rounded-lg text-sm font-mono focus:outline-none focus:ring-1 focus:ring-primary/50"
              min={5}
            />
          </div>

          <div>
            <label className="block text-xs font-bold text-textMuted uppercase mb-1">{t('mcpManager.env')}</label>
            <textarea
              value={envText}
              onChange={e => setEnvText(e.target.value)}
              onBlur={applyChanges}
              rows={3}
              className="w-full px-3 py-1.5 bg-bgLight border border-border rounded-lg text-sm font-mono focus:outline-none focus:ring-1 focus:ring-primary/50 resize-y"
              placeholder="GITHUB_PERSONAL_ACCESS_TOKEN=your_token"
            />
          </div>

          <div>
            <label className="block text-xs font-bold text-textMuted uppercase mb-1">{t('mcpManager.autoApprove')}</label>
            <textarea
              value={autoApproveText}
              onChange={e => setAutoApproveText(e.target.value)}
              onBlur={applyChanges}
              rows={2}
              className="w-full px-3 py-1.5 bg-bgLight border border-border rounded-lg text-sm font-mono focus:outline-none focus:ring-1 focus:ring-primary/50 resize-y"
              placeholder="execute_command"
            />
          </div>
        </div>
      )}
    </div>
  );
};

// ---- Main Component ----

export const McpManagerPage: React.FC<McpManagerPageProps> = ({ onBack }) => {
  const { t } = useTranslation();
  const [mcpServers, setMcpServers] = useState<Record<string, McpServerConfig>>({});
  const [loadingMcp, setLoadingMcp] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saveOk, setSaveOk] = useState(false);

  // 加载中心 MCP 配置（所有 agent 共享）
  useEffect(() => {
    (async () => {
      setLoadingMcp(true);
      setError(null);
      try {
        const data = await mcpAPI.getCentralConfig();
        setMcpServers(data.mcpServers || {});
      } catch (e: any) {
        setError(e.message || t('mcpManager.loadFailed'));
      } finally {
        setLoadingMcp(false);
      }
    })();
  }, []);

  const handleChange = useCallback((name: string, config: McpServerConfig) => {
    setMcpServers(prev => ({ ...prev, [name]: config }));
  }, []);

  const handleDelete = useCallback((name: string) => {
    setMcpServers(prev => {
      const next = { ...prev };
      delete next[name];
      return next;
    });
  }, []);

  const handleRename = useCallback((oldName: string, newName: string) => {
    setMcpServers(prev => {
      const next: Record<string, McpServerConfig> = {};
      (Object.entries(prev) as [string, McpServerConfig][]).forEach(([k, v]) => {
        next[k === oldName ? newName : k] = v;
      });
      return next;
    });
  }, []);

  const handleAddServer = () => {
    const base = 'new-server';
    let name = base;
    let i = 1;
    while (name in mcpServers) {
      name = `${base}-${i++}`;
    }
    setMcpServers(prev => ({ ...prev, [name]: { ...EMPTY_SERVER } }));
  };

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    setSaveOk(false);
    try {
      await mcpAPI.saveCentralConfig(mcpServers);
      setSaveOk(true);
      setTimeout(() => setSaveOk(false), 2000);
    } catch (e: any) {
      setError(e.message || t('mcpManager.saveFailed'));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="flex flex-col h-full w-full bg-bgLight">
      {/* 头部栏 */}
      <div className={`${adminHeaderBar} justify-between`}>
        <div className="flex items-center gap-2 md:gap-2.5">
          <button
            type="button"
            onClick={onBack}
            className={adminHeaderNavBtn}
            title={t('common.back', { defaultValue: 'Back' })}
            aria-label={t('common.back', { defaultValue: 'Back' })}
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
          <div className={`hidden md:flex ${adminHeaderIconBox}`}>
            <Server size={14} className={adminHeaderIcon} />
          </div>
          <div className="flex items-baseline gap-1.5 shrink-0">
            <h2 className={adminHeaderTitle}>{t('mcpManager.title')}</h2>
          </div>
        </div>
        <div className="flex items-center gap-1.5">
          <button
            onClick={handleAddServer}
            disabled={loadingMcp}
            className={adminHeaderCta}
          >
            <Plus size={13} /> {t('mcpManager.addServer')}
          </button>
          <button
            onClick={handleSave}
            disabled={saving || loadingMcp}
            className={adminHeaderCta}
          >
            {saving ? (
              <OpenSquadLoader size={16} />
            ) : saveOk ? (
              <Check size={13} />
            ) : (
              <Save size={13} />
            )}
            {saveOk ? t('mcpManager.saveSuccess') : t('mcpManager.saveAll')}
          </button>
        </div>
      </div>

      {/* MCP servers editor — full width, no sidebar */}
      <div className="flex-1 overflow-y-auto p-4 md:p-6">
        {error && (
          <div className="flex items-center gap-2 p-3 bg-red-500/10 border border-red-500/20 rounded-xl text-red-400 text-sm mb-4">
            <AlertCircle size={16} />
            {error}
          </div>
        )}

        {loadingMcp ? (
          <div className="flex items-center justify-center py-16 text-textMuted">
            <OpenSquadLoader size={32} />
          </div>
        ) : Object.keys(mcpServers).length === 0 ? (
          <div className="flex flex-col items-center justify-center py-16 text-textMuted">
            <Server size={40} className="mb-3 opacity-30" />
            <p className="mb-4">{t('mcpManager.noServers')}</p>
            <button
              onClick={handleAddServer}
              className="flex items-center gap-1.5 px-4 py-2 bg-primary text-white rounded-lg text-sm hover:opacity-90"
            >
              <Plus size={16} /> {t('mcpManager.addServer')}
            </button>
          </div>
        ) : (
          <div className="space-y-3 max-w-3xl mx-auto">
            {Object.entries(mcpServers).map(([name, config]) => (
              <McpServerCard
                key={name}
                serverName={name}
                config={config}
                onChange={handleChange}
                onDelete={handleDelete}
                onRename={handleRename}
                saving={saving}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
};
