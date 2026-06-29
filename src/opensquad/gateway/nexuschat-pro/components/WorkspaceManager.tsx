/**
 * 工作区管理组件
 * 
 * 功能：
 * - 显示当前工作区
 * - 列出最近使用的工作区
 * - 创建新工作区
 * - 切换工作区
 * - 迁移数据向导
 */
import React, { useState, useEffect } from 'react';
import { 
  FolderOpen, 
  Plus, 
  RefreshCw, 
  Check, 
  AlertTriangle,
  X,
  ChevronRight,
  ChevronDown,
  Folder
} from 'lucide-react';
import { useTranslation, Trans } from 'react-i18next';

// ── 迁移报告详情组件 ──────────────────────────────────────────
interface ReportSection {
  label: string;
  items: string[];
  color: string;       // tailwind text color
  bgColor: string;     // tailwind bg color
  borderColor: string; // tailwind border color
  icon: string;
  defaultOpen: boolean;
}

function MigrationReportDetail({ report }: { report: any }) {
  const { t } = useTranslation();
  const [openSections, setOpenSections] = useState<Record<string, boolean>>({});

  const toggle = (key: string) =>
    setOpenSections(prev => ({ ...prev, [key]: !prev[key] }));

  const sections: ReportSection[] = [
    {
      label: t('workspace.report.failed', { count: report.failed_count }),
      items: (report.failed || []).map((f: any) => `${f.item}  —  ${f.error}`),
      color: 'text-red-700',
      bgColor: 'bg-red-50',
      borderColor: 'border-red-300',
      icon: '✗',
      defaultOpen: true,
    },
    {
      label: t('workspace.report.success', { count: report.success_count }),
      items: report.success || [],
      color: 'text-green-700',
      bgColor: 'bg-green-50',
      borderColor: 'border-green-300',
      icon: '✓',
      defaultOpen: false,
    },
    {
      label: t('workspace.report.skipped', { count: report.skipped_count }),
      items: report.skipped || [],
      color: 'text-gray-600',
      bgColor: 'bg-gray-50',
      borderColor: 'border-gray-300',
      icon: '⊘',
      defaultOpen: true,
    },
    {
      label: t('workspace.report.warnings', { count: report.warning_count }),
      items: report.warnings || [],
      color: 'text-amber-700',
      bgColor: 'bg-amber-50',
      borderColor: 'border-amber-300',
      icon: '⚠',
      defaultOpen: true,
    },
  ].filter(s => s.items.length > 0);

  return (
    <div className="space-y-2">
      <p className="text-xs font-semibold text-textMuted uppercase tracking-wide mb-1">{t('workspace.report.title')}</p>
      {sections.map(sec => {
        const key = sec.icon;
        const isOpen = openSections[key] ?? sec.defaultOpen;
        return (
          <div key={key} className={`rounded-lg border ${sec.borderColor} ${sec.bgColor} overflow-hidden`}>
            <button
              onClick={() => toggle(key)}
              className={`w-full flex items-center justify-between px-3 py-2 text-xs font-semibold ${sec.color} hover:opacity-80 transition-opacity`}
            >
              <span>{sec.icon} {sec.label}</span>
              {isOpen ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
            </button>
            {isOpen && (
              <ul className={`px-3 pb-2 space-y-0.5 border-t ${sec.borderColor}`}>
                {sec.items.map((item, i) => (
                  <li key={i} className={`text-xs ${sec.color} py-0.5 break-all font-mono`}>
                    {item}
                  </li>
                ))}
              </ul>
            )}
          </div>
        );
      })}
    </div>
  );
}
// ─────────────────────────────────────────────────────────────

interface WorkspaceInfo {
  path: string;
  name: string;
  is_current: boolean;
  exists: boolean;
  created_at?: string;
  last_used?: string;
}

interface WorkspaceListResponse {
  workspaces: WorkspaceInfo[];
  current: string | null;
}

export default function WorkspaceManager() {
  const { t } = useTranslation();
  const [workspaces, setWorkspaces] = useState<WorkspaceInfo[]>([]);
  const [currentWorkspace, setCurrentWorkspace] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // 切换后"待重启生效"的工作区路径
  const [pendingWorkspace, setPendingWorkspace] = useState<string | null>(null);

  // 创建工作区对话框状态
  const [createDialogOpen, setCreateDialogOpen] = useState(false);
  const [newWorkspacePath, setNewWorkspacePath] = useState('');
  const [newWorkspaceName, setNewWorkspaceName] = useState('');
  const [creating, setCreating] = useState(false);

  // 迁移对话框状态
  const [migrationDialogOpen, setMigrationDialogOpen] = useState(false);
  const [migrationStep, setMigrationStep] = useState(1); // 1: 选择源/目标, 2: 选择模式, 3: 迁移中/完成
  const [migrationMode, setMigrationMode] = useState<'copy' | 'move'>('copy');
  const [migrationConflict, setMigrationConflict] = useState<'skip' | 'overwrite'>('skip');
  const [migrationSource, setMigrationSource] = useState('');
  const [migrationTarget, setMigrationTarget] = useState('');
  const [migrationTaskId, setMigrationTaskId] = useState<string | null>(null);
  const [migrationStatus, setMigrationStatus] = useState<{
    status: string;
    progress: number;
    message: string;
    report?: any;
  } | null>(null);

  // 加载工作区列表
  const loadWorkspaces = async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch('/api/workspace/list');
      if (!response.ok) throw new Error(t('workspace.errors.loadListFailed'));
      const data: WorkspaceListResponse = await response.json();
      setWorkspaces(data.workspaces);
      setCurrentWorkspace(data.current);
    } catch (err: any) {
      setError(err.message || t('workspace.errors.unknown'));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadWorkspaces();
  }, []);

  // 创建新工作区
  const handleCreate = async () => {
    if (!newWorkspacePath.trim()) {
      setError(t('workspace.errors.inputPath'));
      return;
    }

    setCreating(true);
    setError(null);
    try {
      const response = await fetch('/api/workspace/create', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          path: newWorkspacePath,
          name: newWorkspaceName || undefined,
        }),
      });

      if (!response.ok) {
        const data = await response.json();
        throw new Error(data.detail || t('workspace.errors.createFailed'));
      }

      setCreateDialogOpen(false);
      setNewWorkspacePath('');
      setNewWorkspaceName('');
      await loadWorkspaces();
    } catch (err: any) {
      setError(err.message || t('workspace.errors.createWorkspaceFailed'));
    } finally {
      setCreating(false);
    }
  };

  // 切换工作区
  const handleSwitch = async (path: string) => {
    if (path === currentWorkspace) return;

    if (!confirm(t('workspace.errors.switchConfirm'))) return;

    try {
      const response = await fetch('/api/workspace/switch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path }),
      });

      if (!response.ok) {
        const data = await response.json();
        throw new Error(data.detail || t('workspace.errors.switchFailed'));
      }

      // 记录待生效工作区，UI 给出明确提示
      setPendingWorkspace(path);
      await loadWorkspaces();
    } catch (err: any) {
      setError(err.message || t('workspace.errors.switchWorkspaceFailed'));
    }
  };

  // 打开文件选择器（使用 HTML5 File API）
  const handleBrowse = async () => {
    try {
      // 使用 showDirectoryPicker API（需要用户交互触发）
      if ('showDirectoryPicker' in window) {
        const dirHandle = await (window as any).showDirectoryPicker();
        setNewWorkspacePath(dirHandle.name ? await getFullPath(dirHandle) : dirHandle.name);
      } else {
        // 回退方案：提示用户手动输入
        alert(t('workspace.errors.browserNotSupported'));
      }
    } catch (err: any) {
      if (err.name !== 'AbortError') {
        console.error('Folder selection failed:', err);
        alert(t('workspace.errors.folderSelectFailed'));
      }
    }
  };

  // 获取完整路径（辅助函数）
  const getFullPath = async (dirHandle: any): Promise<string> => {
    // 注意：浏览器环境无法获取真实文件系统路径
    // 这里只能获取目录名，用户仍需手动输入完整路径
    return dirHandle.name;
  };

  // 开始迁移
  const handleStartMigration = () => {
    // 初始化迁移状态
    setMigrationSource(currentWorkspace || '');
    setMigrationTarget('');
    setMigrationMode('copy');
    setMigrationConflict('skip');
    setMigrationStep(1);
    setMigrationTaskId(null);
    setMigrationStatus(null);
    setMigrationDialogOpen(true);
  };

  // 执行迁移
  const handleMigrate = async () => {
    if (!migrationSource || !migrationTarget) {
      setError(t('workspace.errors.selectSourceTarget'));
      return;
    }

    if (migrationSource === migrationTarget) {
      setError(t('workspace.errors.sameWorkspace'));
      return;
    }

    setMigrationStep(3);
    setMigrationStatus({ status: 'pending', progress: 0, message: t('workspace.migration.preparing') });

    try {
      // 启动迁移任务
      const response = await fetch('/api/workspace/migrate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          source: migrationSource,
          target: migrationTarget,
          mode: migrationMode,
          conflict: migrationConflict,
        }),
      });

      if (!response.ok) {
        const data = await response.json();
        throw new Error(data.detail || t('workspace.errors.startMigrationFailed'));
      }

      const data = await response.json();
      setMigrationTaskId(data.task_id);

      // 轮询任务状态
      pollMigrationStatus(data.task_id);
    } catch (err: any) {
      setMigrationStatus({ status: 'failed', progress: 0, message: err.message || t('workspace.migration.failed') });
    }
  };

  // 轮询迁移状态
  const pollMigrationStatus = async (taskId: string) => {
    const poll = async () => {
      try {
        const response = await fetch(`/api/workspace/migrate/status/${taskId}`);
        if (!response.ok) throw new Error(t('workspace.errors.getMigrationStatusFailed'));
        
        const data = await response.json();
        setMigrationStatus(data);

        // 如果还在进行中，继续轮询
        if (data.status === 'running' || data.status === 'pending') {
          setTimeout(poll, 1000);
        } else if (data.status === 'completed') {
          // 迁移完成，刷新工作区列表
          await loadWorkspaces();
        }
      } catch (err: any) {
        setMigrationStatus({ status: 'failed', progress: 0, message: err.message || t('workspace.errors.getStatusFailed') });
      }
    };

    poll();
  };

  // 关闭迁移对话框
  const closeMigrationDialog = () => {
    setMigrationDialogOpen(false);
    setMigrationStep(1);
    setMigrationSource('');
    setMigrationTarget('');
    setMigrationTaskId(null);
    setMigrationStatus(null);
  };

  return (
    <div className="space-y-5">
      {/* 当前工作区 */}
      <div className="rounded-xl bg-bgLight border border-border p-5">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold text-textMain flex items-center gap-2">
            <Folder size={18} className="text-primary" />
            {t('workspace.currentWorkspace')}
          </h3>
          <button
            onClick={loadWorkspaces}
            disabled={loading}
            className="p-1.5 hover:bg-panel rounded-lg transition-colors text-textMuted hover:text-textMain"
            title={t('common.refresh')}
          >
            <RefreshCw size={16} className={loading ? 'animate-spin' : ''} />
          </button>
        </div>
        
        {currentWorkspace ? (
          <div className="flex items-start gap-3 p-3 rounded-lg bg-panel border border-border">
            <Check size={20} className="text-green-500 shrink-0 mt-0.5" />
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium text-textMain break-all">{currentWorkspace}</p>
              <p className="text-xs text-textMuted mt-1">{t('workspace.workspaceActivated')}</p>
            </div>
          </div>
        ) : (
          <div className="flex items-start gap-3 p-3 rounded-lg bg-yellow-50 border border-yellow-200">
            <AlertTriangle size={20} className="text-yellow-600 shrink-0 mt-0.5" />
            <div className="flex-1">
              <p className="text-sm text-yellow-800 font-medium">{t('workspace.notConfigured')}</p>
              <p className="text-xs text-yellow-700 mt-1">{t('workspace.createOrSelect')}</p>
            </div>
          </div>
        )}

        {/* 待重启生效提示 */}
        {pendingWorkspace && pendingWorkspace !== currentWorkspace && (
          <div className="mt-3 flex items-start gap-3 p-3 rounded-lg bg-amber-50 border border-amber-300">
            <AlertTriangle size={18} className="text-amber-600 shrink-0 mt-0.5" />
            <div className="flex-1 min-w-0">
              <p className="text-xs font-semibold text-amber-800">{t('workspace.savedRestart')}</p>
              <p className="text-xs text-amber-700 mt-0.5 break-all">{t('workspace.pendingSwitch', { path: pendingWorkspace })}</p>
            </div>
            <button onClick={() => setPendingWorkspace(null)} className="text-amber-600 hover:text-amber-800 shrink-0">
              <X size={14} />
            </button>
          </div>
        )}
      </div>

      {/* 错误提示 */}
      {error && (
        <div className="rounded-xl bg-red-50 border border-red-200 p-4 flex items-start gap-3">
          <AlertTriangle size={18} className="text-red-600 shrink-0 mt-0.5" />
          <div className="flex-1">
            <p className="text-sm text-red-800">{error}</p>
          </div>
          <button onClick={() => setError(null)} className="text-red-600 hover:text-red-800">
            <X size={16} />
          </button>
        </div>
      )}

      {/* 工作区操作 */}
      <div className="rounded-xl bg-bgLight border border-border p-5">
        <h3 className="text-sm font-semibold text-textMain mb-3">{t('workspace.workspaceOps')}</h3>
        <div className="flex gap-3">
          <button
            onClick={() => setCreateDialogOpen(true)}
            className="flex-1 px-4 py-2.5 bg-primary text-white text-sm rounded-lg hover:opacity-90 transition-colors flex items-center justify-center gap-2"
          >
            <Plus size={16} />
            {t('workspace.addOrCreate')}
          </button>
          <button
            onClick={handleStartMigration}
            className="flex-1 px-4 py-2.5 bg-blue-600 text-white text-sm rounded-lg hover:opacity-90 transition-colors flex items-center justify-center gap-2"
          >
            <ChevronRight size={16} />
            {t('workspace.migrateData')}
          </button>
        </div>
      </div>

      {/* 最近使用的工作区 */}
      <div className="rounded-xl bg-bgLight border border-border p-5">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold text-textMain">{t('workspace.workspaceList')}</h3>
        </div>

        {loading ? (
          <div className="flex items-center justify-center py-8">
            <RefreshCw size={20} className="animate-spin text-primary" />
          </div>
        ) : workspaces.length === 0 ? (
          <div className="text-center py-8 text-textMuted text-sm">
            {t('workspace.noWorkspace')}
          </div>
        ) : (
          <div className="space-y-2">
            {workspaces.map((ws) => {
              const isPending = pendingWorkspace === ws.path && !ws.is_current;
              return (
              <div
                key={ws.path}
                className={`flex items-center justify-between p-3 rounded-lg border transition-all ${
                  ws.is_current
                    ? 'bg-primary/10 border-primary'
                    : isPending
                    ? 'bg-amber-50 border-amber-300'
                    : 'bg-panel border-border hover:border-primary/50'
                }`}
              >
                <div className="flex items-start gap-3 flex-1 min-w-0">
                  <FolderOpen size={18} className={ws.is_current ? 'text-primary' : isPending ? 'text-amber-600' : 'text-textMuted'} />
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium text-textMain break-all">
                      {ws.name || ws.path.split(/[\\/]/).pop()}
                      {isPending && <span className="ml-2 text-xs font-normal text-amber-600 bg-amber-100 px-1.5 py-0.5 rounded">{t('workspace.restartAfter')}</span>}
                    </p>
                    <p className="text-xs text-textMuted mt-0.5 break-all">{ws.path}</p>
                    {ws.last_used && (
                      <p className="text-xs text-textMuted mt-0.5">
                        {t('workspace.lastUsed', { time: new Date(ws.last_used).toLocaleString() })}
                      </p>
                    )}
                  </div>
                </div>
                {!ws.is_current && !isPending && (
                  <button
                    onClick={() => handleSwitch(ws.path)}
                    className="ml-3 px-3 py-1 bg-primary text-white text-xs rounded-lg hover:opacity-90 transition-colors shrink-0 flex items-center gap-1"
                  >
                    {t('workspace.switch')}
                    <ChevronRight size={14} />
                  </button>
                )}
              </div>
              );
            })}
          </div>
        )}
      </div>

      {/* 创建工作区对话框 */}
      {createDialogOpen && (
        <div className="fixed inset-0 bg-black/50 z-[150] flex items-center justify-center backdrop-blur-sm">
          <div className="bg-panel rounded-2xl shadow-2xl w-full max-w-md mx-4 border border-border">
            <div className="bg-primary px-6 py-4 flex justify-between items-center text-white rounded-t-2xl">
              <h3 className="font-semibold text-lg">{t('workspace.addOrCreate')}</h3>
              <button onClick={() => setCreateDialogOpen(false)} className="hover:text-white/80">
                <X size={20} />
              </button>
            </div>

            <div className="p-6 space-y-4">
              <div className="rounded-lg bg-blue-50 border border-blue-200 p-3 mb-4">
                <p className="text-xs text-blue-800">
                  {t('workspace.createHint')}
                </p>
              </div>
              <div>
                <label className="block text-xs font-semibold text-textMuted uppercase mb-2">
                  {t('workspace.pathLabel')}
                </label>
                <div className="flex gap-2">
                  <input
                    type="text"
                    value={newWorkspacePath}
                    onChange={(e) => setNewWorkspacePath(e.target.value)}
                    placeholder="C:\Users\YourName\Documents\OpenSquad-Workspace"
                    className="flex-1 px-3 py-2 bg-bgLight border border-border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary/50"
                  />
                  <button
                    onClick={handleBrowse}
                    className="px-3 py-2 bg-bgLight border border-border rounded-lg hover:bg-panel transition-colors"
                    title={t('workspace.browse')}
                  >
                    <FolderOpen size={18} />
                  </button>
                </div>
                <p className="text-xs text-textMuted mt-1">
                  {t('workspace.pathHint')}
                </p>
              </div>

              <div>
                <label className="block text-xs font-semibold text-textMuted uppercase mb-2">
                  {t('workspace.nameOptional')}
                </label>
                <input
                  type="text"
                  value={newWorkspaceName}
                  onChange={(e) => setNewWorkspaceName(e.target.value)}
                  placeholder={t('workspace.namePlaceholder')}
                  className="w-full px-3 py-2 bg-bgLight border border-border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary/50"
                />
              </div>

              <div className="flex gap-3 pt-2">
                <button
                  onClick={() => setCreateDialogOpen(false)}
                  disabled={creating}
                  className="flex-1 px-4 py-2 bg-bgLight border border-border rounded-lg text-sm hover:bg-panel transition-colors disabled:opacity-50"
                >
                  {t('common.cancel')}
                </button>
                <button
                  onClick={handleCreate}
                  disabled={creating || !newWorkspacePath.trim()}
                  className="flex-1 px-4 py-2 bg-primary text-white rounded-lg text-sm hover:opacity-90 transition-colors flex items-center justify-center gap-2 disabled:opacity-50"
                >
                  {creating ? (
                    <>
                      <RefreshCw size={14} className="animate-spin" />
                      {t('workspace.creating')}
                    </>
                  ) : (
                    t('workspace.create')
                  )}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* 迁移向导对话框 */}
      {migrationDialogOpen && (
        <div className="fixed inset-0 bg-black/50 z-[150] flex items-center justify-center backdrop-blur-sm">
          <div className="bg-panel rounded-2xl shadow-2xl w-full max-w-2xl mx-4 border border-border max-h-[90vh] flex flex-col">
            <div className="bg-blue-600 px-6 py-4 flex justify-between items-center text-white rounded-t-2xl shrink-0">
              <h3 className="font-semibold text-lg">{t('workspace.migration.wizard')}</h3>
              <button onClick={closeMigrationDialog} className="hover:text-white/80">
                <X size={20} />
              </button>
            </div>

            <div className="flex-1 overflow-y-auto p-6">
              {/* 步骤指示器 */}
              <div className="flex items-center justify-center mb-6">
                <div className="flex items-center gap-2">
                  <div className={`w-8 h-8 rounded-full flex items-center justify-center text-sm font-semibold ${
                    migrationStep >= 1 ? 'bg-blue-600 text-white' : 'bg-gray-200 text-gray-500'
                  }`}>
                    1
                  </div>
                  <div className={`w-16 h-0.5 ${migrationStep >= 2 ? 'bg-blue-600' : 'bg-gray-200'}`} />
                  <div className={`w-8 h-8 rounded-full flex items-center justify-center text-sm font-semibold ${
                    migrationStep >= 2 ? 'bg-blue-600 text-white' : 'bg-gray-200 text-gray-500'
                  }`}>
                    2
                  </div>
                  <div className={`w-16 h-0.5 ${migrationStep >= 3 ? 'bg-blue-600' : 'bg-gray-200'}`} />
                  <div className={`w-8 h-8 rounded-full flex items-center justify-center text-sm font-semibold ${
                    migrationStep >= 3 ? 'bg-blue-600 text-white' : 'bg-gray-200 text-gray-500'
                  }`}>
                    3
                  </div>
                </div>
              </div>

              {/* 步骤 1: 选择源和目标工作区 */}
              {migrationStep === 1 && (
                <div className="space-y-4">
                  <div className="rounded-lg bg-blue-50 border border-blue-200 p-3 mb-4">
                    <p className="text-sm text-blue-800">
                      {t('workspace.migration.hint')}
                    </p>
                  </div>

                  <div>
                    <label className="block text-sm font-semibold text-textMain mb-2">
                      {t('workspace.migration.sourceLabel')}
                    </label>
                    <select
                      value={migrationSource}
                      onChange={(e) => setMigrationSource(e.target.value)}
                      className="w-full px-3 py-2 bg-bgLight border border-border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                    >
                      <option value="">{t('workspace.migration.selectSource')}</option>
                      {workspaces.filter(ws => ws.exists).map(ws => (
                        <option key={ws.path} value={ws.path}>
                          {ws.name || ws.path} {ws.is_current ? t('workspace.currentSuffix') : ''}
                        </option>
                      ))}
                    </select>
                  </div>

                  <div>
                    <label className="block text-sm font-semibold text-textMain mb-2">
                      {t('workspace.migration.targetLabel')}
                    </label>
                    <select
                      value={migrationTarget}
                      onChange={(e) => setMigrationTarget(e.target.value)}
                      className="w-full px-3 py-2 bg-bgLight border border-border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                    >
                      <option value="">{t('workspace.migration.selectTarget')}</option>
                      {workspaces.filter(ws => ws.exists && ws.path !== migrationSource).map(ws => (
                        <option key={ws.path} value={ws.path}>
                          {ws.name || ws.path} {ws.is_current ? t('workspace.currentSuffix') : ''}
                        </option>
                      ))}
                    </select>
                  </div>

                  {migrationSource && migrationTarget && migrationSource === migrationTarget && (
                    <div className="rounded-lg bg-red-50 border border-red-200 p-3">
                      <p className="text-sm text-red-800">{t('workspace.migration.sameSourceTarget')}</p>
                    </div>
                  )}
                </div>
              )}

              {/* 步骤 2: 选择迁移模式 */}
              {migrationStep === 2 && (
                <div className="space-y-4">
                  <div className="rounded-lg bg-yellow-50 border border-yellow-200 p-3 mb-4">
                    <p className="text-sm text-yellow-800 font-semibold mb-1">{t('workspace.migration.warningTitle')}</p>
                    <p className="text-xs text-yellow-700">
                      {t('workspace.migration.warningDesc')}
                    </p>
                  </div>

                  <div>
                    <label className="block text-sm font-semibold text-textMain mb-2">{t('workspace.migration.modeLabel')}</label>
                    <div className="space-y-3">
                      <label className={`flex items-start gap-3 p-4 rounded-lg border-2 cursor-pointer transition-all ${
                        migrationMode === 'copy' 
                          ? 'border-blue-500 bg-blue-50' 
                          : 'border-border bg-bgLight hover:border-blue-300'
                      }`}>
                        <input
                          type="radio"
                          name="migrationMode"
                          value="copy"
                          checked={migrationMode === 'copy'}
                          onChange={(e) => setMigrationMode(e.target.value as 'copy')}
                          className="mt-0.5"
                        />
                        <div className="flex-1">
                          <p className="text-sm font-semibold text-textMain">{t('workspace.migration.copyMode')}</p>
                          <p className="text-xs text-textMuted mt-1">
                            {t('workspace.migration.copyModeDesc')}
                          </p>
                        </div>
                      </label>

                      <label className={`flex items-start gap-3 p-4 rounded-lg border-2 cursor-pointer transition-all ${
                        migrationMode === 'move' 
                          ? 'border-blue-500 bg-blue-50' 
                          : 'border-border bg-bgLight hover:border-blue-300'
                      }`}>
                        <input
                          type="radio"
                          name="migrationMode"
                          value="move"
                          checked={migrationMode === 'move'}
                          onChange={(e) => setMigrationMode(e.target.value as 'move')}
                          className="mt-0.5"
                        />
                        <div className="flex-1">
                          <p className="text-sm font-semibold text-textMain">{t('workspace.migration.moveMode')}</p>
                          <p className="text-xs text-textMuted mt-1">
                            <Trans i18nKey="workspace.migration.moveModeDesc" components={{ red: <span className="text-red-600 font-semibold" /> }} />
                          </p>
                        </div>
                      </label>
                    </div>
                  </div>

                  <div>
                    <label className="block text-sm font-semibold text-textMain mb-2">{t('workspace.migration.conflictLabel')}</label>
                    <p className="text-xs text-textMuted mb-3">{t('workspace.migration.conflictDesc')}</p>
                    <div className="space-y-3">
                      <label className={`flex items-start gap-3 p-4 rounded-lg border-2 cursor-pointer transition-all ${
                        migrationConflict === 'skip'
                          ? 'border-blue-500 bg-blue-50'
                          : 'border-border bg-bgLight hover:border-blue-300'
                      }`}>
                        <input
                          type="radio"
                          name="migrationConflict"
                          value="skip"
                          checked={migrationConflict === 'skip'}
                          onChange={() => setMigrationConflict('skip')}
                          className="mt-0.5"
                        />
                        <div className="flex-1">
                          <p className="text-sm font-semibold text-textMain">{t('workspace.migration.skipExisting')}</p>
                          <p className="text-xs text-textMuted mt-1">
                            {t('workspace.migration.skipExistingDesc')}{migrationMode === 'move' && <span className="text-red-600"> {t('workspace.migration.skipExistingMoveWarning')}</span>}
                          </p>
                        </div>
                      </label>

                      <label className={`flex items-start gap-3 p-4 rounded-lg border-2 cursor-pointer transition-all ${
                        migrationConflict === 'overwrite'
                          ? 'border-amber-500 bg-amber-50'
                          : 'border-border bg-bgLight hover:border-amber-300'
                      }`}>
                        <input
                          type="radio"
                          name="migrationConflict"
                          value="overwrite"
                          checked={migrationConflict === 'overwrite'}
                          onChange={() => setMigrationConflict('overwrite')}
                          className="mt-0.5"
                        />
                        <div className="flex-1">
                          <p className="text-sm font-semibold text-textMain">
                            {t('workspace.migration.overwriteExisting')}
                            <span className="ml-2 text-xs font-normal text-amber-600">{t('workspace.migration.overwriteBackupNote')}</span>
                          </p>
                          <p className="text-xs text-textMuted mt-1">
                            {t('workspace.migration.overwriteDesc')}
                          </p>
                        </div>
                      </label>
                    </div>
                  </div>

                  <div className="rounded-lg bg-bgLight border border-border p-4">
                    <h4 className="text-sm font-semibold text-textMain mb-2">{t('workspace.migration.overview')}</h4>
                    <div className="space-y-2 text-sm text-textMuted">
                      <p>
                        <span className="font-medium text-textMain">{t('workspace.migration.source')}</span>
                        <br />
                        <span className="text-xs break-all">{migrationSource}</span>
                      </p>
                      <p>
                        <span className="font-medium text-textMain">{t('workspace.migration.target')}</span>
                        <br />
                        <span className="text-xs break-all">{migrationTarget}</span>
                      </p>
                       <p>
                         <span className="font-medium text-textMain">{t('workspace.migration.mode')}</span> 
                         {migrationMode === 'copy' ? t('workspace.migration.copy') : t('workspace.migration.move')}
                       </p>
                       <p>
                         <span className="font-medium text-textMain">{t('workspace.migration.conflict')}</span>
                         {migrationConflict === 'skip' ? t('workspace.migration.skipConflict') : t('workspace.migration.overwriteConflict')}
                       </p>
                    </div>
                  </div>
                </div>
              )}

              {/* 步骤 3: 迁移进度 */}
              {migrationStep === 3 && (
                <div className="space-y-4">
                  {migrationStatus?.status === 'pending' && (
                    <div className="flex flex-col items-center justify-center py-8">
                      <RefreshCw size={48} className="animate-spin text-blue-600 mb-4" />
                      <p className="text-sm text-textMain">{t('workspace.migration.preparing')}</p>
                    </div>
                  )}

                  {migrationStatus?.status === 'running' && (
                    <div className="space-y-4">
                      <div className="flex flex-col items-center justify-center py-8">
                        <RefreshCw size={48} className="animate-spin text-blue-600 mb-4" />
                        <p className="text-sm text-textMain font-semibold mb-2">{t('workspace.migration.migrating')}</p>
                        <p className="text-xs text-textMuted">{migrationStatus.message}</p>
                      </div>
                      {migrationStatus.progress > 0 && (
                        <div className="w-full bg-gray-200 rounded-full h-2">
                          <div 
                            className="bg-blue-600 h-2 rounded-full transition-all duration-300"
                            style={{ width: `${migrationStatus.progress * 100}%` }}
                          />
                        </div>
                      )}
                    </div>
                  )}

                  {migrationStatus?.status === 'completed' && (
                    <div className="space-y-4">
                      <div className="flex flex-col items-center justify-center py-8">
                        <div className="w-16 h-16 rounded-full bg-green-100 flex items-center justify-center mb-4">
                          <Check size={32} className="text-green-600" />
                        </div>
                        <p className="text-lg text-textMain font-semibold mb-2">{t('workspace.migration.completed')}</p>
                        <p className="text-sm text-textMuted">{migrationStatus.message}</p>
                      </div>

                      {migrationStatus.report && (
                        <MigrationReportDetail report={migrationStatus.report} />
                      )}
                    </div>
                  )}

                  {migrationStatus?.status === 'failed' && (
                    <div className="space-y-4">
                      <div className="flex flex-col items-center justify-center py-8">
                        <div className="w-16 h-16 rounded-full bg-red-100 flex items-center justify-center mb-4">
                          <X size={32} className="text-red-600" />
                        </div>
                        <p className="text-lg text-textMain font-semibold mb-2">{t('workspace.migration.failed')}</p>
                        <p className="text-sm text-red-600">{migrationStatus.message}</p>
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>

            {/* 底部按钮 */}
            <div className="px-6 py-4 border-t border-border flex justify-between items-center shrink-0 bg-bgLight rounded-b-2xl">
              {migrationStep === 1 && (
                <>
                  <button
                    onClick={closeMigrationDialog}
                    className="px-4 py-2 bg-bgLight border border-border rounded-lg text-sm hover:bg-panel transition-colors"
                  >
                    {t('common.cancel')}
                  </button>
                  <button
                    onClick={() => setMigrationStep(2)}
                    disabled={!migrationSource || !migrationTarget || migrationSource === migrationTarget}
                    className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm hover:opacity-90 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    {t('workspace.migration.next')}
                  </button>
                </>
              )}

              {migrationStep === 2 && (
                <>
                  <button
                    onClick={() => setMigrationStep(1)}
                    className="px-4 py-2 bg-bgLight border border-border rounded-lg text-sm hover:bg-panel transition-colors"
                  >
                    {t('workspace.migration.prev')}
                  </button>
                  <button
                    onClick={handleMigrate}
                    className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm hover:opacity-90 transition-colors flex items-center gap-2"
                  >
                    {t('workspace.migration.start')}
                    <ChevronRight size={16} />
                  </button>
                </>
              )}

              {migrationStep === 3 && (
                <>
                  <div />
                  <button
                    onClick={closeMigrationDialog}
                    disabled={migrationStatus?.status === 'running' || migrationStatus?.status === 'pending'}
                    className="px-4 py-2 bg-primary text-white rounded-lg text-sm hover:opacity-90 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    {migrationStatus?.status === 'completed' ? t('workspace.migration.done') : t('common.close')}
                  </button>
                </>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
