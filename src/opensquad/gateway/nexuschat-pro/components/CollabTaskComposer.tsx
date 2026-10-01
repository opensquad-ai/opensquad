import React, { useCallback, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Paperclip, Send, X } from 'lucide-react';

import { collabBoardAPI, uploadAPI } from '../services/api';

interface PendingFile {
  url: string;
  name: string;
  size: string;
  type: string;
}

interface Props {
  collabId: string;
  /** Called after a successful post so the window can refresh the thread. */
  onSent?: () => void;
}

/**
 * The user's side of a task thread.
 *
 * Messages go to the task itself — the board plus the agents working on it — and
 * never to a group. That is what keeps the group readable while a task stays a
 * private, temporary conversation you enter from its card.
 */
export const CollabTaskComposer: React.FC<Props> = ({ collabId, onSent }) => {
  const { t } = useTranslation();
  const [draft, setDraft] = useState('');
  const [pending, setPending] = useState<PendingFile[]>([]);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const pickFiles = useCallback(async (files: FileList | null) => {
    if (!files?.length) return;
    setBusy(true);
    setFailed(false);
    try {
      const added: PendingFile[] = [];
      for (const file of Array.from(files)) {
        const res = (await uploadAPI.uploadFile(file)) as { url?: string; name?: string; size?: string } | null;
        const url = String(res?.url || '');
        if (!url) continue;
        added.push({
          url,
          name: String(res?.name || file.name),
          size: String(res?.size || ''),
          type: file.type.startsWith('image/') ? 'image' : 'file',
        });
      }
      if (added.length) setPending((prev) => [...prev, ...added]);
    } catch {
      setFailed(true);
    } finally {
      setBusy(false);
    }
  }, []);

  const send = useCallback(async () => {
    const text = draft.trim();
    if ((!text && !pending.length) || busy) return;
    setBusy(true);
    setFailed(false);
    try {
      await collabBoardAPI.postTaskMessage(collabId, text, pending);
      setDraft('');
      setPending([]);
      onSent?.();
    } catch {
      setFailed(true);
    } finally {
      setBusy(false);
    }
  }, [busy, collabId, draft, onSent, pending]);

  return (
    <div className="shrink-0 border-t border-border bg-panel px-3 py-2" data-testid="collab-task-composer">
      {pending.length ? (
        <div className="mb-1.5 flex flex-wrap gap-1.5">
          {pending.map((f) => (
            <span
              key={f.url}
              className="inline-flex items-center gap-1 rounded-lg border border-border bg-bgLight px-2 py-0.5 text-[11px] text-textMuted"
            >
              {f.name}
              <button
                type="button"
                className="border-0 bg-transparent p-0 text-textMuted hover:text-textMain"
                aria-label={t('common.cancel')}
                onClick={() => setPending((prev) => prev.filter((p) => p.url !== f.url))}
              >
                <X size={11} />
              </button>
            </span>
          ))}
        </div>
      ) : null}
      <div className="flex items-end gap-2">
        <button
          type="button"
          className="shrink-0 rounded-lg border border-border p-1.5 text-textMuted hover:bg-primary/10 hover:text-textMain"
          title={t('collabTask.attach')}
          aria-label={t('collabTask.attach')}
          onClick={() => fileRef.current?.click()}
        >
          <Paperclip size={15} />
        </button>
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault();
              void send();
            }
          }}
          rows={1}
          placeholder={t('collabTask.messagePlaceholder')}
          data-testid="collab-task-input"
          className="max-h-32 min-h-[34px] flex-1 resize-y rounded-lg border border-border bg-bgLight px-2.5 py-1.5 text-[13px] text-textMain outline-none focus:border-primary/50"
        />
        <button
          type="button"
          disabled={busy || (!draft.trim() && !pending.length)}
          onClick={() => void send()}
          data-testid="collab-task-send"
          className="inline-flex shrink-0 items-center gap-1 rounded-lg bg-primary px-3 py-1.5 text-[12px] text-white hover:opacity-90 disabled:opacity-40"
        >
          <Send size={13} />
          {t('collabTask.send')}
        </button>
      </div>
      {failed ? <div className="mt-1 text-[11px] text-rose-500">{t('collabTask.sendFailed')}</div> : null}
      <input
        ref={fileRef}
        type="file"
        multiple
        className="hidden"
        data-testid="collab-task-file-input"
        onChange={(e) => {
          void pickFiles(e.target.files);
          e.target.value = '';
        }}
      />
    </div>
  );
};

export default CollabTaskComposer;
