import React, { useState, useRef, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import i18next from 'i18next';
import { Bold, Italic, Link, List, Code, Smile, Folder, Paperclip, Send, X, Image as ImageIcon, Mic } from 'lucide-react';
import { User } from '../types';
import { parse } from 'marked';
import { AvatarImg } from './AvatarImg';

interface MessageInputProps {
  value: string;
  onChange: (val: string) => void;
  onSend: () => void;
  onAddAI: () => void;
  onFileSelect: () => void;
  onFolderSelect: () => void;
  onImageSelect?: () => void; // 专门用于选择图片
  onVoiceRecord?: (blob: Blob, duration: number) => void; // 语音录制回调
  placeholder?: string;
  groupMembers?: User[];
  onUploadFile?: (file: File) => void;
  onUploadFolder?: (files: FileList) => void;
  onPasteFiles?: (files: File[]) => void; // 粘贴文件/图片回调
  hasAttachments?: boolean; // 是否有待发送的附件
}

// 文件大小限制配置
const MAX_FILE_SIZE = 100 * 1024 * 1024; // 100MB
const MAX_IMAGE_SIZE = 10 * 1024 * 1024; // 10MB
const MAX_IMAGE_DIMENSION = 1920; // 最大图片尺寸

// 图片压缩函数
const compressImage = (file: File, maxWidth = MAX_IMAGE_DIMENSION, maxHeight = MAX_IMAGE_DIMENSION, quality = 0.8): Promise<File> => {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.readAsDataURL(file);
    reader.onload = (event) => {
      const img = new Image();
      img.src = event.target?.result as string;
      img.onload = () => {
        const canvas = document.createElement('canvas');
        let width = img.width;
        let height = img.height;

        // 计算缩放比例
        if (width > height) {
          if (width > maxWidth) {
            height *= maxWidth / width;
            width = maxWidth;
          }
        } else {
          if (height > maxHeight) {
            width *= maxHeight / height;
            height = maxHeight;
          }
        }

        canvas.width = width;
        canvas.height = height;

        const ctx = canvas.getContext('2d');
        if (!ctx) {
          reject(new Error(i18next.t('chat.canvasContextError')));
          return;
        }

        ctx.drawImage(img, 0, 0, width, height);

        canvas.toBlob(
          (blob) => {
            if (blob) {
              const compressedFile = new File([blob], file.name, {
                type: 'image/jpeg',
                lastModified: Date.now()
              });
              resolve(compressedFile);
            } else {
              reject(new Error(i18next.t('chat.imageCompressFailed')));
            }
          },
          'image/jpeg',
          quality
        );
      };
      img.onerror = () => reject(new Error(i18next.t('chat.imageLoadFailed')));
    };
    reader.onerror = () => reject(new Error(i18next.t('chat.fileReadFailed')));
  });
};

// 验证文件大小
const validateFileSize = (file: File): { valid: boolean; error?: string } => {
  if (file.size > MAX_FILE_SIZE) {
    return {
      valid: false,
      error: i18next.t('chat.fileTooLarge', { mb: MAX_FILE_SIZE / 1024 / 1024 })
    };
  }

  if (file.type.startsWith('image/') && file.size > MAX_IMAGE_SIZE) {
    return {
      valid: false,
      error: i18next.t('chat.imageTooLarge', { mb: MAX_IMAGE_SIZE / 1024 / 1024 })
    };
  }

  return { valid: true };
};

