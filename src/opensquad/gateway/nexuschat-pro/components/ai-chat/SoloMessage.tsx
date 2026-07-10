/**
 * SoloMessage — document-stream message for Solo UI mode (Codex / Cursor Agent style).
 * Reuses MessageBubble parsing/rendering with a non-bubble layout.
 */
import React from 'react';
import { MessageBubble, type ChatMessage } from './MessageBubble';

interface SoloMessageProps {
  message: ChatMessage;
  isStreaming?: boolean;
  senderName?: string;
  senderAvatar?: string | null;
}

export const SoloMessage: React.FC<SoloMessageProps> = (props) => (
  <MessageBubble {...props} variant="solo" />
);
