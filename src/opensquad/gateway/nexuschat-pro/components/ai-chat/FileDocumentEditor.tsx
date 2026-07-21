/**
 * FileDocumentEditor — TipTap rich text / source / preview for workspace files.
 * Markdown files round-trip via tiptap-markdown; other text saves as plain text.
 */
import React, { useCallback, useEffect, useMemo, useRef } from 'react';
import { useEditor, EditorContent } from '@tiptap/react';
import StarterKit from '@tiptap/starter-kit';
import Underline from '@tiptap/extension-underline';
import Link from '@tiptap/extension-link';
import Placeholder from '@tiptap/extension-placeholder';
import { Markdown } from 'tiptap-markdown';
import {
  Bold,
  Italic,
  Underline as UnderlineIcon,
  Strikethrough,
  Code,
  Heading1,
  Heading2,
  Heading3,
  List,
  ListOrdered,
  Quote,
  Code2,
  Link as LinkIcon,
  Undo2,
  Redo2,
} from 'lucide-react';
import { getLangForFile, highlightLine, HLJS_THEME_CSS } from '../../utils/codeHighlight';
import { AI_MARKDOWN_CLASS, renderFencedMarkdown } from '../../utils/fencedMarkdown';

export type FileDocMode = 'rich' | 'source' | 'preview';