// 表情列表
const EMOJIS = [
  '😀', '😃', '😄', '😁', '😆', '😅', '😂', '🤣', '😊', '😇',
  '🙂', '🙃', '😉', '😌', '😍', '🥰', '😘', '😗', '😙', '😚',
  '😋', '😛', '😝', '😜', '🤪', '🤨', '🧐', '🤓', '😎', '🥸',
  '🤩', '🥳', '😏', '😒', '😞', '😔', '😟', '😕', '🙁', '☹️',
  '😣', '😖', '😫', '😩', '🥺', '😢', '😭', '😤', '😠', '😡',
  '🤬', '🤯', '😳', '🥵', '🥶', '😱', '😨', '😰', '😥', '😓',
  '🤗', '🤔', '🤭', '🤫', '🤥', '😶', '😐', '😑', '😬', '🙄',
  '😯', '😦', '😧', '😮', '😲', '🥱', '😴', '🤤', '😪', '😵',
  '🤐', '🥴', '🤢', '🤮', '🤧', '😷', '🤒', '🤕', '🤑', '🤠',
  '👍', '👎', '👏', '🙌', '👐', '🤲', '🤝', '🙏', '✌️', '🤞',
  '❤️', '🧡', '💛', '💚', '💙', '💜', '🖤', '🤍', '🤎', '💔',
  '🔥', '💯', '✨', '🎉', '🎊', '🎈', '🎁', '🎄', '🎃', '🎅',
];

