import React, { useEffect, useRef, useState, useLayoutEffect, useCallback, useMemo } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import { MoreHorizontal, Paperclip, Pin, Reply, Trash2, Copy, MessageSquare, Download, Folder, File as FileIcon, X, AtSign, ArrowLeft, Edit2, Check, ChevronLeft, ChevronRight, ChevronUp, ChevronDown, ZoomIn, Image as ImageIcon, RotateCcw, Play, Pause, Film, Mic } from 'lucide-react';
import { Message, User, Group, MessageType, Attachment } from '../types';
import { MessageInput } from './MessageInput';
import { uploadAPI, SERVER_BASE_URL, messageAPI, agentSessionAPI } from '../services/api';
import { parse } from 'marked';
import { AvatarImg } from './AvatarImg';
import {
  CollabStepApprovalCard,
  parseCollabApproval,
} from './CollabStepApprovalCard';
import {
  ProposeOptionsCard,
  parseProposeOptions,
} from './ProposeOptionsCard';
import { useMobileChatSwipe } from '../hooks/useMobileChatSwipe';

// 全局消息位置记忆缓存：groupId -> { messageId, scrollTop }
// 使用模块级变量，确保组件重新挂载后缓存仍然有效
const globalScrollPositionCache: Record<string, { messageId: string; scrollTop: number }> = {};

// 辅助函数：递归遍历文件夹
const traverseFileTree = (item: any, path = ""): Promise<{file: File, path: string}[]> => {
  return new Promise((resolve) => {
    if (item.isFile) {
      item.file((file: File) => {
        resolve([{ file, path: path + file.name }]);
      });
    } else if (item.isDirectory) {
      const dirReader = item.createReader();
      let allEntries: any[] = [];

      const readAllEntries = () => {
        dirReader.readEntries(async (entries: any[]) => {
          if (entries.length > 0) {
            allEntries = [...allEntries, ...entries];
            readAllEntries(); // 继续读取，直到为空
          } else {
            // 所有条目读取完毕，开始处理
            const promises = allEntries.map((entry) =>
              traverseFileTree(entry, path + item.name + "/")
            );
            const results = await Promise.all(promises);
            resolve(results.flat());
          }
        }, (error: any) => {
          console.error("Read entries failed:", error);
          resolve([]);
        });
      };

      readAllEntries();
    } else {
      resolve([]);
    }
  });
};

interface ChatWindowProps {
  group: Group;
  groups: Group[];
  messages: Message[];
  users: Record<string, User>;
  currentUser: User;
  onSendMessage: (content: string, type: MessageType, attachments?: Attachment[], replyToId?: string) => void;
  onDeleteMessage: (msgId: string) => void;
  onUndoRecall: (msgId: string) => void;
  onPermanentDelete: (msgId: string) => void;
  onEditMessage: (msgId: string, newContent: string) => void;
  onPinMessage: (msgId: string) => void;
  onPrependMessages: (groupId: string, beforeTimestamp: number) => Promise<number>;
  onConsumeMention: (groupId: string) => void;
  toggleRightPanel: () => void;
  /** Mobile: open group settings (RightPanel). Used by left-swipe gesture. */
  onOpenGroupSettings?: () => void;
  filter: { text: string; userId: string | null; dateFrom: string | null; dateTo: string | null; };
  onBack: () => void;
  shouldJumpToMention?: boolean;
  // Bidirectional lazy loading props
  onReplaceMessages: (groupId: string, messages: Message[]) => void;
  onLoadMessagesAround: (groupId: string, messageId: string, timestamp: string) => Promise<void>;
  // True when the active group's messages are being loaded for the first time
  // (i.e. cache miss — never visited or never prefetched on hover). Drives the
  // skeleton placeholder in the messages area so the user sees "loading"
  // instead of an empty panel during the fetch. Cached groups never set this.
  isMessagesLoading?: boolean;
}

// 语音播放器组件
interface VoicePlayerProps {
  url: string;
  duration: number;
}

const VoicePlayer: React.FC<VoicePlayerProps> = ({ url, duration }) => {
  const [isPlaying, setIsPlaying] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const audioRef = useRef<HTMLAudioElement>(null);

  useEffect(() => {
    const audio = audioRef.current;
    if (!audio) return;

    const handleTimeUpdate = () => setCurrentTime(audio.currentTime);
    const handleEnded = () => {
      setIsPlaying(false);
      setCurrentTime(0);
    };

    audio.addEventListener('timeupdate', handleTimeUpdate);
    audio.addEventListener('ended', handleEnded);

    return () => {
      audio.removeEventListener('timeupdate', handleTimeUpdate);
      audio.removeEventListener('ended', handleEnded);
    };
  }, []);

  const togglePlay = () => {
    const audio = audioRef.current;
    if (!audio) return;

    if (isPlaying) {
      audio.pause();
    } else {
      audio.play().catch(err => console.error('播放失败:', err));
    }
    setIsPlaying(!isPlaying);
  };

  const formatTime = (seconds: number) => {
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return `${mins}:${secs.toString().padStart(2, '0')}`;
  };

  const progress = duration > 0 ? (currentTime / duration) * 100 : 0;

  return (
    <div className="flex items-center gap-2 p-2 bg-primary/10 rounded-lg min-w-[180px] max-w-[250px]">
      <audio ref={audioRef} src={url} preload="metadata" />

      <button
        onClick={togglePlay}
        className="w-8 h-8 flex items-center justify-center bg-primary text-white rounded-full hover:bg-primary/90 transition-colors flex-shrink-0"
      >
        {isPlaying ? <Pause size={14} /> : <Play size={14} className="ml-0.5" />}
      </button>

      <div className="flex-1 flex flex-col gap-1 min-w-0">
        {/* 进度条 */}
        <div className="h-1.5 bg-primary/20 rounded-full overflow-hidden">
          <div
            className="h-full bg-primary rounded-full transition-all duration-100"
            style={{ width: `${progress}%` }}
          />
        </div>

        {/* 时间显示 */}
        <div className="flex justify-between text-[10px] text-textMuted">
          <span>{formatTime(currentTime)}</span>
          <span>{formatTime(duration)}</span>
        </div>
      </div>
    </div>
  );
};

