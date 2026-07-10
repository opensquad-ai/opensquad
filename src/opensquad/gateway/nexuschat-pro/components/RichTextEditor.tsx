import React, { useState, useRef, useEffect } from 'react';
import { Bold, Italic, Link, List, Code, Smile, Eye, EyeOff } from 'lucide-react';
import { User } from '../types';
import { parse } from 'marked';
import { AvatarImg } from './AvatarImg';

interface RichTextEditorProps {
  value: string;
  onChange: (val: string) => void;
  onSend: () => void;
  onAddAI: () => void;
  placeholder?: string;
  groupMembers?: User[];
}

export const RichTextEditor: React.FC<RichTextEditorProps> = ({ value, onChange, onSend, onAddAI, placeholder, groupMembers = [] }) => {
  const [isFocused, setIsFocused] = useState(false);
  const [showMentionList, setShowMentionList] = useState(false);
  const [showPreview, setShowPreview] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

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
    if (e.key === 'Enter' && !e.shiftKey) {
      if (showMentionList) {
         e.preventDefault();
         setShowMentionList(false);
         return;
      }
      e.preventDefault();
      onSend();
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
      const newValue = value.slice(0, -1) + `@${userName} `; // Replace trailing @
      onChange(newValue);
      setShowMentionList(false);
      textareaRef.current?.focus();
  };

  const insertFormat = (format: string) => {
     onChange(value + format);
     textareaRef.current?.focus();
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
    <div className={`flex flex-col border rounded-xl bg-panel transition-colors duration-200 relative ${isFocused ? 'border-primary ring-1 ring-primary' : 'border-border'}`}>

      {/* Mention Popup */}
      {showMentionList && groupMembers.length > 0 && (
          <div className="absolute bottom-full left-0 mb-2 w-48 bg-panel border border-border rounded-lg shadow-xl z-50 overflow-hidden animate-in fade-in slide-in-from-bottom-2">
              <div className="bg-bgLight px-3 py-2 text-xs font-bold text-textMuted border-b border-border">Mention Member</div>
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

      {/* Toolbar */}
      <div className="flex items-center justify-between p-2 border-b border-border bg-bgLight rounded-t-xl">
        <div className="flex items-center gap-2">
          <button onClick={() => insertFormat('**bold**')} className="p-1.5 hover:bg-border rounded text-textMuted" title="Bold"><Bold size={16} /></button>
          <button onClick={() => insertFormat('*italic*')} className="p-1.5 hover:bg-border rounded text-textMuted" title="Italic"><Italic size={16} /></button>
          <button onClick={() => insertFormat('- ')} className="p-1.5 hover:bg-border rounded text-textMuted" title="List"><List size={16} /></button>
          <button onClick={() => insertFormat('`code`')} className="p-1.5 hover:bg-border rounded text-textMuted" title="Code"><Code size={16} /></button>
          <div className="h-4 w-px bg-border mx-1"></div>
          <button
            onClick={onAddAI}
            className="flex items-center gap-1 px-2 py-1 bg-purple-100 text-purple-700 hover:bg-purple-200 rounded text-xs font-semibold transition-colors"
          >
            <span>@</span>
          </button>
        </div>

        {/* Preview Toggle */}
        <button
          onClick={() => setShowPreview(!showPreview)}
          className={`flex items-center gap-1 px-2 py-1 rounded text-xs font-semibold transition-colors ${
            showPreview ? 'bg-primary text-white' : 'bg-border text-textMuted hover:bg-border'
          }`}
          title={showPreview ? 'Hide Preview' : 'Show Preview'}
        >
          {showPreview ? <EyeOff size={14} /> : <Eye size={14} />}
          <span>{showPreview ? 'Hide' : 'Preview'}</span>
        </button>
      </div>

      {/* Live Preview */}
      {showPreview && value.trim() && (
        <div className="border-b border-border bg-bgLight/50">
          <div className="px-3 py-2 text-xs text-textMuted font-medium border-b border-border/50">
            Preview
          </div>
          <div
            className="p-3 prose prose-sm max-w-none prose-p:my-0.5 prose-ul:my-1 prose-ol:my-1 prose-li:my-0 prose-headings:my-1 prose-code:bg-border prose-code:px-1 prose-code:py-0.5 prose-code:rounded prose-code:text-sm prose-pre:bg-gray-800 prose-pre:text-white prose-pre:p-3 prose-pre:rounded-lg prose-blockquote:border-l-4 prose-blockquote:border-primary prose-blockquote:pl-3 prose-blockquote:italic prose-blockquote:text-textMuted break-words min-h-[40px] max-h-[150px] overflow-y-auto"
            dangerouslySetInnerHTML={{ __html: parseContent(value) }}
          />
        </div>
      )}

      <textarea
        ref={textareaRef}
        value={value}
        onChange={handleChange}
        onKeyDown={handleKeyDown}
        onFocus={() => setIsFocused(true)}
        onBlur={() => {
            setIsFocused(false);
            // Delay hide to allow click
            setTimeout(() => setShowMentionList(false), 200);
        }}
        placeholder={placeholder}
        className="w-full p-3 min-h-[80px] max-h-[200px] resize-none focus:outline-none bg-transparent text-sm leading-relaxed"
      />

      <div className="flex justify-between items-center p-2">
         <button className="text-textMuted hover:text-textMuted p-1"><Smile size={20} /></button>
         <span className="text-xs text-textMuted">Enter to send, Shift+Enter for new line</span>
      </div>
    </div>
  );
};
