import React, { useState, useEffect, useRef, useCallback } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import { Search, Plus, AtSign, Volume2, VolumeX, X, Camera, Save, LogOut, Palette, Mail, Bell, Send, User as UserIcon, Paperclip, Image as ImageIcon, FileText, XCircle, Download, ZoomIn, ChevronLeft, ChevronRight, BotMessageSquare, Menu } from 'lucide-react';
import { Group, User } from '../types';
import { uploadAPI, directMessageAPI } from '../services/api';
import { getAvatarUrl, getLocalAvatarFallback } from '../utils/image';
import { formatTime } from '../utils/time';
import { useWebSocket } from '../hooks/useWebSocket';
import { parse } from 'marked';

interface ChatListProps {
  groups: Group[];
  activeGroupId: string | null;
  onSelectGroup: (id: string, jumpToMention?: boolean) => void;
  onCreateGroup: (name: string) => void;
  onJoinGroup?: (groupId: string) => void;
  onToggleGroupSound: (groupId: string) => void;
  lastMessages: Record<string, { content: string, timestamp: number }>;
  currentUser: User | null;
  onUpdateUser: (user: User) => void;
  onLogout: () => void;
  theme?: string;
  onToggleTheme?: () => void;
  onSwitchView?: (view: 'chat' | 'admin') => void;
  // Hover-prefetch: called when the user hovers/taps a group row, giving App
  // a chance to load the group's messages in the background before the user
  // actually clicks. By the time the click happens, the messages are already
  // in state and the new ChatWindow renders instantly with no flash.
  onPrefetchGroup?: (id: string) => void;
}