export const ChatWindow: React.FC<ChatWindowProps> = ({
  group, groups, messages, users, currentUser, onSendMessage, onDeleteMessage, onUndoRecall, onPermanentDelete, onEditMessage, onPinMessage, onPrependMessages, onConsumeMention, toggleRightPanel, onOpenGroupSettings, filter, onBack, shouldJumpToMention, onReplaceMessages, onLoadMessagesAround, isMessagesLoading
}) => {
  const { t } = useTranslation();
  const [inputText, setInputText] = useState('');
  const [sttDictating, setSttDictating] = useState(false);
  const [isDragging, setIsDragging] = useState(false);
  const [replyTo, setReplyTo] = useState<Message | null>(null);
  const [isLoadingHistory, setIsLoadingHistory] = useState(false);
  const [showPinnedMessages, setShowPinnedMessages] = useState(false);

  // Staged files/images: accumulated before sending (support multi-select + paste)
  interface StagedItem {
    id: string;
    file: File;
    localUrl: string;       // object URL for preview
    attachment?: Attachment; // set once upload completes
    uploading: boolean;
    uploadProgress?: number; // 0..100, updated via XHR progress events
    error?: boolean;
    isFolder?: boolean;      // true for folder uploads (render as file-style bar, not image)
    displayName?: string;    // override for file.name (e.g. folder name)
    displaySize?: number;    // override for file.size (e.g. total folder size)
  }
  const [stagedItems, setStagedItems] = useState<StagedItem[]>([]);

  // Active downloads: track per-attachment download progress so the user
  // sees an in-app progress bar instead of having to look at the browser's
  // download shelf (which is easy to miss in Electron, and invisible in
  // some kiosk-style deployments).
  interface DownloadState {
    attId: string;
    progress: number;   // 0..100
    fileName: string;
  }
  const [downloads, setDownloads] = useState<DownloadState[]>([]);

  // Bidirectional lazy loading state
  const [pinnedMessages, setPinnedMessages] = useState<Message[]>([]);
  const [hasMoreFuture, setHasMoreFuture] = useState(true);
  const [isLoadingFuture, setIsLoadingFuture] = useState(false);
  const [jumpTargetMessage, setJumpTargetMessage] = useState<string | null>(null);

  // Auto-loading state for jump to message
  const [isAutoLoadingForJump, setIsAutoLoadingForJump] = useState(false);
  const [autoLoadingTargetId, setAutoLoadingTargetId] = useState<string | null>(null);
  const [autoLoadingProgress, setAutoLoadingProgress] = useState(0);

  // Image lightbox state
  const [lightboxImages, setLightboxImages] = useState<Array<{url: string, name: string}>>([]);
  const [lightboxIndex, setLightboxIndex] = useState<number | null>(null);
  const [showLightbox, setShowLightbox] = useState(false);
  const [lightboxScale, setLightboxScale] = useState(1);
  const [lightboxOffset, setLightboxOffset] = useState({ x: 0, y: 0 });
  const [isDraggingLightbox, setIsDraggingLightbox] = useState(false);
  const [dragStart, setDragStart] = useState({ x: 0, y: 0 });

  // Reset zoom when image changes or lightbox closes
  useEffect(() => {
    setLightboxScale(1);
    setLightboxOffset({ x: 0, y: 0 });
  }, [lightboxIndex, showLightbox]);

  const handleLightboxWheel = (e: React.WheelEvent) => {
    e.stopPropagation();
    if (e.ctrlKey || e.metaKey) {
        // Zoom
        const delta = e.deltaY > 0 ? -0.1 : 0.1;
        setLightboxScale(prev => Math.max(0.5, Math.min(5, prev + delta)));
    } else {
        // Regular scroll might want to pan if zoomed in?
        // For now let's just allow ctrl+wheel for zoom
    }
  };

  const handleLightboxMouseDown = (e: React.MouseEvent) => {
    if (lightboxScale > 1) {
        e.stopPropagation();
        setIsDraggingLightbox(true);
        setDragStart({ x: e.clientX - lightboxOffset.x, y: e.clientY - lightboxOffset.y });
    }
  };

  // Clamp pan offset so the zoomed image never goes off-viewport.
  // Image is at most 90vw x 90vh at scale=1; after scaling it grows from
  // the viewport center, so the max pan = (scaledSize - viewportSize) / 2.
  const clampLightboxOffset = useCallback((offset: {x: number, y: number}, scale: number) => {
    if (scale <= 1) return { x: 0, y: 0 };
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const maxX = Math.max(0, (vw * 0.9 * scale - vw) / 2);
    const maxY = Math.max(0, (vh * 0.9 * scale - vh) / 2);
    return {
      x: Math.max(-maxX, Math.min(maxX, offset.x)),
      y: Math.max(-maxY, Math.min(maxY, offset.y)),
    };
  }, []);

  const handleLightboxMouseMove = (e: React.MouseEvent) => {
    if (isDraggingLightbox) {
        e.stopPropagation();
        setLightboxOffset(clampLightboxOffset({
            x: e.clientX - dragStart.x,
            y: e.clientY - dragStart.y
        }, lightboxScale));
    }
  };

  const handleLightboxMouseUp = () => {
    setIsDraggingLightbox(false);
  };

  // Pinch to zoom for mobile
  const [touchDist, setTouchDist] = useState<number | null>(null);
  const handleLightboxTouchMove = (e: React.TouchEvent) => {
    if (e.touches.length === 2) {
        e.stopPropagation();
        const dist = Math.hypot(
            e.touches[0].pageX - e.touches[1].pageX,
            e.touches[0].pageY - e.touches[1].pageY
        );
        if (touchDist === null) {
            setTouchDist(dist);
        } else {
            const delta = (dist - touchDist) / 200;
            setLightboxScale(prev => Math.max(0.5, Math.min(5, prev + delta)));
            setTouchDist(dist);
        }
    } else if (e.touches.length === 1 && lightboxScale > 1) {
        // Pan with one finger if zoomed
        e.stopPropagation();
        // Implement pan logic here if needed, but keeping it simple for now
    }
  };

  const handleLightboxTouchEnd = () => {
    setTouchDist(null);
  };

  // Check for mentions in other groups (for back button notification)
  const hasMentionsInOtherGroups = useMemo(() => {
    return groups.some(g => g.id !== group.id && g.hasUnreadMention);
  }, [groups, group.id]);

  // Count mentions in other groups
  const otherGroupsMentionCount = useMemo(() => {
    return groups.filter(g => g.id !== group.id && g.hasUnreadMention).length;
  }, [groups, group.id]);

  // 统一的缓和提示音 - 适用于@提及和私信
  const playGentleNotificationSound = useCallback(() => {
    try {
      const audioContext = new (window.AudioContext || (window as any).webkitAudioContext)();

      // 创建双音调缓和提示音
      const oscillator1 = audioContext.createOscillator();
      const oscillator2 = audioContext.createOscillator();
      const gainNode = audioContext.createGain();

      oscillator1.connect(gainNode);
      oscillator2.connect(gainNode);
      gainNode.connect(audioContext.destination);

      // 缓和的双音调（类似风铃）
      oscillator1.type = 'sine';
      oscillator1.frequency.setValueAtTime(523.25, audioContext.currentTime); // C5 - 中音
      oscillator1.frequency.exponentialRampToValueAtTime(659.25, audioContext.currentTime + 0.3); // E5

      oscillator2.type = 'sine';
      oscillator2.frequency.setValueAtTime(659.25, audioContext.currentTime); // E5
      oscillator2.frequency.exponentialRampToValueAtTime(783.99, audioContext.currentTime + 0.3); // G5

      // 极低的音量，缓和的淡入淡出
      gainNode.gain.setValueAtTime(0, audioContext.currentTime);
      gainNode.gain.linearRampToValueAtTime(0.03, audioContext.currentTime + 0.1); // 仅3%音量
      gainNode.gain.exponentialRampToValueAtTime(0.0001, audioContext.currentTime + 0.8);

      oscillator1.start(audioContext.currentTime);
      oscillator2.start(audioContext.currentTime);
      oscillator1.stop(audioContext.currentTime + 0.8);
      oscillator2.stop(audioContext.currentTime + 0.8);
    } catch (e) {
      // 静默失败
    }
  }, []);

  // 处理资源 URL
  const getResourceUrl = (url: string): string => {
    if (!url) return '';
    if (url.startsWith('http://') || url.startsWith('https://')) {
      return url;
    }
    if (!url.startsWith('/')) {
      return '/' + url;
    }
    return url;
  };

  // 复制文本到剪贴板 - 支持移动端
  const copyToClipboard = async (text: string) => {
    try {
      // 方法1: 现代 Clipboard API (需要 HTTPS 或 localhost)
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(text);
        console.log('[ChatWindow] Text copied via Clipboard API');
        showCopyToast(t('chat.copiedToClipboard'));
        return true;
      }

      // 方法2: 移动端友好的复制方案
      // 创建一个可见的 textarea（移动端需要可见元素才能复制）
      const textArea = document.createElement('textarea');
      textArea.value = text;
      textArea.style.cssText = `
        position: fixed;
        top: 50%;
        left: 50%;
        transform: translate(-50%, -50%);
        width: 1px;
        height: 1px;
        padding: 0;
        border: none;
        outline: none;
        boxShadow: none;
        background: transparent;
        opacity: 0;
        z-index: -1;
      `;
      document.body.appendChild(textArea);

      // 移动端需要 focus 和 select range
      textArea.focus();
      textArea.setSelectionRange(0, textArea.value.length);

      // 尝试复制
      let successful = false;
      try {
        successful = document.execCommand('copy');
      } catch (err) {
        console.log('[ChatWindow] execCommand failed:', err);
      }

      document.body.removeChild(textArea);

      if (successful) {
        console.log('[ChatWindow] Text copied via execCommand');
        showCopyToast(t('chat.copiedToClipboard'));
        return true;
      }

      // 方法3: 如果自动复制失败，显示复制弹窗让用户手动复制
      throw new Error('Auto copy failed');

    } catch (err) {
      console.error('[ChatWindow] Copy methods failed:', err);
      // 显示手动复制弹窗
      showManualCopyModal(text);
      return false;
    }
  };

  // 显示复制成功提示
  const [copyToast, setCopyToast] = useState<{show: boolean, message: string}>({show: false, message: ''});

  const showCopyToast = (message: string) => {
    setCopyToast({show: true, message});
    setTimeout(() => setCopyToast({show: false, message: ''}), 2000);
  };

  // 手动复制弹窗状态
  const [manualCopyText, setManualCopyText] = useState<string | null>(null);

  // 显示手动复制弹窗
  const showManualCopyModal = (text: string) => {
    setManualCopyText(text);
  };

  // 下载文件
  const downloadFile = async (url: string, filename: string) => {
    try {
      const fullUrl = getResourceUrl(url);
      const response = await fetch(fullUrl);
      if (!response.ok) throw new Error('Download failed');

      const blob = await response.blob();
      const downloadUrl = window.URL.createObjectURL(blob);

      const link = document.createElement('a');
      link.href = downloadUrl;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);

      window.URL.revokeObjectURL(downloadUrl);
    } catch (error) {
      console.error('Download failed:', error);
      window.open(getResourceUrl(url), '_blank');
    }
  };

  // Listen for @mentions and play gentle notification sound
  useEffect(() => {
    // Check if there are new mentions in the current group
    if (group.hasUnreadMention) {
      // Play gentle notification sound
      playGentleNotificationSound();
    }
  }, [group.hasUnreadMention, playGentleNotificationSound]);

  // Edit State
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editContent, setEditContent] = useState('');

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const folderInputRef = useRef<HTMLInputElement>(null);
  const imageInputRef = useRef<HTMLInputElement>(null);
  const previousScrollHeightRef = useRef<number>(0);
  const previousFirstMessageIdRef = useRef<string | null>(null);

  // 使用全局缓存，确保组件重新挂载后位置记忆仍然有效
  const isRestoringPositionRef = useRef(false);

  // 使用 ref 存储 messages 的最新值，解决异步函数中的闭包问题
  const messagesRef = useRef(messages);
  useEffect(() => {
    messagesRef.current = messages;
  }, [messages]);

  // Pull to load more history states
  const [pullDistance, setPullDistance] = useState(0);
  const [isPulling, setIsPulling] = useState(false);
  const [pullTriggered, setPullTriggered] = useState(false);
  const [showLoadedToast, setShowLoadedToast] = useState(false);
  const [loadedCount, setLoadedCount] = useState(0);
  const pullStartYRef = useRef(0);
  const maxPullDistance = 120; // 最大下拉距离
  const triggerThreshold = 80; // 触发加载的阈值
  const hasRestoredPositionRef = useRef(false);
  const currentGroupIdRef = useRef(group.id); // 用于在 cleanup 中访问正确的 group.id
  // Tracks whether the initial scroll/restore for the current group has
  // completed. Prevents the 3 scroll effects (init/restore, history-prepend
  // layout effect, new-message auto-scroll) from racing each other on group
  // switch — the old behavior fired up to 3 scrollIntoView calls within
  // ~300ms which produced a visible "jumpy" flicker.
  const initialScrollDoneRef = useRef<string | null>(null);

  // Tracks the 500ms "isRestoringPositionRef = false" timeout scheduled by
  // the async restore fallback, so we can cancel it when the user switches
  // groups mid-flight. Without this, a late callback from the previous
  // group would clobber the new group's restore state and let the
  // history-prepend layout effect scroll us away from the restored spot.
  const restoringPositionTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // 滚动到顶/底按钮的显示状态 — 使用 refs 避免滚动时触发 re-render
  const [showScrollTop, setShowScrollTop] = useState(false);
  const [showScrollBottom, setShowScrollBottom] = useState(false);
  const [scrollActive, setScrollActive] = useState(false);
  const scrollHideTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const scrollRafRef = useRef<number>(0);
  const lastScrollTopRef = useRef(false);
  const lastScrollBottomRef = useRef(false);
  const lastScrollActiveRef = useRef(false);

  // Filter messages logic including Date Range — memoized to avoid re-filter on every render
  const filteredMessages = useMemo(() => messages.filter(m => {
    const matchesText = m.content.toLowerCase().includes(filter.text.toLowerCase());
    const matchesUser = filter.userId ? m.senderId === filter.userId : true;

    // Date Range Logic
    let matchesDate = true;
    if (filter.dateFrom) {
        matchesDate = matchesDate && m.timestamp >= new Date(filter.dateFrom).setHours(0,0,0,0);
    }
    if (filter.dateTo) {
        matchesDate = matchesDate && m.timestamp <= new Date(filter.dateTo).setHours(23,59,59,999);
    }

    return matchesText && matchesUser && matchesDate;
  }), [messages, filter.text, filter.userId, filter.dateFrom, filter.dateTo]);

  // O(1) message lookup map for reply context — avoids O(n²) in render loop
  const messageMap = useMemo(() => {
    const map = new Map<string, typeof messages[0]>();
    for (const m of messages) map.set(m.id, m);
    return map;
  }, [messages]);

  // Use independently loaded pinned messages instead of filtering from messages prop

  // 保存当前滚动位置（在组件卸载或群组切换前）
  const saveScrollPosition = () => {
    const groupIdToSave = currentGroupIdRef.current;
    if (scrollContainerRef.current && messages.length > 0 && !isRestoringPositionRef.current) {
      const container = scrollContainerRef.current;
      const scrollTop = container.scrollTop;

      // 找到当前视口中最上面的可见消息
      // 使用所有子元素（消息元素都有 id 属性）
      const allElements = container.querySelectorAll('[id]');
      let visibleMessageId: string | null = null;
      let minDistance = Infinity;

      allElements.forEach((el) => {
        // 只考虑消息元素（排除其他有 id 的元素）
        // 消息元素的 ID 是消息 UUID，其他元素可能有不同前缀
        if (!el.id || el.id.length < 10 || el.id.includes('_')) return;

        const rect = el.getBoundingClientRect();
        const containerRect = container.getBoundingClientRect();
        const relativeTop = rect.top - containerRect.top;

        // 找到在视口内或最接近视口顶部的消息
        if (relativeTop >= -50 && relativeTop < minDistance) {
          minDistance = relativeTop;
          visibleMessageId = el.id;
        }
      });

      // 如果没找到可见消息，使用第一条消息
      if (!visibleMessageId && messages.length > 0) {
        visibleMessageId = messages[0].id;
      }

      if (visibleMessageId) {
        globalScrollPositionCache[groupIdToSave] = {
          messageId: visibleMessageId,
          scrollTop: scrollTop
        };
      }
    }
  };

  // ========== Bidirectional Lazy Loading Functions ==========

  // Load pinned messages independently from the message list
  const loadPinnedMessages = async () => {
    try {
      const pinned = await messageAPI.getPinnedMessages(group.id);
      // Convert API response to Message format
      const formattedPinned: Message[] = pinned.map(p => ({
        id: p.id,
        senderId: p.sender_id,
        content: p.content,
        timestamp: new Date(p.timestamp).getTime(),
        type: p.type as MessageType,
        attachments: p.attachments?.map((a: any) => ({
          id: a.id || `att_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`,
          name: a.name,
          size: a.size,
          url: a.url,
          type: (a.type || 'file') as 'image' | 'video' | 'file' | 'folder'
        })),
        isPinned: true,
        isEdited: p.is_edited,
        mentions: p.mentions || []
      }));
      setPinnedMessages(formattedPinned);
    } catch (error) {
      console.error('Failed to load pinned messages:', error);
    }
  };

  // Load messages centered around a target timestamp (bidirectional loading)
  const loadMessagesAround = async (targetId: string, targetTimestamp: number) => {
    setIsRestoringPositionRef.current = true;
    setJumpTargetMessage(targetId);

    try {
      const timestamp = new Date(targetTimestamp).toISOString();
      const messagesAround = await messageAPI.getMessagesAround(
        group.id,
        timestamp,
        20, // before limit
        20  // after limit
      );

      // Convert API response to Message format
      const formattedMessages: Message[] = messagesAround.map(m => ({
        id: m.id,
        senderId: m.sender_id,
        content: m.content,
        timestamp: new Date(m.timestamp).getTime(),
        type: m.type as MessageType,
        attachments: m.attachments?.map(a => ({
          id: a.id,
          name: a.name,
          size: a.size,
          url: a.url,
          type: a.type as 'image' | 'video' | 'file' | 'folder'
        })),
        replyToId: m.reply_to_id,
        isPinned: m.is_pinned,
        isEdited: m.is_edited,
        mentions: m.mentions
      }));

      // Replace the entire message list via parent callback
      onReplaceMessages(group.id, formattedMessages);

      // Update hasMoreFuture based on whether we got future messages
      const targetIndex = formattedMessages.findIndex(m => m.id === targetId);
      setHasMoreFuture(targetIndex === -1 || formattedMessages.length - targetIndex - 1 >= 20);

    } catch (error) {
      console.error('Failed to load messages around target:', error);
    } finally {
      setTimeout(() => {
        setIsRestoringPositionRef.current = false;
      }, 300);
    }
  };

  // Load more recent messages (downward scrolling)
  const loadMoreFutureMessages = async () => {
    if (isLoadingFuture || !hasMoreFuture || messages.length === 0) return;

    setIsLoadingFuture(true);

    try {
      // Get the latest message timestamp
      const latestMsg = messages[messages.length - 1];
      const after = new Date(latestMsg.timestamp + 1).toISOString();

      // Load messages after the latest one
      const futureMessages = await messageAPI.getMessages(group.id, undefined, 20);

      if (futureMessages.length === 0) {
        setHasMoreFuture(false);
        return;
      }

      // Filter out messages we already have
      const existingIds = new Set(messages.map(m => m.id));
      const newMessages = futureMessages.filter(m => !existingIds.has(m.id));

      if (newMessages.length === 0) {
        setHasMoreFuture(false);
        return;
      }

      // Format and append new messages
      const formattedMessages: Message[] = newMessages.map(m => ({
        id: m.id,
        senderId: m.sender_id,
        content: m.content,
        timestamp: new Date(m.timestamp).getTime(),
        type: m.type as MessageType,
        attachments: m.attachments?.map(a => ({
          id: a.id,
          name: a.name,
          size: a.size,
          url: a.url,
          type: a.type as 'image' | 'video' | 'file' | 'folder'
        })),
        replyToId: m.reply_to_id,
        isPinned: m.is_pinned,
        isEdited: m.is_edited,
        mentions: m.mentions
      }));

      // Merge and sort
      const mergedMessages = [...messages, ...formattedMessages];
      mergedMessages.sort((a, b) => a.timestamp - b.timestamp);

      onReplaceMessages(group.id, mergedMessages);

      // If we got fewer than 20 messages, there are no more future messages
      if (newMessages.length < 20) {
        setHasMoreFuture(false);
      }
    } catch (error) {
      console.error('Failed to load future messages:', error);
    } finally {
      setIsLoadingFuture(false);
    }
  };

  // Handle scrolling down to load more recent messages
  const handleScrollDown = async () => {
    if (isLoadingFuture || !hasMoreFuture) return;

    const container = scrollContainerRef.current;
    if (!container) return;

    // If within 100px of bottom
    const distanceFromBottom = container.scrollHeight - container.scrollTop - container.clientHeight;
    if (distanceFromBottom < 100) {
      await loadMoreFutureMessages();
    }
  };

  // 恢复滚动位置
  const restoreScrollPosition = async () => {
    const cachedPosition = globalScrollPositionCache[group.id];

    if (!cachedPosition) {
      return false;
    }

    isRestoringPositionRef.current = true;

    const { messageId, scrollTop } = cachedPosition;

    // 先尝试滚动到保存的消息ID
    let element = document.getElementById(messageId);

    // 如果消息元素不存在，尝试加载历史消息直到找到（跳过滚动，只加载）
    if (!element) {
      const targetMsg = messages.find(m => m.id === messageId);
      if (targetMsg) {
        await loadMessagesUntilFound(messageId, targetMsg.timestamp, true); // true = skipScroll
        element = document.getElementById(messageId);
      }
    }

    if (element && scrollContainerRef.current) {
      // 直接设置 scrollTop，不使用 scrollIntoView，避免任何滚动动画
      // scrollTop 是容器的滚动位置，直接使用保存的值
      scrollContainerRef.current.scrollTop = scrollTop;

      isRestoringPositionRef.current = false;
      return true;
    }

    isRestoringPositionRef.current = false;
    return false;
  };

  // 加载置顶消息（独立于消息列表）
  useEffect(() => {
    loadPinnedMessages();
  }, [group.id]);

  // 监听消息置顶事件，刷新置顶消息列表
  useEffect(() => {
    const handleMessagePinned = () => {
      loadPinnedMessages();
    };
    window.addEventListener('messagePinned', handleMessagePinned);
    return () => window.removeEventListener('messagePinned', handleMessagePinned);
  }, [group.id]);

  // 页面加载时自动滚动到底部或恢复之前的位置
  //
  // 拆成两个 effect：
  //   1) useLayoutEffect（同步）：处理"缓存命中 + 元素已在 DOM"的快路径
  //      以及"无缓存首次访问"的滚到底部。这两个分支都不需要等任何东西，
  //      可以在浏览器 paint 之前完成 → 用户看不到"先跳到某个位置再跳回
  //      来"的两段式闪烁（之前 300ms setTimeout 允许浏览器先把上一个群
  //      的 scrollTop 画出来，然后再被 initScroll 覆盖）。
  //   2) useEffect（异步）："缓存命中 + 元素不在 DOM"的慢路径 —— 缓存
  //      的消息在更早的历史里，需要 loadMessagesUntilFound 把消息加载
  //      进来再恢复位置。这种情况只能等异步加载，无法同步完成。
  useLayoutEffect(() => {
    // Point the ref at the new group. This ref is read by saveScrollPosition
    // and by the scroll-event listener; both run against whichever group
    // is currently mounted, so they always save/restore the right key.
    currentGroupIdRef.current = group.id;

    // Reset the initial-scroll gate so the history-prepend layout effect and
    // the new-message auto-scroll effect won't fire until this effect's
    // init has run. Without this guard, the 3 effects raced each other and
    // produced up to 3 scrollIntoView calls within ~300ms of a switch.
    initialScrollDoneRef.current = null;

    // 关键：如果有缓存的位置，立即设置标记以防止 useLayoutEffect 干扰
    const hasCachedPosition = !!globalScrollPositionCache[group.id];
    if (hasCachedPosition) {
      isRestoringPositionRef.current = true;
    }

    // 重置恢复标记和历史记录引用
    hasRestoredPositionRef.current = false;
    previousFirstMessageIdRef.current = null;
    previousScrollHeightRef.current = 0;
    // Reset bidirectional loading state
    setHasMoreFuture(true);
    setJumpTargetMessage(null);

    // Cancel any leftover restore-complete timer from a prior group.
    if (restoringPositionTimeoutRef.current) {
      clearTimeout(restoringPositionTimeoutRef.current);
      restoringPositionTimeoutRef.current = null;
    }

    const cached = globalScrollPositionCache[group.id];

    if (cached) {
      // --- 同步快路径：缓存命中 + 消息元素已在 DOM ---
      // 直接在 paint 之前把 scrollTop 设到缓存值，避免浏览器先把
      // 上一个群的 scrollTop 画出来造成一帧的"错位"。
      const element = document.getElementById(cached.messageId);
      if (element && scrollContainerRef.current) {
        scrollContainerRef.current.scrollTop = cached.scrollTop;
        hasRestoredPositionRef.current = true;
        if (scrollContainerRef.current) {
          previousScrollHeightRef.current = scrollContainerRef.current.scrollHeight;
          previousFirstMessageIdRef.current = messages.length > 0 ? messages[0].id : null;
        }
        initialScrollDoneRef.current = group.id;
        // Schedule the 500ms "restore complete" timer; the cleanup below
        // can cancel it if the user switches groups mid-flight.
        restoringPositionTimeoutRef.current = setTimeout(() => {
          isRestoringPositionRef.current = false;
          restoringPositionTimeoutRef.current = null;
        }, 500);
      }
      // 缓存命中但元素不在 DOM：交给下面的 async useEffect 处理
      // （loadMessagesUntilFound 加载历史后再恢复）。这里什么都不做，
      // 不设 initialScrollDoneRef / hasRestoredPositionRef，让
      // history-prepend layout effect 知道我们还在恢复中。
    } else {
      // --- 无缓存（首次访问 / 从未访问过）---
      // 同步滚到底部（best effort）。如果消息是异步加载的、第一次 paint
      // 时还没到，history-prepend layout effect 的 isInitialLoad 分支
      // 会在消息到达时再滚一次。
      if (messagesEndRef.current) {
        messagesEndRef.current.scrollIntoView({ behavior: 'auto' });
      }
      if (scrollContainerRef.current) {
        previousScrollHeightRef.current = scrollContainerRef.current.scrollHeight;
        previousFirstMessageIdRef.current = messages.length > 0 ? messages[0].id : null;
      }
      hasRestoredPositionRef.current = true;
      initialScrollDoneRef.current = group.id;
    }

    // 组件卸载或群组切换时保存位置
    // React 在依赖变化时按 cleanup → 新 setup 顺序执行：此时
    // currentGroupIdRef.current 仍指向"正要离开的群"（上面 setup 里设的
    // 值，新 setup 还没跑），直接 save 就会写到正确的 key。
    //
    // 之前这里把 ref 临时设成 previousGroupId 再 save，会把当前群的
    // scrollTop 写到上一个群的 cache，把 cache 覆盖掉 —— 结果就是切回
    // 来的群 restore 失败、fallback 到 scrollIntoView 滚到底部。
    return () => {
      if (restoringPositionTimeoutRef.current) {
        clearTimeout(restoringPositionTimeoutRef.current);
        restoringPositionTimeoutRef.current = null;
      }
      saveScrollPosition();
    };
  }, [group.id]); // 当切换群组时触发

  // 异步 fallback：缓存命中 + 缓存的消息不在当前 DOM 中
  // （即消息在更早的历史里，需要先把历史加载进来）。
  useEffect(() => {
    const cached = globalScrollPositionCache[group.id];
    if (!cached) return; // 无缓存 → 同步 effect 已经处理

    // 同步 effect 已经处理了元素已在 DOM 的快路径
    if (document.getElementById(cached.messageId)) return;

    // 缓存的消息不在当前 messages 列表里（被删了？），fallback 到滚到底部
    const targetMsg = messages.find(m => m.id === cached.messageId);
    if (!targetMsg) {
      isRestoringPositionRef.current = false;
      if (messagesEndRef.current) {
        messagesEndRef.current.scrollIntoView({ behavior: 'auto' });
      }
      if (scrollContainerRef.current) {
        previousScrollHeightRef.current = scrollContainerRef.current.scrollHeight;
        previousFirstMessageIdRef.current = messages.length > 0 ? messages[0].id : null;
      }
      hasRestoredPositionRef.current = true;
      initialScrollDoneRef.current = group.id;
      return;
    }

    isRestoringPositionRef.current = true;
    let cancelled = false;

    (async () => {
      // 加载更早的历史直到找到目标消息（skipScroll=true，只加载不滚动）
      await loadMessagesUntilFound(cached.messageId, targetMsg.timestamp, true);
      // 如果用户在加载过程中又切了群，这次的恢复结果就丢弃掉
      if (cancelled) return;

      const el = document.getElementById(cached.messageId);
      if (el && scrollContainerRef.current) {
        scrollContainerRef.current.scrollTop = cached.scrollTop;
      }
      hasRestoredPositionRef.current = true;
      if (scrollContainerRef.current) {
        previousScrollHeightRef.current = scrollContainerRef.current.scrollHeight;
        previousFirstMessageIdRef.current = messages.length > 0 ? messages[0].id : null;
      }
      initialScrollDoneRef.current = group.id;

      // Schedule the 500ms "restore complete" timer; the cleanup below
      // can cancel it if the user switches groups mid-flight.
      if (restoringPositionTimeoutRef.current) {
        clearTimeout(restoringPositionTimeoutRef.current);
      }
      restoringPositionTimeoutRef.current = setTimeout(() => {
        isRestoringPositionRef.current = false;
        restoringPositionTimeoutRef.current = null;
      }, 500);
    })();

    return () => {
      // 群组切换时取消 in-flight 的恢复，避免上一个群的 load 完成
      // 后去碰下一个群的 DOM / refs
      cancelled = true;
      if (restoringPositionTimeoutRef.current) {
        clearTimeout(restoringPositionTimeoutRef.current);
        restoringPositionTimeoutRef.current = null;
      }
      // 解除 isRestoringPosition 阻塞，让下一个群的 scroll-save handler
      // 可以正常工作
      isRestoringPositionRef.current = false;
    };
  }, [group.id]); // 当切换群组时触发

  // 监听滚动事件，实时保存位置（防抖）
  useEffect(() => {
    let scrollTimeout: NodeJS.Timeout;

    const handleScrollSave = () => {
      // 清除之前的定时器
      clearTimeout(scrollTimeout);
      // 延迟保存，避免频繁更新
      scrollTimeout = setTimeout(() => {
        if (!isRestoringPositionRef.current) {
          saveScrollPosition();
        }
      }, 500);
    };

    const container = scrollContainerRef.current;
    if (container) {
      container.addEventListener('scroll', handleScrollSave);
    }

    return () => {
      clearTimeout(scrollTimeout);
      if (container) {
        container.removeEventListener('scroll', handleScrollSave);
      }
    };
  }, [group.id]);

  // 监听外部跳转消息事件（来自搜索结果点击）
  useEffect(() => {
    const handleJumpToMessage = async (e: CustomEvent<{ messageId: string; clearFilter?: boolean; timestamp?: number }>) => {
      const { messageId, clearFilter = true, timestamp } = e.detail;

      // 如果需要，先清除搜索过滤器以显示所有消息
      if (clearFilter) {
        // 触发清除过滤器事件
        window.dispatchEvent(new CustomEvent('clearSearchFilter'));
      }

      // 延迟执行以确保消息已渲染
      await new Promise(resolve => setTimeout(resolve, clearFilter ? 150 : 100));

      // 先尝试直接滚动
      const element = document.getElementById(messageId);
      if (element) {
        element.scrollIntoView({ behavior: 'auto', block: 'center' });
        element.classList.remove('animate-flash-highlight');
        void element.offsetWidth;
        element.classList.add('animate-flash-highlight');
        setTimeout(() => element.classList.remove('animate-flash-highlight'), 2000);
        return;
      }

      // 如果不在当前视图，优先使用传入的时间戳，否则从当前消息列表中查找
      let targetTimestamp = timestamp;
      if (!targetTimestamp) {
        const targetMsg = messages.find(m => m.id === messageId);
        if (targetMsg) {
          targetTimestamp = targetMsg.timestamp;
        }
      }

      if (targetTimestamp) {
        // 启动自动加载模式
        setIsAutoLoadingForJump(true);
        setAutoLoadingTargetId(messageId);
        setAutoLoadingProgress(0);

        // 使用连续加载策略，自动向上滚动并加载，直到找到消息
        await loadMessagesUntilFoundWithProgress(messageId, targetTimestamp);
      }
    };

    window.addEventListener('jumpToMessage', handleJumpToMessage as EventListener);
    return () => {
      window.removeEventListener('jumpToMessage', handleJumpToMessage as EventListener);
    };
  }, [messages, filter]); // 依赖 messages 和 filter

  // 加载消息直到找到目标消息
  const loadMessagesUntilFound = async (targetId: string, targetTimestamp: number, skipScroll: boolean = false) => {
    let attempts = 0;
    const maxAttempts = 5;

    // 标记正在加载历史消息，防止 useLayoutEffect 自动滚动
    isRestoringPositionRef.current = true;

    while (attempts < maxAttempts) {
      // 如果不跳过滚动，尝试滚动到消息
      if (!skipScroll) {
        const success = await scrollToMessage(targetId, false);
        if (success) {
          isRestoringPositionRef.current = false;
          return; // 成功找到并滚动
        }
      } else {
        // 只检查消息是否存在
        const element = document.getElementById(targetId);
        if (element) {
          isRestoringPositionRef.current = false;
          return; // 消息已加载
        }
      }

      // 未找到，加载更多历史消息
      setIsLoadingHistory(true);
      try {
        // 获取当前最早的消息时间
        const earliestMsg = messages[0];
        if (earliestMsg && earliestMsg.timestamp > targetTimestamp) {
          // 目标消息比当前最早消息还早，需要加载更多
          await onPrependMessages(group.id, earliestMsg.timestamp);
          attempts++;
        } else {
          // 已经加载到目标时间范围，但未找到消息
          break;
        }
      } catch (error) {
        console.error('Failed to load messages:', error);
        break;
      } finally {
        setIsLoadingHistory(false);
      }
    }

    // 最后尝试一次（如果不跳过滚动）
    if (!skipScroll) {
      await scrollToMessage(targetId, false);
    }

    isRestoringPositionRef.current = false;
  };

  // 带进度追踪的连续加载，直到找到目标消息
  const loadMessagesUntilFoundWithProgress = async (targetId: string, targetTimestamp: number) => {
    let attempts = 0;
    const maxAttempts = 10;

    // 标记正在自动加载
    isRestoringPositionRef.current = true;

    while (attempts < maxAttempts) {
      // 更新进度
      setAutoLoadingProgress(attempts + 1);

      // 检查是否已找到消息
      const element = document.getElementById(targetId);
      if (element) {
        // 找到消息，滚动到它
        element.scrollIntoView({ behavior: 'auto', block: 'center' });
        element.classList.remove('animate-flash-highlight');
        void element.offsetWidth;
        element.classList.add('animate-flash-highlight');
        setTimeout(() => element.classList.remove('animate-flash-highlight'), 2000);

        // 清理状态
        setIsAutoLoadingForJump(false);
        setAutoLoadingTargetId(null);
        setAutoLoadingProgress(0);
        isRestoringPositionRef.current = false;
        return;
      }

      // 未找到，需要加载更多历史消息
      setIsLoadingHistory(true);

      try {
        // 使用 ref 获取最新的 messages 状态，避免闭包问题
        const currentMessages = messagesRef.current;
        const earliestMsg = currentMessages[0];

        console.log(`[AutoLoad] Attempt ${attempts + 1}: earliestMsg timestamp = ${earliestMsg?.timestamp}, target = ${targetTimestamp}`);

        if (earliestMsg && earliestMsg.timestamp > targetTimestamp) {
          // 目标消息比当前最早消息还早，需要加载更多
          console.log(`[AutoLoad] Loading more history before ${earliestMsg.timestamp}`);

          // 加载更多历史消息
          const loadedCount = await onPrependMessages(group.id, earliestMsg.timestamp);
          attempts++;

          console.log(`[AutoLoad] Loaded ${loadedCount} messages`);

          // 等待渲染完成
          await new Promise(resolve => setTimeout(resolve, 300));

          // 强制滚动到顶部，触发下一次加载（如果有更多）
          if (scrollContainerRef.current) {
            scrollContainerRef.current.scrollTop = 0;
            console.log('[AutoLoad] Scrolled to top for next load');
          }

          // 再等待一下让用户看到加载的内容
          await new Promise(resolve => setTimeout(resolve, 200));
        } else {
          // 已经加载到目标时间范围，但未找到消息
          console.log('[AutoLoad] 已加载到目标时间范围，但未找到消息:', targetId);
          break;
        }
      } catch (error) {
        console.error('[AutoLoad] 自动加载消息失败:', error);
        break;
      } finally {
        setIsLoadingHistory(false);
      }
    }

    // 清理状态
    setIsAutoLoadingForJump(false);
    setAutoLoadingTargetId(null);
    setAutoLoadingProgress(0);
    isRestoringPositionRef.current = false;

    // 最后尝试一次滚动（如果消息已加载）
    const finalElement = document.getElementById(targetId);
    if (finalElement) {
      finalElement.scrollIntoView({ behavior: 'auto', block: 'center' });
      finalElement.classList.remove('animate-flash-highlight');
      void finalElement.offsetWidth;
      finalElement.classList.add('animate-flash-highlight');
      setTimeout(() => finalElement.classList.remove('animate-flash-highlight'), 2000);
    } else {
      console.warn(`经过 ${maxAttempts} 次尝试仍未找到消息:`, targetId);
    }
  };

  // Trigger jump if prop is true
  useEffect(() => {
    let attempts = 0;
    let timer: any = null;

    const tryJump = () => {
        if (shouldJumpToMention && group.hasUnreadMention && messages.length > 0) {
             const result = handleJumpToMention();
             if (result) {
                 // Success
             } else if (attempts < 10) {
                 // Retry for up to 500ms (10 * 50ms)
                 attempts++;
                 timer = setTimeout(tryJump, 50);
             } else {
                 // Stop trying, consume anyway to clear state
                 onConsumeMention(group.id);
             }
        }
    };

    // Initial slight delay to allow render
    if (shouldJumpToMention) {
        timer = setTimeout(tryJump, 100);
    }

    return () => clearTimeout(timer);
  }, [shouldJumpToMention, group.id, messages.length]); // Depend on messages.length to retry if messages load late

  // Handle Scroll Restoration for History Loading
  useLayoutEffect(() => {
    // Wait for the init/restore effect to complete before doing anything.
    // Otherwise, on group switch this effect would race the init scroll and
    // produce a second scrollIntoView call within the same frame.
    if (initialScrollDoneRef.current !== group.id) {
        return;
    }

    // 关键修复：如果正在恢复位置，或者存在缓存位置但尚未恢复，则完全跳过自动滚动
    if (isRestoringPositionRef.current) {
        return;
    }

    // 如果存在该群组的缓存位置，且我们还没有恢复过位置，则跳过所有自动滚动
    // 这确保 restoreScrollPosition 有机会执行
    if (globalScrollPositionCache[group.id] && !hasRestoredPositionRef.current) {
        // 只更新历史记录引用，不进行任何滚动
        if (scrollContainerRef.current) {
            previousScrollHeightRef.current = scrollContainerRef.current.scrollHeight;
            previousFirstMessageIdRef.current = messages.length > 0 ? messages[0].id : null;
        }
        return;
    }

    // 如果已经恢复过位置，跳过所有自动滚动逻辑
    if (hasRestoredPositionRef.current) {
        return;
    }

    if (scrollContainerRef.current) {
        const currentScrollHeight = scrollContainerRef.current.scrollHeight;
        const firstMessageId = messages.length > 0 ? messages[0].id : null;

        // Detect if messages were prepended (history load)
        // 注意：previousFirstMessageIdRef.current 可能为 null（首次加载）
        if (
            previousFirstMessageIdRef.current !== null &&
            firstMessageId !== previousFirstMessageIdRef.current &&
            currentScrollHeight > previousScrollHeightRef.current
        ) {
             const heightDifference = currentScrollHeight - previousScrollHeightRef.current;
             scrollContainerRef.current.scrollTop = heightDifference - 50;
             setIsLoadingHistory(false);
        }
        // 只有在没有缓存位置且不在恢复位置的情况下才自动滚动到底部
        else if (!isLoadingHistory && !shouldJumpToMention) {
            // 检查是否在底部附近（500px 内）或者是首次加载（消息少于10条）
            const isNearBottom = scrollContainerRef.current.scrollHeight - scrollContainerRef.current.scrollTop - scrollContainerRef.current.clientHeight < 500;
            const isInitialLoad = messages.length < 10 && previousFirstMessageIdRef.current === null;

            if (isNearBottom || isInitialLoad) {
                // Auto scroll to bottom only if NOT trying to jump to mention and NO cached position
                messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
            }
        }

        // 更新历史记录引用（只要不是正在恢复位置）
        if (!isRestoringPositionRef.current) {
            previousScrollHeightRef.current = currentScrollHeight;
            previousFirstMessageIdRef.current = firstMessageId;
        }
    }
  }, [messages, isLoadingHistory, group.id]);

  // Auto-consume mention if the mentioned message is already visible in viewport
  useEffect(() => {
    if (!group.hasUnreadMention || messages.length === 0) return;

    const mentionMsg = [...messages].reverse().find(m => m.mentions?.includes(currentUser.id));
    if (!mentionMsg) {
      onConsumeMention(group.id);
      return;
    }

    const element = document.getElementById(mentionMsg.id);
    if (!element) return;

    const container = scrollContainerRef.current;
    if (!container) return;

    const containerRect = container.getBoundingClientRect();
    const elementRect = element.getBoundingClientRect();

    // Check if element is fully within the visible viewport
    const isFullyVisible =
      elementRect.top >= containerRect.top &&
      elementRect.bottom <= containerRect.bottom;

    if (isFullyVisible) {
      // Message already visible, auto-consume
      onConsumeMention(group.id);
    }
  }, [group.hasUnreadMention, messages, currentUser.id, group.id, onConsumeMention]);

  // 监听新消息到达，自动滚动确保最后一条消息可见
  useEffect(() => {
    // Wait for the init/restore effect to complete. On group switch, the
    // messages.length dependency would otherwise fire this effect once for
    // the initial message load and produce an extra smooth scroll on top of
    // the init scroll.
    if (initialScrollDoneRef.current !== group.id) {
      return;
    }

    // 如果正在恢复位置或加载历史，跳过
    if (isRestoringPositionRef.current || isLoadingHistory) {
      return;
    }

    if (scrollContainerRef.current && messages.length > 0) {
      const container = scrollContainerRef.current;
      const isNearBottom =
        container.scrollHeight - container.scrollTop - container.clientHeight < 150;

      // 如果用户已经在底部附近，或者这是首次加载，则平滑滚动
      if (isNearBottom || messages.length <= 10) {
        // 延迟一帧，确保消息已渲染
        requestAnimationFrame(() => {
          if (container && messagesEndRef.current) {
            // 滚动到让最后一条消息刚好可见的位置（减去输入框高度）
            const targetScroll = container.scrollHeight - container.clientHeight + 10; // 10px 让消息紧贴输入框
            container.scrollTo({
              top: Math.max(0, targetScroll),
              behavior: 'smooth'
            });
          }
        });
      }
    }
  }, [messages.length, isLoadingHistory]); // 仅依赖 messages.length


  // 使用 ref 来防止重复触发加载
  const isLoadingHistoryRef = useRef(false);
  const lastLoadTimeRef = useRef(0);
  const lastFutureLoadTimeRef = useRef(0);

  const scrollToTop = useCallback(() => {
    scrollContainerRef.current?.scrollTo({ top: 0, behavior: 'smooth' });
  }, []);

  const scrollToBottom = useCallback(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, []);

  const handleScroll = async () => {
    if (!scrollContainerRef.current || messages.length === 0) {
      return;
    }

    const container = scrollContainerRef.current;
    const scrollTop = container.scrollTop;
    const scrollHeight = container.scrollHeight;
    const clientHeight = container.clientHeight;

    // 更新顶/底按钮可见性 — 用 rAF 批量更新，避免每次滚动都 re-render
    const newTop = scrollTop > 200;
    const newBottom = scrollHeight - scrollTop - clientHeight > 200;
    if (scrollRafRef.current) cancelAnimationFrame(scrollRafRef.current);
    scrollRafRef.current = requestAnimationFrame(() => {
      if (newTop !== lastScrollTopRef.current) { setShowScrollTop(newTop); lastScrollTopRef.current = newTop; }
      if (newBottom !== lastScrollBottomRef.current) { setShowScrollBottom(newBottom); lastScrollBottomRef.current = newBottom; }
      if (!lastScrollActiveRef.current) { setScrollActive(true); lastScrollActiveRef.current = true; }
    });

    // 滚动时显示按钮，1.5s 无操作后隐藏
    if (scrollHideTimerRef.current) clearTimeout(scrollHideTimerRef.current);
    scrollHideTimerRef.current = setTimeout(() => { setScrollActive(false); lastScrollActiveRef.current = false; }, 1500);

    // ========== 向上滚动：加载历史消息 ==========
    // 当滚动到顶部附近（scrollTop < 50）时加载更多历史消息
    // 但如果正在下拉刷新，不要触发（避免冲突和闪烁）
    if (scrollTop < 50 && !isPulling && !isLoadingHistoryRef.current) {
      console.log('Scroll triggered load, scrollTop:', scrollTop);
      // 防抖：至少间隔 1 秒才能再次加载
      const now = Date.now();
      if (now - lastLoadTimeRef.current < 1000) {
        console.log('Load debounced, too soon');
        return;
      }
      lastLoadTimeRef.current = now;
      isLoadingHistoryRef.current = true;
      setIsLoadingHistory(true);

      // 记录当前滚动位置（相对于顶部）
      const oldScrollHeight = container.scrollHeight;

      try {
        console.log('Loading via scroll...');
        // 调用父组件传入的函数加载更多历史消息
        const loadedCount = await onPrependMessages(group.id, messages[0].timestamp);
        console.log('Scroll load completed, count:', loadedCount);

        // 加载完成后恢复滚动位置（保持在原位置，不要跳到底部）
        requestAnimationFrame(() => {
          if (container) {
            const newScrollHeight = container.scrollHeight;
            const heightDiff = newScrollHeight - oldScrollHeight;
            container.scrollTop = heightDiff; // 保持视图位置不变
          }
        });
      } catch (error) {
        console.error('Failed to load more messages:', error);
      } finally {
        isLoadingHistoryRef.current = false;
        setIsLoadingHistory(false);
      }
    }

    // ========== 向下滚动：加载未来消息 ==========
    // 当滚动到底部附近（距离底部 < 100px）时加载更多最近消息
    const distanceFromBottom = scrollHeight - scrollTop - clientHeight;
    if (distanceFromBottom < 100 && hasMoreFuture && !isLoadingFuture) {
      const now = Date.now();
      // 防抖：至少间隔 1 秒才能再次加载
      if (now - lastFutureLoadTimeRef.current < 1000) {
        return;
      }
      lastFutureLoadTimeRef.current = now;

      await loadMoreFutureMessages();
    }
  };

  // 播放发送消息的温和提示音
  const playSendSound = () => {
    try {
      const audioContext = new (window.AudioContext || (window as any).webkitAudioContext)();
      const oscillator = audioContext.createOscillator();
      const gainNode = audioContext.createGain();

      // 创建更柔和的"水滴"声
      oscillator.connect(gainNode);
      gainNode.connect(audioContext.destination);

      // 使用正弦波，频率更低更温和：从 600Hz 轻微下降到 400Hz
      oscillator.type = 'sine';
      oscillator.frequency.setValueAtTime(600, audioContext.currentTime);
      oscillator.frequency.exponentialRampToValueAtTime(400, audioContext.currentTime + 0.15);

      // 非常轻柔的音量 - 仅3%音量，极柔和的淡出
      gainNode.gain.setValueAtTime(0, audioContext.currentTime);
      gainNode.gain.linearRampToValueAtTime(0.03, audioContext.currentTime + 0.05);
      gainNode.gain.exponentialRampToValueAtTime(0.0001, audioContext.currentTime + 0.3);

      // 播放
      oscillator.start(audioContext.currentTime);
      oscillator.stop(audioContext.currentTime + 0.25);
    } catch (e) {
      // 如果音频播放失败（如浏览器不支持），静默失败
      console.log('Audio play failed:', e);
    }
  };

  const handleSend = () => {
    const hasText = inputText.trim().length > 0;
    const hasStaged = stagedItems.length > 0;
    if (!hasText && !hasStaged) return;

    // Don't send while any file is still uploading
    if (stagedItems.some(i => i.uploading)) return;

    const attachments = stagedItems
      .map(i => i.attachment)
      .filter((a): a is Attachment => !!a);

    // Determine message type
    let msgType: MessageType;
    if (hasText) {
      msgType = MessageType.TEXT;
    } else {
      const allImages = attachments.every(a => a.type === 'image');
      msgType = allImages ? MessageType.IMAGE : MessageType.FILE;
    }

    onSendMessage(inputText, msgType, attachments.length > 0 ? attachments : undefined, replyTo?.id);
    setInputText('');
    setStagedItems(prev => {
      prev.forEach(i => URL.revokeObjectURL(i.localUrl));
      return [];
    });
    setReplyTo(null);
    // 播放温和的提示音
    playSendSound();
  };

  // ---- Mobile horizontal swipe navigation (full screen) ----
  const {
    swipeOffset,
    swipeTransition,
    handleNavSwipeStart,
    handleNavSwipeMove,
    handleNavSwipeEnd,
    resetNavSwipe,
    isNavSwipeHorizontal,
    springBackNavSwipe,
  } = useMobileChatSwipe({
    groupId: group.id,
    onSwipeRight: onBack,
    onSwipeLeft: () => (onOpenGroupSettings ?? toggleRightPanel)(),
    onHorizontalLock: () => {
      setIsPulling(false);
      setPullDistance(0);
      setPullTriggered(false);
    },
  });

  // Pull to load more handlers
  const handleTouchStart = (e: React.TouchEvent) => {
    handleNavSwipeStart(e);
    if (!scrollContainerRef.current || isLoadingHistoryRef.current) return;
    // Horizontal nav swipe owns the gesture — don't start pull
    if (isNavSwipeHorizontal()) return;

    // Only allow pull when at top of scroll
    if (scrollContainerRef.current.scrollTop <= 0) {
      pullStartYRef.current = e.touches[0].clientY;
      setIsPulling(true);
      setPullDistance(0);
    }
  };

  const handleTouchMove = (e: React.TouchEvent) => {
    handleNavSwipeMove(e);
    if (isNavSwipeHorizontal()) return;
    if (!isPulling || !scrollContainerRef.current) return;

    const currentY = e.touches[0].clientY;
    const deltaY = currentY - pullStartYRef.current;

    // Only allow pulling down (positive delta)
    if (deltaY > 0) {
      // Elastic resistance: the further pulled, the more resistance
      // Using square root for natural elastic feel
      const resistance = 0.6;
      const elasticDistance = Math.min(
        Math.sqrt(deltaY) * 6,
        maxPullDistance
      );

      setPullDistance(elasticDistance);

      // Check if threshold reached
      if (elasticDistance >= triggerThreshold && !pullTriggered) {
        setPullTriggered(true);
      } else if (elasticDistance < triggerThreshold && pullTriggered) {
        setPullTriggered(false);
      }
    }
  };

  const handleTouchEnd = async () => {
    const wasHorizontal = isNavSwipeHorizontal();
    handleNavSwipeEnd();
    if (wasHorizontal) return;

    if (!isPulling) {
      console.log('TouchEnd: not pulling, returning');
      return;
    }

    console.log('TouchEnd: pullTriggered=', pullTriggered, 'messages.length=', messages.length);
    setIsPulling(false);

    if (pullTriggered && messages.length > 0) {
      console.log('Triggering load...');
      // Trigger loading
      isLoadingHistoryRef.current = true;
      setIsLoadingHistory(true);

      const container = scrollContainerRef.current;
      const oldScrollHeight = container?.scrollHeight || 0;

      try {
        const firstMessage = messages[0];
        console.log('Loading messages before timestamp:', firstMessage.timestamp);

        // Load messages and get count
        const loadedMessages = await onPrependMessages(group.id, firstMessage.timestamp);
        console.log('Loaded messages count:', loadedMessages);

        // Restore scroll position
        requestAnimationFrame(() => {
          if (container) {
            const newScrollHeight = container.scrollHeight;
            const heightDiff = newScrollHeight - oldScrollHeight;
            container.scrollTop = heightDiff;
          }
        });

        // Show loaded toast
        if (loadedMessages > 0) {
          setLoadedCount(loadedMessages);
          setShowLoadedToast(true);
          setTimeout(() => setShowLoadedToast(false), 3000);
        } else {
          console.log('No new messages loaded');
        }
      } catch (error) {
        console.error('Failed to load history:', error);
      } finally {
        isLoadingHistoryRef.current = false;
        setIsLoadingHistory(false);
      }
    } else {
      console.log('Not triggering load: pullTriggered=', pullTriggered, 'hasMessages=', messages.length > 0);
    }

    // Spring back animation
    setPullDistance(0);
    setPullTriggered(false);
  };

  // ---- Staged file upload (supports multi-select, paste, drag) ----

  const handleStageFiles = useCallback((files: File[]) => {
    if (files.length === 0) return;
    const MAX_FILE_SIZE = 100 * 1024 * 1024;
    const MAX_IMAGE_SIZE = 10 * 1024 * 1024;

    files.forEach(async (file) => {
      if (file.size > MAX_FILE_SIZE) {
        alert(t('chat.fileTooLarge', { mb: 100 }));
        return;
      }

      const id = `staged_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
      const localUrl = URL.createObjectURL(file);

      setStagedItems(prev => [...prev, { id, file, localUrl, uploading: true }]);

      try {
        let fileToUpload = file;
        if (file.type.startsWith('image/') && file.size > MAX_IMAGE_SIZE) {
          try { fileToUpload = await compressImage(file); } catch {}
        }

        const result = await uploadAPI.uploadFile(fileToUpload, undefined, (ratio) => {
          setStagedItems(prev => prev.map(item =>
            item.id === id ? { ...item, uploadProgress: Math.round(ratio * 100) } : item
          ));
        });
        const attachment: Attachment = {
          id: `att_${Date.now()}`,
          name: result.name,
          size: result.size,
          type: file.type.startsWith('image/') ? 'image' : file.type.startsWith('video/') ? 'video' : ((result.type as any) || 'file'),
          url: result.url,
        };

        setStagedItems(prev => prev.map(item =>
          item.id === id ? { ...item, uploading: false, uploadProgress: 100, attachment } : item
        ));
      } catch {
        setStagedItems(prev => prev.map(item =>
          item.id === id ? { ...item, uploading: false, error: true } : item
        ));
      }
    });
  }, [t]);

  const handleFileSelect = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (!files || files.length === 0) return;
    handleStageFiles(Array.from(files) as File[]);
    e.target.value = '';
  };

  const handleFolderSelect = async (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files.length > 0) {
        // 验证文件夹总大小
        const totalSize = Array.from(e.target.files).reduce((sum, file) => sum + file.size, 0);
        const MAX_FOLDER_SIZE = 200 * 1024 * 1024; // 200MB

        if (totalSize > MAX_FOLDER_SIZE) {
            alert(t('chat.folderTooLarge', { mb: MAX_FOLDER_SIZE / 1024 / 1024 }));
            return;
        }

        // Stage the folder upload so the user sees a progress bar in the
        // staged-items preview, exactly like single-file uploads. Without
        // this, a 92MB folder upload showed zero feedback until the
        // backend finished zipping — which could take a minute+.
        const folderName = e.target.files[0].webkitRelativePath?.split('/')[0]
          || e.target.files[0].name || 'folder';
        const stagedId = `staged_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
        // No localUrl preview for a folder (it's many files); use empty string.
        // isFolder=true forces the file-style progress bar layout (not image preview)
        // even if the first file happens to be an image.
        setStagedItems(prev => [...prev, {
          id: stagedId,
          file: e.target.files[0], // representative file (unused for display when isFolder)
          localUrl: '',
          uploading: true,
          uploadProgress: 0,
          isFolder: true,
          displayName: `📦 ${folderName}`,
          displaySize: totalSize,
        }]);

        try {
            // 使用新的 uploadFolder 方法，自动打包成 ZIP；onProgress updates the staged item.
            const result = await uploadAPI.uploadFolder(e.target.files, (ratio) => {
              setStagedItems(prev => prev.map(item =>
                item.id === stagedId ? { ...item, uploadProgress: Math.round(ratio * 100) } : item
              ));
            });

            // 创建 ZIP 压缩包附件
            const zipAttachment: Attachment = {
                id: `zip_${Date.now()}`,
                name: result.name,
                size: result.size,
                type: 'folder',
                url: result.url
            };

            // Mark staged item as done (briefly show 100%) then remove it,
            // because folder uploads auto-send immediately (unlike single
            // files which the user can review before sending).
            setStagedItems(prev => prev.map(item =>
              item.id === stagedId ? { ...item, uploading: false, uploadProgress: 100, attachment: zipAttachment } : item
            ));
            setTimeout(() => {
              setStagedItems(prev => prev.filter(item => item.id !== stagedId));
            }, 600);

            // 发送文件夹消息，显示 ZIP 信息和内部文件列表
            const fileList = result.files.slice(0, 10).map(f => `• ${f.name} (${f.size})`).join('\n');
            const moreFiles = result.files.length > 10 ? `\n${t('chat.moreFiles', { count: result.files.length - 10 })}` : '';

            onSendMessage(
                `📦 ${result.original_name}\n${t('chat.filesCount', { count: result.file_count })}\n\n${fileList}${moreFiles}`,
                MessageType.TEXT,
                [zipAttachment]
            );
            // 保持输入框焦点
            const inputElement = document.querySelector('textarea');
            if (inputElement) {
              setTimeout(() => inputElement.focus(), 100);
            }
        } catch (error: any) {
            console.error('Failed to upload folder:', error);
            setStagedItems(prev => prev.map(item =>
              item.id === stagedId ? { ...item, uploading: false, error: true } : item
            ));
            setTimeout(() => {
              setStagedItems(prev => prev.filter(item => item.id !== stagedId));
            }, 2000);
            alert(`${t('chat.uploadFolderFailed')}: ${error.message || 'Unknown error'}`);
        }
    }
  };

  // 处理图片选择 - 专门用于快速发送图片
  const handleImageSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files.length > 0) {
        const imageFiles = Array.from(e.target.files).filter(f => f.type.startsWith('image/'));
        if (imageFiles.length > 0) {
            handleStageFiles(imageFiles);
        }
        e.target.value = '';
    }
  };

  // 图片压缩函数
  const compressImage = (file: File, maxWidth = 1920, maxHeight = 1920, quality = 0.8): Promise<File> => {
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
                    reject(new Error(t('chat.canvasContextError')));
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
                            reject(new Error(t('chat.imageCompressFailed')));
                        }
                    },
                    'image/jpeg',
                    quality
                );
            };
            img.onerror = () => reject(new Error(t('chat.imageLoadFailed')));
        };
        reader.onerror = () => reject(new Error(t('chat.fileReadFailed')));
    });
};

  // 格式化文件大小
  const formatFileSize = (bytes: number): string => {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
  };

  // 语音录制 → ASR 填入发送框（与 Agent 聊听写一致，不再发语音附件）
  const handleVoiceRecord = async (audioBlob: Blob, _duration: number) => {
    try {
      setSttDictating(true);
      const audioFile = new File([audioBlob], `voice_${Date.now()}.webm`, {
        type: audioBlob.type || 'audio/webm',
      });
      const res = await agentSessionAPI.groupTranscribe(audioFile, {
        filename: audioFile.name,
        language: 'zh',
      });
      const text = (res.text || '').trim();
      if (!text) {
        alert(t('chat.sendVoiceFailed') || '语音转写结果为空');
        return;
      }
      setInputText((prev) => {
        if (!prev.trimEnd()) return text;
        const joiner = /[\s\n]$/.test(prev) ? '' : ' ';
        return `${prev}${joiner}${text}`;
      });
    } catch (error) {
      console.error('Failed to transcribe voice:', error);
      const msg = error instanceof Error ? error.message : String(error || '');
      alert(msg || t('chat.sendVoiceFailed'));
    } finally {
      setSttDictating(false);
    }
  };

  const startEditing = (msg: Message) => {
      setEditingId(msg.id);
      setEditContent(msg.content);
  };

  const saveEdit = () => {
      if (editingId && editContent.trim()) {
          onEditMessage(editingId, editContent);
          setEditingId(null);
          setEditContent('');
      }
  };

  const cancelEdit = () => {
      setEditingId(null);
      setEditContent('');
  };

  // 滚动到指定消息，使用中心加载策略
  const scrollToMessage = async (id: string, autoLoadHistory: boolean = false): Promise<boolean> => {
      const element = document.getElementById(id);
      if (element) {
          // 'block: center' forces the element to the middle of the viewport
          element.scrollIntoView({ behavior: 'auto', block: 'center' });

          // Flash animation
          element.classList.remove('animate-flash-highlight'); // Reset if active
          void element.offsetWidth; // Trigger reflow
          element.classList.add('animate-flash-highlight');

          setTimeout(() => element.classList.remove('animate-flash-highlight'), 2000);
          setJumpTargetMessage(null);
          return true;
      }

      // 如果消息不在当前列表中，使用中心加载策略
      if (autoLoadHistory) {
          const targetMsg = messages.find(m => m.id === id);
          if (targetMsg) {
              // 消息在列表中但可能还未渲染，等待一下再试
              await new Promise(resolve => setTimeout(resolve, 100));
              const retryElement = document.getElementById(id);
              if (retryElement) {
                  retryElement.scrollIntoView({ behavior: 'auto', block: 'center' });
                  retryElement.classList.remove('animate-flash-highlight');
                  void retryElement.offsetWidth;
                  retryElement.classList.add('animate-flash-highlight');
                  setTimeout(() => retryElement.classList.remove('animate-flash-highlight'), 2000);
                  setJumpTargetMessage(null);
                  return true;
              }
          }

          // 消息不在当前列表中，尝试在完整消息列表中查找时间戳
          // 如果找到了，使用中心加载
          const allMessages = messages; // 当前已加载的消息
          const targetWithTimestamp = allMessages.find(m => m.id === id);
          if (targetWithTimestamp) {
              await loadMessagesAround(id, targetWithTimestamp.timestamp);
              // 等待渲染后滚动
              await new Promise(resolve => setTimeout(resolve, 150));
              const loadedElement = document.getElementById(id);
              if (loadedElement) {
                  loadedElement.scrollIntoView({ behavior: 'auto', block: 'center' });
                  loadedElement.classList.remove('animate-flash-highlight');
                  void loadedElement.offsetWidth;
                  loadedElement.classList.add('animate-flash-highlight');
                  setTimeout(() => loadedElement.classList.remove('animate-flash-highlight'), 2000);
                  return true;
              }
          }
      }

      return false;
  };

  const handleJumpToMention = async (): Promise<boolean> => {
      // Find the latest message mentioning current user
      const mentionMsg = [...messages].reverse().find(m => m.mentions?.includes(currentUser.id));

      if (mentionMsg) {
          // First try to scroll if message is already loaded
          const element = document.getElementById(mentionMsg.id);
          if (element) {
              element.scrollIntoView({ behavior: 'auto', block: 'center' });
              element.classList.remove('animate-flash-highlight');
              void element.offsetWidth;
              element.classList.add('animate-flash-highlight');
              setTimeout(() => {
                  element.classList.remove('animate-flash-highlight');
                  onConsumeMention(group.id);
              }, 2000);
              return true;
          }

          // Message not in current view, use auto-loading approach
          // 启动自动加载模式并派发事件
          setIsAutoLoadingForJump(true);
          setAutoLoadingTargetId(mentionMsg.id);
          setAutoLoadingProgress(0);

          window.dispatchEvent(new CustomEvent('jumpToMessage', {
              detail: {
                  messageId: mentionMsg.id,
                  clearFilter: true,
                  timestamp: mentionMsg.timestamp
              }
          }));

          // 消费@提及标记
          setTimeout(() => {
              onConsumeMention(group.id);
          }, 500);

          return true;
      } else {
          // If no mention found in data, just clear the flag
          onConsumeMention(group.id);
          return true;
      }
      return false;
  };

  const handleMentionButtonClick = () => {
      setInputText(prev => prev + '@');
  };

  const groupMembers = group.members.map(id => users[id]).filter(Boolean);

  // 解析消息内容缓存 — 避免每次渲染都重新调用 marked.parse()
  const parsedContentCacheRef = useRef<Map<string, string>>(new Map());
  // 当 messages 引用变化时清理已删除消息的缓存（防止无限增长）
  useEffect(() => {
    const cache = parsedContentCacheRef.current;
    if (cache.size > messages.length * 2) {
      const msgIds = new Set(messages.map(m => m.id));
      for (const key of cache.keys()) {
        const id = key.split(':')[0];
        if (!msgIds.has(id)) cache.delete(key);
      }
    }
  }, [messages]);

  // 解析消息内容，将 @提及 转换为可点击的链接
  const parseMessageContent = (content: string, currentMsgId: string) => {
    // 缓存命中检查
    const cacheKey = currentMsgId + ':' + content;
    const cached = parsedContentCacheRef.current.get(cacheKey);
    if (cached) return cached;
    // 预处理：把裸 URL 转为显式 Markdown 链接
    // 防止 marked 的 autolink 把中文标点（，。！？等）误识别为 URL 的一部分
    const withExplicitLinks = content.replace(
      /(https?:\/\/[^\s\u4e00-\u9fff\uff00-\uffef<>\[\]"']+)/g,
      (url) => {
        const clean = url.replace(/[.,;:!?\)\]}"']+$/, ''); // 剔除末尾半角标点
        return `[${clean}](${clean})`;
      }
    );

    // 先处理 markdown
    const withMentions = withExplicitLinks.replace(/@(\w+)/g, '**@$1**');
    let parsed = parse(withMentions) as string;

    // 将 @提及 转换为可点击的 span，带有 data-username 属性
    parsed = parsed.replace(/@(\w+)/g, '<span class="mention-link text-primary font-bold cursor-pointer hover:underline" data-username="$1">@$1</span>');

    // 修复链接：在新标签页打开，防止跳转当前页
    parsed = parsed.replace(/<a\s+href=/g, '<a target="_blank" rel="noopener noreferrer" href=');

    // 存入缓存
    parsedContentCacheRef.current.set(cacheKey, parsed);
    return parsed;
  };

  // 处理消息内容点击事件（用于 @提及 跳转）
  const handleMessageContentClick = (e: React.MouseEvent, currentMsgId: string) => {
    const target = e.target as HTMLElement;

    // 检查是否点击了 @提及
    if (target.classList.contains('mention-link')) {
      const username = target.getAttribute('data-username');
      if (username) {
        // 查找该用户名对应的用户ID
        const targetUser = Object.values(users).find(u => u.name === username);
        if (!targetUser) return;

        // 在当前消息之前查找该用户最近发送的一条消息
        const currentIndex = filteredMessages.findIndex(m => m.id === currentMsgId);
        const targetMsg = filteredMessages.slice(0, currentIndex).reverse().find(m =>
          m.senderId === targetUser.id
        );

        if (targetMsg) {
          scrollToMessage(targetMsg.id);
        } else {
          // 如果没找到，尝试在整个消息列表中查找该用户的第一条消息
          const anyMsg = filteredMessages.find(m => m.senderId === targetUser.id);
          if (anyMsg) {
            scrollToMessage(anyMsg.id);
          }
        }
      }
    }
  };

  return (
    <div
        data-testid="chat-window"
        className="flex-1 flex flex-col h-full relative bg-bgLight w-full min-w-0 md:touch-auto"
        style={{
          touchAction: 'pan-y',
          transform: swipeOffset ? `translateX(${swipeOffset}px)` : undefined,
          transition: swipeTransition ? 'transform 180ms ease-out' : undefined,
          willChange: swipeOffset ? 'transform' : undefined,
          boxShadow: swipeOffset > 8 ? '-8px 0 24px rgba(0,0,0,0.18)' : undefined,
        }}
        onTouchStart={handleTouchStart}
        onTouchMove={handleTouchMove}
        onTouchEnd={handleTouchEnd}
        onTouchCancel={() => {
          if (isNavSwipeHorizontal()) {
            springBackNavSwipe();
          } else {
            setIsPulling(false);
            setPullDistance(0);
            setPullTriggered(false);
            resetNavSwipe();
          }
        }}
        onDragOver={(e) => { e.preventDefault(); setIsDragging(true); }}
        onDragLeave={() => setIsDragging(false)}
        onDrop={async (e) => {
            e.preventDefault();
            setIsDragging(false);

            const dt = e.dataTransfer;
            if (!dt) return;

            // 1. 尝试从 files 获取（简单文件）
            const files = Array.from(dt.files);

            // 2. 尝试从 items 获取（支持文件夹）
            const items = dt.items;
            const entries: any[] = [];
            if (items) {
                for (let i = 0; i < items.length; i++) {
                    const entry = items[i].webkitGetAsEntry();
                    if (entry) entries.push(entry);
                }
            }

            try {
                let allItemsToUpload: {file: File, path: string}[] = [];
                let hasDirectory = false;

                for (const entry of entries) {
                    if (entry.isDirectory) {
                        hasDirectory = true;
                        const folderFiles = await traverseFileTree(entry);
                        allItemsToUpload = [...allItemsToUpload, ...folderFiles];
                    } else if (entry.isFile) {
                        const file = await new Promise<File>((resolve, reject) => {
                            entry.file(resolve, reject);
                        });
                        // 过滤掉无法读取的系统文件或空目录伪装的文件
                        if (file.size > 0 || file.type) {
                            allItemsToUpload.push({ file, path: file.name });
                        }
                    }
                }

                if (allItemsToUpload.length === 0) return;

                // 核心逻辑：如果是文件夹，或者文件数量大于1，统一使用打包接口
                if (hasDirectory || allItemsToUpload.length > 1) {
                    const result = await uploadAPI.uploadFolder(allItemsToUpload);
                    const attachment: Attachment = {
                        id: `zip_${Date.now()}`,
                        name: result.name,
                        size: result.size,
                        type: 'folder',
                        url: result.url
                    };
                    const fileList = result.files.slice(0, 5).map(f => `• ${f.name}`).join('\n');
                    onSendMessage(`📦 ${result.original_name}\n\n${fileList}`, MessageType.TEXT, [attachment]);
                } else if (allItemsToUpload.length === 1) {
                    const item = allItemsToUpload[0];
                    const result = await uploadAPI.uploadFile(item.file);
                    const attachment: Attachment = {
                        id: `att_${Date.now()}`,
                        name: result.name,
                        size: result.size,
                        type: item.file.type.startsWith('image/') ? 'image' : 'file',
                        url: result.url
                    };
                    onSendMessage("", item.file.type.startsWith('image/') ? MessageType.IMAGE : MessageType.FILE, [attachment]);
                }
            } catch (error: any) {
                console.error('Upload Error:', error);
                alert(t('chat.uploadFailedClickButton'));
            }
        }}
    >
      <style>{`
        @keyframes flash-highlight {
          0%, 100% { background-color: transparent; }
          50% { background-color: rgba(253, 224, 71, 0.5); }
        }
        .animate-flash-highlight {
          animation: flash-highlight 1.5s ease-in-out;
        }
      `}</style>

      {/* Drag Overlay */}
      {isDragging && (
        <div className="absolute inset-0 bg-primary/20 backdrop-blur-sm z-50 flex flex-col items-center justify-center border-4 border-dashed border-primary m-4 rounded-xl">
            <Download size={64} className="text-primary mb-4 animate-bounce" />
            <h3 className="text-2xl font-bold text-primary">{t('chat.dropToUpload')}</h3>
        </div>
      )}



        {/* Header - Mobile Optimized (Floating Style) */}
        <div className="absolute top-0 left-0 right-0 z-30 pointer-events-none">

        {/* Left: Back Button (Mobile Only) */}
        <button
            onClick={onBack}
            data-testid="chat-back"
            className="md:hidden absolute top-2 left-2 w-10 h-10 flex items-center justify-center text-textMain bg-panel/90 backdrop-blur-sm rounded-full shadow-sm active:scale-95 transition-transform pointer-events-auto relative"
        >
            <ArrowLeft size={20} />
            {/* Red notification badge for mentions in other groups */}
            {hasMentionsInOtherGroups && (
                <span className="absolute -top-1 -right-1 w-5 h-5 bg-red-500 text-white text-xs rounded-full flex items-center justify-center font-bold animate-pulse">
                    {otherGroupsMentionCount > 9 ? '9+' : otherGroupsMentionCount}
                </span>
            )}
        </button>


        {/* Right: Actions */}
        <div className="absolute top-2 right-2 flex items-center gap-1 pointer-events-auto">
            {/* Pinned Messages */}
            {pinnedMessages.length > 0 && (
                <div className="relative">
                    <button
                        onClick={() => setShowPinnedMessages(!showPinnedMessages)}
                        className="w-9 h-9 md:w-10 md:h-10 flex items-center justify-center rounded-full bg-panel/90 backdrop-blur-sm shadow-sm hover:bg-yellow-50 text-yellow-600 transition-colors"
                    >
                        <Pin size={18} />
                        <span className="absolute -top-0.5 -right-0.5 w-4 h-4 bg-yellow-500 text-white text-[10px] rounded-full flex items-center justify-center">
                            {pinnedMessages.length}
                        </span>
                    </button>

                    {/* Pinned Messages Dropdown */}
                    {showPinnedMessages && (
                        <div className="absolute top-full right-0 mt-2 w-72 md:w-80 bg-panel rounded-xl shadow-xl border border-border overflow-hidden z-50">
                            <div className="bg-primary/10 px-4 py-2 border-b border-border flex justify-between items-center">
                                <span className="text-xs font-bold text-primary">{t('chat.pinnedMessages')}</span>
                                <button onClick={() => setShowPinnedMessages(false)} className="text-primary hover:bg-primary/10 rounded p-1">
                                    <X size={14} />
                                </button>
                            </div>
                            <div className="max-h-64 overflow-y-auto custom-scrollbar">
                                {pinnedMessages.map(pm => {
                                    const pSender = users[pm.senderId];
                                    return (
                                        <div
                                            key={pm.id}
                                            className="p-3 border-b border-border hover:bg-bgLight cursor-pointer transition-colors"
                                            onClick={() => {
                                                // 使用自动加载跳转机制（带时间戳）
                                                window.dispatchEvent(new CustomEvent('jumpToMessage', {
                                                    detail: {
                                                        messageId: pm.id,
                                                        clearFilter: true,
                                                        timestamp: pm.timestamp
                                                    }
                                                }));
                                                setShowPinnedMessages(false);
                                            }}
                                        >
                                            <div className="flex items-center gap-2 mb-1">
                                                <AvatarImg avatar={pSender?.avatar} seed={pSender?.id} label={pSender?.name} className="w-5 h-5 rounded-full" />
                                                <span className="text-xs font-semibold text-textMain">{pSender?.name}</span>
                                                <span className="text-[10px] text-textMuted ml-auto">{new Date(pm.timestamp).toLocaleDateString('zh-CN')}</span>
                                            </div>
                                            <p className="text-sm text-textMuted line-clamp-2">
                                                {pm.type === MessageType.TEXT || pm.type === MessageType.SYSTEM ? pm.content.replace(/<[^>]+>/g, '') : `[${pm.type}]`}
                                            </p>
                                        </div>
                                    );
                                })}
                            </div>
                        </div>
                    )}
                </div>
            )}

            {/* Group Info Button */}
            <button
                onClick={toggleRightPanel}
                className="w-9 h-9 md:w-10 md:h-10 flex items-center justify-center rounded-full bg-panel/90 backdrop-blur-sm shadow-sm hover:bg-bgLight text-textMain transition-colors"
            >
                <MoreHorizontal size={20} />
            </button>
        </div>
      </div>

      {/* Loading Indicator - Fixed at top */}
      {isLoadingHistory && (
        <div className="absolute top-16 md:top-20 left-0 right-0 flex justify-center py-2 z-20 pointer-events-none">
          <div className="bg-panel/80 backdrop-blur-sm rounded-full px-4 py-2 shadow-sm border border-border flex items-center gap-2">
            <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-primary"></div>
            <span className="text-xs text-textMuted">{t('chat.loadingHistory')}</span>
          </div>
        </div>
      )}

      {/* Future Loading Indicator - Fixed at bottom */}
      {isLoadingFuture && (
        <div className="absolute bottom-24 md:bottom-28 left-0 right-0 flex justify-center py-2 z-20 pointer-events-none">
          <div className="bg-panel/80 backdrop-blur-sm rounded-full px-4 py-2 shadow-sm border border-border flex items-center gap-2">
            <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-primary"></div>
            <span className="text-xs text-textMuted">{t('chat.loadingRecentMessages')}</span>
          </div>
        </div>
      )}

      {/* Auto-loading for jump indicator */}
      {isAutoLoadingForJump && (
        <div className="absolute top-16 md:top-20 left-0 right-0 flex justify-center py-2 z-30 pointer-events-none">
          <div className="bg-primary/90 text-white rounded-full px-4 py-2 shadow-lg flex items-center gap-2">
            <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-white"></div>
            <span className="text-xs font-medium">
              {t('chat.locatingMessage', { progress: autoLoadingProgress })}
            </span>
          </div>
        </div>
      )}

      {/* Pull to refresh indicator */}
      <div
        className="absolute top-14 md:top-16 left-0 right-0 z-20 pointer-events-none flex justify-center transition-transform duration-200 ease-out"
        style={{
          transform: `translateY(${isPulling || pullDistance > 0 ? pullDistance : 0}px)`,
          opacity: isPulling || pullDistance > 0 ? 1 : 0
        }}
      >
        <div className="bg-panel/90 backdrop-blur-sm rounded-full px-4 py-2 shadow-lg border border-border flex items-center gap-2">
          <div
            className={`transition-transform duration-300 ${pullTriggered ? 'rotate-180' : ''}`}
            style={{ transform: `rotate(${(pullDistance / maxPullDistance) * 360}deg)` }}
          >
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-primary">
              <path d="M12 5v14M19 12l-7 7-7-7"/>
            </svg>
          </div>
          <span className="text-xs text-textMain font-medium">
            {pullTriggered ? t('chat.releaseToLoad') : t('chat.pullToLoadMore')}
          </span>
        </div>
      </div>

      {/* Loaded Toast Notification */}
      {showLoadedToast && (
        <div className="absolute top-20 md:top-24 left-0 right-0 z-20 pointer-events-none flex justify-center animate-in fade-in slide-in-from-top-2 duration-300">
          <div className="bg-green-500/90 text-white rounded-full px-4 py-2 shadow-lg flex items-center gap-2">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M20 6L9 17l-5-5"/>
            </svg>
            <span className="text-xs font-medium">{t('chat.messagesLoaded', { count: loadedCount })}</span>
          </div>
        </div>
      )}

      {/* Messages Area */}
      {isMessagesLoading && messages.length <= 1 ? (
        // ── Skeleton placeholder for uncached groups ──
        // Shown only when the active group is loading for the first time
        // (cache miss). Cached groups (hover-prefetched or previously
        // visited) render the real message list immediately and never see
        // this skeleton, eliminating the "blank panel" flash.
        <div
          className="flex-1 overflow-y-auto px-2 md:px-4 pt-14 md:pt-16 pb-16 md:pb-24 custom-scrollbar w-full overscroll-contain"
          style={{ contain: 'layout style', willChange: 'scroll-position' }}
          ref={scrollContainerRef}
        >
          <div className="min-h-full flex flex-col">
            {/* Spacer to push skeleton to bottom when few rows */}
            <div className="flex-1"></div>
            <div className="space-y-3 animate-pulse pb-2">
              <div className="flex gap-2 mb-1">
                <div className="w-8 h-8 rounded-full bg-border shrink-0"></div>
                <div className="flex flex-col max-w-[70%] min-w-0 items-start gap-1">
                  <div className="h-3 w-16 bg-border rounded"></div>
                  <div className="h-10 w-48 bg-border rounded-2xl"></div>
                </div>
              </div>
              <div className="flex gap-2 mb-1 flex-row-reverse">
                <div className="w-8 h-8 rounded-full bg-border shrink-0"></div>
                <div className="flex flex-col max-w-[70%] min-w-0 items-end gap-1">
                  <div className="h-3 w-16 bg-border rounded"></div>
                  <div className="h-10 w-40 bg-border rounded-2xl"></div>
                </div>
              </div>
              <div className="flex gap-2 mb-1">
                <div className="w-8 h-8 rounded-full bg-border shrink-0"></div>
                <div className="flex flex-col max-w-[70%] min-w-0 items-start gap-1">
                  <div className="h-3 w-20 bg-border rounded"></div>
                  <div className="h-12 w-56 bg-border rounded-2xl"></div>
                </div>
              </div>
            </div>
          </div>
        </div>
      ) : (
      <div
        className="flex-1 overflow-y-auto px-2 md:px-4 pt-14 md:pt-16 pb-16 md:pb-24 custom-scrollbar w-full overscroll-contain"
        style={{ contain: 'layout style', willChange: 'scroll-position' }}
        ref={scrollContainerRef}
        onScroll={handleScroll}
      >
        <div className="min-h-full flex flex-col">
          {/* Spacer to push messages to bottom when few messages */}
          <div className="flex-1"></div>

        {filteredMessages.map((msg, index) => {
            const isSelf = msg.senderId === currentUser.id;
            const sender = users[msg.senderId];
            const prevMsg = messages[index - 1];
            const isSequence = prevMsg && prevMsg.senderId === msg.senderId && (msg.timestamp - prevMsg.timestamp < 300000);
            const isMentioned = msg.mentions?.includes(currentUser.id);
            const isEditing = editingId === msg.id;

            // Bottom N messages get eager image loading + high fetch priority
            // so images arrive in sync with the rest of the message text
            // instead of popping in after the auto-scroll lands. Older
            // messages keep native lazy-loading to avoid wasted bandwidth.
            const isRecentMessage = index >= filteredMessages.length - 8;

            const replyContextMsg = msg.replyToId ? messageMap.get(msg.replyToId) ?? null : null;
            const replyContextUser = replyContextMsg ? users[replyContextMsg.senderId] : null;

            // SYSTEM messages: propose-options resolve tips stay plain text;
            // other system notes keep the soft banner.
            if (msg.type === MessageType.SYSTEM) {
                const plainTip = /^(✅\s*已选择|⏭\s*已忽略|✏️\s*自定义)/.test((msg.content || '').trim());
                return (
                    <div key={msg.id} id={msg.id} className="flex justify-center my-2">
                        {plainTip ? (
                            <div className="max-w-[90%] md:max-w-[75%] px-1 py-0.5 text-xs text-textMuted whitespace-pre-wrap break-words text-center">
                                {msg.content}
                            </div>
                        ) : (
                            <div className="max-w-[90%] md:max-w-[75%] px-4 py-3 bg-primary/5 border border-primary/20 rounded-xl text-sm text-textMain whitespace-pre-wrap break-words text-center">
                                <div
                                    className="prose prose-sm max-w-full prose-p:my-0 inline-block text-left"
                                    dangerouslySetInnerHTML={{ __html: parseMessageContent(msg.content, msg.id) }}
                                    onClick={(e) => handleMessageContentClick(e, msg.id)}
                                />
                            </div>
                        )}
                    </div>
                );
            }

            const interactiveApproval =
                !msg.isDeleted && msg.type === MessageType.TEXT ? parseCollabApproval(msg.content || '') : null;
            const interactiveProposal =
                !msg.isDeleted && msg.type === MessageType.TEXT ? parseProposeOptions(msg.content || '') : null;
            const isInteractiveCard = !!(interactiveApproval || interactiveProposal);

            return (
                <div key={msg.id} id={msg.id} className={`group flex gap-2 md:gap-3 mb-1 ${isSequence ? 'mt-1' : 'mt-4'} ${isSelf ? 'flex-row-reverse' : ''}`}>
                    {/* Avatar */}
                    <div className="w-8 md:w-10 flex-shrink-0 flex flex-col items-center">
                        {!isSequence && (
                            <AvatarImg avatar={sender?.avatar} seed={sender?.id} label={sender?.name} className="w-8 h-8 md:w-9 h-9 rounded-full object-cover border border-gray-100" title={sender?.name} />
                        )}
                    </div>

                    <div className={`flex flex-col max-w-[85%] md:max-w-[70%] min-w-0 ${isSelf ? 'items-end' : 'items-start'}`}>
                        {/* Sender Name & Time */}
                        {!isSequence && (
                            <div className={`flex items-center gap-2 mb-1 ${isSelf ? 'mr-1' : 'ml-1'}`}>
                                {!isSelf && <span className="text-xs md:text-sm font-semibold text-gray-700">{sender?.name}</span>}
                                <span className="text-[10px] md:text-xs text-gray-400">{new Date(msg.timestamp).toLocaleTimeString('zh-CN', {hour: '2-digit', minute:'2-digit', hour12: false})}</span>
                            </div>
                        )}

                        {/* Message Bubble (Or Edit Input) */}
                        {isEditing ? (
                            <div className={`relative p-2 shadow-sm rounded-xl border border-primary bg-panel w-full min-w-[200px]`}>
                                <textarea
                                    value={editContent}
                                    onChange={(e) => setEditContent(e.target.value)}
                                    className="w-full text-sm p-2 focus:outline-none resize-none bg-bgLight rounded mb-2 text-textMain"
                                    rows={3}
                                    autoFocus
                                />
                                <div className="flex justify-end gap-2">
                                    <button onClick={cancelEdit} className="p-1.5 text-red-500 hover:bg-red-50 rounded-full transition-colors"><X size={16} /></button>
                                    <button onClick={saveEdit} className="p-1.5 text-green-500 hover:bg-green-50 rounded-full transition-colors"><Check size={16} /></button>
                                </div>
                            </div>
                        ) : msg.isDeleted ? (
                            /* Recalled: subtle gray hint — no bubble chrome */
                            <div className="px-1 py-0.5 text-xs text-gray-400 italic select-none">
                                {t('chat.messageRecalled')}
                            </div>
                        ) : (
                            <div className={
                                isInteractiveCard
                                    ? 'relative text-sm leading-relaxed text-textMain break-all overflow-visible max-w-full'
                                    : `relative px-3 md:px-4 py-2 md:py-2.5 shadow-sm text-sm leading-relaxed text-textMain break-all overflow-hidden max-w-full ${
                                        isSelf
                                            ? 'bg-chatBubbleSelf rounded-2xl rounded-tr-sm border border-border'
                                            : isMentioned
                                                ? 'bg-yellow-50 rounded-2xl rounded-tl-sm border border-yellow-300 ring-2 ring-yellow-100'
                                                : 'bg-chatBubbleOther rounded-2xl rounded-tl-sm border border-border'
                                    }`
                            }>
                                 {/* Reply Context UI */}
                                 {msg.replyToId && (() => {
                                     const targetMsg = messageMap.get(msg.replyToId!);
                                     return (
                                         <div
                                             className={`mb-2 p-2 rounded text-xs border-l-4 cursor-pointer hover:bg-black/5 transition-colors flex flex-col gap-0.5 max-w-full overflow-hidden
                                                 ${isSelf ? 'bg-bgLight border-primary' : 'bg-bgLight border-border'}
                                             `}
                                             onClick={async () => {
                                                 const element = document.getElementById(msg.replyToId!);
                                                 if (element) {
                                                     element.scrollIntoView({ behavior: 'auto', block: 'center' });
                                                     element.classList.add('animate-flash-highlight');
                                                     setTimeout(() => element.classList.remove('animate-flash-highlight'), 2000);
                                                     return;
                                                 }
                                                 if (targetMsg) {
                                                     await loadMessagesAround(msg.replyToId!, targetMsg.timestamp);
                                                 }
                                             }}
                                         >
                                             <span className="font-bold text-primary flex items-center gap-1 truncate max-w-full">
                                                 {users[targetMsg?.senderId || '']?.name || 'User'}
                                             </span>
                                             {/* Reply content: render based on message type */}
                                             {!targetMsg ? (
                                                 <span className="text-textMuted italic text-xs">{t('chat.loadingMessage')}</span>
                                             ) : targetMsg.isDeleted ? (
                                                 <span className="text-textMuted italic text-xs">{t('chat.messageRecalled')}</span>
                                             ) : targetMsg.type === MessageType.IMAGE && targetMsg.attachments?.some(a => a.type === 'image') ? (
                                                 <div className="flex items-center gap-1.5">
                                                     <img
                                                         src={(() => { const u = targetMsg.attachments!.find(a => a.type === 'image')!.url; return u.startsWith('http') ? u : `${SERVER_BASE_URL}${u}`; })()}
                                                         alt="img"
                                                         className="h-8 w-8 rounded object-cover flex-shrink-0 border border-border"
                                                         loading="lazy"
                                                     />
                                                     {targetMsg.attachments!.filter(a => a.type === 'image').length > 1 && (
                                                         <span className="text-textMuted text-xs italic">{t('chat.imageCount', { count: targetMsg.attachments!.filter(a => a.type === 'image').length })}</span>
                                                     )}
                                                 </div>
                                             ) : targetMsg.type === MessageType.VIDEO && targetMsg.attachments?.[0] ? (
                                                 <div className="flex items-center gap-1 text-textMuted text-xs">
                                                     <Film size={12} className="flex-shrink-0" />
                                                     <span className="truncate max-w-[120px]">{targetMsg.attachments[0].name}</span>
                                                 </div>
                                             ) : targetMsg.type === MessageType.VOICE && targetMsg.attachments?.[0] ? (
                                                 <div className="flex items-center gap-1 text-textMuted text-xs">
                                                     <Mic size={12} className="flex-shrink-0" />
                                                     <span className="italic">{t('chat.voiceMessageLabel')}</span>
                                                 </div>
                                             ) : targetMsg.type === MessageType.FILE && targetMsg.attachments?.[0] ? (
                                                 <div className="flex items-center gap-1 text-textMuted text-xs">
                                                     <FileIcon size={12} className="flex-shrink-0" />
                                                     <span className="truncate max-w-[120px]">{targetMsg.attachments[0].name}</span>
                                                 </div>
                                             ) : (
                                                 <span className="text-textMuted line-clamp-2 italic break-all overflow-hidden max-w-full text-xs">
                                                     {targetMsg.content?.replace(/<[^>]+>/g, '') || `[${targetMsg.type}]`}
                                                 </span>
                                             )}
                                         </div>
                                     );
                                 })()}

                                 {/* Content */}
                                {msg.type === MessageType.TEXT ? (
                                    <div className="flex flex-col">
                                        {(() => {
                                            const approval = !msg.isDeleted ? parseCollabApproval(msg.content || '') : null;
                                            if (approval) {
                                                return (
                                                    <CollabStepApprovalCard
                                                        payload={approval}
                                                        groupId={group.id}
                                                        messageId={msg.id}
                                                        onResolve={async (action) => {
                                                            await messageAPI.resolveCollabApproval(
                                                                group.id,
                                                                approval.id,
                                                                action,
                                                                { messageId: msg.id }
                                                            );
                                                        }}
                                                    />
                                                );
                                            }
                                            const proposal = !msg.isDeleted ? parseProposeOptions(msg.content || '') : null;
                                            if (proposal) {
                                                return (
                                                    <ProposeOptionsCard
                                                        payload={proposal}
                                                        groupId={group.id}
                                                        messageId={msg.id}
                                                        onResolve={async (action, value) => {
                                                            await messageAPI.resolveProposeOptions(
                                                                group.id,
                                                                proposal.id,
                                                                action,
                                                                value,
                                                                { messageId: msg.id }
                                                            );
                                                        }}
                                                    />
                                                );
                                            }
                                            return (
                                                <div
                                                    className="prose prose-sm max-w-full prose-p:my-0 prose-ul:my-1 break-all"
                                                    style={{ wordBreak: 'break-all', overflowWrap: 'break-word' }}
                                                    dangerouslySetInnerHTML={{ __html: parseMessageContent(msg.content, msg.id) }}
                                                    onClick={(e) => handleMessageContentClick(e, msg.id)}
                                                />
                                            );
                                        })()}
                                        {msg.isEdited && !isInteractiveCard && (
                                            <span className="text-[10px] text-gray-400 self-end mt-1 italic">{t('chat.edited')}</span>
                                        )}
                                    </div>
                                ) : null}

                                 {/* Attachments */}
                                 {!msg.isDeleted && msg.attachments?.map(att => {
                                     // 构建完整的下载 URL
                                     const fullUrl = att.url.startsWith('http') ? att.url : `${SERVER_BASE_URL}${att.url}`;

                                    // 处理文件下载
                                    // Use XHR with onprogress so we can show an in-app
                                    // progress bar. The previous <a download> approach
                                    // triggered the browser's native downloader, whose
                                    // progress is only visible in Chrome's download shelf
                                    // — easy to miss in Electron / kiosk deployments.
                                    // XHR responseType='blob' streams to disk via the
                                    // browser's blob backing store, and onprogress gives
                                    // us byte-level progress for the UI.
                                    const handleDownload = (e: React.MouseEvent) => {
                                        e.preventDefault();
                                        const attId = att.id;
                                        const fileName = att.name || 'download';
                                        const downloadUrl = `${SERVER_BASE_URL}/api/ai-web/download-file?path=${encodeURIComponent(att.url)}`;

                                        // Add a download state entry for in-app progress.
                                        setDownloads(prev => [...prev, { attId, progress: 0, fileName }]);

                                        const xhr = new XMLHttpRequest();
                                        xhr.open('GET', downloadUrl, true);
                                        xhr.responseType = 'blob';

                                        xhr.onprogress = (event: ProgressEvent) => {
                                            if (event.lengthComputable) {
                                                const pct = Math.round((event.loaded / event.total) * 100);
                                                setDownloads(prev => prev.map(d =>
                                                    d.attId === attId ? { ...d, progress: pct } : d
                                                ));
                                            }
                                        };

                                        xhr.onload = () => {
                                            if (xhr.status >= 200 && xhr.status < 300) {
                                                const blob = xhr.response as Blob;
                                                const url = window.URL.createObjectURL(blob);
                                                const link = document.createElement('a');
                                                link.href = url;
                                                link.download = fileName;
                                                document.body.appendChild(link);
                                                link.click();
                                                document.body.removeChild(link);
                                                window.URL.revokeObjectURL(url);
                                            } else {
                                                console.error('Download failed:', xhr.status);
                                                alert(t('chat.downloadFailed'));
                                            }
                                            // Remove the download state after a brief 100% flash.
                                            setTimeout(() => {
                                                setDownloads(prev => prev.filter(d => d.attId !== attId));
                                            }, 800);
                                        };

                                        xhr.onerror = () => {
                                            console.error('Download network error');
                                            alert(t('chat.downloadFailed'));
                                            setDownloads(prev => prev.filter(d => d.attId !== attId));
                                        };

                                        xhr.send();
                                    };

                                    return (
                                        <div key={att.id} className="mt-1">
                                            {att.type === 'image' ? (
                                                <div
                                                    className="relative group cursor-pointer"
                                                    onClick={() => {
                                                        // 收集该消息中的所有图片
                                                        const images = msg.attachments?.filter(a => a.type === 'image').map(a => ({
                                                            url: a.url.startsWith('http') ? a.url : `${SERVER_BASE_URL}${a.url}`,
                                                            name: a.name
                                                        })) || [];
                                                        const index = images.findIndex(img => img.url === fullUrl);
                                                        console.log('[ChatWindow] Opening lightbox, images:', images.length, 'index:', index);
                                                        if (images.length > 0 && index >= 0) {
                                                            setLightboxImages(images);
                                                            setLightboxIndex(index);
                                                            setShowLightbox(true);
                                                        }
                                                    }}
                                                >
                                                    <img
                                                        src={fullUrl}
                                                        alt={att.name}
                                                        // Recent (visible-on-load) images load eagerly with high
                                                        // priority so they don't pop in after the auto-scroll;
                                                        // older images keep native lazy-loading.
                                                        loading={isRecentMessage ? "eager" : "lazy"}
                                                        fetchPriority={isRecentMessage ? "high" : "auto"}
                                                        decoding={isRecentMessage ? "sync" : "async"}
                                                        className="max-w-full rounded-lg max-h-80 object-cover hover:opacity-95 transition-opacity"
                                                    />
                                                    <div className="absolute inset-0 bg-black/0 group-hover:bg-black/20 transition-colors rounded-lg flex items-center justify-center opacity-0 group-hover:opacity-100">
                                                        <ZoomIn size={24} className="text-white" />
                                                    </div>
                                                </div>
                                            ) : att.type === 'voice' ? (
                                                <VoicePlayer url={fullUrl} duration={att.duration || 0} />
                                            ) : att.type === 'video' ? (
                                                <div className="rounded-lg overflow-hidden max-w-xs">
                                                    <video
                                                        src={fullUrl}
                                                        controls
                                                        className="max-w-full max-h-72 rounded-lg"
                                                        preload="metadata"
                                                    />
                                                    <div className="flex items-center justify-between px-1 pt-1">
                                                        <span className="text-xs text-textMuted truncate max-w-[150px]">{att.name}</span>
                                                        <button
                                                            onClick={handleDownload}
                                                            className="p-1 hover:bg-border rounded text-textMuted"
                                                            title={t('chat.download')}
                                                        >
                                                            <Download size={14} />
                                                        </button>
                                                    </div>
                                                </div>
                                            ) : (
                                                <div className="flex items-center gap-3 p-3 bg-bgLight border border-border rounded-lg">
                                                    {att.type === 'folder' ? <Folder className="text-primary" /> : <FileIcon className="text-primary" />}
                                                    <div className="flex flex-col min-w-0">
                                                        <span className="font-medium truncate max-w-[150px] text-textMain">{att.name}</span>
                                                        <span className="text-xs text-textMuted">{att.size}</span>
                                                    </div>
                                                    <button
                                                        onClick={handleDownload}
                                                        className="p-2 hover:bg-border rounded-full ml-auto text-textMuted"
                                                        title={t('chat.download')}
                                                    >
                                                        <Download size={16} />
                                                    </button>
                                                </div>
                                            )}
                                        </div>
                                    );
                                })}

                                {/* Status/Pin Indicators */}
                                <div className="flex justify-end gap-1 mt-1">
                                    {msg.isPinned && <Pin size={10} className="text-yellow-500" />}

                                    {/* 消息发送状态指示 */}
                                    {isSelf && msg.status && (
                                        <span className="text-[9px] text-gray-400 flex items-center gap-1">
                                            {msg.status === 'sending' && (
                                                <>
                                                    <svg className="animate-spin h-3 w-3" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                                                        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                                                        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                                                    </svg>
                                                    {t('chat.sending')}
                                                </>
                                            )}
                                            {msg.status === 'sent' && (
                                                <span className="text-green-500">{t('chat.sent')}</span>
                                            )}
                                            {msg.status === 'delivered' && (
                                                <span className="text-blue-500">{t('chat.delivered')}</span>
                                            )}
                                            {msg.status === 'failed' && (
                                                <span className="text-red-500 cursor-pointer hover:underline" onClick={() => {
                                                    // 重新发送逻辑
                                                    if (msg.content) {
                                                        onSendMessage(msg.content, msg.type, msg.attachments, msg.replyToId);
                                                    }
                                                }}>
                                                    {t('chat.sendFailed')}
                                                </span>
                                            )}
                                        </span>
                                    )}
                                </div>

                            </div>
                        )}

                        {/* Action Menu */}
                        {!isEditing && (
                            <div className={`flex items-center gap-1 mt-1 opacity-0 group-hover:opacity-100 transition-opacity duration-200 ${isSelf ? 'flex-row-reverse' : ''}`}>
                                {!msg.isDeleted && (
                                    <>
                                        <button onClick={() => setReplyTo(msg)} className="p-1 hover:bg-border rounded text-textMuted" title={t('common.reply')}><Reply size={14}/></button>
                                        <button onClick={() => copyToClipboard(msg.content)} className="p-1 hover:bg-border rounded text-textMuted" title={t('common.copy')}><Copy size={14}/></button>
                                        <button onClick={() => onPinMessage(msg.id)} className={`p-1 hover:bg-border rounded text-textMuted ${msg.isPinned ? 'text-yellow-500' : ''}`} title={t('common.pin')}><Pin size={14}/></button>
                                    </>
                                )}

                                {isSelf && (
                                    <>
                                        {!msg.isDeleted && msg.type === MessageType.TEXT && (
                                            <button
                                                onClick={() => startEditing(msg)}
                                                className="p-1 hover:bg-border rounded text-textMuted"
                                                title={t('common.edit')}
                                            >
                                                <Edit2 size={14} />
                                            </button>
                                        )}

                                        {msg.isDeleted ? (
                                            <>
                                                {/* 已撤回：显示取消撤回和永久删除 */}
                                                <button
                                                    onClick={(e) => {
                                                        e.stopPropagation();
                                                        onUndoRecall(msg.id);
                                                    }}
                                                    className="p-1 hover:bg-green-100/50 hover:text-green-600 rounded text-textMuted"
                                                    title={t('chat.undoRecall')}
                                                >
                                                    <RotateCcw size={14} />
                                                </button>
                                                <button
                                                    onClick={(e) => {
                                                        e.stopPropagation();
                                                        if (confirm(t('chat.deleteConfirm'))) {
                                                            onPermanentDelete(msg.id);
                                                        }
                                                    }}
                                                    className="p-1 hover:bg-red-100/50 hover:text-red-600 rounded text-textMuted"
                                                    title={t('chat.permanentDelete')}
                                                >
                                                    <Trash2 size={14} />
                                                </button>
                                            </>
                                        ) : (
                                            // 未撤回：显示撤回按钮
                                            <button
                                                onClick={(e) => {
                                                    e.stopPropagation();
                                                    onDeleteMessage(msg.id);
                                                }}
                                                className="p-1 hover:bg-orange-100/50 hover:text-orange-500 rounded text-textMuted"
                                                title={t('chat.recall')}
                                            >
                                                <Trash2 size={14} />
                                            </button>
                                        )}
                                    </>
                                )}
                            </div>
                        )}
                    </div>
                </div>
            );
        })}
        <div ref={messagesEndRef} />

        {/* 滚动到顶/底浮动按钮，与 AIChatPage 保持一致 */}
        {(showScrollTop || showScrollBottom) && (
          <div
            className="sticky bottom-4 z-10 pointer-events-none flex justify-end pr-1 transition-opacity duration-300"
            style={{ opacity: scrollActive ? 1 : 0, pointerEvents: scrollActive ? undefined : 'none' }}
          >
            <div className="flex flex-col gap-2 pointer-events-auto">
              {showScrollTop && (
                <button
                  onClick={scrollToTop}
                  className="w-8 h-8 bg-white border border-gray-200 rounded-full shadow-md flex items-center justify-center hover:bg-gray-50 transition-colors"
                  title={t('common.scrollToTop')}
                >
                  <ChevronUp size={18} className="text-gray-500" />
                </button>
              )}
              {showScrollBottom && (
                <button
                  onClick={scrollToBottom}
                  className="w-8 h-8 bg-white border border-gray-200 rounded-full shadow-md flex items-center justify-center hover:bg-gray-50 transition-colors"
                  title={t('common.scrollToBottom')}
                >
                  <ChevronDown size={18} className="text-gray-500" />
                </button>
              )}
            </div>
          </div>
        )}
        </div>
      </div>
      )}

      {/* Input Area - Stick to bottom */}
      <div className="bg-bgLight z-10 w-full relative shrink-0">
        {/* Floating Mention Button */}
        {group.hasUnreadMention && (
          <button
            onClick={() => handleJumpToMention()}
            className="absolute -top-10 right-4 z-40 bg-red-500 hover:bg-red-600 text-white px-2.5 py-1.5 rounded-full shadow-lg flex items-center gap-1.5 text-xs font-bold animate-bounce transition-transform"
          >
              <AtSign size={14} />
          </button>
        )}

        {/* Reply Context */}
        {replyTo && (
            <div className="flex items-center justify-between mb-1 md:mb-2 px-2 py-1.5 md:p-3 bg-indigo-50 rounded-md md:rounded-lg border-l-2 md:border-l-4 border-primary animate-in fade-in slide-in-from-bottom-2 duration-200">
                <div className="text-xs md:text-sm flex flex-col min-w-0">
                    <span className="font-bold text-primary flex items-center gap-1">
                        <Reply size={10} className="md:w-3 md:h-3" /> {t('chat.replyingTo', { name: users[replyTo.senderId]?.name })}
                    </span>
                    <p className="text-gray-600 truncate max-w-[150px] md:max-w-md text-[10px] md:text-xs mt-0.5">
                        {replyTo.type === MessageType.TEXT
                             ? replyTo.content.replace(/<[^>]+>/g, '')
                             : `[${replyTo.type}]`}
                    </p>
                </div>
                <button onClick={() => setReplyTo(null)} className="p-1 hover:bg-indigo-100 rounded-full text-indigo-400 hover:text-indigo-600 transition-colors">
                    <X size={14} className="md:w-4 md:h-4"/>
                </button>
            </div>
        )}

        {/* Staged files preview */}
        {stagedItems.length > 0 && (
            <div className="flex flex-wrap gap-2 px-2 py-1.5 bg-gray-50 border-t border-gray-100">
                {stagedItems.map(item => {
                    const pct = item.uploadProgress ?? 0;
                    // Folders always use the file-style bar (not image preview),
                    // even if the representative file is an image.
                    const showAsImage = !item.isFolder && item.file.type.startsWith('image/');
                    const displayName = item.displayName || item.file.name;
                    const displaySize = item.displaySize ?? item.file.size;
                    return (
                    <div key={item.id} className="relative group flex-shrink-0">
                        {showAsImage ? (
                            <div className="relative w-14 h-14 md:w-16 md:h-16 rounded-lg overflow-hidden border border-gray-200 bg-white">
                                <img
                                    src={item.localUrl}
                                    loading="lazy"
                                    alt={item.file.name}
                                    className="w-full h-full object-cover"
                                />
                                {item.uploading && (
                                    <div className="absolute inset-0 bg-black/50 flex flex-col items-center justify-center gap-1">
                                        {/* Progress ring with percentage in the middle */}
                                        <div className="relative w-7 h-7">
                                            <svg className="w-7 h-7 -rotate-90" viewBox="0 0 28 28">
                                                <circle cx="14" cy="14" r="11" fill="none" stroke="rgba(255,255,255,0.25)" strokeWidth="3" />
                                                <circle cx="14" cy="14" r="11" fill="none" stroke="white" strokeWidth="3"
                                                    strokeDasharray={`${2 * Math.PI * 11}`}
                                                    strokeDashoffset={`${2 * Math.PI * 11 * (1 - pct / 100)}`}
                                                    strokeLinecap="round"
                                                    className="transition-[stroke-dashoffset] duration-150 ease-out"
                                                />
                                            </svg>
                                            <span className="absolute inset-0 flex items-center justify-center text-[8px] font-bold text-white">
                                                {pct}%
                                            </span>
                                        </div>
                                    </div>
                                )}
                                {item.error && (
                                    <div className="absolute inset-0 bg-red-500/40 flex items-center justify-center">
                                        <X size={14} className="text-white" />
                                    </div>
                                )}
                            </div>
                        ) : (
                            <div className="flex flex-col gap-1 px-2 py-1.5 rounded-lg border border-gray-200 bg-white min-w-[120px] max-w-[180px]">
                                <div className="flex items-center gap-1.5">
                                    <Paperclip size={12} className="text-gray-400 flex-shrink-0" />
                                    <div className="min-w-0 flex-1">
                                        <p className="text-xs text-gray-700 truncate leading-tight">{displayName}</p>
                                        <p className="text-[10px] text-gray-400 leading-tight">{formatFileSize(displaySize)}</p>
                                    </div>
                                </div>
                                {item.uploading ? (
                                    <div className="flex items-center gap-1.5">
                                        <div className="flex-1 h-1.5 bg-gray-200 rounded-full overflow-hidden">
                                            <div
                                                className="h-full bg-primary rounded-full transition-[width] duration-150 ease-out"
                                                style={{ width: `${pct}%` }}
                                            />
                                        </div>
                                        <span className="text-[9px] text-gray-500 tabular-nums w-7 text-right">{pct}%</span>
                                    </div>
                                ) : null}
                            </div>
                        )}
                        {/* Remove button */}
                        <button
                            onClick={() => setStagedItems(prev => {
                                const toRemove = prev.find(i => i.id === item.id);
                                if (toRemove) URL.revokeObjectURL(toRemove.localUrl);
                                return prev.filter(i => i.id !== item.id);
                            })}
                            className="absolute -top-1.5 -right-1.5 w-4 h-4 bg-gray-500 hover:bg-red-500 text-white rounded-full hidden group-hover:flex items-center justify-center transition-colors z-10"
                        >
                            <X size={8} />
                        </button>
                    </div>
                    );
                })}
            </div>
        )}

        {/* Message Input */}
        <MessageInput
            value={inputText}
            onChange={setInputText}
            onSend={handleSend}
            onAddAI={() => setInputText(prev => prev + '@')}
            onFileSelect={() => fileInputRef.current?.click()}
            onFolderSelect={() => folderInputRef.current?.click()}
            onImageSelect={() => imageInputRef.current?.click()}
            onVoiceRecord={handleVoiceRecord}
            voiceDictating={sttDictating}
            onPasteFiles={handleStageFiles}
            hasAttachments={stagedItems.length > 0}
            placeholder=""
            groupMembers={groupMembers}
        />

        {/* Hidden file inputs */}
        <input type="file" ref={fileInputRef} className="hidden" multiple onChange={handleFileSelect} />
        <input
            type="file"
            ref={folderInputRef}
            className="hidden"
            onChange={handleFolderSelect}
            webkitdirectory=""
            directory=""
        />
        {/* 图片专用输入 - 移动端默认打开图片库 */}
        <input
            type="file"
            ref={imageInputRef}
            className="hidden"
            accept="image/*"
            multiple
            onChange={handleImageSelect}
        />

        {/* Download progress indicator — fixed at bottom-right, shows all
            active downloads with live percentage. Without this, the user
            sees nothing in-app when downloading (the browser's native
            download shelf is easy to miss in Electron). */}
        {downloads.length > 0 && (
            <div className="fixed bottom-4 right-4 z-[400] flex flex-col gap-2 max-w-[280px]">
                {downloads.map(dl => (
                    <div key={dl.attId} className="bg-white rounded-lg shadow-lg border border-gray-200 px-3 py-2 flex flex-col gap-1.5 animate-in slide-in-from-bottom-2 duration-200">
                        <div className="flex items-center gap-2">
                            <Download size={12} className="text-primary flex-shrink-0" />
                            <p className="text-xs text-gray-700 truncate flex-1">{dl.fileName}</p>
                            <span className="text-[10px] text-gray-500 tabular-nums">{dl.progress}%</span>
                        </div>
                        <div className="h-1.5 bg-gray-200 rounded-full overflow-hidden">
                            <div
                                className="h-full bg-primary rounded-full transition-[width] duration-150 ease-out"
                                style={{ width: `${dl.progress}%` }}
                            />
                        </div>
                    </div>
                ))}
            </div>
        )}

        {/* Image Lightbox - rendered via portal to escape ChatWindow's parent
            stacking context (otherwise sibling Sidebar at z-30 paints on top). */}
        {showLightbox && lightboxIndex !== null && lightboxImages.length > 0 && createPortal(
            <div
                className="fixed inset-0 bg-black/95 z-[500] flex items-center justify-center animate-in fade-in duration-200 overflow-hidden touch-none"
                onClick={() => {
                    setShowLightbox(false);
                    setLightboxIndex(null);
                }}
                onWheel={handleLightboxWheel}
                onMouseMove={handleLightboxMouseMove}
                onMouseUp={handleLightboxMouseUp}
                onMouseLeave={handleLightboxMouseUp}
                onTouchMove={handleLightboxTouchMove}
                onTouchEnd={handleLightboxTouchEnd}
            >
                {/* Close Button - positioned at top-left to avoid browser UI overlap */}
                <button
                    onClick={() => {
                        setShowLightbox(false);
                        setLightboxIndex(null);
                    }}
                    className="absolute top-4 left-4 p-2 text-white/80 hover:text-white z-[550] bg-black/30 rounded-full hover:bg-black/50 transition-colors"
                >
                    <X size={28} />
                </button>

                {/* Navigation - Hide when zoomed in to avoid interference */}
                {lightboxImages.length > 1 && lightboxScale === 1 && (
                    <>
                        <button
                            onClick={(e) => {
                                e.stopPropagation();
                                setLightboxIndex((prev) =>
                                    prev === null ? 0 : (prev - 1 + lightboxImages.length) % lightboxImages.length
                                );
                            }}
                            className="absolute left-4 top-1/2 -translate-y-1/2 p-3 text-white/80 hover:text-white bg-black/50 rounded-full hover:bg-black/70 transition-colors"
                        >
                            <ChevronLeft size={28} />
                        </button>
                        <button
                            onClick={(e) => {
                                e.stopPropagation();
                                setLightboxIndex((prev) =>
                                    prev === null ? 0 : (prev + 1) % lightboxImages.length
                                );
                            }}
                            className="absolute right-4 top-1/2 -translate-y-1/2 p-3 text-white/80 hover:text-white bg-black/50 rounded-full hover:bg-black/70 transition-colors"
                        >
                            <ChevronRight size={28} />
                        </button>
                    </>
                )}

                {/* Image Counter */}
                {lightboxImages.length > 1 && lightboxScale === 1 && (
                    <div className="absolute top-4 left-1/2 -translate-x-1/2 px-4 py-2 bg-black/50 rounded-full text-white text-sm">
                        {lightboxIndex + 1} / {lightboxImages.length}
                    </div>
                )}

                {/* Download Button */}
                <button
                    onClick={(e) => {
                        e.stopPropagation();
                        const img = lightboxImages[lightboxIndex];
                        if (img) {
                            downloadFile(img.url, img.name);
                        }
                    }}
                    className="absolute bottom-4 right-4 p-3 text-white bg-black/50 rounded-full hover:bg-black/70 transition-colors flex items-center gap-2"
                >
                    <Download size={20} />
                    <span className="text-sm">{t('chat.download')}</span>
                </button>

                {/* Image Info */}
                {lightboxScale === 1 && (
                    <div className="absolute bottom-4 left-4 px-4 py-2 bg-black/50 rounded-lg text-white text-sm max-w-md">
                        <p className="truncate">{lightboxImages[lightboxIndex]?.name}</p>
                    </div>
                )}

                {/* Zoom Hint */}
                {lightboxScale === 1 && (
                    <div className="absolute bottom-20 left-1/2 -translate-x-1/2 px-3 py-1 bg-white/10 backdrop-blur-md rounded-full text-white/50 text-[10px] pointer-events-none">
                        {t('chat.zoomHint')}
                    </div>
                )}

                {/* Main Image */}
                {(() => {
                    const currentImage = lightboxImages[lightboxIndex];
                    return currentImage ? (
                        <img
                            src={currentImage.url}
                            alt={currentImage.name}
                            className={`max-w-[90vw] max-h-[85vh] object-contain cursor-default transition-transform duration-100 ${isDraggingLightbox ? 'transition-none' : ''}`}
                            style={{
                                transform: `translate(${lightboxOffset.x}px, ${lightboxOffset.y}px) scale(${lightboxScale})`,
                                cursor: lightboxScale > 1 ? 'grab' : 'default'
                            }}
                            loading="lazy"
                            onMouseDown={handleLightboxMouseDown}
                            onClick={(e) => e.stopPropagation()}
                        />
                    ) : null;
                })()}
            </div>,
            document.body
        )}

        {/* Copy Success Toast */}
        {copyToast.show && (
            <div className="fixed bottom-20 left-1/2 -translate-x-1/2 px-4 py-2 bg-primary text-white rounded-lg shadow-lg z-[600] animate-in fade-in slide-in-from-bottom-2 duration-200">
                <div className="flex items-center gap-2">
                    <Check size={16} />
                    <span className="text-sm">{copyToast.message}</span>
                </div>
            </div>
        )}

        {/* Manual Copy Modal - for mobile when auto-copy fails */}
        {manualCopyText && (
            <div
                className="fixed inset-0 bg-black/70 z-[600] flex items-center justify-center backdrop-blur-sm animate-in fade-in duration-200 p-4"
                onClick={() => setManualCopyText(null)}
            >
                <div
                    className="bg-panel rounded-xl shadow-2xl w-full max-w-sm p-6"
                    onClick={(e) => e.stopPropagation()}
                >
                    <h3 className="text-lg font-semibold text-textMain mb-4">{t('chat.longPressCopy')}</h3>
                    <p className="text-sm text-textMuted mb-4">{t('chat.longPressCopyHint')}</p>
                    <textarea
                        value={manualCopyText}
                        readOnly
                        className="w-full h-32 px-3 py-2 bg-bgLight border border-border rounded-lg text-textMain text-sm resize-none mb-4"
                        onClick={(e) => {
                            (e.target as HTMLTextAreaElement).select();
                        }}
                    />
                    <div className="flex gap-3">
                        <button
                            onClick={() => setManualCopyText(null)}
                            className="flex-1 py-2.5 bg-primary text-white rounded-lg font-medium hover:bg-primary/90 transition-colors"
                        >
                            {t('chat.done')}
                        </button>
                    </div>
                </div>
            </div>
        )}
      </div>
    </div>
  );
};
