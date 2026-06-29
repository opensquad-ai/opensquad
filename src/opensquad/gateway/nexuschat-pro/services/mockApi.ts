import { Message, MessageType } from '../types';

export const fetchHistory = async (groupId: string, beforeTimestamp: number): Promise<Message[]> => {
  // Simulate network delay
  await new Promise(resolve => setTimeout(resolve, 800));
  
  const newMessages: Message[] = [];
  for(let i=0; i<10; i++) {
    newMessages.unshift({
      id: `hist_${Date.now()}_${i}`,
      senderId: ['u1', 'u2', 'u3'][Math.floor(Math.random() * 3)],
      content: `Loaded history message ${i} from before ${new Date(beforeTimestamp).toLocaleTimeString()}`,
      timestamp: beforeTimestamp - (1000 * 60 * 5 * (i + 1)),
      type: MessageType.TEXT
    });
  }
  return newMessages;
};

export const uploadFile = async (file: File): Promise<string> => {
    await new Promise(resolve => setTimeout(resolve, 1500));
    return URL.createObjectURL(file);
}