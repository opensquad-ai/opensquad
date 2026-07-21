/**
 * AgentWebComposer — full Agent Web input bar (per split pane, independent state).
 * Visually matches the main AIChatPage composer: changes bar, rounded input,
 * attach / mode / model / effort / mic / send, and context footer.
 */
import React, { useCallback, useRef, useState } from 'react';
import { FileIcon, FileText, Mic, Send, Square, X } from 'lucide-react';
import { agentSessionAPI, type ModelCardInfo, type SkillInfo } from '../../services/api';
import { ModePicker, type AgentMode } from './ModePicker';
import { SoloModelPicker } from './SoloModelPicker';
import { EffortPicker, type ReasoningEffort } from './EffortPicker';
import { SoloAttachMenu } from './SoloAttachMenu';
import { SoloContextFooter, type SoloTokenStats } from './SoloContextFooter';
import { SessionChangesBar, type SessionChangesSummary } from './SessionChangesBar';

export type ComposerUploadedFile = {
  path: string;
  filename: string;
  original_name: string;
  url: string;
  size: number;
  content_type: string;
  is_image: boolean;
  is_audio?: boolean;
  is_video?: boolean;
  type?: string;
  duration?: number;
};

export type ComposerSendPayload = {
  text: string;
  images: string[];
  attachments: ComposerUploadedFile[];
  skillDir?: string;
  skillName?: string;
};

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}

export interface AgentWebComposerProps {
  agentId: string;
  columnClass?: string;
  disabled?: boolean;
  /** New-session centered layout — softer chrome, no top divider */
  landing?: boolean;
  /** Agent is streaming/working for this pane's live session */
  busy?: boolean;
  agentMode: AgentMode;
  onModeChange: (mode: AgentMode) => void;
  modelCards: ModelCardInfo[];
  currentCardName: string | null;
  modelName: string;
  fallbackLabel: string;
  switchingModel?: boolean;
  onSelectModel: (cardName: string) => void;
  reasoningEffort: ReasoningEffort;
  onEffortChange: (effort: ReasoningEffort) => void;
  cwd: string | null;
  tokenStats: SoloTokenStats | null;
  onViewReport?: () => void;
  sessionChanges?: SessionChangesSummary | null;
  changesBusy?: boolean;
  onOpenChanges?: () => void;
  onCommitPush?: () => void | Promise<void>;
  /** Workflow-style Plan card — below Changes, above pending / input */
  planPanel?: React.ReactNode;
  /** Queued outbound messages — below Plan, above input */
  pendingPanel?: React.ReactNode;
  availableSkills: SkillInfo[];
  skillsLoading?: boolean;
  onOpenSkills?: () => void;
  autoSpeechEnabled?: boolean;
  onToggleAutoSpeech?: (enabled: boolean) => void;
  onSend: (payload: ComposerSendPayload) => void | Promise<void>;
  onStop?: () => void;
  onActivate?: () => void;
  /** Extra status line (e.g. multi-pane queue hint) */
  statusHint?: string | null;
}

