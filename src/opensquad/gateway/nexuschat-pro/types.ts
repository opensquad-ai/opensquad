export enum MessageType {
  TEXT = 'TEXT',
  IMAGE = 'IMAGE',
  FILE = 'FILE',
  VIDEO = 'VIDEO',
  VOICE = 'VOICE',
  SYSTEM = 'SYSTEM'
}

export type MessageStatus = 'sending' | 'sent' | 'failed' | 'delivered';

export interface User {
  id: string;
  name: string;
  avatar: string;
  status: 'online' | 'offline' | 'busy';
}

export interface Attachment {
  id: string;
  name: string;
  size: string;
  url: string;
  type: 'image' | 'video' | 'file' | 'folder' | 'voice';
  duration?: number; // 语音消息时长（秒）
}

export interface Message {
  id: string;
  senderId: string;
  content: string; // HTML/Rich text content
  timestamp: number;
  type: MessageType;
  attachments?: Attachment[];
  status?: MessageStatus; // 消息发送状态
  replyToId?: string;
  isPinned?: boolean;
  isEdited?: boolean;
  mentions?: string[]; // User IDs mentioned in this message
  isDeleted?: boolean; // 是否已撤回
  deletedAt?: number; // 撤回时间（时间戳）
  canUndo?: boolean; // 是否可以取消撤回（2分钟内）
}

export interface Group {
  id: string;
  name: string;
  avatar: string;
  description: string;
  members: string[]; // User IDs
  pinnedMessageId?: string;
  unreadCount: number;
  hasUnreadMention: boolean; // True if the user has been mentioned and hasn't seen it
  isPrivate: boolean;
  createdAt: number;
  notificationSoundEnabled: boolean; // Controls whether sound plays on new message
}

export interface ChatState {
  activeGroupId: string | null;
  groups: Group[];
  messages: Record<string, Message[]>; // groupId -> messages
  users: Record<string, User>;
  currentUser: User;
  isRightPanelOpen: boolean;
  error?: string;  // transient error message for UI banner
  searchQuery: {
    text: string;
    userId: string | null;
    dateFrom: string | null;
    dateTo: string | null;
  };
}