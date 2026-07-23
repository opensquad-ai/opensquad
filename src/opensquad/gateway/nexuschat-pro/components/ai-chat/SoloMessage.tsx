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
  /** DOM id fragment for user-message nav jump targets */
  anchorId?: string;
  /** Agent id for message TTS */
  agentId?: string;
  canWithdraw?: boolean;
  onWithdraw?: () => void;
}

export const SoloMessage: React.FC<SoloMessageProps> = React.memo((props) => (
  <MessageBubble {...props} variant="solo" />
));