export interface FileDocumentEditorProps {
  fileName: string;
  value: string;
  onChange: (next: string) => void;
  mode: FileDocMode;
  isMarkdown: boolean;
  readOnly?: boolean;
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

/** Plain text → TipTap HTML (one paragraph per line). */
function plainTextToHtml(text: string): string {
  if (!text) return '<p></p>';
  return text
    .split(/\r?\n/)
    .map((line) => (line ? `<p>${escapeHtml(line)}</p>` : '<p></p>'))
    .join('');
}

function ToolBtn({
  title,
  active,
  disabled,
  onClick,
  children,
}: {
  title: string;
  active?: boolean;
  disabled?: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      title={title}
      disabled={disabled}
      onClick={onClick}
      className={`p-1 rounded border-0 bg-transparent ${
        active
          ? 'bg-black/[0.08] dark:bg-white/15 text-textMain'
          : 'text-textMuted hover:bg-black/[0.05] dark:hover:bg-white/10 hover:text-textMain'
      } disabled:opacity-40 disabled:pointer-events-none`}
    >
      {children}
    </button>
  );
}

const SourceEditor: React.FC<{
  value: string;
  onChange: (v: string) => void;
  readOnly?: boolean;
}> = ({ value, onChange, readOnly }) => (
  <textarea
    className="flex-1 min-h-0 w-full resize-none bg-[#0d1117] text-gray-200 font-mono text-[11px] leading-5 p-2 outline-none border-0"
    value={value}
    readOnly={readOnly}
    spellCheck={false}
    onChange={(e) => onChange(e.target.value)}
  />
);

const PreviewPane: React.FC<{ fileName: string; content: string; isMarkdown: boolean }> = ({
  fileName,
  content,
  isMarkdown,
}) => {
  const lang = useMemo(() => getLangForFile(fileName), [fileName]);
  const mdHtml = useMemo(
    () => (isMarkdown ? renderFencedMarkdown(content || '') : ''),
    [content, isMarkdown],
  );
  const lines = useMemo(() => (isMarkdown ? [] : (content || '').split('\n')), [content, isMarkdown]);

  if (isMarkdown) {
    return (
      <div className="flex-1 min-h-0 overflow-auto bg-[#f7f5f0] dark:bg-[#0d1117]">
        <style>{HLJS_THEME_CSS}</style>
        <div
          className={`${AI_MARKDOWN_CLASS} prose prose-sm dark:prose-invert max-w-3xl mx-auto break-words px-6 py-5 text-[13px] leading-relaxed text-textMain`}
          dangerouslySetInnerHTML={{ __html: mdHtml }}
        />
      </div>
    );
  }

  return (
    <div className="flex-1 min-h-0 overflow-auto bg-[#0d1117] font-mono text-[11px] leading-5">
      <style>{HLJS_THEME_CSS}</style>
      <div className="min-w-full inline-block">
        {lines.map((line, i) => (
          <div key={i} className="flex items-start hover:bg-white/[0.03]">
            <span className="select-none w-10 shrink-0 text-right pr-2 text-gray-600 tabular-nums text-[10px] border-r border-gray-800">
              {i + 1}
            </span>
            <span
              className="flex-1 min-w-0 whitespace-pre-wrap break-words pl-2 text-gray-200"
              dangerouslySetInnerHTML={{ __html: highlightLine(line, lang) }}
            />
          </div>
        ))}
      </div>
    </div>
  );
};

export const FileDocumentEditor: React.FC<FileDocumentEditorProps> = ({
  fileName,
  value,
  onChange,
  mode,
  isMarkdown,
  readOnly = false,
}) => {
  const extensions = useMemo(() => {
    const base = [
      StarterKit.configure({
        heading: { levels: [1, 2, 3] },
        codeBlock: { HTMLAttributes: { class: 'rounded bg-black/10 dark:bg-white/10 p-2 font-mono text-[12px]' } },
      }),
      Underline,
      Link.configure({
        openOnClick: false,
        HTMLAttributes: { class: 'text-sky-600 underline' },
      }),
      Placeholder.configure({
        placeholder: isMarkdown ? '开始撰写 Markdown…' : '开始编辑…',
      }),
    ];
    if (isMarkdown) {
      base.push(
        Markdown.configure({
          html: false,
          transformPastedText: true,
          transformCopiedText: true,
        }) as never,
      );
    }
    return base;
  }, [isMarkdown]);

  const serialize = useCallback(
    (editor: NonNullable<ReturnType<typeof useEditor>>) => {
      if (isMarkdown) {
        const md = (editor.storage as { markdown?: { getMarkdown?: () => string } }).markdown
          ?.getMarkdown?.();
        return typeof md === 'string' ? md : editor.getText({ blockSeparator: '\n' });
      }
      return editor.getText({ blockSeparator: '\n' });
    },
    [isMarkdown],
  );

  const lastPushedRef = useRef<string | null>(null);

  const editor = useEditor(
    {
      extensions,
      content: isMarkdown ? value || '' : plainTextToHtml(value || ''),
      editable: !readOnly && mode === 'rich',
      editorProps: {
        attributes: {
          class:
            'prose prose-sm dark:prose-invert max-w-3xl mx-auto min-h-full px-6 py-5 outline-none text-[13px] leading-relaxed text-textMain focus:outline-none',
        },
      },
      onUpdate: ({ editor: ed }) => {
        if (readOnly || mode !== 'rich') return;
        const next = serialize(ed);
        lastPushedRef.current = next;
        onChange(next);
      },
    },
    [isMarkdown, extensions],
  );

  // Remount via parent key on file change. Sync when draft is reset externally.
  useEffect(() => {
    if (!editor || mode !== 'rich') return;
    if (lastPushedRef.current === value) return;
    const current = serialize(editor);
    if (current === value) {
      lastPushedRef.current = value;
      return;
    }
    editor.commands.setContent(isMarkdown ? value || '' : plainTextToHtml(value || ''), {
      emitUpdate: false,
    });
    lastPushedRef.current = value;
  }, [value, editor, mode, isMarkdown, serialize]);

  useEffect(() => {
    if (!editor) return;
    editor.setEditable(!readOnly && mode === 'rich');
  }, [editor, readOnly, mode]);

  if (mode === 'preview') {
    return <PreviewPane fileName={fileName} content={value} isMarkdown={isMarkdown} />;
  }

  if (mode === 'source') {
    return (
      <div className="flex-1 min-h-0 flex flex-col">
        <SourceEditor value={value} onChange={onChange} readOnly={readOnly} />
      </div>
    );
  }

  // rich
  return (
    <div className="flex-1 min-h-0 flex flex-col bg-[#f7f5f0] dark:bg-[#161b22]">
      {!readOnly ? (
        <div className="flex-shrink-0 flex flex-wrap items-center gap-0.5 px-2 py-1.5 border-b border-border/70 bg-panel">
          <ToolBtn title="撤销" disabled={!editor?.can().undo()} onClick={() => editor?.chain().focus().undo().run()}>
            <Undo2 size={14} />
          </ToolBtn>
          <ToolBtn title="重做" disabled={!editor?.can().redo()} onClick={() => editor?.chain().focus().redo().run()}>
            <Redo2 size={14} />
          </ToolBtn>
          <span className="w-px h-4 bg-border/80 mx-0.5" />
          <ToolBtn
            title="加粗"
            active={!!editor?.isActive('bold')}
            onClick={() => editor?.chain().focus().toggleBold().run()}
          >
            <Bold size={14} />
          </ToolBtn>
          <ToolBtn
            title="斜体"
            active={!!editor?.isActive('italic')}
            onClick={() => editor?.chain().focus().toggleItalic().run()}
          >
            <Italic size={14} />
          </ToolBtn>
          <ToolBtn
            title="下划线"
            active={!!editor?.isActive('underline')}
            onClick={() => editor?.chain().focus().toggleUnderline().run()}
          >
            <UnderlineIcon size={14} />
          </ToolBtn>
          <ToolBtn
            title="删除线"
            active={!!editor?.isActive('strike')}
            onClick={() => editor?.chain().focus().toggleStrike().run()}
          >
            <Strikethrough size={14} />
          </ToolBtn>
          <ToolBtn
            title="行内代码"
            active={!!editor?.isActive('code')}
            onClick={() => editor?.chain().focus().toggleCode().run()}
          >
            <Code size={14} />
          </ToolBtn>
          <span className="w-px h-4 bg-border/80 mx-0.5" />
          <ToolBtn
            title="标题 1"
            active={!!editor?.isActive('heading', { level: 1 })}
            onClick={() => editor?.chain().focus().toggleHeading({ level: 1 }).run()}
          >
            <Heading1 size={14} />
          </ToolBtn>
          <ToolBtn
            title="标题 2"
            active={!!editor?.isActive('heading', { level: 2 })}
            onClick={() => editor?.chain().focus().toggleHeading({ level: 2 }).run()}
          >
            <Heading2 size={14} />
          </ToolBtn>
          <ToolBtn
            title="标题 3"
            active={!!editor?.isActive('heading', { level: 3 })}
            onClick={() => editor?.chain().focus().toggleHeading({ level: 3 }).run()}
          >
            <Heading3 size={14} />
          </ToolBtn>
          <span className="w-px h-4 bg-border/80 mx-0.5" />
          <ToolBtn
            title="无序列表"
            active={!!editor?.isActive('bulletList')}
            onClick={() => editor?.chain().focus().toggleBulletList().run()}
          >
            <List size={14} />
          </ToolBtn>
          <ToolBtn
            title="有序列表"
            active={!!editor?.isActive('orderedList')}
            onClick={() => editor?.chain().focus().toggleOrderedList().run()}
          >
            <ListOrdered size={14} />
          </ToolBtn>
          <ToolBtn
            title="引用"
            active={!!editor?.isActive('blockquote')}
            onClick={() => editor?.chain().focus().toggleBlockquote().run()}
          >
            <Quote size={14} />
          </ToolBtn>
          <ToolBtn
            title="代码块"
            active={!!editor?.isActive('codeBlock')}
            onClick={() => editor?.chain().focus().toggleCodeBlock().run()}
          >
            <Code2 size={14} />
          </ToolBtn>
          <ToolBtn
            title="链接"
            active={!!editor?.isActive('link')}
            onClick={() => {
              if (!editor) return;
              if (editor.isActive('link')) {
                editor.chain().focus().unsetLink().run();
                return;
              }
              const prev = editor.getAttributes('link').href as string | undefined;
              const url = window.prompt('链接 URL', prev || 'https://');
              if (!url) return;
              editor.chain().focus().extendMarkRange('link').setLink({ href: url }).run();
            }}
          >
            <LinkIcon size={14} />
          </ToolBtn>
        </div>
      ) : null}
      <div className="flex-1 min-h-0 overflow-auto">
        <EditorContent editor={editor} className="min-h-full" />
      </div>
    </div>
  );
};
