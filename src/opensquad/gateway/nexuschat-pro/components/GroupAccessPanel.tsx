/**
 * GroupAccessPanel — inviting another machine in, and letting one in.
 *
 * Two things an operator needs on the group page: the invite string for this
 * group (the other machine pastes it into pair_with_node / join_by_invite) and,
 * for private groups, the queue of machines waiting to be let in. Both are
 * backend-driven; this only presents them.
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Check, Copy, UserPlus } from 'lucide-react';

import { GATEWAY_ENDPOINT, groupsAPI, nodesAPI } from '../services/api';
import { NodePairingPanel } from './NodePairingPanel';

/** An address another machine cannot dial — and the one a browser page is usually served from. */
const isLoopbackHost = (value: string): boolean =>
  /^(localhost|127\.|0\.0\.0\.0|\[?::1\]?)/i.test(String(value || '').trim());

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

  // The other machine needs THIS deployment's gateway, not whatever address this browser happens to
  // use: under Vite DEV the page sits on :5173 while agents talk to the gateway on :9555. And the
  // page is usually served from a loopback address the peer cannot reach, while the browser cannot
  // read this host's interfaces — so the address always comes from the backend, which can.
  const [inviteHost, setInviteHost] = useState(GATEWAY_ENDPOINT.host);
  const hostIsLoopback = isLoopbackHost(inviteHost);

  useEffect(() => {
    let alive = true;
    void nodesAPI
      .localAddresses()
      .then((res) => {
        const best = (res?.addresses || [])
          .map((address) => String(address || '').trim())
          .find((address) => address && !isLoopbackHost(address));
        if (alive && best) setInviteHost(best);
      })
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, []);

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
        {/* The host is detected, not typed: only the paired invite below is needed, and the one
            thing worth saying is when detection could not find a reachable address. */}
        {hostIsLoopback ? (
          <p className="text-[11px] text-amber-600" data-testid="group-invite-loopback">
            {t('groupAccess.loopbackWarning')}
          </p>
        ) : null}
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
      <NodePairingPanel host={inviteHost} groupId={group.id} />
    </div>
  );
};

export default GroupAccessPanel;
