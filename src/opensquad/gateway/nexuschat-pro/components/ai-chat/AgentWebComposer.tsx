/**
 * AgentWebComposer — full Agent Web input bar (per split pane, independent state).
 * Visually matches the main AIChatPage composer: changes bar, rounded input,
 * attach / mode / model / effort / mic / send, and context footer.
 */
import React, {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
} from 'react';
import { FileIcon, FileText, Mic, Send, Square, X } from 'lucide-react';
import { agentSessionAPI, type ModelCardInfo, type SkillInfo } from '../../services/api';
import { blobToWavFile } from '../../utils/mediaDevices';
import { CHAT_DOCUMENT_COLUMN_CLASS } from '../../utils/chatLayout';
import { ModePicker, type AgentMode } from './ModePicker';
import { MobileComposerMenu } from './MobileComposerMenu';
import { SoloModelPicker } from './SoloModelPicker';
import { EffortPicker, type ReasoningEffort } from './EffortPicker';
import { SoloAttachMenu } from './SoloAttachMenu';
import { SoloContextFooter, type SoloTokenStats } from './SoloContextFooter';
import { SessionChangesBar, type SessionChangesSummary } from './SessionChangesBar';
import { SlashMenu } from './SlashMenu';
import { OpenSquadLoader } from '../OpenSquadLoader';
import { VoicePanel, type VoiceCardBindings } from './VoicePanel';
import { VoiceRecordPill } from './VoiceRecordPill';
import {
  filterGoalSubcommands,
  filterSkillsForSlash,
  filterSlashCommands,
  parseGoalSendQuery,
  parseSlashInput,
  slashCommandTriggerText,
  type GoalSubcommandDef,
  type SlashCommandDef,
} from './slashCommands';

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

/** Parent (AIChatPage) uses this for withdraw refill + window file drops. */
export type AgentWebComposerHandle = {
  setText: (text: string) => void;
  focus: () => void;
  uploadFiles: (files: File[]) => Promise<void>;
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
  /** Re-fetch model cards when the picker opens (keeps list in sync with desktop). */
  onRefreshModelCards?: () => void;
  reasoningEffort: ReasoningEffort;
  onEffortChange: (effort: ReasoningEffort) => void;
  cwd: string | null;
  tokenStats: SoloTokenStats | null;
  onViewReport?: () => void;
  onCompressContext?: () => void;
  compressing?: boolean;
  compressDisabled?: boolean;
  sessionChanges?: SessionChangesSummary | null;
  changesBusy?: boolean;
  onOpenChanges?: () => void;
  onCommitPush?: () => void | Promise<void>;
  /** Independent Plan card — sits behind the input with a slight overlap */
  planPanel?: React.ReactNode;
  /** Queued outbound messages — below Changes, above Plan/input stack */
  pendingPanel?: React.ReactNode;
  /** Mode-switch / propose-options approval cards — above pending & composer */
  approvalPanel?: React.ReactNode;
  /** Background-terminals indicator bar — topmost slot above the composer */
  terminalsPanel?: React.ReactNode;
  availableSkills: SkillInfo[];
  skillsLoading?: boolean;
  /** Prefetch / open skill list (also used when typing `/skill `). */
  onOpenSkills?: () => void;
  /** `/goal` lifecycle actions (pause / resume / clear / set / status). */
  onGoalAction?: (
    action: 'set' | 'pause' | 'resume' | 'clear' | 'status',
    objective?: string,
  ) => void;
  autoSpeechEnabled?: boolean;
  onToggleAutoSpeech?: (enabled: boolean) => void;
  /** Per-session draft text: 初始化/恢复输入框内容（切换会话后重挂载时保留草稿）。 */
  draftText?: string;
  /** 输入内容变化时写回外部草稿存储（按 sessionId 隔离）。 */
  onDraftChange?: (text: string) => void;
  onSend: (payload: ComposerSendPayload) => void | Promise<void>;
  onStop?: () => void;
  onActivate?: () => void;
  /** Extra status line (e.g. multi-pane queue hint) */
  statusHint?: string | null;
  /** Voice panel / realtime call (agent-level; only host pane should open UI). */
  voicePanelOpen?: boolean;
  onVoicePanelOpenChange?: (open: boolean) => void;
  /** Mount VoicePanel only on the focused pane (avoids duplicate mic capture). */
  voiceHost?: boolean;
  voiceRealtimeStatus?: string;
  voiceRealtimeError?: string;
  voiceTranscript?: string;
  voiceBindings?: VoiceCardBindings;
  onVoiceBindingsChange?: (next: VoiceCardBindings) => void | Promise<void>;
  onRealtimeStart?: (opts?: { forceAskAgent?: boolean }) => void;
  onRealtimeStop?: () => void;
  onAudioChunk?: (pcm16Base64: string) => void;
  onMouthpieceUtterance?: (pcm16Base64: string, sampleRate: number) => void;
  onForceAskAgentChange?: (force: boolean) => void;
}