export const MessageInput: React.FC<MessageInputProps> = ({
  value,
  onChange,
  onSend,
  onAddAI,
  onFileSelect,
  onFolderSelect,
  onImageSelect,
  onVoiceRecord,
  placeholder,
  groupMembers = [],
  onPasteFiles,
  hasAttachments = false,
}) => {
  const { t } = useTranslation();
  const [isFocused, setIsFocused] = useState(false);
  const [showMentionList, setShowMentionList] = useState(false);
  const [showEmojiPicker, setShowEmojiPicker] = useState(false);
  const [isRichTextMode, setIsRichTextMode] = useState(false);
  const [isRecording, setIsRecording] = useState(false);
  const [recordingDuration, setRecordingDuration] = useState(0);
  const recordingDurationRef = useRef(0);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const emojiPickerRef = useRef<HTMLDivElement>(null);
  const emojiButtonRef = useRef<HTMLButtonElement>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);
  const recordingTimerRef = useRef<NodeJS.Timeout | null>(null);
  const onVoiceRecordRef = useRef(onVoiceRecord);

  // 更新 ref 当 prop 变化时
  useEffect(() => {
    onVoiceRecordRef.current = onVoiceRecord;
  }, [onVoiceRecord]);

  // 点击外部关闭表情选择器
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      const target = e.target as Node;
      // 检查点击是否在表情面板内或表情按钮上
      const isClickInsidePicker = emojiPickerRef.current?.contains(target);
      const isClickOnButton = emojiButtonRef.current?.contains(target);

      if (!isClickInsidePicker && !isClickOnButton && showEmojiPicker) {
        setShowEmojiPicker(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [showEmojiPicker]);

  // Effect to handle external triggers (like clicking the @ button)
  useEffect(() => {
    if (value.endsWith('@')) {
      setShowMentionList(true);
      textareaRef.current?.focus();
    } else if (showMentionList && !value.includes('@')) {
      setShowMentionList(false);
    }
  }, [value]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    // 检测是否为移动端
    const isMobile = window.innerWidth < 768;

    // 移动端：Enter 直接发送
    if (isMobile && e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      if (value.trim() || hasAttachments) {
        onSend();
      }
      return;
    }

    // 桌面端：Enter 直接发送（不带修饰键）
    if (!isMobile && e.key === 'Enter' && !e.shiftKey && !e.ctrlKey && !e.metaKey) {
      if (showMentionList) {
        e.preventDefault();
        setShowMentionList(false);
        return;
      }
      e.preventDefault();
      if (value.trim() || hasAttachments) {
        onSend();
      }
      return;
    }

    // 桌面端：Ctrl+Enter 换行（允许默认行为）
    if (!isMobile && e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
      // 允许默认行为（换行）
      return;
    }

    // 移动端和桌面端：Shift+Enter 都换行
    if (e.key === 'Enter' && e.shiftKey) {
      // 允许换行，不阻止默认行为
      return;
    }
  };

  // 开始录音
  const startRecording = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mediaRecorder = new MediaRecorder(stream);
      mediaRecorderRef.current = mediaRecorder;
      audioChunksRef.current = [];

      mediaRecorder.ondataavailable = (e) => {
        if (e.data.size > 0) {
          audioChunksRef.current.push(e.data);
        }
      };

      mediaRecorder.onstop = () => {
        const audioBlob = new Blob(audioChunksRef.current, { type: 'audio/webm' });
        const duration = recordingDurationRef.current;

        if (onVoiceRecordRef.current) {
          onVoiceRecordRef.current(audioBlob, duration);
        }

        // 停止所有轨道
        stream.getTracks().forEach(track => track.stop());
      };

      mediaRecorder.start();
      setIsRecording(true);
      setRecordingDuration(0);

      // 开始计时
      recordingDurationRef.current = 0;
      recordingTimerRef.current = setInterval(() => {
        recordingDurationRef.current += 1;
        setRecordingDuration(recordingDurationRef.current);
        if (recordingDurationRef.current >= 60) { // 最大60秒
          stopRecording();
        }
      }, 1000);
    } catch (err) {
      console.error('无法访问麦克风:', err);
      const isMobile = /iPhone|iPad|iPod|Android/i.test(navigator.userAgent);
      const message = isMobile
        ? t('chat.microphoneErrorMobile')
        : t('chat.microphoneErrorDesktop');
      alert(message);
    }
  };

  // 停止录音
  const stopRecording = () => {
    if (mediaRecorderRef.current && isRecording) {
      mediaRecorderRef.current.stop();
      setIsRecording(false);

      if (recordingTimerRef.current) {
        clearInterval(recordingTimerRef.current);
        recordingTimerRef.current = null;
      }
    }
  };

  const handleChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const val = e.target.value;
    onChange(val);

    // Manual typing trigger
    if (val.endsWith('@')) {
      setShowMentionList(true);
    } else if (showMentionList && val.endsWith(' ')) {
      setShowMentionList(false);
    }
  };

  const insertMention = (userName: string) => {
    const newValue = value.slice(0, -1) + `@${userName} `;
    onChange(newValue);
    setShowMentionList(false);
    textareaRef.current?.focus();
  };

  const insertFormat = (format: string) => {
    onChange(value + format);
    textareaRef.current?.focus();
  };

  const insertEmoji = (emoji: string) => {
    onChange(value + emoji);
    textareaRef.current?.focus();
    // 不关闭表情选择器，方便连续选择
  };

  // Parse markdown content for preview
  const parseContent = (content: string) => {
    if (!content.trim()) return '';
    const withMentions = content.replace(/@(\w+)/g, '**@$1**');
    let parsed = parse(withMentions) as string;
    parsed = parsed.replace(/@(\w+)/g, '<span class="text-primary font-bold">@$1</span>');
    return parsed;
  };

  return (
    <div className="flex flex-col transition-colors duration-200 relative">

      {/* Mention Popup */}
      {showMentionList && groupMembers.length > 0 && (
        <div className="absolute bottom-full left-0 mb-2 w-48 bg-panel border border-border rounded-lg shadow-xl z-50 overflow-hidden animate-in fade-in slide-in-from-bottom-2">
          <div className="bg-bgLight px-3 py-2 text-xs font-bold text-textMuted border-b border-border">{t('chat.mentionMember')}</div>
          <div className="max-h-40 overflow-y-auto">
            {groupMembers.map(user => (
              <button
                key={user.id}
                className="w-full text-left px-3 py-2 hover:bg-primary/10 flex items-center gap-2 transition-colors"
                onClick={() => insertMention(user.name)}
              >
                <AvatarImg avatar={user.avatar} seed={user.id} label={user.name} className="w-5 h-5 rounded-full" />
                <span className="text-sm text-textMain">{user.name}</span>
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Emoji Picker - Responsive: absolute on desktop, fixed on mobile */}
      {showEmojiPicker && (
        <div
          ref={emojiPickerRef}
          className="bg-panel border border-border rounded-xl shadow-2xl z-[100] overflow-hidden animate-in fade-in slide-in-from-bottom-2 absolute bottom-full right-0 mb-2 w-[280px] max-md:fixed max-md:bottom-[120px] max-md:left-1/2 max-md:-translate-x-1/2 max-md:w-[90vw] max-md:max-w-[320px]"
          onMouseDown={(e) => e.preventDefault()} // 阻止失去焦点
        >
          <div className="bg-bgLight px-3 py-2 text-xs font-bold text-textMuted border-b border-border flex justify-between items-center">
            <span>Emoji</span>
            <button
              onClick={(e) => {
                e.stopPropagation();
                setShowEmojiPicker(false);
                textareaRef.current?.focus();
              }}
              onMouseDown={(e) => e.preventDefault()}
              className="p-1 hover:bg-border rounded"
            >
              <X size={14} />
            </button>
          </div>
          <div className="p-3 grid grid-cols-8 gap-1 max-h-[200px] overflow-y-auto">
            {EMOJIS.map((emoji, index) => (
              <button
                key={index}
                className="text-xl hover:bg-border rounded p-1 transition-colors active:scale-90"
                onClick={(e) => {
                  e.preventDefault();
                  e.stopPropagation();
                  insertEmoji(emoji);
                  // 插入表情后保持输入框焦点，但不关闭面板
                  setTimeout(() => textareaRef.current?.focus(), 0);
                }}
                onMouseDown={(e) => e.preventDefault()}
              >
                {emoji}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Toolbar - Mobile Optimized */}
      <div className="flex items-center justify-between px-2 py-0.5 md:py-1.5 border-b border-border bg-bgLight min-h-[32px] md:min-h-[44px]">
        <div className="flex items-center gap-0.5 md:gap-1">
          {/* 富文本模式开关 */}
          <button
            onClick={(e) => {
              e.preventDefault();
              e.stopPropagation();
              setIsRichTextMode(!isRichTextMode);
              setTimeout(() => textareaRef.current?.focus(), 0);
            }}
            onMouseDown={(e) => e.preventDefault()}
            className={`flex items-center gap-0.5 md:gap-1 px-1 md:px-2 py-0.5 md:py-1 rounded text-[10px] md:text-xs font-semibold transition-colors ${
              isRichTextMode ? 'bg-primary text-white' : 'bg-border text-textMuted hover:bg-border/80'
            }`}
            title={isRichTextMode ? t('chat.richTextOff') : t('chat.richTextOn')}
          >
            <span className="text-xs">✨</span>
            <span className="hidden sm:inline">{t('chat.richText')}</span>
          </button>

          {isRichTextMode && (
            <>
              <div className="h-3 w-px bg-border mx-0.5 md:mx-1"></div>
              <button onClick={(e) => { e.preventDefault(); e.stopPropagation(); insertFormat('**bold**'); }} onMouseDown={(e) => e.preventDefault()} className="p-1 md:p-1.5 hover:bg-border rounded text-textMuted" title="Bold"><Bold size={12} className="md:w-4 md:h-4" /></button>
              <button onClick={(e) => { e.preventDefault(); e.stopPropagation(); insertFormat('*italic*'); }} onMouseDown={(e) => e.preventDefault()} className="p-1 md:p-1.5 hover:bg-border rounded text-textMuted" title="Italic"><Italic size={12} className="md:w-4 md:h-4" /></button>
              <button onClick={(e) => { e.preventDefault(); e.stopPropagation(); insertFormat('- '); }} onMouseDown={(e) => e.preventDefault()} className="p-1 md:p-1.5 hover:bg-border rounded text-textMuted" title="List"><List size={12} className="md:w-4 md:h-4" /></button>
              <button onClick={(e) => { e.preventDefault(); e.stopPropagation(); insertFormat('`code`'); }} onMouseDown={(e) => e.preventDefault()} className="p-1 md:p-1.5 hover:bg-border rounded text-textMuted" title="Code"><Code size={12} className="md:w-4 md:h-4" /></button>
              <button onClick={(e) => { e.preventDefault(); e.stopPropagation(); insertFormat('[link](url)'); }} onMouseDown={(e) => e.preventDefault()} className="p-1 md:p-1.5 hover:bg-border rounded text-textMuted" title="Link"><Link size={12} className="md:w-4 md:h-4" /></button>
            </>
          )}

          <div className="h-4 w-px bg-gray-300 mx-1"></div>

          {/* AI 按钮 */}
          <button
            onClick={(e) => {
              e.preventDefault();
              e.stopPropagation();
              onAddAI();
              setTimeout(() => textareaRef.current?.focus(), 0);
            }}
            onMouseDown={(e) => e.preventDefault()}
            className="flex items-center gap-1 px-2 py-1 bg-purple-100 text-purple-700 hover:bg-purple-200 rounded text-xs font-semibold transition-colors"
          >
            <span>@</span>
          </button>
        </div>

        <div className="flex items-center gap-0.5 md:gap-1">
          {/* 附件按钮 */}
          <button
            onClick={(e) => {
              e.preventDefault();
              e.stopPropagation();
              onFileSelect();
            }}
            onMouseDown={(e) => e.preventDefault()} // 阻止失去焦点
            className="p-1 md:p-1.5 hover:bg-border rounded text-textMuted"
            title={t('chat.uploadFile')}
          >
            <Paperclip size={14} className="md:w-4 md:h-4" />
          </button>

          {/* 文件夹按钮 */}
          <button
            onClick={(e) => {
              e.preventDefault();
              e.stopPropagation();
              onFolderSelect();
            }}
            onMouseDown={(e) => e.preventDefault()} // 阻止失去焦点
            className="p-1 md:p-1.5 hover:bg-border rounded text-textMuted"
            title={t('chat.uploadFolder')}
          >
            <Folder size={14} className="md:w-4 md:h-4" />
          </button>

          {/* 图片按钮 - 专门用于快速选择图片 */}
          {onImageSelect && (
            <button
              onClick={(e) => {
                e.preventDefault();
                e.stopPropagation();
                onImageSelect();
              }}
              onMouseDown={(e) => e.preventDefault()} // 阻止失去焦点
              className="p-1 md:p-1.5 hover:bg-border rounded text-textMuted hover:text-primary"
              title={t('chat.sendImage')}
            >
              <ImageIcon size={14} className="md:w-4 md:h-4" />
            </button>
          )}

          {/* 表情按钮 */}
          <button
            ref={emojiButtonRef}
            onClick={(e) => {
              e.preventDefault();
              e.stopPropagation();
              // 切换表情面板显示状态
              const newState = !showEmojiPicker;
              setShowEmojiPicker(newState);
              // 如果关闭面板，保持输入框焦点
              if (!newState) {
                setTimeout(() => textareaRef.current?.focus(), 0);
              }
            }}
            onMouseDown={(e) => e.preventDefault()} // 阻止失去焦点
            className={`p-1 md:p-1.5 rounded transition-colors ${showEmojiPicker ? 'bg-primary text-white' : 'hover:bg-border text-textMuted'}`}
            title={t('chat.emoji')}
          >
            <Smile size={14} className="md:w-4 md:h-4" />
          </button>
        </div>
      </div>

      {/* Live Preview (仅在富文本模式下显示) */}
      {isRichTextMode && value.trim() && (
        <div className="border-b border-border bg-bgLight/50">
          <div className="px-3 py-1 text-xs text-textMuted font-medium border-b border-border/50">
            {t('chat.preview')}
          </div>
          <div
            className="p-3 prose prose-sm max-w-none prose-p:my-0.5 prose-ul:my-1 prose-ol:my-1 prose-li:my-0 prose-headings:my-1 prose-code:bg-border prose-code:px-1 prose-code:py-0.5 prose-code:rounded prose-code:text-sm prose-pre:bg-gray-800 prose-pre:text-white prose-pre:p-3 prose-pre:rounded-lg prose-blockquote:border-l-4 prose-blockquote:border-primary prose-blockquote:pl-3 prose-blockquote:italic prose-blockquote:text-textMuted break-words min-h-[40px] max-h-[100px] overflow-y-auto"
            dangerouslySetInnerHTML={{ __html: parseContent(value) }}
          />
        </div>
      )}


      <div className="flex items-center gap-1.5 md:gap-2 px-2 py-1 md:py-2 flex-nowrap w-full">
        <textarea
          ref={textareaRef}
          value={value}
          onChange={handleChange}
          onKeyDown={handleKeyDown}
          onFocus={() => setIsFocused(true)}
          onBlur={() => {
            setIsFocused(false);
            setTimeout(() => setShowMentionList(false), 200);
          }}
          onPaste={(e) => {
            if (!onPasteFiles) return;
            const files = Array.from(e.clipboardData.items)
              .filter(item => item.kind === 'file')
              .map(item => item.getAsFile())
              .filter((f): f is File => f !== null);
            if (files.length > 0) {
              e.preventDefault();
              onPasteFiles(files);
            }
          }}
          placeholder={placeholder}
          className="flex-1 px-2 py-1 md:px-3 md:py-2 h-[32px] md:h-[44px] max-h-[80px] md:max-h-[120px] resize-none focus:outline-none bg-bgLight border border-border rounded-md md:rounded-xl text-sm leading-relaxed min-w-0"
          rows={1}
        />

        <button
          onClick={(e) => {
            e.preventDefault();
            e.stopPropagation();
            if (value.trim() || hasAttachments) {
              onSend();
              // 发送后保持输入框焦点，不关闭键盘
              setTimeout(() => textareaRef.current?.focus(), 0);
            }
          }}
          onMouseDown={(e) => e.preventDefault()} // 阻止失去焦点
          disabled={!value.trim() && !hasAttachments}
          className={`flex items-center justify-center w-[32px] h-[32px] md:w-[44px] md:h-[44px] rounded-md md:rounded-xl transition-all flex-shrink-0 ${
            (value.trim() || hasAttachments)
              ? 'bg-primary text-white hover:bg-primary/90 shadow-sm'
              : 'bg-border text-textMuted cursor-not-allowed'
          }`}
        >
          <Send size={16} className="md:w-5 md:h-5" />
        </button>

        {/* 语音录制按钮 - 仅桌面端显示 */}
        <button
          onMouseDown={() => startRecording()}
          onMouseUp={() => stopRecording()}
          onMouseLeave={() => isRecording && stopRecording()}
          className={`hidden md:flex items-center justify-center w-[32px] h-[32px] md:w-[44px] md:h-[44px] rounded-md md:rounded-xl transition-all flex-shrink-0 ${
            isRecording
              ? 'bg-red-500 text-white animate-pulse'
              : 'bg-red-100 text-red-600 hover:bg-red-200'
          }`}
          title={isRecording ? t('chat.recording', { duration: recordingDuration }) : t('chat.holdToRecord')}
        >
          <Mic size={16} className="md:w-5 md:h-5" />
        </button>
      </div>

      {/* 录制状态指示器 */}
      {isRecording && (
        <div className="absolute bottom-full mb-2 left-1/2 -translate-x-1/2 bg-red-500 text-white px-3 py-1.5 rounded-full text-xs md:text-sm font-medium flex items-center gap-2 shadow-lg whitespace-nowrap z-50">
          <span className="w-2 h-2 bg-white rounded-full animate-pulse"></span>
          {t('chat.recordingStatus', { duration: recordingDuration })}
        </div>
      )}
    </div>
  );
};
