/**
 * CloseWorkspaceModal — confirm closing L1 workspace tab (data kept on disk).
 */
import React from 'react';
import { X } from 'lucide-react';
import { SoftOverlay } from '../SoftOverlay';

interface CloseWorkspaceModalProps {
  open: boolean;
  workspaceName: string;
  onCancel: () => void;
  onConfirm: () => void;
}

export const CloseWorkspaceModal: React.FC<CloseWorkspaceModalProps> = ({
  open,
  workspaceName,
  onCancel,
  onConfirm,
}) => (
  <SoftOverlay
    open={open}
    onBackdrop={onCancel}
    panelClassName="w-[min(420px,92vw)] rounded-xl bg-white dark:bg-[#252526] border border-black/10 dark:border-white/10 shadow-2xl"
  >
    <div role="dialog" aria-modal="true">
      <div className="flex items-center justify-between px-4 pt-4 pb-2">
        <h3 className="text-[15px] font-semibold text-textMain">关闭工作区</h3>
        <button
          type="button"
          onClick={onCancel}
          className="p-1 rounded-md hover:bg-primary/10"
          title="关闭"
        >
          <X size={16} className="text-textMuted" />
        </button>
      </div>
      <div className="px-4 pb-4 text-[13px] text-textMuted leading-relaxed">
        确定要关闭「{workspaceName}」吗？关闭后不会删除任何文件，聊天记录和数据均存储在本地，不会丢失。你可以随时重新打开此工作区。
      </div>
      <div className="flex justify-end gap-2 px-4 pb-4">
        <button
          type="button"
          onClick={onCancel}
          className="px-3 py-1.5 rounded-lg text-[13px] border border-border text-textMain hover:bg-primary/10"
        >
          取消
        </button>
        <button
          type="button"
          onClick={onConfirm}
          className="px-3 py-1.5 rounded-lg text-[13px] font-medium bg-textMain text-bgLight hover:opacity-90"
        >
          确认关闭
        </button>
      </div>
    </div>
  </SoftOverlay>
);

export default CloseWorkspaceModal;
