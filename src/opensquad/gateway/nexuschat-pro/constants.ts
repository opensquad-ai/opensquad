import { Group, Message, MessageType, User } from './types';

export const CURRENT_USER: User = {
  id: 'u1',
  name: 'Alex Developer',
  avatar: 'https://picsum.photos/id/1012/200/200',
  status: 'online',
};

export const USERS: Record<string, User> = {
  'u1': CURRENT_USER,
  'u2': { id: 'u2', name: 'Sarah Designer', avatar: 'https://picsum.photos/id/1027/200/200', status: 'busy' },
  'u3': { id: 'u3', name: 'Mike Manager', avatar: 'https://picsum.photos/id/1005/200/200', status: 'offline' },
  'u4': { id: 'u4', name: 'ai', avatar: 'https://picsum.photos/id/237/200/200', status: 'online' },
};

export const INITIAL_GROUPS: Group[] = [
  {
    id: 'g1',
    name: 'Frontend Team',
    avatar: 'https://picsum.photos/id/1/200/200',
    description: 'Discussion for UI/UX and React implementation.',
    members: ['u1', 'u2', 'u3', 'u4'],
    unreadCount: 3,
    hasUnreadMention: true,
    isPrivate: false,
    createdAt: Date.now() - 10000000,
    pinnedMessageId: 'm1',
    notificationSoundEnabled: true,
  },
  {
    id: 'g2',
    name: 'Project Alpha',
    avatar: 'https://picsum.photos/id/2/200/200',
    description: 'Top secret project details.',
    members: ['u1', 'u3'],
    unreadCount: 0,
    hasUnreadMention: false,
    isPrivate: true,
    createdAt: Date.now() - 20000000,
    notificationSoundEnabled: true,
  },
  {
    id: 'g3',
    name: 'General Chat',
    avatar: 'https://picsum.photos/id/3/200/200',
    description: 'Water cooler talk.',
    members: ['u1', 'u2', 'u3', 'u4'],
    unreadCount: 12,
    hasUnreadMention: false,
    isPrivate: false,
    createdAt: Date.now() - 30000000,
    notificationSoundEnabled: false,
  }
];

const generateHistory = (count: number, groupId: string): Message[] => {
  const msgs: Message[] = [];
  for (let i = 0; i < count; i++) {
    msgs.push({
      id: `m_hist_${groupId}_${i}`,
      senderId: ['u1', 'u2', 'u3'][Math.floor(Math.random() * 3)],
      content: `This is historical message #${i} for context.`,
      timestamp: Date.now() - (1000 * 60 * 60 * 24) + (i * 60000),
      type: MessageType.TEXT,
    });
  }
  return msgs;
};

export const INITIAL_MESSAGES: Record<string, Message[]> = {
  'g1': [
    ...generateHistory(20, 'g1'),
    {
      id: 'm1',
      senderId: 'u3',
      content: 'Welcome to the new frontend channel! Please read the guidelines.',
      timestamp: Date.now() - 86400000,
      type: MessageType.TEXT,
      isPinned: true,
    },
    {
      id: 'm2',
      senderId: 'u2',
      content: 'Here are the design assets for the new dashboard.',
      timestamp: Date.now() - 3600000,
      type: MessageType.IMAGE,
      attachments: [{
        id: 'a1', name: 'dashboard_v1.png', size: '2.4MB', type: 'image', url: 'https://picsum.photos/id/20/800/600'
      }]
    },
    {
      id: 'm3',
      senderId: 'u2',
      content: 'Does this look good to everyone?',
      timestamp: Date.now() - 3500000,
      type: MessageType.TEXT,
      replyToId: 'm2'
    },
    {
      id: 'm4',
      senderId: 'u1',
      content: 'Looks great! I will start implementing the components.',
      timestamp: Date.now() - 3400000,
      type: MessageType.TEXT,
    },
    {
      id: 'm5',
      senderId: 'u3',
      content: 'Great, thanks @Alex Developer.',
      timestamp: Date.now() - 3300000,
      type: MessageType.TEXT,
      mentions: ['u1']
    }
  ],
  'g2': generateHistory(10, 'g2'),
  'g3': generateHistory(5, 'g3'),
};