/**
 * GroupAccessPanel — inviting another machine in, and letting one in.
 *
 * Two things an operator needs on the group page: the invite string for this
 * group (the other machine pastes it into pair_with_node / join_by_invite) and,
 * for private groups, the queue of machines waiting to be let in. Both are
 * backend-driven; this only presents them.
 */
import React, { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Check, Copy, UserPlus } from 'lucide-react';

import { groupsAPI } from '../services/api';
import { buildInviteString, DEFAULT_PORT } from '../utils/invite';
import { NodePairingPanel } from './NodePairingPanel';

interface Props {
  group: { id: string; name: string; isPrivate?: boolean };
  /** Only the group's owner may decide requests; the backend enforces it too. */
  isOwner: boolean;
}

export const GroupAccessPanel: React.FC<Props> = ({ group, isOwner }) => {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);
  const [requests, setRequests] = useState<{ id: string; user_id: string; message: string }[]>([]);
  const [busy, setBusy] = useState('');

  const invite = buildInviteString(window.location.hostname || '127.0.0.1', group.id, {
    port: Number(window.location.port) || DEFAULT_PORT,
    secure: window.location.protocol === 'https:',
  });

  const load = useCallback(async () => {
    if (!group.isPrivate) return;
    try {
      const res = await groupsAPI.listJoinRequests(group.id);
      setRequests(res.requests || []);
    } catch {
      setRequests([]);
    }
  }, [group.id, group.isPrivate]);

  useEffect(() => {
    void load();
  }, [load]);

  const decide = async (requestId: string, action: 'approve' | 'reject') => {
    setBusy(requestId);
    try {
      await groupsAPI.decideJoinRequest(group.id, requestId, action);
      await load();
    } finally {
      setBusy('');
    }
  };

  return (
    <div className="mt-4 space-y-3" data-testid="group-access-panel">
      <div>
        <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-textMuted">
          {t('groupAccess.inviteTitle')}
        </div>
        <div className="flex items-center gap-1">
          <code className="min-w-0 flex-1 truncate rounded-lg border border-border bg-bgLight px-2 py-1 text-[11px] text-textMain">
            {invite}
          </code>
          <button
            type="button"
            className="shrink-0 rounded-lg border border-border p-1.5 text-textMuted hover:text-textMain"
            title={t('common.copy')}
            aria-label={t('common.copy')}
            onClick={() => {
              void navigator.clipboard?.writeText(invite).catch(() => undefined);
              setCopied(true);
              window.setTimeout(() => setCopied(false), 1200);
            }}
          >
            {copied ? <Check size={13} /> : <Copy size={13} />}
          </button>
        </div>
        <p className="mt-1 text-[11px] text-textMuted">{t('groupAccess.inviteHint')}</p>
      </div>

      {group.isPrivate ? (
        <div>
          <div className="mb-1 flex items-center gap-1 text-xs font-semibold uppercase tracking-wide text-textMuted">
            <UserPlus size={12} />
            {t('groupAccess.requestsTitle')}
            {requests.length ? <span className="font-normal">({requests.length})</span> : null}
          </div>
          {requests.length ? (
            <div className="space-y-1" data-testid="group-join-requests">
              {requests.map((r) => (
                <div key={r.id} className="rounded-lg border border-border bg-bgLight px-2 py-1.5">
                  <div className="truncate text-[12px] text-textMain">{r.user_id}</div>
                  {r.message ? <div className="truncate text-[11px] text-textMuted">{r.message}</div> : null}
                  {isOwner ? (
                    <div className="mt-1 flex gap-1.5">
                      <button
                        type="button"
                        disabled={busy === r.id}
                        onClick={() => void decide(r.id, 'approve')}
                        className="rounded-md bg-primary px-2 py-0.5 text-[11px] text-white disabled:opacity-50"
                      >
                        {t('groupAccess.approve')}
                      </button>
                      <button
                        type="button"
                        disabled={busy === r.id}
                        onClick={() => void decide(r.id, 'reject')}
                        className="rounded-md border border-border px-2 py-0.5 text-[11px] text-textMain disabled:opacity-50"
                      >
                        {t('groupAccess.reject')}
                      </button>
                    </div>
                  ) : (
                    <div className="mt-0.5 text-[10px] text-textMuted">{t('groupAccess.ownerOnly')}</div>
                  )}
                </div>
              ))}
            </div>
          ) : (
            <div className="text-[11px] text-textMuted">{t('groupAccess.requestsEmpty')}</div>
          )}
        </div>
      ) : null}

      {/* The other half of "let another machine in": the code it submits and the
          machines already paired with this deployment. */}
      <NodePairingPanel />
    </div>
  );
};

export default GroupAccessPanel;
