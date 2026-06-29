import React, { useMemo, useState } from 'react';
import { Bot } from 'lucide-react';
import { marked } from 'marked';
import { SERVER_BASE_URL } from '../../services/api';

interface StreamingMessageProps {
  content: string;
  isComplete?: boolean;
  avatarSrc?: string;
}

export const StreamingMessage: React.FC<StreamingMessageProps> = ({ 
  content, 
  isComplete, 
  avatarSrc
}) => {
  const [avatarError, setAvatarError] = useState(false);

  // 清理系统标签（<title> 等），直接展示其余内容
  const visibleContent = useMemo(() => {
    if (!content) return '';
    return content.replace(/<title>.*?<\/title>/gs, '');
  }, [content]);

  const resolvedAvatar = useMemo(() => {
    if (!avatarSrc || avatarError) return null;
    if (avatarSrc.startsWith('http')) return avatarSrc;
    return `${SERVER_BASE_URL}${avatarSrc.startsWith('/') ? avatarSrc : '/' + avatarSrc}`;
  }, [avatarSrc, avatarError]);

  // 渲染 Markdown
  const renderedHtml = useMemo(() => {
    if (!visibleContent) return '';
    try {
      return marked.parse(visibleContent, { breaks: true }) as string;
    } catch {
      return visibleContent;
    }
  }, [visibleContent]);

  if (!visibleContent) return null;

  return (
    <div className="flex gap-2 mb-4 flex-row w-full max-w-full">
      {/* Avatar */}
      <div className="w-7 h-7 rounded-full flex items-center justify-center flex-shrink-0 overflow-hidden bg-emerald-500/20 mt-1">
        {resolvedAvatar
          ? <img src={resolvedAvatar} alt="" className="w-full h-full object-cover" onError={() => setAvatarError(true)} />
          : <Bot size={14} className="text-emerald-500" />
        }
      </div>

      {/* Content Area */}
      <div className="flex flex-col gap-2 max-w-[85%] sm:max-w-[80%] min-w-0">
        {/* Main Response Bubble */}
        <div className="bg-chatBubbleOther text-textMain rounded-xl rounded-tl-sm border border-border px-3 py-2 sm:px-3.5 sm:py-2.5 text-sm leading-relaxed overflow-hidden shadow-sm">
          <div
            className="prose prose-sm prose-invert max-w-none break-words overflow-x-auto ai-markdown"
            dangerouslySetInnerHTML={{ __html: renderedHtml }}
          />
          {!isComplete && (
            <span className="inline-block w-1.5 h-4 bg-primary/60 animate-pulse ml-0.5 align-middle" />
          )}
        </div>
      </div>
    </div>
  );
};