export const AgentWebComposer: React.FC<AgentWebComposerProps> = ({
  agentId,
  columnClass = 'max-w-3xl mx-auto w-full',
  disabled = false,
  landing = false,
  busy = false,
  agentMode,
  onModeChange,
  modelCards,
  currentCardName,
  modelName,
  fallbackLabel,
  switchingModel = false,
  onSelectModel,
  reasoningEffort,
  onEffortChange,
  cwd,
  tokenStats,
  onViewReport,
  sessionChanges,
  changesBusy = false,
  onOpenChanges,
  onCommitPush,
  planPanel = null,
  pendingPanel = null,
  availableSkills,
  skillsLoading = false,
  onOpenSkills,
  autoSpeechEnabled = false,
  onToggleAutoSpeech,
  onSend,
  onStop,
  onActivate,
  statusHint = null,
}) => {
  const [inputText, setInputText] = useState('');
  const [images, setImages] = useState<string[]>([]);
  const [attachments, setAttachments] = useState<ComposerUploadedFile[]>([]);
  const [pendingSkill, setPendingSkill] = useState<{ dir: string; name: string } | null>(null);
  const [isUploading, setIsUploading] = useState(false);
  const [sending, setSending] = useState(false);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const clearComposer = useCallback(() => {
    setInputText('');
    setImages([]);
    setAttachments([]);
    setPendingSkill(null);
    if (inputRef.current) inputRef.current.style.height = 'auto';
  }, []);

  const uploadFiles = useCallback(
    async (fileArray: File[]) => {
      if (!fileArray.length) return;
      setIsUploading(true);
      try {
        if (fileArray.length === 1) {
          const resp = await agentSessionAPI.uploadFile(agentId, fileArray[0]);
          if (resp.is_image) setImages((prev) => [...prev, resp.url]);
          else setAttachments((prev) => [...prev, resp as ComposerUploadedFile]);
        } else {
          const resp = await agentSessionAPI.uploadFiles(agentId, fileArray);
          for (const f of resp.files) {
            if (f.is_image) setImages((prev) => [...prev, f.url]);
            else setAttachments((prev) => [...prev, f as ComposerUploadedFile]);
          }
        }
      } catch (err) {
        console.error('[AgentWebComposer] upload failed', err);
      } finally {
        setIsUploading(false);
      }
    },
    [agentId],
  );

  const submit = useCallback(async () => {
    if (disabled || sending) return;
    const text = inputText.trim();
    if (!text && images.length === 0 && attachments.length === 0 && !pendingSkill) return;
    onActivate?.();
    setSending(true);
    try {
      await onSend({
        text,
        images: [...images],
        attachments: attachments.map((a) => ({ ...a })),
        skillDir: pendingSkill?.dir,
        skillName: pendingSkill?.name,
      });
      clearComposer();
    } finally {
      setSending(false);
    }
  }, [
    disabled,
    sending,
    inputText,
    images,
    attachments,
    pendingSkill,
    onSend,
    onActivate,
    clearComposer,
  ]);

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      void submit();
    }
  };

  const onPaste = (e: React.ClipboardEvent) => {
    const items = e.clipboardData?.items;
    if (!items) return;
    const files: File[] = [];
    for (let i = 0; i < items.length; i++) {
      const it = items[i];
      if (it.kind === 'file') {
        const f = it.getAsFile();
        if (f) files.push(f);
      }
    }
    if (files.length) {
      e.preventDefault();
      void uploadFiles(files);
    }
  };

  const canSend =
    !disabled &&
    !sending &&
    (!!inputText.trim() || images.length > 0 || attachments.length > 0 || !!pendingSkill);

  const showChanges =
    !!sessionChanges &&
    (sessionChanges.count > 0 || sessionChanges.additions > 0 || sessionChanges.deletions > 0);

  return (
    <div
      className="flex-shrink-0 flex flex-col"
      onMouseDown={(e) => {
        e.stopPropagation();
        onActivate?.();
      }}
    >
      {showChanges ? (
        <div className="px-2 sm:px-4 pt-2 flex-shrink-0">
          <div className={columnClass}>
            <SessionChangesBar
              summary={sessionChanges!}
              busy={changesBusy}
              onOpenChanges={() => onOpenChanges?.()}
              onCommitPush={async () => {
                await onCommitPush?.();
              }}
            />
          </div>
        </div>
      ) : null}

      {/* Order: Changes → Plan → pending → input */}
      {planPanel ? (
        <div className="px-2 sm:px-4 pt-2 flex-shrink-0">
          <div className={columnClass}>{planPanel}</div>
        </div>
      ) : null}

      {pendingPanel ? (
        <div className="px-2 sm:px-4 pt-2 flex-shrink-0">
          <div className={columnClass}>{pendingPanel}</div>
        </div>
      ) : null}

      {statusHint ? (
        <div className="px-2 sm:px-4 pt-1.5 flex-shrink-0">
          <div className={`${columnClass} text-[11px] text-amber-600 dark:text-amber-400`}>
            {statusHint}
          </div>
        </div>
      ) : null}

      {(images.length > 0 || attachments.length > 0 || isUploading) && (
        <div className="px-2 sm:px-4 py-2 flex gap-2 flex-wrap items-center flex-shrink-0 border-t border-border/20">
          <div className={`${columnClass} flex gap-2 flex-wrap items-center`}>
            {images.map((img, i) => (
              <div key={`img-${i}`} className="relative group">
                <img
                  src={
                    img.startsWith('http') || img.startsWith('/')
                      ? img
                      : `/uploads/${img.split(/[/\\]/).pop()}`
                  }
                  alt=""
                  className="w-16 h-16 rounded-lg object-cover border border-border"
                />
                <button
                  type="button"
                  onClick={() => setImages((prev) => prev.filter((_, idx) => idx !== i))}
                  className="absolute -top-1 -right-1 w-4 h-4 bg-red-500 rounded-full flex items-center justify-center opacity-0 group-hover:opacity-100"
                >
                  <X size={10} className="text-white" />
                </button>
              </div>
            ))}
            {attachments.map((att, i) => (
              <div
                key={`att-${i}`}
                className="relative group flex items-center gap-2 px-3 py-2 rounded-lg border border-border bg-bgLight max-w-[200px]"
              >
                <FileIcon size={16} className="text-textMuted flex-shrink-0" />
                <div className="min-w-0 flex-1">
                  <p className="text-xs text-textMain truncate">{att.original_name}</p>
                  <p className="text-[10px] text-textMuted">
                    {att.type === 'voice' || att.is_audio ? 'VOICE' : 'FILE'} •{' '}
                    {formatFileSize(att.size)}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => setAttachments((prev) => prev.filter((_, idx) => idx !== i))}
                  className="w-4 h-4 bg-red-500 rounded-full flex items-center justify-center opacity-0 group-hover:opacity-100 flex-shrink-0"
                >
                  <X size={10} className="text-white" />
                </button>
              </div>
            ))}
            {isUploading && (
              <div className="flex items-center gap-1.5 px-3 py-2 rounded-lg border border-border bg-bgLight">
                <div className="w-3 h-3 border-2 border-primary border-t-transparent rounded-full animate-spin" />
                <span className="text-xs text-textMuted">Uploading...</span>
              </div>
            )}
          </div>
        </div>
      )}

      <div
        className={`flex-shrink-0 overflow-visible px-2 sm:px-4 ${
          landing ? 'py-2 sm:py-3 border-t-0' : 'py-3 sm:py-4 border-t border-border/20'
        }`}
      >
        <div className={`${columnClass}${disabled ? ' opacity-50 pointer-events-none' : ''}`}>
          <div
            className={`w-full flex flex-col rounded-[22px] border border-border/60 focus-within:ring-1 focus-within:ring-primary/40 relative ${
              landing
                ? 'shadow-[0_8px_32px_rgba(0,0,0,0.07)]'
                : 'shadow-[0_4px_24px_rgba(0,0,0,0.06)]'
            } ${disabled ? 'bg-border/40' : 'bg-white dark:bg-[#1e1e20]'}`}
          >
            {pendingSkill ? (
              <div className="px-3.5 pt-3 pb-0">
                <button
                  type="button"
                  onClick={() => setPendingSkill(null)}
                  className="inline-flex items-center gap-0.5 rounded-md px-1.5 py-0.5 text-[13px] font-medium border-0 cursor-pointer"
                  style={{
                    color: '#b08d57',
                    background: 'color-mix(in srgb, #b08d57 12%, transparent)',
                  }}
                  title={`Remove skill /${pendingSkill.dir}`}
                >
                  <span>/{pendingSkill.dir}</span>
                  <X size={12} className="opacity-70" />
                </button>
              </div>
            ) : null}

            <textarea
              ref={inputRef}
              value={inputText}
              onChange={(e) => setInputText(e.target.value)}
              onKeyDown={onKeyDown}
              onPaste={onPaste}
              onFocus={onActivate}
              placeholder={
                pendingSkill
                  ? 'Add details for this skill…'
                  : landing
                    ? '帮我把这个想法变成一个技术方案…（输入 / 召唤指令）'
                    : '输入消息... (输入 / 召唤指令)'
              }
              disabled={disabled}
              className={`w-full border-0 px-3.5 pt-3.5 pb-2 text-[15px] text-textMain placeholder-textMuted resize-none focus:outline-none min-h-[72px] max-h-[200px] bg-transparent leading-6 ${
                disabled ? 'text-textMuted cursor-not-allowed' : ''
              }`}
              rows={2}
              style={{ height: 'auto' }}
              onInput={(e) => {
                const target = e.target as HTMLTextAreaElement;
                target.style.height = 'auto';
                target.style.height = `${Math.min(target.scrollHeight, 200)}px`;
              }}
            />

            <div className="flex items-center gap-1.5 px-2.5 pb-2.5 pt-0.5">
              <div className="flex items-center gap-0.5 shrink-0">
                <SoloAttachMenu
                  disabled={disabled}
                  skills={availableSkills}
                  skillsLoading={skillsLoading}
                  onOpenSkills={onOpenSkills}
                  autoSpeechEnabled={autoSpeechEnabled}
                  onToggleAutoSpeech={onToggleAutoSpeech}
                  onSelectSkill={(skill) =>
                    setPendingSkill({ dir: skill.dir || skill.name, name: skill.display_name || skill.name })
                  }
                  onUploadFiles={() => {
                    const input = document.createElement('input');
                    input.type = 'file';
                    input.multiple = true;
                    input.onchange = async (ev) => {
                      const files = (ev.target as HTMLInputElement).files;
                      if (files) await uploadFiles(Array.from(files));
                    };
                    input.click();
                  }}
                  onUploadFolder={() => {
                    const input = document.createElement('input');
                    input.type = 'file';
                    (input as any).webkitdirectory = true;
                    (input as any).directory = true;
                    input.multiple = true;
                    input.onchange = async (ev) => {
                      const files = (ev.target as HTMLInputElement).files;
                      if (files) await uploadFiles(Array.from(files));
                    };
                    input.click();
                  }}
                  onUploadImages={() => fileInputRef.current?.click()}
                />
                <button
                  type="button"
                  disabled={disabled}
                  onClick={() => {
                    const input = document.createElement('input');
                    input.type = 'file';
                    input.multiple = true;
                    input.onchange = async (ev) => {
                      const files = (ev.target as HTMLInputElement).files;
                      if (files) await uploadFiles(Array.from(files));
                    };
                    input.click();
                  }}
                  className="w-7 h-7 rounded-full flex items-center justify-center text-textMuted hover:text-textMain hover:bg-black/[0.05] dark:hover:bg-white/[0.08] transition-colors border-0 bg-transparent cursor-pointer disabled:opacity-50"
                  title="上传文件"
                >
                  <FileText size={16} strokeWidth={1.75} />
                </button>
                <ModePicker mode={agentMode} disabled={disabled} onSelect={onModeChange} />
              </div>

              <div className="flex-1 min-w-0" />

              <div className="flex items-center gap-1.5 shrink-0">
                <SoloModelPicker
                  cards={modelCards}
                  currentCardName={currentCardName}
                  modelName={modelName}
                  fallbackLabel={fallbackLabel}
                  switching={switchingModel}
                  disabled={disabled}
                  onSelect={onSelectModel}
                  onAddModels={() => {
                    window.dispatchEvent(new CustomEvent('switchView', { detail: 'models' }));
                  }}
                />
                {(() => {
                  const selected =
                    (currentCardName && modelCards.find((c) => c.name === currentCardName)) ||
                    (modelName && modelCards.find((c) => c.model_name === modelName)) ||
                    null;
                  if (!selected?.is_think) return null;
                  const deepseekish = /deepseek/i.test(
                    `${selected.model_name || ''} ${selected.base_url || ''} ${selected.name || ''}`,
                  );
                  return (
                    <EffortPicker
                      effort={reasoningEffort}
                      deepseekStyle={deepseekish}
                      disabled={disabled || switchingModel}
                      onSelect={onEffortChange}
                    />
                  );
                })()}
                <button
                  type="button"
                  disabled={disabled}
                  className="w-8 h-8 rounded-full flex items-center justify-center text-textMuted hover:text-textMain hover:bg-black/[0.05] dark:hover:bg-white/[0.08] bg-transparent border-0 cursor-pointer disabled:opacity-50"
                  title="语音（请在焦点窗格使用完整语音面板）"
                  onClick={onActivate}
                >
                  <Mic size={18} strokeWidth={1.75} />
                </button>
                {busy ? (
                  <>
                    <button
                      type="button"
                      onClick={() => void submit()}
                      disabled={!canSend}
                      className="w-8 h-8 rounded-full bg-amber-500 hover:bg-amber-600 transition-colors flex items-center justify-center disabled:opacity-50 disabled:cursor-not-allowed border-0 cursor-pointer"
                      title="排队发送"
                    >
                      <Send size={14} className="text-white" />
                    </button>
                    <button
                      type="button"
                      onClick={onStop}
                      className="w-8 h-8 rounded-full bg-red-500 hover:bg-red-600 transition-colors flex items-center justify-center border-0 cursor-pointer"
                      title="Stop"
                    >
                      <Square size={14} className="text-white" />
                    </button>
                  </>
                ) : (
                  <button
                    type="button"
                    onClick={() => void submit()}
                    disabled={!canSend}
                    className="w-8 h-8 rounded-full bg-primary hover:bg-primary/90 transition-colors flex items-center justify-center disabled:opacity-50 disabled:cursor-not-allowed border-0 cursor-pointer"
                    title="Send"
                  >
                    <Send size={14} className="text-white" />
                  </button>
                )}
              </div>
            </div>
          </div>

          <SoloContextFooter
            cwd={cwd}
            tokenStats={tokenStats}
            locked
            onViewReport={onViewReport}
          />
        </div>
      </div>

      <input
        ref={fileInputRef}
        type="file"
        accept="image/*"
        multiple
        className="hidden"
        onChange={(e) => {
          const files = e.target.files;
          if (files) void uploadFiles(Array.from(files));
          e.target.value = '';
        }}
      />
    </div>
  );
};