export const AgentWebComposer = forwardRef<AgentWebComposerHandle, AgentWebComposerProps>(function AgentWebComposer(
  {
    agentId,
    columnClass = CHAT_DOCUMENT_COLUMN_CLASS,
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
    onRefreshModelCards,
    reasoningEffort,
    onEffortChange,
    cwd,
    tokenStats,
    onViewReport,
    onCompressContext,
    compressing = false,
    compressDisabled = false,
    sessionChanges,
    changesBusy = false,
    onOpenChanges,
    onCommitPush,
    planPanel = null,
    pendingPanel = null,
    approvalPanel = null,
    terminalsPanel = null,
    availableSkills,
    skillsLoading = false,
    onOpenSkills,
    onGoalAction,
    autoSpeechEnabled = false,
    onToggleAutoSpeech,
    draftText,
    onDraftChange,
    onSend,
    onStop,
    onActivate,
    statusHint = null,
    voicePanelOpen = false,
    onVoicePanelOpenChange,
    voiceHost = true,
    voiceRealtimeStatus = 'idle',
    voiceRealtimeError = '',
    voiceTranscript = '',
    voiceBindings,
    onVoiceBindingsChange,
    onRealtimeStart,
    onRealtimeStop,
    onAudioChunk,
    onMouthpieceUtterance,
    onForceAskAgentChange,
  },
  ref,
) {
  const [inputText, setInputText] = useState<string>(() => draftText ?? '');
  // 草稿双向同步：内部输入 → onDraftChange 写回外部（按 session 隔离）；
  // 外部 draftText 变化（如切回会话后的恢复/外部 refill）→ 同步回内部。
  // lastSyncedTextRef 记录"最近一次已同步的值"，避免 external↔internal 两个
  // effect 互相回写形成渲染死循环（Maximum update depth exceeded）。
  const lastSyncedTextRef = useRef<string>(inputText);
  useEffect(() => {
    const next = draftText ?? '';
    if (next !== lastSyncedTextRef.current) {
      lastSyncedTextRef.current = next;
      setInputText(next);
    }
  }, [draftText]);
  useEffect(() => {
    if (inputText !== lastSyncedTextRef.current) {
      lastSyncedTextRef.current = inputText;
      onDraftChange?.(inputText);
    }
  }, [inputText, onDraftChange]);
  const [images, setImages] = useState<string[]>([]);
  const [attachments, setAttachments] = useState<ComposerUploadedFile[]>([]);
  const [pendingSkill, setPendingSkill] = useState<{ dir: string; name: string } | null>(null);
  const [isUploading, setIsUploading] = useState(false);
  const [sending, setSending] = useState(false);
  const [slashHighlight, setSlashHighlight] = useState(0);
  const [sttDictating, setSttDictating] = useState(false);
  const [voiceCapture, setVoiceCapture] = useState({
    recording: false,
    durationSec: 0,
    level: 0,
  });
  const voiceCaptureApiRef = useRef<{ stopRecord: () => void } | null>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const voiceEnabled = typeof onVoicePanelOpenChange === 'function';
  const voiceCallActive =
    voiceRealtimeStatus === 'connected' ||
    voiceRealtimeStatus === 'tool_running' ||
    voiceRealtimeStatus === 'connecting';

  const appendTranscript = useCallback((text: string) => {
    setInputText((prev) => {
      const cur = prev.trimEnd();
      if (!cur) return text;
      const joiner = /[\s\n]$/.test(prev) ? '' : ' ';
      return `${prev}${joiner}${text}`;
    });
    requestAnimationFrame(() => {
      const el = inputRef.current;
      if (!el) return;
      el.focus();
      el.style.height = 'auto';
      el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
    });
  }, []);

  const handleSendVoiceMessage = useCallback(
    async (blob: Blob, _durationSec: number) => {
      try {
        setSttDictating(true);
        const file = await blobToWavFile(blob, `voice_${Date.now()}.wav`);
        const res = await agentSessionAPI.transcribe(agentId, file, {
          filename: file.name,
          language: 'zh',
        });
        const text = (res.text || '').trim();
        if (!text) {
          console.warn('[AgentWebComposer] STT returned empty text');
          return;
        }
        appendTranscript(text);
        onVoicePanelOpenChange?.(false);
      } catch (err) {
        console.error('[AgentWebComposer] STT failed', err);
        throw err instanceof Error ? err : new Error(String(err));
      } finally {
        setSttDictating(false);
      }
    },
    [agentId, appendTranscript, onVoicePanelOpenChange],
  );

  const toggleVoicePanel = useCallback(() => {
    onActivate?.();
    onVoicePanelOpenChange?.(!voicePanelOpen);
  }, [onActivate, onVoicePanelOpenChange, voicePanelOpen]);

  const slashMode = useMemo(() => parseSlashInput(inputText), [inputText]);
  const slashResetKey = slashMode ? `${slashMode.kind}:${slashMode.query}` : null;
  const slashCommandOptions = useMemo(
    () => (slashMode?.kind === 'commands' ? filterSlashCommands(slashMode.query) : []),
    [slashMode],
  );
  const slashGoalOptions = useMemo(
    () => (slashMode?.kind === 'goal' ? filterGoalSubcommands(slashMode.query) : []),
    [slashMode],
  );
  const slashSkillOptions = useMemo(
    () => (slashMode?.kind === 'skill' ? filterSkillsForSlash(availableSkills, slashMode.query) : []),
    [slashMode, availableSkills],
  );
  const slashOptionCount =
    slashMode?.kind === 'skill'
      ? slashSkillOptions.length
      : slashMode?.kind === 'goal'
        ? slashGoalOptions.length
        : slashMode?.kind === 'plan'
          ? 1
          : slashCommandOptions.length;

  useEffect(() => {
    if (slashMode?.kind !== 'skill') return;
    onOpenSkills?.();
  }, [slashMode?.kind, onOpenSkills]);

  useEffect(() => {
    setSlashHighlight(0);
  }, [slashResetKey]);

  useEffect(() => {
    if (slashResetKey === null) return;
    if (slashHighlight >= slashOptionCount) {
      setSlashHighlight(Math.max(0, slashOptionCount - 1));
    }
  }, [slashResetKey, slashHighlight, slashOptionCount]);

  const clearComposer = useCallback(() => {
    setInputText('');
    setImages([]);
    setAttachments([]);
    setPendingSkill(null);
    if (inputRef.current) inputRef.current.style.height = 'auto';
  }, []);

  const focusInputEnd = useCallback(() => {
    requestAnimationFrame(() => {
      const el = inputRef.current;
      if (!el) return;
      el.focus();
      const len = el.value.length;
      el.setSelectionRange(len, len);
    });
  }, []);

  const selectSlashCommand = useCallback(
    (cmd: SlashCommandDef) => {
      setInputText(slashCommandTriggerText(cmd));
      focusInputEnd();
    },
    [focusInputEnd],
  );

  const selectSkillFromSlash = useCallback(
    (skill: SkillInfo) => {
      const dir = (skill.dir || skill.name || '').trim();
      if (!dir) return;
      setPendingSkill({
        dir,
        name: skill.display_name || skill.name || dir,
      });
      setInputText('');
      requestAnimationFrame(() => {
        if (inputRef.current) inputRef.current.style.height = 'auto';
        inputRef.current?.focus();
      });
    },
    [],
  );

  const selectGoalSubcommand = useCallback(
    (cmd: GoalSubcommandDef) => {
      onGoalAction?.(cmd.id);
      setInputText('');
      focusInputEnd();
    },
    [onGoalAction, focusInputEnd],
  );

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

  const setTextAndFocus = useCallback(
    (text: string) => {
      setInputText(text);
      requestAnimationFrame(() => {
        const el = inputRef.current;
        if (!el) return;
        el.focus();
        el.style.height = 'auto';
        el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
        const len = el.value.length;
        el.setSelectionRange(len, len);
      });
    },
    [],
  );

  useImperativeHandle(
    ref,
    () => ({
      setText: setTextAndFocus,
      focus: focusInputEnd,
      uploadFiles,
    }),
    [setTextAndFocus, focusInputEnd, uploadFiles],
  );

  const submit = useCallback(async () => {
    if (disabled || sending) return;

    const slash = parseSlashInput(inputText);
    if (slash?.kind === 'plan') {
      const topic = slash.query.trim() || 'Plan the next change';
      onModeChange('plan');
      onActivate?.();
      setSending(true);
      try {
        await onSend({
          text: `<user_plan>${topic}</user_plan>`,
          images: [...images],
          attachments: attachments.map((a) => ({ ...a })),
          skillDir: pendingSkill?.dir,
          skillName: pendingSkill?.name,
        });
        clearComposer();
      } finally {
        setSending(false);
      }
      return;
    }
    if (slash?.kind === 'goal') {
      const parsed = parseGoalSendQuery(slash.query);
      if (
        parsed.action === 'status' ||
        parsed.action === 'pause' ||
        parsed.action === 'resume' ||
        parsed.action === 'clear'
      ) {
        onGoalAction?.(parsed.action);
        setInputText('');
        return;
      }
      const objective = (parsed.objective || '').trim();
      if (!objective) {
        onGoalAction?.('status');
        setInputText('');
        return;
      }
      onGoalAction?.('set', objective);
      onActivate?.();
      setSending(true);
      try {
        await onSend({
          text: `<user_goal>${objective}</user_goal>`,
          images: [...images],
          attachments: attachments.map((a) => ({ ...a })),
          skillDir: pendingSkill?.dir,
          skillName: pendingSkill?.name,
        });
        clearComposer();
      } finally {
        setSending(false);
      }
      return;
    }

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
    onModeChange,
    onGoalAction,
    clearComposer,
  ]);

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (slashMode) {
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        if (slashOptionCount === 0) return;
        setSlashHighlight((i) => (i + 1) % slashOptionCount);
        return;
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault();
        if (slashOptionCount === 0) return;
        setSlashHighlight((i) => (i - 1 + slashOptionCount) % slashOptionCount);
        return;
      }
      if (e.key === 'Escape') {
        e.preventDefault();
        setInputText('');
        return;
      }
      if (slashMode.kind === 'goal') {
        if (e.key === 'Tab') {
          e.preventDefault();
          const cmd = slashGoalOptions[slashHighlight];
          if (cmd) selectGoalSubcommand(cmd);
          return;
        }
        if (e.key === 'Enter' && !e.shiftKey) {
          e.preventDefault();
          void submit();
          return;
        }
      } else if (slashMode.kind === 'plan') {
        if ((e.key === 'Enter' && !e.shiftKey) || e.key === 'Tab') {
          e.preventDefault();
          void submit();
          return;
        }
      } else if ((e.key === 'Enter' && !e.shiftKey) || e.key === 'Tab') {
        e.preventDefault();
        if (slashMode.kind === 'commands') {
          const cmd = slashCommandOptions[slashHighlight];
          if (cmd) selectSlashCommand(cmd);
        } else {
          const skill = slashSkillOptions[slashHighlight];
          if (skill) selectSkillFromSlash(skill);
        }
        return;
      }
    }
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

  // ── Settings shared by the desktop pickers and the narrow-viewport overflow
  //    menu (MobileComposerMenu) so the two can never disagree. ──
  const selectedCard =
    (currentCardName && modelCards.find((c) => c.name === currentCardName)) ||
    (modelName && modelCards.find((c) => c.model_name === modelName)) ||
    null;
  const effortDeepseekish = /deepseek/i.test(
    `${selectedCard?.model_name || ''} ${selectedCard?.base_url || ''} ${selectedCard?.name || ''}`,
  );
  const showEffort = !!selectedCard?.is_think;

  /**
   * Send / Stop, rendered twice on purpose. Desktop keeps it as the last control
   * of the toolbar; below `md` it moves into the input row beside the textarea
   * (group-chat composer parity). Each copy is retired at the other's
   * breakpoint, so exactly one is ever mounted.
   */
  const sendOrStop = busy ? (
    <button
      type="button"
      onClick={onStop}
      className="w-8 h-8 rounded-full bg-red-500 hover:bg-red-600 transition-colors flex items-center justify-center border-0 cursor-pointer shrink-0"
      title="Stop"
    >
      <Square size={14} className="text-white" />
    </button>
  ) : (
    <button
      type="button"
      onClick={() => void submit()}
      disabled={!canSend}
      className="w-8 h-8 rounded-full bg-primary hover:bg-primary/90 transition-colors flex items-center justify-center disabled:opacity-50 disabled:cursor-not-allowed border-0 cursor-pointer shrink-0"
      title="Send"
      aria-label="Send"
    >
      <Send size={14} className="text-white" />
    </button>
  );

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

      {/* Order: terminals → Changes → approvals → pending → Plan (behind) overlapping input (front) */}
      {terminalsPanel ? (
        <div className="px-2 sm:px-4 pt-2 flex-shrink-0">
          <div className={columnClass}>{terminalsPanel}</div>
        </div>
      ) : null}

      {approvalPanel ? (
        <div className="px-2 sm:px-4 pt-2 flex-shrink-0">
          <div className={columnClass}>{approvalPanel}</div>
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
        <div className="px-2 sm:px-4 py-2 flex gap-2 flex-wrap items-center flex-shrink-0">
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
                <OpenSquadLoader size={16} />
                <span className="text-xs text-textMuted">Uploading...</span>
              </div>
            )}
          </div>
        </div>
      )}

      <div
        className={`flex-shrink-0 overflow-visible px-2 sm:px-4 ${
          landing ? 'py-2 sm:py-3' : 'pt-2 pb-3 sm:pb-4'
        }`}
      >
        <div className={`${columnClass}${disabled ? ' opacity-50 pointer-events-none' : ''}`}>
          <div className={`os-composer-overlap ${planPanel ? 'has-plan' : ''}`}>
            {planPanel ? (
              <div className="os-composer-plan-layer">{planPanel}</div>
            ) : null}

            <div
              className={`os-composer-input-layer w-full flex flex-col rounded-[22px] border border-boundary focus-within:ring-1 focus-within:ring-primary/40 relative transition-shadow duration-[420ms] ease-[cubic-bezier(0.22,1,0.36,1)] ${
                landing
                  ? 'shadow-[0_8px_32px_rgba(0,0,0,0.07)]'
                  : 'shadow-[0_4px_24px_rgba(0,0,0,0.06)]'
              } ${disabled ? 'bg-border/40' : 'bg-panel'}`}
            >
            {slashMode?.kind === 'commands' ? (
              <SlashMenu
                mode="commands"
                commands={slashCommandOptions}
                highlightIndex={slashHighlight}
                onHighlightIndexChange={setSlashHighlight}
                onSelectCommand={selectSlashCommand}
              />
            ) : null}
            {slashMode?.kind === 'skill' ? (
              <SlashMenu
                mode="skill"
                skills={slashSkillOptions}
                loading={skillsLoading}
                highlightIndex={slashHighlight}
                onHighlightIndexChange={setSlashHighlight}
                onSelectSkill={selectSkillFromSlash}
              />
            ) : null}
            {slashMode?.kind === 'goal' ? (
              <SlashMenu
                mode="goal"
                subcommands={slashGoalOptions}
                highlightIndex={slashHighlight}
                onHighlightIndexChange={setSlashHighlight}
                onSelectSubcommand={selectGoalSubcommand}
              />
            ) : null}
            {slashMode?.kind === 'plan' ? (
              <SlashMenu
                mode="plan"
                topicHint={slashMode.query}
                highlightIndex={slashHighlight}
                onHighlightIndexChange={setSlashHighlight}
                onConfirmTopic={() => void submit()}
              />
            ) : null}
            {voiceEnabled && voiceHost ? (
              <VoicePanel
                open={voicePanelOpen}
                onClose={() => onVoicePanelOpenChange?.(false)}
                onOpen={() => onVoicePanelOpenChange?.(true)}
                disabled={disabled}
                realtimeStatus={voiceRealtimeStatus}
                realtimeError={voiceRealtimeError}
                transcript={voiceTranscript}
                dictating={sttDictating}
                modelCards={modelCards}
                voiceBindings={voiceBindings}
                onVoiceBindingsChange={onVoiceBindingsChange}
                onSendVoiceMessage={handleSendVoiceMessage}
                onRealtimeStart={(opts) => onRealtimeStart?.(opts)}
                onRealtimeStop={() => onRealtimeStop?.()}
                onAudioChunk={(b64) => onAudioChunk?.(b64)}
                onMouthpieceUtterance={(b64, sampleRate) =>
                  onMouthpieceUtterance?.(b64, sampleRate)
                }
                onForceAskAgentChange={onForceAskAgentChange}
                onCaptureStateChange={setVoiceCapture}
                captureApiRef={voiceCaptureApiRef}
              />
            ) : null}
            {pendingSkill ? (
              <div className="px-3.5 pt-3 pb-0 order-2 md:order-1 max-md:px-2.5 max-md:pt-2">
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

            {/* Input row — desktop: the textarea alone, full width.
                Narrow viewport: textarea and a pinned Send share the row, the
                way the group-chat composer does it (Send used to be the last
                control of the toolbar and fell off the right edge of the
                screen — see MobileComposerMenu for the measurements). */}
            <div className="order-3 md:order-2 flex items-end gap-1.5 max-md:px-2 max-md:py-1.5">
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
                className={`flex-1 min-w-0 border-0 px-3.5 pt-3.5 pb-2 text-[15px] text-textMain placeholder-textMuted resize-none focus:outline-none min-h-[72px] max-h-[200px] bg-transparent leading-6 max-md:px-2.5 max-md:pt-1.5 max-md:pb-1.5 max-md:text-[14px] max-md:leading-5 max-md:min-h-[36px] max-md:max-h-[120px] ${
                  disabled ? 'text-textMuted cursor-not-allowed' : ''
                }`}
                rows={1}
                style={{ height: 'auto' }}
                onInput={(e) => {
                  const target = e.target as HTMLTextAreaElement;
                  target.style.height = 'auto';
                  target.style.height = `${Math.min(target.scrollHeight, 200)}px`;
                }}
              />
              {/* Narrow-viewport copy of Send/Stop; the toolbar copy owns md+. */}
              <div className="md:hidden shrink-0 pb-0.5">
                {sendOrStop}
              </div>
            </div>

            {/* Toolbar. Desktop: under the textarea. Narrow viewport: promoted to
                the first row and given the group-chat strip treatment
                (bg-bgLight + bottom hairline + card top radius). Mode / model /
                effort move into MobileComposerMenu there — the row cannot wrap
                and both ends are shrink-0, so anything extra pushes Send off
                the right edge of the screen. */}
            <div className="flex items-center gap-1.5 px-2.5 pb-2.5 pt-0.5 order-1 md:order-3 max-md:rounded-t-[22px] max-md:border-b max-md:border-border max-md:bg-bgLight max-md:px-2 max-md:pt-1 max-md:pb-1.5 max-md:min-h-[38px]">
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
                  className="w-7 h-7 rounded-full flex items-center justify-center text-textMuted hover:text-textMain hover:bg-primary/10 transition-colors border-0 bg-transparent cursor-pointer disabled:opacity-50 max-md:hidden"
                  title="上传文件"
                >
                  <FileText size={16} strokeWidth={1.75} />
                </button>
                {/* Desktop-only from here down: on narrow viewports these three
                    settings live in MobileComposerMenu. */}
                <div className="max-md:hidden">
                  <ModePicker mode={agentMode} disabled={disabled} onSelect={onModeChange} />
                </div>
              </div>

              <div className="flex-1 min-w-0" />

              <div className="flex items-center gap-1.5 shrink-0">
                <div className="max-md:hidden">
                  <SoloModelPicker
                    cards={modelCards}
                    currentCardName={currentCardName}
                    modelName={modelName}
                    fallbackLabel={fallbackLabel}
                    switching={switchingModel}
                    disabled={disabled}
                    onSelect={onSelectModel}
                    onWillOpen={onRefreshModelCards}
                    onAddModels={() => {
                      window.dispatchEvent(new CustomEvent('switchView', { detail: 'models' }));
                    }}
                  />
                </div>
                {showEffort ? (
                  <div className="max-md:hidden">
                    <EffortPicker
                      effort={reasoningEffort}
                      deepseekStyle={effortDeepseekish}
                      disabled={disabled || switchingModel}
                      onSelect={onEffortChange}
                    />
                  </div>
                ) : null}
                {/* Narrow viewport only: Mode + Model + Effort, folded away. */}
                <MobileComposerMenu
                  className="md:hidden"
                  disabled={disabled}
                  mode={agentMode}
                  onModeChange={onModeChange}
                  modelCards={modelCards}
                  currentCardName={currentCardName}
                  modelName={modelName}
                  switchingModel={switchingModel}
                  onSelectModel={onSelectModel}
                  onWillOpen={onRefreshModelCards}
                  onAddModels={() => {
                    window.dispatchEvent(new CustomEvent('switchView', { detail: 'models' }));
                  }}
                  effort={reasoningEffort}
                  onEffortChange={onEffortChange}
                  showEffort={showEffort}
                  deepseekStyle={effortDeepseekish}
                />
                {voiceEnabled && (voiceCapture.recording || sttDictating) ? (
                  <VoiceRecordPill
                    durationSec={voiceCapture.durationSec}
                    level={voiceCapture.level}
                    dictating={sttDictating}
                    disabled={disabled}
                    onClick={() => {
                      if (sttDictating) return;
                      voiceCaptureApiRef.current?.stopRecord();
                    }}
                    title={sttDictating ? '正在转写…' : '点击停止并转写'}
                  />
                ) : voiceEnabled ? (
                  <button
                    type="button"
                    disabled={disabled}
                    onClick={toggleVoicePanel}
                    className={`w-8 h-8 rounded-full flex items-center justify-center relative border-0 cursor-pointer transition-colors ${
                      voicePanelOpen
                        ? 'bg-primary/20 text-primary'
                        : voiceCallActive
                          ? 'bg-emerald-500/20 text-emerald-500'
                          : 'text-textMuted hover:text-textMain hover:bg-primary/10 bg-transparent'
                    }`}
                    title={
                      voiceCallActive
                        ? '实时通话进行中（点击展开/折叠）'
                        : '语音消息 / 实时通话'
                    }
                  >
                    <Mic size={18} strokeWidth={1.75} />
                    {voiceCallActive ? (
                      <span className="absolute top-1 right-1 h-1.5 w-1.5 rounded-full bg-emerald-500" />
                    ) : null}
                  </button>
                ) : null}
                {/* Desktop copy of Send/Stop; the narrow-viewport copy lives in
                    the input row, so exactly one is ever visible. */}
                <div className="max-md:hidden">{sendOrStop}</div>
              </div>
            </div>
          </div>
          </div>

          <SoloContextFooter
            cwd={cwd}
            tokenStats={tokenStats}
            locked
            onViewReport={onViewReport}
            onCompressContext={onCompressContext}
            compressing={compressing}
            compressDisabled={compressDisabled}
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
});