export const ChatList: React.FC<ChatListProps> = ({
    groups, activeGroupId, onSelectGroup, onCreateGroup, onJoinGroup, onToggleGroupSound, lastMessages, currentUser, onUpdateUser, onLogout, theme, onToggleTheme, onSwitchView, onPrefetchGroup
}) => {
  const { t } = useTranslation();
  const [showCreateInput, setShowCreateInput] = useState(false);
  const [showJoinInput, setShowJoinInput] = useState(false);
  const [newGroupName, setNewGroupName] = useState('');
  const [joinGroupId, setJoinGroupId] = useState('');
  const [contextMenu, setContextMenu] = useState<{x: number, y: number, groupId: string} | null>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  // Profile Modal State
  const [isProfileOpen, setIsProfileOpen] = useState(false);
  const [editName, setEditName] = useState('');
  const [editAvatar, setEditAvatar] = useState('');
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Theme Menu State
  const [showThemeMenu, setShowThemeMenu] = useState(false);
  const themeMenuRef = useRef<HTMLDivElement>(null);

  // Notification State
  const [showNotifications, setShowNotifications] = useState(false);
  const [notifications, setNotifications] = useState<Array<{id: string, title: string, content: string, sender: string, senderAvatar?: string, timestamp: number, read: boolean, attachments?: Array<{url: string, type: string, name: string, size: string}>}>>([]);
  const [notificationFilter, setNotificationFilter] = useState<'all' | 'unread' | 'read'>('all');
  const notificationRef = useRef<HTMLDivElement>(null);
  const unreadCount = notifications.filter(n => !n.read).length;
  const filteredNotifications = notificationFilter === 'unread'
    ? notifications.filter(n => !n.read)
    : notificationFilter === 'read'
      ? notifications.filter(n => n.read)
      : notifications;

  // Compose Message Modal State
  const [showComposeModal, setShowComposeModal] = useState(false);
  const [recipientName, setRecipientName] = useState('');
  const [messageTitle, setMessageTitle] = useState('');
  const [messageContent, setMessageContent] = useState('');
  const [sendError, setSendError] = useState<string | null>(null);
  const [sendSuccess, setSendSuccess] = useState(false);
  const [isSending, setIsSending] = useState(false);
  const [attachments, setAttachments] = useState<Array<{url: string, type: string, name: string, size: string}>>([]);

  // Message Detail Modal State
  const [selectedMessage, setSelectedMessage] = useState<typeof notifications[0] | null>(null);
  const [showMessageDetail, setShowMessageDetail] = useState(false);
  const [selectedImageIndex, setSelectedImageIndex] = useState<number | null>(null);

  // Lightbox Zoom/Pan State
  const [lightboxScale, setLightboxScale] = useState(1);
  const [lightboxOffset, setLightboxOffset] = useState({ x: 0, y: 0 });
  const [isDraggingLightbox, setIsDraggingLightbox] = useState(false);
  const [dragStart, setDragStart] = useState({ x: 0, y: 0 });
  const [touchDist, setTouchDist] = useState<number | null>(null);

  // Compose file upload refs & state
  const composeFileInputRef = useRef<HTMLInputElement>(null);
  const [isUploading, setIsUploading] = useState(false);

  // Detect http(s) links in text, flag images by extension
  const detectLinks = (text: string): Array<{url: string, isImage: boolean}> => {
    const urlRegex = /https?:\/\/[^\s<>"']+/g;
    const matches = text.match(urlRegex) || [];
    return matches.map(url => ({
      url,
      isImage: /\.(jpg|jpeg|png|gif|webp|svg|bmp)(\?.*)?$/i.test(url),
    }));
  };

  // Handle file selection in compose modal (upload then append to attachments)
  const handleFileSelect = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (!files || files.length === 0) return;
    setIsUploading(true);
    try {
      const uploaded: Array<{url: string, type: string, name: string, size: string}> = [];
      for (const file of Array.from(files)) {
        const result = await uploadAPI.uploadFile(file);
        const isImage = file.type.startsWith('image/');
        uploaded.push({
          url: (result as any).url || (result as any).file_url || '',
          type: isImage ? 'image' : 'file',
          name: file.name,
          size: `${(file.size / 1024).toFixed(1)} KB`,
        });
      }
      setAttachments(prev => [...prev, ...uploaded]);
    } catch (err) {
      console.error('Attachment upload failed:', err);
    } finally {
      setIsUploading(false);
      e.target.value = '';
    }
  };

  // Reset zoom when image changes or lightbox closes
  useEffect(() => {
    setLightboxScale(1);
    setLightboxOffset({ x: 0, y: 0 });
  }, [selectedImageIndex]);

  const handleLightboxWheel = (e: React.WheelEvent) => {
    e.stopPropagation();
    if (e.ctrlKey || e.metaKey) {
        // Zoom
        const delta = e.deltaY > 0 ? -0.1 : 0.1;
        setLightboxScale(prev => Math.max(0.5, Math.min(5, prev + delta)));
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
    }
  };

  const handleLightboxTouchEnd = () => {
    setTouchDist(null);
  };

  const getResourceUrl = (url: string): string => {

    if (!url) return '';
    // 如果已经是完整 URL，直接返回
    if (url.startsWith('http://') || url.startsWith('https://')) {
      return url;
    }
    // 如果是相对路径，确保以 / 开头
    if (!url.startsWith('/')) {
      return '/' + url;
    }
    return url;
  };

  // 下载文件
  const downloadFile = async (url: string, filename: string) => {
    try {
      const response = await fetch(url);
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
      // 如果下载失败，尝试直接打开链接
      window.open(url, '_blank');
    }
  };

  // Listen for WebSocket direct message events
  useEffect(() => {
    console.log('[ChatList] Setting up WebSocket listener for direct messages');
    const handleDirectMessage = (event: any) => {
      console.log('[ChatList] Received websocket_message event:', event.detail);
      if (event.detail && event.detail.type === 'new_direct_message') {
        console.log('[ChatList] Playing gentle notification sound for new direct message');
        // Play gentle notification sound for new message
        playGentleNotificationSound();

        // Reload messages when new one arrives
        const reload = async () => {
          try {
            const messages = await directMessageAPI.getDirectMessages('all');
            const formattedNotifications = messages.map(msg => ({
              id: msg.id,
              title: msg.title,
              content: msg.content,
              sender: msg.is_sender ? `To: ${msg.other_party}` : msg.sender,
              senderAvatar: msg.sender_avatar,
              timestamp: new Date(msg.timestamp).getTime(),
              // 发送者发送的消息应该始终标记为已读，接收者的消息用后端状态
              read: msg.is_sender ? true : msg.is_read,
              attachments: msg.attachments || [],
            }));
            setNotifications(formattedNotifications);
          } catch (error) {
            console.error('Failed to reload messages:', error);
          }
        };
        reload();
      }
    };

    window.addEventListener('websocket_message', handleDirectMessage);
    return () => window.removeEventListener('websocket_message', handleDirectMessage);
  }, []);

  // Close notification panel when clicking outside
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (notificationRef.current && !notificationRef.current.contains(e.target as Node)) {
        setShowNotifications(false);
      }
    };
    if (showNotifications) {
      document.addEventListener('mousedown', handleClickOutside);
      return () => document.removeEventListener('mousedown', handleClickOutside);
    }
  }, [showNotifications]);

  // Theme list - same as Sidebar
  const themeList = [
    { id: 'default', name: 'Original Light' },
    { id: 'warm', name: 'Warm Coral' },
    { id: 'coffee', name: 'Coffee Cream' },
    { id: 'coffee-dark', name: 'Coffee Dark' },
    { id: 'red', name: 'Rose' },
    { id: 'orange', name: 'Amber' },
    { id: 'pink', name: 'Blush' },
    { id: 'yellow', name: 'Sunshine' },
    { id: 'green', name: 'Sage' },
    { id: 'cyan', name: 'Mint' },
    { id: 'blue', name: 'Sky' },
    { id: 'purple', name: 'Lavender' },
    { id: 'midnight', name: 'Midnight' },
    { id: 'opencode', name: 'Dark' },
    { id: 'tokyonight', name: 'Tokyo Night' },
    { id: 'catppuccin', name: 'Catppuccin' },
    { id: 'catppuccin-macchiato', name: 'Catppuccin Macchiato' },
    { id: 'nord', name: 'Nord' },
    { id: 'onedark', name: 'One Dark' },
    { id: 'everforest', name: 'Everforest' },
    { id: 'gruvbox', name: 'Gruvbox' },
    { id: 'kanagawa', name: 'Kanagawa' },
    { id: 'sakura', name: 'Sakura' },
    { id: 'dracula', name: 'Dracula' },
    { id: 'ayu', name: 'Ayu Mirage' },
    { id: 'monokai', name: 'Monokai' },
    { id: 'matrix', name: 'Matrix' }
  ];

  // Close theme menu when clicking outside
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (themeMenuRef.current && !themeMenuRef.current.contains(e.target as Node)) {
        setShowThemeMenu(false);
      }
    };
    if (showThemeMenu) {
      document.addEventListener('mousedown', handleClickOutside);
      return () => document.removeEventListener('mousedown', handleClickOutside);
    }
  }, [showThemeMenu]);

  useEffect(() => {
      const handleClickOutside = (e: MouseEvent) => {
          if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
              setContextMenu(null);
          }
      };
      document.addEventListener('mousedown', handleClickOutside);
      return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const handleCreate = () => {
      if(newGroupName.trim()) {
          onCreateGroup(newGroupName);
          setNewGroupName('');
          setShowCreateInput(false);
      }
  };

  const handleJoin = () => {
      if(joinGroupId.trim() && onJoinGroup) {
          onJoinGroup(joinGroupId);
          setJoinGroupId('');
          setShowJoinInput(false);
      }
  };

  const handleContextMenu = (e: React.MouseEvent, groupId: string) => {
      e.preventDefault();
      setContextMenu({ x: e.clientX, y: e.clientY, groupId });
  };

  const handleOpenProfile = () => {
      if (currentUser) {
          setEditName(currentUser.name);
          setEditAvatar(currentUser.avatar);
          setSelectedFile(null);
          setIsProfileOpen(true);
      }
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
      if (e.target.files && e.target.files[0]) {
          const file = e.target.files[0];
          setSelectedFile(file);
          setEditAvatar(URL.createObjectURL(file));
      }
  };

  const handleSaveProfile = async () => {
      if (!currentUser) return;

      try {
          let newAvatarUrl = editAvatar;
          if (selectedFile) {
              // 使用真实的上传API
              const result = await uploadAPI.uploadFile(selectedFile);
              newAvatarUrl = result.url;
          }

          const updatedUser: User = {
              ...currentUser,
              name: editName,
              avatar: newAvatarUrl
          };

          onUpdateUser(updatedUser);
          setIsProfileOpen(false);
          setSelectedFile(null);
      } catch (error) {
          console.error('Failed to save profile:', error);
          alert(t('profile.saveFailed'));
      }
  };

  return (
    <div data-testid="chat-list" className="w-full h-full bg-bgLight border-r border-border flex flex-col z-20 relative overflow-hidden">
      <div className="p-5">
        <div className="flex justify-between items-center mb-4">
            <div className="flex items-center gap-3">
                 {/* Mobile Profile Access */}
                {currentUser && (
                    <img
                        src={getAvatarUrl(currentUser.avatar, currentUser.id, currentUser.name)}
                        className="w-10 h-10 rounded-full object-cover cursor-pointer hover:opacity-80 md:hidden bg-border"
                        onClick={handleOpenProfile}
                        alt=""
                        loading="lazy"
                        onError={(e) => {
                          const img = e.currentTarget;
                          if (img.dataset.fallbackApplied) return;
                          img.dataset.fallbackApplied = '1';
                          img.src = getLocalAvatarFallback(currentUser.id, currentUser.name);
                        }}
                    />
                )}

            </div>
              <div className="flex gap-2 shrink-0 flex-nowrap items-center">
                    <button
                        onClick={() => window.dispatchEvent(new CustomEvent('openMobileNav'))}
                        className="p-2 rounded-lg text-textMuted hover:bg-primary/10 hover:text-primary transition-colors md:hidden shrink-0"
                        aria-label="Navigation menu"
                    >
                        <Menu size={20} />
                    </button>
                    <button
                        onClick={() => onSwitchView?.('admin')}
                        className="p-2 rounded-full transition-colors bg-primary/10 text-primary hover:bg-primary/20 md:hidden shrink-0"
                        title={t('chatList.aiAgent')}
                    >
                        <BotMessageSquare size={20} />
                    </button>

                    {/* Notification Button */}
                    <div className="relative shrink-0">
                        <button
                            onClick={() => setShowNotifications(!showNotifications)}
                            className={`p-2 rounded-full transition-colors relative ${showNotifications ? 'bg-primary text-white' : 'bg-bgLight text-textMuted hover:bg-primary/10 hover:text-primary'}`}
                            title={t('chatList.notifications')}
                        >
                            <Mail size={20} />
                            {unreadCount > 0 && (
                                <span className="absolute -top-1 -right-1 w-5 h-5 bg-red-500 text-white text-[10px] font-bold rounded-full flex items-center justify-center">
                                    {unreadCount > 9 ? '9+' : unreadCount}
                                </span>
                            )}
                        </button>
                    </div>

                    {/* Compose Message Button - Paper Plane */}
                    <button
                        onClick={() => {
                            setShowComposeModal(true);
                            setSendError('');
                            setSendSuccess(false);
                            setAttachments([]);
                        }}
                        className="p-2 rounded-full transition-colors bg-bgLight text-textMuted hover:bg-primary/10 hover:text-primary shrink-0"
                        title={t('chatList.sendMessage')}
                    >
                        <Send size={20} />
                    </button>

                    {/* Notification Panel */}
                    {showNotifications && (
                        <div
                            ref={notificationRef}
                            className="fixed top-20 left-1/2 -translate-x-1/2 mt-2 w-[90vw] md:w-[600px] lg:w-[800px] max-w-[1200px] bg-panel border border-border rounded-xl shadow-2xl z-[100] overflow-hidden animate-in fade-in zoom-in-95 duration-200 max-h-[80vh]"
                        >
                            <div className="flex items-center justify-between px-4 py-3 border-b border-border">
                                <div className="flex items-center gap-2">
                                    <Bell size={18} className="text-primary" />
                                    <span className="font-semibold text-textMain">{t('chatList.notifications')}</span>
                                    {unreadCount > 0 && (
                                        <span className="bg-primary/20 text-primary text-xs px-2 py-0.5 rounded-full font-medium">
                                            {t('chatList.newCount', { count: unreadCount })}
                                        </span>
                                    )}
                                </div>
                                {notifications.length > 0 && (
                                    <button
                                        onClick={async () => {
                                            // Call API to mark all as read
                                            try {
                                                const unreadNotifications = notifications.filter(n => !n.read);
                                                await Promise.all(
                                                    unreadNotifications.map(n => directMessageAPI.markAsRead(n.id))
                                                );
                                                // Update local state after API success
                                                setNotifications(prev => prev.map(n => ({...n, read: true})));
                                            } catch (error) {
                                                console.error('Failed to mark all as read:', error);
                                            }
                                        }}
                                        className="text-xs text-textMuted hover:text-primary transition-colors"
                                    >
                                        {t('chatList.markAllRead')}
                                    </button>
                                )}
                            </div>

                            {/* Filter Tabs */}
                            <div className="flex border-b border-border bg-bgLight/30">
                                <button
                                    onClick={() => setNotificationFilter('all')}
                                    className={`flex-1 py-2 text-xs font-medium transition-colors ${
                                        notificationFilter === 'all'
                                            ? 'text-primary border-b-2 border-primary bg-primary/5'
                                            : 'text-textMuted hover:text-textMain'
                                    }`}
                                >
                                    {t('chat.allNotif')} ({notifications.length})
                                </button>
                                <button
                                    onClick={() => setNotificationFilter('unread')}
                                    className={`flex-1 py-2 text-xs font-medium transition-colors ${
                                        notificationFilter === 'unread'
                                            ? 'text-primary border-b-2 border-primary bg-primary/5'
                                            : 'text-textMuted hover:text-textMain'
                                    }`}
                                >
                                    {t('chat.unreadNotif')} ({unreadCount})
                                </button>
                                <button
                                    onClick={() => setNotificationFilter('read')}
                                    className={`flex-1 py-2 text-xs font-medium transition-colors ${
                                        notificationFilter === 'read'
                                            ? 'text-primary border-b-2 border-primary bg-primary/5'
                                            : 'text-textMuted hover:text-textMain'
                                    }`}
                                >
                                    {t('chat.readNotif')} ({notifications.length - unreadCount})
                                </button>
                            </div>

                            <div className="max-h-80 overflow-y-auto">
                                {filteredNotifications.length === 0 ? (
                                    <div className="flex flex-col items-center justify-center py-8 text-textMuted">
                                        <Mail size={40} className="mb-2 opacity-50" />
                                        <p className="text-sm">{t('chatList.noNotificationsYet')}</p>
                                    </div>
                                ) : (
                                    filteredNotifications.map((notification) => (
                                        <div
                                            key={notification.id}
                                            className={`px-4 py-3 border-b border-border last:border-b-0 cursor-pointer transition-colors hover:bg-bgLight ${!notification.read ? 'bg-primary/5' : ''}`}
                                            onClick={async () => {
                                                // 标记为已读
                                                if (!notification.read) {
                                                    try {
                                                        await directMessageAPI.markAsRead(notification.id);
                                                        setNotifications(prev => prev.map(n =>
                                                            n.id === notification.id ? {...n, read: true} : n
                                                        ));
                                                    } catch (error) {
                                                        console.error('Failed to mark as read:', error);
                                                    }
                                                }
                                                // 打开详情弹窗
                                                setSelectedMessage(notification);
                                                setShowMessageDetail(true);
                                                setSelectedImageIndex(null);
                                            }}
                                        >
                                            <div className="flex items-start gap-3">
                                                {/* Sender Avatar */}
                                                <div className="flex-shrink-0">
                                                    {notification.senderAvatar ? (
                                                        <img
                                                            src={notification.senderAvatar}
                                                            alt={notification.sender || 'Unknown'}
                                                            className="w-8 h-8 rounded-full object-cover"
                                                            loading="lazy"
                                                        />
                                                    ) : (
                                                        <div className="w-8 h-8 rounded-full bg-primary/20 flex items-center justify-center text-primary text-xs font-bold">
                                                            {(notification.sender || 'U').charAt(0).toUpperCase()}
                                                        </div>
                                                    )}
                                                </div>

                                                <div className="flex-1 min-w-0">
                                                    {/* Title and Sender */}
                                                    <div className="flex items-center gap-2">
                                                        <h4 className={`text-sm font-medium truncate ${!notification.read ? 'text-textMain' : 'text-textMuted'}`}>
                                                            {notification.title}
                                                        </h4>
                                                        {!notification.read && (
                                                            <div className="w-2 h-2 bg-primary rounded-full flex-shrink-0"></div>
                                                        )}
                                                    </div>

                                                    {/* Sender Name */}
                                                    <p className="text-xs text-primary font-medium mt-0.5">
                                                        {notification.sender || 'Unknown'}
                                                    </p>

                                                     {/* Content */}
                                                    <p className="text-xs text-textMuted mt-0.5 line-clamp-2">
                                                        {notification.content}
                                                    </p>

                                                    {/* Link Preview (auto-detected from content) */}
                                                    {detectLinks(notification.content).filter(l => l.isImage).length > 0 && (
                                                        <div className="flex flex-wrap gap-1 mt-2">
                                                            {detectLinks(notification.content)
                                                                .filter(l => l.isImage)
                                                                .slice(0, 2)
                                                                .map((link, idx) => (
                                                                    <img
                                                                        key={idx}
                                                                        src={link.url}
                                                                        alt="Link preview"
                                                                        className="w-16 h-16 object-cover rounded border border-border/50"
                                                                        onError={(e) => {
                                                                            (e.target as HTMLImageElement).style.display = 'none';
                                                                        }}
                                                                        loading="lazy"
                                                                    />
                                                                ))}
                                                        </div>
                                                    )}

                                                    {/* Attachments */}
                                                    {notification.attachments && notification.attachments.length > 0 && (
                                                        <div className="flex flex-wrap gap-1 mt-2">
                                                            {notification.attachments.slice(0, 3).map((att, idx) => (
                                                                att.type === 'image' ? (
                                                                    <img
                                                                        key={idx}
                                                                        src={getResourceUrl(att.url)}
                                                                        alt={att.name}
                                                                        className="w-16 h-16 object-cover rounded border border-border/50"
                                                                        onError={(e) => {
                                                                            console.error('[ChatList] Failed to load thumbnail:', att.url);
                                                                            (e.target as HTMLImageElement).style.display = 'none';
                                                                        }}
                                                                        loading="lazy"
                                                                    />
                                                                ) : (
                                                                    <div key={idx} className="flex items-center gap-1 px-2 py-1 bg-bgLight border border-border/50 rounded text-[10px]">
                                                                        <FileText size={10} className="text-primary" />
                                                                        <span className="truncate max-w-[80px]">{att.name}</span>
                                                                    </div>
                                                                )
                                                            ))}
                                                            {notification.attachments.length > 3 && (
                                                                <div className="w-16 h-16 flex items-center justify-center bg-bgLight border border-border/50 rounded text-xs text-textMuted">
                                                                    +{notification.attachments.length - 3}
                                                                </div>
                                                            )}
                                                        </div>
                                                    )}

                                                    {/* Time */}
                                                    <span className="text-[10px] text-textMuted/70 mt-1 block">
                                                        {new Date(notification.timestamp).toLocaleString()}
                                                    </span>
                                                </div>
                                            </div>
                                        </div>
                                    ))
                                )}
                            </div>

                            {notifications.length > 0 && (
                                <div className="px-4 py-2 border-t border-border bg-bgLight/50 text-center">
                                    <button className="text-xs text-primary hover:underline">
                                        {t('chatList.viewAllNotifications')}
                                    </button>
                                </div>
                            )}
                        </div>
                    )}

                {/* Theme Toggle Button */}
                <div className="relative shrink-0">
                    <button
                        onClick={() => setShowThemeMenu(!showThemeMenu)}
                        className={`p-2 rounded-full transition-colors ${showThemeMenu ? 'bg-primary text-white' : 'bg-bgLight text-textMuted hover:bg-primary/10 hover:text-primary'} shrink-0`}
                        title={t('chatList.changeTheme')}
                    >
                        <Palette size={20} />
                    </button>

                    {/* Theme Menu Dropdown */}
                    {showThemeMenu && (
                        <div
                            ref={themeMenuRef}
                            className="absolute top-full right-0 mt-2 w-48 bg-panel border border-border rounded-xl shadow-2xl p-2 z-50 animate-in fade-in slide-in-from-top-2 duration-200 max-h-80 overflow-y-auto"
                        >
                            <div className="text-xs font-bold text-textMuted px-2 py-1 mb-1 uppercase tracking-wider border-b border-border pb-1">{t('chatList.selectTheme')}</div>
                            <div className="flex flex-col gap-1 mt-1">
                                {themeList.map(t => (
                                    <button
                                        key={t.id}
                                        onClick={() => {
                                            window.dispatchEvent(new CustomEvent('setTheme', { detail: t.id }));
                                            setShowThemeMenu(false);
                                        }}
                                        className={`w-full text-left px-3 py-2 rounded-lg text-sm transition-colors ${theme === t.id ? 'bg-primary/20 text-primary font-bold' : 'text-textMain hover:bg-primary/10'}`}
                                    >
                                        {t.name}
                                    </button>
                                ))}
                            </div>
                        </div>
                    )}
                </div>

                <button
                    onClick={() => setShowJoinInput(!showJoinInput)}
                    className="p-2 bg-bgLight rounded-full text-textMuted hover:bg-primary/10 hover:text-primary transition-colors shrink-0"
                    title={t('chatList.joinGroup')}
                >
                    <span className="text-sm font-bold">+</span>
                </button>
                <button
                    onClick={() => setShowCreateInput(!showCreateInput)}
                    data-testid="chat-list-create"
                    className="p-2 bg-bgLight rounded-full text-textMuted hover:bg-primary/10 hover:text-primary transition-colors shrink-0"
                    title={t('chatList.createGroup')}
                >
                    <Plus size={20} className={showCreateInput ? "rotate-45 transition-transform" : "transition-transform"} />
                </button>
            </div>
        </div>

        {/* Create Group Input */}
        {showCreateInput && (
            <div className="mb-4 animate-in slide-in-from-top-2">
                <div className="flex gap-2">
                    <input
                        type="text"
                        data-testid="chat-list-create-name"
                        value={newGroupName}
                        onChange={(e) => setNewGroupName(e.target.value)}
                        placeholder={t('chatList.newGroupName')}
                        className="flex-1 px-3 py-2 bg-bgLight border border-border text-textMain placeholder:text-textMuted focus:outline-none focus:ring-1 focus:ring-primary"
                        onKeyDown={(e) => e.key === 'Enter' && handleCreate()}
                        autoFocus
                    />
                    <button
                        onClick={handleCreate}
                        data-testid="chat-list-create-submit"
                        className="px-3 py-2 bg-primary text-white rounded-lg text-sm font-semibold hover:bg-primary/90"
                    >
                        {t('chatList.add')}
                    </button>
                </div>
            </div>
        )}

        {/* Join Group Input */}
        {showJoinInput && (
            <div className="mb-4 animate-in slide-in-from-top-2">
                <div className="flex gap-2">
                    <input
                        type="text"
                        value={joinGroupId}
                        onChange={(e) => setJoinGroupId(e.target.value)}
                        placeholder={t('chatList.enterGroupId')}
                        className="flex-1 px-3 py-2 bg-bgLight border border-border text-textMain placeholder:text-textMuted focus:outline-none focus:ring-1 focus:ring-primary"
                        onKeyDown={(e) => e.key === 'Enter' && handleJoin()}
                        autoFocus
                    />
                    <button
                        onClick={handleJoin}
                        className="px-3 py-2 bg-green-500 text-white rounded-lg text-sm font-semibold hover:bg-green-600"
                    >
                        {t('chatList.join')}
                    </button>
                </div>
                <p className="text-xs text-textMuted mt-1">{t('chatList.enterGroupIdHint')}</p>
            </div>
        )}

        <div className="relative">
          <Search className="absolute left-3 top-3 text-textMuted" size={18} />
          <input
            type="text"
            placeholder={t('chatList.searchGroups')}
            className="w-full pl-10 pr-4 py-2.5 bg-bgLight border border-border rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-primary/50 transition-all"
          />
        </div>
      </div>

      <div className="flex-1 overflow-y-auto custom-scrollbar">
        {groups.map(group => (
          <div
            key={group.id}
            data-testid="chat-list-item"
            data-group-id={group.id}
            onClick={() => onSelectGroup(group.id, false)}
            // Hover/touch prefetch: load the group's messages in the background
            // so the eventual click renders with no flash. The callback in App
            // dedupes per session — each group is fetched at most once.
            onPointerEnter={() => onPrefetchGroup?.(group.id)}
            onContextMenu={(e) => handleContextMenu(e, group.id)}
            className={`flex items-center gap-3 px-5 py-4 cursor-pointer transition-colors border-l-4 ${
              activeGroupId === group.id
                ? 'bg-primary/10 border-primary'
                : 'hover:bg-bgLight border-transparent'
            }`}
          >
            <div className="relative flex-shrink-0">
                <img
                  src={getAvatarUrl(group.avatar, group.id, group.name)}
                  alt=""
                  className="w-12 h-12 rounded-full object-cover shadow-sm bg-border"
                  loading="lazy"
                  onError={(e) => {
                    const img = e.currentTarget;
                    if (img.dataset.fallbackApplied) return;
                    img.dataset.fallbackApplied = '1';
                    img.src = getLocalAvatarFallback(group.id, group.name);
                  }}
                />
                {group.unreadCount > 0 && (
                    <div className="absolute -top-1 -right-1 bg-red-500 text-white text-[10px] font-bold px-1.5 py-0.5 rounded-full border-2 border-panel min-w-[20px] text-center">
                        {group.unreadCount}
                    </div>
                )}
                {/* Visual Cue for Unread Mention - Clickable to jump */}
                {group.hasUnreadMention && (
                     <div
                        className="absolute -bottom-1 -right-1 bg-yellow-500 text-white p-0.5 rounded-full border-2 border-panel hover:scale-110 transition-transform cursor-pointer z-10"
                        title={t('chatList.youWereMentioned')}
                        onClick={(e) => {
                            e.stopPropagation();
                            onSelectGroup(group.id, true);
                        }}
                     >
                         <AtSign size={10} />
                     </div>
                )}
                {/* Muted Indicator */}
                {!group.notificationSoundEnabled && (
                    <div className="absolute bottom-0 left-1 bg-border text-textMuted p-0.5 rounded-full border border-panel">
                        <VolumeX size={8} />
                    </div>
                )}
            </div>
            <div className="flex-1 min-w-0">
                <div className="flex justify-between items-baseline mb-1">
                    <h3 className={`text-sm font-semibold truncate ${activeGroupId === group.id ? 'text-primary' : 'text-textMain'}`}>
                        {group.name}
                    </h3>
                    <span className="text-[10px] text-textMuted shrink-0 ml-2">
                        {lastMessages[group.id] ? formatTime(lastMessages[group.id].timestamp, t) : formatTime(group.createdAt, t)}
                    </span>
                </div>
                <p className="text-sm text-textMuted truncate flex items-center gap-1">
                    {group.hasUnreadMention && <span className="text-primary font-bold">@You</span>}
                    <span className="truncate">{lastMessages[group.id]?.content || group.description}</span>
                </p>
                {/* Group ID */}
                <p className="text-[10px] text-textMuted/50 mt-0.5 font-mono">ID: {group.id}</p>
            </div>
          </div>
        ))}
      </div>

      {/* Context Menu */}
      {contextMenu && (
          <div
            ref={menuRef}
            className="fixed z-50 bg-panel rounded-lg shadow-xl border border-border py-1 w-48 animate-in fade-in zoom-in-95 duration-100"
            style={{ top: contextMenu.y, left: contextMenu.x }}
          >
              <button
                onClick={() => {
                    onToggleGroupSound(contextMenu.groupId);
                    setContextMenu(null);
                }}
                className="w-full text-left px-4 py-2.5 text-sm text-textMain hover:bg-bgLight flex items-center gap-2"
              >
                 {groups.find(g => g.id === contextMenu.groupId)?.notificationSoundEnabled ? (
                     <>
                        <VolumeX size={16} className="text-red-500" />
                        <span>{t('chatList.muteNotifications')}</span>
                     </>
                 ) : (
                     <>
                        <Volume2 size={16} className="text-green-500" />
                        <span>{t('chatList.unmuteNotifications')}</span>
                     </>
                 )}
              </button>
          </div>
      )}

      {/* Profile Modal (Mobile & Desktop if needed, though Sidebar has one too) */}
      {isProfileOpen && (
            <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center backdrop-blur-sm animate-in fade-in duration-200">
                <div className="bg-panel rounded-2xl shadow-2xl w-full max-w-sm overflow-hidden transform transition-all scale-100">
                    <div className="bg-primary px-6 py-4 flex justify-between items-center">
                        <h3 className="text-white font-semibold text-lg">{t('chatList.editProfile')}</h3>
                        <button onClick={() => setIsProfileOpen(false)} className="text-white/80 hover:text-white">
                            <X size={20} />
                        </button>
                    </div>

                    <div className="p-6 flex flex-col items-center">
                        <div className="relative mb-6 group">
                            <img src={editAvatar} className="w-24 h-24 rounded-full object-cover border-4 border-border shadow-lg" loading="lazy" />
                            <button
                                onClick={() => fileInputRef.current?.click()}
                                className="absolute bottom-0 right-0 p-2 bg-panel rounded-full shadow-md text-textMuted hover:text-primary transition-colors border border-border"
                                title={t('chatList.changeAvatar')}
                            >
                                <Camera size={16} />
                            </button>
                            <input
                                type="file"
                                ref={fileInputRef}
                                className="hidden"
                                accept="image/*"
                                onChange={handleFileChange}
                            />
                        </div>

                        <div className="w-full space-y-4">
                            <div>
                                <label className="block text-xs font-bold text-textMuted uppercase mb-1">{t('chatList.displayName')}</label>
                                <input
                                    type="text"
                                    value={editName}
                                    onChange={(e) => setEditName(e.target.value)}
                                    className="w-full px-4 py-2 bg-bgLight border border-border rounded-lg focus:outline-none focus:ring-2 focus:ring-primary/50 text-sm"
                                />
                            </div>

                            <button
                                onClick={handleSaveProfile}
                                className="w-full py-2.5 bg-primary text-white rounded-lg font-semibold hover:bg-primary/90 transition-colors flex items-center justify-center gap-2 mt-4"
                            >
                                <Save size={18} /> {t('chatList.saveChanges')}
                            </button>

                            <button
                                onClick={() => {
                                    setIsProfileOpen(false);
                                    onLogout();
                                }}
                                className="w-full py-2.5 bg-red-500/10 text-red-600 rounded-lg font-semibold hover:bg-red-500/20 transition-colors flex items-center justify-center gap-2"
                            >
                                <LogOut size={18} /> {t('chatList.logOut')}
                            </button>
                        </div>
                    </div>
                </div>
            </div>
        )}

        {/* Compose Message Modal */}
        {showComposeModal && (
            <div className="fixed inset-0 bg-black/50 z-[200] flex items-center justify-center backdrop-blur-sm animate-in fade-in duration-200">
                <div className="bg-panel rounded-2xl shadow-2xl w-[90vw] md:w-[500px] max-w-[600px] overflow-hidden transform transition-all scale-100 mx-4">
                    {/* Header */}
                    <div className="bg-primary px-6 py-4 flex justify-between items-center">
                        <div className="flex items-center gap-2">
                            <Send size={20} className="text-white" />
                            <h3 className="text-white font-semibold text-lg">{t('chatList.sendDm')}</h3>
                        </div>
                        <button
                            onClick={() => setShowComposeModal(false)}
                            className="text-white/80 hover:text-white"
                        >
                            <X size={20} />
                        </button>
                    </div>

                    {/* Form */}
                    <div className="p-6 space-y-4">
                        {/* Recipient */}
                        <div>
                            <label className="block text-xs font-bold text-textMuted uppercase mb-2">{t('chatList.recipient')}</label>
                            <div className="relative">
                                <UserIcon className="absolute left-3 top-3 text-textMuted" size={18} />
                                <input
                                    type="text"
                                    value={recipientName}
                                    onChange={(e) => setRecipientName(e.target.value)}
                                    placeholder={t('chatList.recipientPlaceholder')}
                                    className="w-full pl-10 pr-4 py-2.5 bg-bgLight border border-border rounded-lg text-textMain placeholder:text-textMuted focus:outline-none focus:ring-2 focus:ring-primary/50"
                                    autoFocus
                                />
                            </div>
                            <p className="text-[10px] text-textMuted/70 mt-1">{t('chat.composeHint')}</p>
                        </div>

                        {/* Title */}
                        <div>
                            <label className="block text-xs font-bold text-textMuted uppercase mb-2">{t('chatList.title')}</label>
                            <input
                                type="text"
                                value={messageTitle}
                                onChange={(e) => setMessageTitle(e.target.value)}
                                placeholder={t('chatList.titlePlaceholder')}
                                className="w-full px-4 py-2.5 bg-bgLight border border-border rounded-lg text-textMain placeholder:text-textMuted focus:outline-none focus:ring-2 focus:ring-primary/50"
                            />
                        </div>

                        {/* Message Content */}
                        <div>
                            <label className="block text-xs font-bold text-textMuted uppercase mb-2">{t('chatList.content')}</label>
                            <textarea
                                value={messageContent}
                                onChange={(e) => setMessageContent(e.target.value)}
                                placeholder={t('chatList.contentPlaceholder')}
                                rows={4}
                                className="w-full px-4 py-3 bg-bgLight border border-border rounded-lg text-textMain placeholder:text-textMuted focus:outline-none focus:ring-2 focus:ring-primary/50 resize-none"
                            />
                            <p className="text-[10px] text-textMuted/70 mt-1">{t('chatList.linkSupport')}</p>
                        </div>

                        {/* Attachments */}
                        {attachments.length > 0 && (
                            <div className="space-y-2">
                                <label className="block text-xs font-bold text-textMuted uppercase mb-2">{t('chatList.attachments')}</label>
                                <div className="flex flex-wrap gap-2">
                                    {attachments.map((att, idx) => (
                                        <div key={idx} className="relative group">
                                            {att.type === 'image' ? (
                                                <div className="relative">
                                                    <img
                                                        src={att.url}
                                                        alt={att.name}
                                                        className="w-20 h-20 object-cover rounded-lg border border-border"
                                                        loading="lazy"
                                                    />
                                                    <button
                                                        onClick={() => removeAttachment(idx)}
                                                        className="absolute -top-2 -right-2 w-5 h-5 bg-red-500 text-white rounded-full flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity"
                                                    >
                                                        <XCircle size={12} />
                                                    </button>
                                                </div>
                                            ) : (
                                                <div className="flex items-center gap-2 px-3 py-2 bg-bgLight border border-border rounded-lg">
                                                    <FileText size={16} className="text-primary" />
                                                    <div className="max-w-[120px]">
                                                        <p className="text-xs text-textMain truncate">{att.name}</p>
                                                        <p className="text-[10px] text-textMuted">{att.size}</p>
                                                    </div>
                                                    <button
                                                        onClick={() => removeAttachment(idx)}
                                                        className="text-red-500 hover:text-red-600"
                                                    >
                                                        <XCircle size={14} />
                                                    </button>
                                                </div>
                                            )}
                                        </div>
                                    ))}
                                </div>
                            </div>
                        )}

                        {/* Link Preview (from content) */}
                        {messageContent && detectLinks(messageContent).filter(l => l.isImage).length > 0 && (
                            <div className="space-y-2">
                                <label className="block text-xs font-bold text-textMuted uppercase mb-2">{t('chatList.linkPreview')}</label>
                                <div className="flex flex-wrap gap-2">
                                    {detectLinks(messageContent)
                                        .filter(l => l.isImage)
                                        .slice(0, 3) // 最多显示3个预览
                                        .map((link, idx) => (
                                            <div key={idx} className="relative">
                                                <img
                                                    src={link.url}
                                                    alt="Preview"
                                                    className="w-24 h-24 object-cover rounded-lg border border-border"
                                                    onError={(e) => {
                                                        (e.target as HTMLImageElement).style.display = 'none';
                                                    }}
                                                    loading="lazy"
                                                />
                                                <div className="absolute bottom-0 left-0 right-0 bg-black/50 text-white text-[10px] px-2 py-1 rounded-b-lg truncate">
                                                    {t('chatList.imageLinkDetected')}
                                                </div>
                                            </div>
                                        ))}
                                </div>
                            </div>
                        )}

                        {/* Error/Success Messages */}
                        {sendError && (
                            <div className="p-3 bg-red-500/10 border border-red-500/20 rounded-lg text-red-600 text-sm">
                                {sendError}
                            </div>
                        )}
                        {sendSuccess && (
                            <div className="p-3 bg-green-500/10 border border-green-500/20 rounded-lg text-green-600 text-sm">
                                {t('chatList.messageSentSuccessfully')}
                            </div>
                        )}

                        {/* Upload and Buttons */}
                        <div className="flex items-center gap-2 pt-2">
                            <input
                                type="file"
                                ref={composeFileInputRef}
                                className="hidden"
                                multiple
                                onChange={handleFileSelect}
                            />
                            <button
                                onClick={() => composeFileInputRef.current?.click()}
                                disabled={isUploading}
                                className="p-2.5 border border-border text-textMuted rounded-lg hover:bg-bgLight hover:text-primary transition-colors disabled:opacity-50"
                                title={t('chatList.attachFiles')}
                            >
                                {isUploading ? (
                                    <div className="w-4 h-4 border-2 border-primary/30 border-t-primary rounded-full animate-spin" />
                                ) : (
                                    <Paperclip size={18} />
                                )}
                            </button>
                            <button
                                onClick={() => {
                                    composeFileInputRef.current!.accept = 'image/*';
                                    composeFileInputRef.current?.click();
                                    composeFileInputRef.current!.accept = '';
                                }}
                                disabled={isUploading}
                                className="p-2.5 border border-border text-textMuted rounded-lg hover:bg-bgLight hover:text-primary transition-colors disabled:opacity-50"
                                title={t('chatList.attachImages')}
                            >
                                <ImageIcon size={18} />
                            </button>
                            <div className="flex-1"></div>
                            <button
                                onClick={() => setShowComposeModal(false)}
                                className="py-2.5 px-4 border border-border text-textMain rounded-lg font-medium hover:bg-bgLight transition-colors"
                            >
                                {t('chatList.cancel')}
                            </button>
                            <button
                                onClick={async () => {
                                    if (!recipientName.trim()) {
                                        setSendError(t('chatList.pleaseEnterRecipient'));
                                        return;
                                    }
                                    if (!messageContent.trim()) {
                                        setSendError(t('chatList.pleaseEnterContent'));
                                        return;
                                    }

                                    // Prevent double submission
                                    if (isSending) return;

                                    setIsSending(true);
                                    setSendError('');

                                    try {
                                        // 准备附件数据
                                        const attachmentData = attachments.map(att => ({
                                            url: att.url!,
                                            type: att.type,
                                            name: att.name,
                                            size: att.size
                                        }));

                                        // Call real API to send direct message with attachments
                                        const response = await directMessageAPI.sendDirectMessage(
                                            recipientName.trim(),
                                            messageTitle.trim(),
                                            messageContent.trim(),
                                            attachmentData.length > 0 ? attachmentData : undefined
                                        );

                                        // Play send success sound
                                        playSendSuccessSound();

                                        // Add to local notifications
                                        const sentNotification = {
                                            id: response.id,
                                            title: response.title || t('chatList.messageSentSuccessfully'),
                                            content: t('chatList.sentTo', { name: recipientName.trim(), content: messageContent.trim() }),
                                            sender: `${t('chatList.toPrefix')} ${recipientName.trim()}`,
                                            timestamp: Date.now(),
                                            read: true
                                        };
                                        setNotifications(prev => [sentNotification, ...prev]);
                                        setSendSuccess(true);

                                        // Show success for 1 second, then close modal
                                        setTimeout(() => {
                                            setShowComposeModal(false);
                                            setRecipientName('');
                                            setMessageTitle('');
                                            setMessageContent('');
                                            setAttachments([]);
                                            setSendSuccess(false);
                                            setIsSending(false);
                                        }, 1000);
                                    } catch (error: any) {
                                        setSendError(error.message || t('chatList.failedToSend'));
                                        setIsSending(false);
                                    }
                                }}
                                disabled={!recipientName.trim() || !messageContent.trim() || isSending}
                                className="flex-1 py-2.5 bg-primary text-white rounded-lg font-medium hover:bg-primary/90 transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2"
                            >
                                {isSending ? (
                                    <>
                                        <div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                                        <span>{t('chatList.sending')}</span>
                                    </>
                                ) : sendSuccess ? (
                                    <>
                                        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                                        </svg>
                                        <span>{t('chatList.sent')}</span>
                                    </>
                                ) : (
                                    <>
                                        <Send size={16} />
                                        <span>{t('chatList.send')}</span>
                                    </>
                                )}
                            </button>
                        </div>
                     </div>
                </div>
            </div>
        )}

        {/* Message Detail Modal */}
        {showMessageDetail && selectedMessage && (
            <div
                className="fixed inset-0 bg-black/70 z-[300] flex items-center justify-center backdrop-blur-sm animate-in fade-in duration-200 p-4"
                onClick={() => {
                    // 点击背景返回列表
                    setShowMessageDetail(false);
                    setSelectedImageIndex(null);
                }}
            >
                <div
                    className="bg-panel rounded-2xl shadow-2xl w-full max-w-2xl max-h-[90vh] overflow-hidden transform transition-all scale-100 flex flex-col"
                    onClick={(e) => e.stopPropagation()} // 阻止冒泡，点击内容不关闭
                >
                    {/* Header */}
                    <div className="bg-primary px-6 py-4 flex justify-between items-center flex-shrink-0">
                        <div className="flex items-center gap-3">
                            {selectedMessage.senderAvatar ? (
                                <img
                                    src={selectedMessage.senderAvatar}
                                    alt={selectedMessage.sender}
                                    className="w-10 h-10 rounded-full object-cover border-2 border-white/30"
                                    loading="lazy"
                                />
                            ) : (
                                <div className="w-10 h-10 rounded-full bg-white/20 flex items-center justify-center text-white font-bold">
                                    {(selectedMessage.sender || 'U').charAt(0).toUpperCase()}
                                </div>
                            )}
                            <div>
                                <h3 className="text-white font-semibold text-lg">{selectedMessage.title}</h3>
                                <p className="text-white/80 text-sm">{selectedMessage.sender}</p>
                            </div>
                        </div>
                        <button
                            onClick={() => {
                                setShowMessageDetail(false);
                                setSelectedImageIndex(null);
                            }}
                            className="text-white/80 hover:text-white p-1"
                        >
                            <X size={24} />
                        </button>
                    </div>

                    {/* Content - Scrollable */}
                    <div className="flex-1 overflow-y-auto p-6">
                        {/* Message Text */}
                        <div className="bg-bgLight rounded-xl p-4 mb-6">
                            <div
                                className="prose prose-sm max-w-none text-textMain whitespace-pre-wrap"
                                dangerouslySetInnerHTML={{
                                    __html: typeof parse === 'function' ? parse(selectedMessage.content) : selectedMessage.content
                                }}
                            />

                            {/* Link Preview in Content */}
                            {detectLinks(selectedMessage.content).filter(l => l.isImage).length > 0 && (
                                <div className="mt-4 pt-4 border-t border-border">
                                    <p className="text-xs text-textMuted mb-2">Links detected:</p>
                                    <div className="flex flex-wrap gap-2">
                                        {detectLinks(selectedMessage.content)
                                            .filter(l => l.isImage)
                                            .map((link, idx) => (
                                                <div key={idx} className="relative group">
                                                    <img
                                                        src={getResourceUrl(link.url)}
                                                        alt="Link preview"
                                                        className="w-32 h-32 object-cover rounded-lg border border-border cursor-pointer hover:border-primary transition-colors"
                                                        onClick={() => window.open(link.url, '_blank')}
                                                        onError={(e) => {
                                                            console.error('[ChatList] Failed to load link preview:', link.url);
                                                            (e.target as HTMLImageElement).style.display = 'none';
                                                        }}
                                                        loading="lazy"
                                                    />
                                                    <button
                                                        onClick={(e) => {
                                                            e.stopPropagation();
                                                            downloadFile(link.url, `image-${idx + 1}.jpg`);
                                                        }}
                                                        className="absolute bottom-2 right-2 p-1.5 bg-black/60 text-white rounded-full opacity-0 group-hover:opacity-100 transition-opacity hover:bg-black/80"
                                                    >
                                                        <Download size={14} />
                                                    </button>
                                                </div>
                                            ))}
                                    </div>
                                </div>
                            )}
                        </div>

                        {/* Attachments Section */}
                        {selectedMessage.attachments && selectedMessage.attachments.length > 0 && (
                            <div className="space-y-4">
                                <div className="flex items-center gap-2">
                                    <Paperclip size={18} className="text-primary" />
                                    <h4 className="font-semibold text-textMain">{t('chatList.attachments')} ({selectedMessage.attachments.length})</h4>
                                    <button
                                        onClick={() => console.log('[ChatList] Debug attachments:', selectedMessage.attachments)}
                                        className="text-xs text-textMuted hover:text-primary"
                                    >
                                        [Debug]
                                    </button>
                                </div>

                                {/* Images Grid */}
                                {selectedMessage.attachments.filter(att => att.type === 'image').length > 0 && (
                                    <div className="grid grid-cols-3 sm:grid-cols-4 gap-3">
                                        {selectedMessage.attachments
                                            .filter(att => att.type === 'image')
                                            .map((att, idx) => (
                                                <div
                                                    key={idx}
                                                    className="relative group aspect-square cursor-pointer"
                                                    onClick={() => {
                                                        const imageList = selectedMessage.attachments.filter(a => a.type === 'image');
                                                        const index = imageList.findIndex(a => a.url === att.url);
                                                        console.log('[ChatList] Image container clicked, opening lightbox, index:', index, 'url:', att.url);
                                                        if (index >= 0) {
                                                            setSelectedImageIndex(index);
                                                        }
                                                    }}
                                                >
                                                    <img
                                                        src={getResourceUrl(att.url)}
                                                        alt={att.name}
                                                        className="w-full h-full object-cover rounded-lg border border-border hover:border-primary transition-colors pointer-events-none"
                                                        onError={(e) => {
                                                            console.error('[ChatList] Failed to load grid image:', att.url);
                                                            (e.target as HTMLImageElement).style.opacity = '0.5';
                                                        }}
                                                        loading="lazy"
                                                    />
                                                    <div className="absolute inset-0 bg-black/0 group-hover:bg-black/20 transition-colors rounded-lg flex items-center justify-center">
                                                        <ZoomIn size={20} className="text-white opacity-0 group-hover:opacity-100 transition-opacity" />
                                                    </div>
                                                    <button
                                                        onClick={(e) => {
                                                            e.stopPropagation();
                                                            console.log('[ChatList] Downloading image:', att.url);
                                                            downloadFile(getResourceUrl(att.url), att.name);
                                                        }}
                                                        className="absolute bottom-2 right-2 p-1.5 bg-black/60 text-white rounded-full opacity-0 group-hover:opacity-100 transition-opacity hover:bg-black/80"
                                                        title={t('chatList.download')}
                                                    >
                                                        <Download size={14} />
                                                    </button>
                                                </div>
                                            ))}
                                    </div>
                                )}

                                {/* Files List */}
                                {selectedMessage.attachments.filter(att => att.type !== 'image').length > 0 && (
                                    <div className="space-y-2">
                                        {selectedMessage.attachments
                                            .filter(att => att.type !== 'image')
                                            .map((att, idx) => (
                                                <div key={idx} className="flex items-center justify-between p-3 bg-bgLight border border-border rounded-lg hover:border-primary/50 transition-colors group">
                                                    <div className="flex items-center gap-3 min-w-0">
                                                        <div className="p-2 bg-primary/10 rounded-lg">
                                                            <FileText size={20} className="text-primary" />
                                                        </div>
                                                        <div className="min-w-0">
                                                            <p className="text-sm font-medium text-textMain truncate">{att.name}</p>
                                                            <p className="text-xs text-textMuted">{att.size}</p>
                                                        </div>
                                                    </div>
                                                    <button
                                                        onClick={() => {
                                                            console.log('[ChatList] Downloading file:', att.url);
                                                            downloadFile(getResourceUrl(att.url), att.name);
                                                        }}
                                                        className="p-2 text-primary hover:bg-primary/10 rounded-lg transition-colors flex-shrink-0"
                                                        title={t('chatList.download')}
                                                    >
                                                        <Download size={18} />
                                                    </button>
                                                </div>
                                            ))}
                                    </div>
                                )}
                            </div>
                        )}

                        {/* Timestamp */}
                        <div className="mt-6 pt-4 border-t border-border text-center">
                            <span className="text-xs text-textMuted">
                                {new Date(selectedMessage.timestamp).toLocaleString()}
                            </span>
                        </div>
                    </div>
                </div>
            </div>
        )}

        {/* Image Lightbox - rendered via portal to escape ChatList's stacking context
            (ChatList has z-20 + position:relative; without the portal, the sibling
            Sidebar at z-30 paints on top of this z-[400] lightbox). */}
        {selectedImageIndex !== null && selectedImageIndex >= 0 && selectedMessage && createPortal(
            <div
                className="fixed inset-0 bg-black/95 z-[400] flex items-center justify-center animate-in fade-in duration-200 overflow-hidden touch-none"
                onClick={() => {
                    console.log('[ChatList] Closing lightbox');
                    setSelectedImageIndex(null);
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
                    onClick={() => setSelectedImageIndex(null)}
                    className="absolute top-4 left-4 p-2 text-white/80 hover:text-white z-[450] bg-black/30 rounded-full hover:bg-black/50 transition-colors"
                >
                    <X size={28} />
                </button>

                {/* Navigation */}
                {selectedMessage.attachments.filter((att: any) => att.type === 'image').length > 1 && lightboxScale === 1 && (
                    <>
                        <button
                            onClick={(e) => {
                                e.stopPropagation();
                                const images = selectedMessage.attachments.filter((att: any) => att.type === 'image');
                                setSelectedImageIndex((prev) =>
                                    prev === null ? 0 : (prev - 1 + images.length) % images.length
                                );
                            }}
                            className="absolute left-4 top-1/2 -translate-y-1/2 p-3 text-white/80 hover:text-white bg-black/50 rounded-full hover:bg-black/70 transition-colors"
                        >
                            <ChevronLeft size={28} />
                        </button>
                        <button
                            onClick={(e) => {
                                e.stopPropagation();
                                const images = selectedMessage.attachments.filter((att: any) => att.type === 'image');
                                setSelectedImageIndex((prev) =>
                                    prev === null ? 0 : (prev + 1) % images.length
                                );
                            }}
                            className="absolute right-4 top-1/2 -translate-y-1/2 p-3 text-white/80 hover:text-white bg-black/50 rounded-full hover:bg-black/70 transition-colors"
                        >
                            <ChevronRight size={28} />
                        </button>
                    </>
                )}

                {/* Image Counter */}
                {selectedMessage.attachments.filter((att: any) => att.type === 'image').length > 1 && lightboxScale === 1 && (
                    <div className="absolute top-4 left-1/2 -translate-x-1/2 px-4 py-2 bg-black/50 rounded-full text-white text-sm">
                        {selectedImageIndex + 1} / {selectedMessage.attachments.filter((att: any) => att.type === 'image').length}
                    </div>
                )}

                {/* Download Button */}
                <button
                    onClick={(e) => {
                        e.stopPropagation();
                        const images = selectedMessage.attachments.filter((att: any) => att.type === 'image');
                        const img = images[selectedImageIndex];
                        if (img) {
                            console.log('[ChatList] Downloading from lightbox:', img.url);
                            downloadFile(getResourceUrl(img.url), img.name);
                        }
                    }}
                    className="absolute bottom-4 right-4 p-3 text-white bg-black/50 rounded-full hover:bg-black/70 transition-colors flex items-center gap-2"
                >
                    <Download size={20} />
                    <span className="text-sm">{t('chatList.download')}</span>
                </button>

                {/* Zoom Hint */}
                {lightboxScale === 1 && (
                    <div className="absolute bottom-20 left-1/2 -translate-x-1/2 px-3 py-1 bg-white/10 backdrop-blur-md rounded-full text-white/50 text-[10px] pointer-events-none">
                        {t('chatList.zoomHint')}
                    </div>
                )}

                {/* Main Image */}
                {(() => {
                    const imageList = selectedMessage.attachments.filter((att: any) => att.type === 'image');
                    const currentImage = imageList[selectedImageIndex];
                    const imageUrl = currentImage ? getResourceUrl(currentImage.url) : null;
                    return currentImage && imageUrl ? (
                        <img
                            src={imageUrl}
                            alt={currentImage.name || 'Full size'}
                            className={`max-w-[90vw] max-h-[90vh] object-contain cursor-default transition-transform duration-100 ${isDraggingLightbox ? 'transition-none' : ''}`}
                            style={{
                                transform: `translate(${lightboxOffset.x}px, ${lightboxOffset.y}px) scale(${lightboxScale})`,
                                cursor: lightboxScale > 1 ? 'grab' : 'default'
                            }}
                            onMouseDown={handleLightboxMouseDown}
                            onClick={(e) => e.stopPropagation()}
                            onError={(e) => {
                                console.error('[ChatList] Failed to load lightbox image:', imageUrl);
                                (e.target as HTMLImageElement).src = '';
                                (e.target as HTMLImageElement).alt = 'Failed to load image';
                            }}
                            loading="lazy"
                        />
                    ) : (
                        <div className="text-white p-8">Image not found at index {selectedImageIndex}</div>
                    );
                })()}
            </div>,
            document.body
        )}
    </div>
  );
};
