import React, { useMemo, useState } from 'react';
import { Bot } from 'lucide-react';
import { SERVER_BASE_URL } from '../../services/api';
import { toAbsoluteMediaUrl } from '../../utils/image';
import { AI_MARKDOWN_CLASS, renderFencedMarkdown } from '../../utils/fencedMarkdown';

interface StreamingMessageProps {
  content: string;
  isComplete?: boolean;
  avatarSrc?: string;
  /** classic = bubble; solo = document stream */
  variant?: 'classic' | 'solo';
  senderName?: string;
}

export const StreamingMessage: React.FC<StreamingMessageProps> = ({
  content,
  isComplete,
  avatarSrc,
  variant = 'classic',
  senderName,
}) => {
  const [avatarError, setAvatarError] = useState(false);

  const visibleContent = useMemo(() => {
    if (!content) return '';
    return content.replace(/<title>.*?<\/title>/gs, '');
  }, [content]);

  const resolvedAvatar = useMemo(() => {
    if (!avatarSrc || avatarError) return null;
    return toAbsoluteMediaUrl(avatarSrc, SERVER_BASE_URL);
  }, [avatarSrc, avatarError]);

  const renderedHtml = useMemo(() => {
    if (!visibleContent) return '';
    try {
      return renderFencedMarkdown(visibleContent);
    } catch {
      return visibleContent;
    }
  }, [visibleContent]);

  if (!visibleContent) return null;

  if (variant === 'solo') {
    return (
      <div className="mb-5 w-full">
        <div className="text-[11px] font-medium text-textMuted mb-1.5">
          {senderName || 'Agent'}
        </div>
        <div className="text-sm leading-relaxed text-textMain w-full min-w-0">
          <div
            className={AI_MARKDOWN_CLASS}
            dangerouslySetInnerHTML={{ __html: renderedHtml }}
          />
          {!isComplete && (
            <span className="inline-block w-1.5 h-4 bg-primary/60 animate-pulse ml-0.5 align-middle" />
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="flex gap-2 mb-4 flex-row w-full max-w-full">
      <div className="w-7 h-7 rounded-full flex items-center justify-center flex-shrink-0 overflow-hidden bg-emerald-500/20 mt-1">
        {resolvedAvatar
          ? <img src={resolvedAvatar} alt="" className="w-full h-full object-cover" onError={() => setAvatarError(true)} />
          : <Bot size={14} className="text-emerald-500" />
        }
      </div>

      <div className="flex flex-col gap-2 max-w-[85%] sm:max-w-[80%] min-w-0">
        <div className="bg-chatBubbleOther text-textMain rounded-xl rounded-tl-sm border border-border px-3 py-2 sm:px-3.5 sm:py-2.5 text-sm leading-relaxed overflow-hidden shadow-sm">
          <div
            className={AI_MARKDOWN_CLASS}
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
