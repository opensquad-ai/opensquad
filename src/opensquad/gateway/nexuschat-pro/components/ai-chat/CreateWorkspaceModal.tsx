/**
 * CreateWorkspaceModal — custom name + required folder path.
 */
import React, { useState } from 'react';
import { Folder, X } from 'lucide-react';
import { pickFolder } from '../../utils/cwdRecents';
import { SoftOverlay } from '../SoftOverlay';

interface CreateWorkspaceModalProps {
  open: boolean;
  onCancel: () => void;
  onCreate: (name: string, rootPath: string) => void;
}

export const CreateWorkspaceModal: React.FC<CreateWorkspaceModalProps> = ({
  open,
  onCancel,
  onCreate,
}) => {
  const [name, setName] = useState('');
  const [rootPath, setRootPath] = useState('');
  const [picking, setPicking] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reset = () => {
    setName('');
    setRootPath('');
    setError(null);
  };

  const handlePick = async () => {
    setPicking(true);
    setError(null);
    try {
      const result = await pickFolder(rootPath || null);
      if (result.cancelled) return;
      if (result.error) {
        setError(result.error);
        return;
      }
      if (result.path) {
        setRootPath(result.path);
        if (!name.trim()) {
          const parts = result.path.replace(/\\/g, '/').split('/').filter(Boolean);
          setName(parts[parts.length - 1] || '');
        }
      } else {
        setError('未能获取文件夹绝对路径，请使用桌面端或 Launcher 选择目录');
      }
    } finally {
      setPicking(false);
    }
  };

  const handleCreate = () => {
    const n = name.trim();
    const p = rootPath.trim();
    if (!n) {
      setError('请输入工作区名称');
      return;
    }
    if (n.length > 32) {
      setError('名称最多 32 个字符');
      return;
    }
    if (!p) {
      setError('请选择存放位置');
      return;
    }
    onCreate(n, p);
    reset();
  };

  return (
    <SoftOverlay
      open={open}
      onBackdrop={onCancel}
      panelClassName="w-[min(440px,92vw)] rounded-xl bg-white dark:bg-[#252526] border border-black/10 dark:border-white/10 shadow-2xl"
    >
      <div className="flex items-center justify-between px-4 pt-4 pb-2">
        <h3 className="text-[15px] font-semibold text-textMain">创建新工作区</h3>
        <button
          type="button"
          onClick={onCancel}
          className="p-1 rounded-md hover:bg-black/[0.05] dark:hover:bg-white/10"
          title="关闭"
        >
          <X size={16} className="text-textMuted" />
        </button>
      </div>
      <div className="px-4 pb-4 space-y-3">
        <div>
          <label className="block text-[12px] text-textMuted mb-1">名称</label>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="w-full px-2.5 py-1.5 text-[13px] rounded-lg border border-border bg-bgLight outline-none focus:border-primary/50"
            placeholder="工作区名称"
            maxLength={32}
            autoFocus
          />
        </div>
        <div>
          <label className="block text-[12px] text-textMuted mb-1">存放位置</label>
          <button
            type="button"
            onClick={() => void handlePick()}
            disabled={picking}
            className="w-full flex items-center gap-2 px-2.5 py-1.5 text-[13px] rounded-lg border border-border bg-bgLight text-left hover:bg-black/[0.03] dark:hover:bg-white/[0.04] disabled:opacity-50"
          >
            <Folder size={14} className="text-neutral-400 shrink-0" />
            <span className={`truncate ${rootPath ? 'text-textMain' : 'text-textMuted'}`}>
              {picking ? '选择中…' : rootPath || '选择文件夹…'}
            </span>
          </button>
        </div>
        {error ? <div className="text-[12px] text-rose-500">{error}</div> : null}
        <div className="flex justify-end gap-2 pt-1">
          <button
            type="button"
            onClick={() => {
              reset();
              onCancel();
            }}
            className="px-3 py-1.5 rounded-lg text-[13px] border border-border text-textMain hover:bg-black/[0.04] dark:hover:bg-white/10"
          >
            取消
          </button>
          <button
            type="button"
            onClick={handleCreate}
            className="px-3 py-1.5 rounded-lg text-[13px] font-medium bg-primary text-white hover:opacity-90"
          >
            创建
          </button>
        </div>
      </div>
    </SoftOverlay>
  );
};

export default CreateWorkspaceModal;
