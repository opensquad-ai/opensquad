/**
 * NodePairingPanel — letting another machine into this deployment.
 *
 * The host side of pairing: show a short-lived code, answer the machines that
 * submitted it, and see (or revoke) who is already paired. A paired machine never
 * receives node_secret; it gets a token whose scopes are fixed on the backend, so
 * this panel is the whole authority story for a second machine.
 */
import React, { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Check, Copy, KeyRound, ShieldCheck, Trash2 } from 'lucide-react';

import { GATEWAY_ENDPOINT, nodesAPI } from '../services/api';
import { buildInviteString } from '../utils/invite';

interface Pending {
  id: string;
  name: string;
}

interface Peer {
  id: string;
  name: string;
  scopes: string[];
  /** When this peer's token last authenticated; null means it never has. */
  last_seen_at?: number | null;
  revoked: boolean;
}

interface Props {
  /** The gateway address the other machine should dial (from the invite panel). */
  host?: string;
  /** Group being viewed, so the invite and the code can be merged into one string. */
  groupId?: string;
}

export const NodePairingPanel: React.FC<Props> = ({ host = '', groupId = '' }) => {
  const { t } = useTranslation();
  const [code, setCode] = useState('');
  const [pending, setPending] = useState<Pending[]>([]);
  const [paired, setPaired] = useState<Peer[]>([]);
  const [busy, setBusy] = useState('');
  const [copied, setCopied] = useState(false);
  // What the peer actually pastes: pairing needs the code, and the invite is shown
  // in a different panel, so the operator used to assemble the two by hand — and
  // get it wrong. Buildable only while both halves are known.
  const codedInvite =
    code && host.trim() && groupId
      ? buildInviteString(host.trim(), groupId, {
          port: GATEWAY_ENDPOINT.port,
          secure: GATEWAY_ENDPOINT.secure,
          code,
        })
      : '';

  const load = useCallback(async () => {
    try {
      const [requests, peers] = await Promise.all([nodesAPI.listPairingRequests(), nodesAPI.listPeers()]);
      setPending(requests.requests || []);
      setPaired((peers.peers || []).filter((p) => !p.revoked));
    } catch {
      /* the panel is informational; a failure just leaves the lists as they were */
    }
  }, []);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), 5000);
    return () => window.clearInterval(timer);
  }, [load]);

  const decide = async (requestId: string, action: 'approve' | 'reject') => {
    setBusy(requestId);
    try {
      await nodesAPI.decidePairing(requestId, action);
      await load();
    } finally {
      setBusy('');
    }
  };

  return (
    <div className="mt-4 space-y-3" data-testid="node-pairing-panel">
      <div>
        <div className="mb-1 flex items-center gap-1 text-xs font-semibold uppercase tracking-wide text-textMuted">
          <KeyRound size={12} />
          {t('nodePairing.codeTitle')}
        </div>
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={() => void nodesAPI.startPairing().then((r) => setCode(r.code)).catch(() => setCode(''))}
            className="rounded-lg border border-border px-2 py-1 text-[11px] text-textMain hover:bg-primary/10"
          >
            {t('nodePairing.generate')}
          </button>
          {code ? (
            <code className="rounded-lg bg-bgLight px-2 py-1 font-mono text-[13px] tracking-widest text-textMain">
              {code}
            </code>
          ) : null}
        </div>
        <p className="mt-1 text-[11px] text-textMuted">{t('nodePairing.codeHint')}</p>
        {codedInvite ? (
          <div className="mt-2" data-testid="node-pairing-coded-invite">
            <div className="mb-1 text-[11px] text-textMuted">{t('nodePairing.codedTitle')}</div>
            <div className="flex items-center gap-1">
              <code className="min-w-0 flex-1 truncate rounded-lg border border-border bg-bgLight px-2 py-1 text-[11px] text-textMain">
                {codedInvite}
              </code>
              <button
                type="button"
                title={t('nodePairing.copy')}
                aria-label={t('nodePairing.copy')}
                onClick={() => {
                  void navigator.clipboard.writeText(codedInvite);
                  setCopied(true);
                  window.setTimeout(() => setCopied(false), 1200);
                }}
                className="shrink-0 rounded-md border border-border px-1.5 py-1 text-textMuted hover:text-textMain"
              >
                {copied ? <Check size={13} /> : <Copy size={13} />}
              </button>
            </div>
            <p className="mt-1 text-[11px] text-textMuted">{t('nodePairing.codedHint')}</p>
          </div>
        ) : null}
      </div>

      <div>
        <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-textMuted">
          {t('nodePairing.pendingTitle')}
        </div>
        {pending.length ? (
          <div className="space-y-1" data-testid="node-pairing-requests">
            {pending.map((r) => (
              <div key={r.id} className="flex items-center gap-1.5 rounded-lg border border-border bg-bgLight px-2 py-1.5">
                <span className="min-w-0 flex-1 truncate text-[12px] text-textMain">{r.name}</span>
                <button
                  type="button"
                  disabled={busy === r.id}
                  onClick={() => void decide(r.id, 'approve')}
                  className="rounded-md bg-primary px-2 py-0.5 text-[11px] text-white disabled:opacity-50"
                >
                  {t('nodePairing.approve')}
                </button>
                <button
                  type="button"
                  disabled={busy === r.id}
                  onClick={() => void decide(r.id, 'reject')}
                  className="rounded-md border border-border px-2 py-0.5 text-[11px] text-textMain disabled:opacity-50"
                >
                  {t('nodePairing.reject')}
                </button>
              </div>
            ))}
          </div>
        ) : (
          <div className="text-[11px] text-textMuted">{t('nodePairing.empty')}</div>
        )}
      </div>

      {paired.length ? (
        <div>
          <div className="mb-1 flex items-center gap-1 text-xs font-semibold uppercase tracking-wide text-textMuted">
            <ShieldCheck size={12} />
            {t('nodePairing.pairedTitle')}
          </div>
          <div className="space-y-1" data-testid="node-pairing-peers">
            {paired.map((p) => (
              <div key={p.id} className="flex items-center gap-1.5 rounded-lg border border-border bg-bgLight px-2 py-1.5">
                <span className="min-w-0 flex-1 truncate text-[12px] text-textMain">{p.name}</span>
                {/* Whether this token ever authenticated is what tells a live pairing from a dead
                    one — and the reason it is shown here. Three peers with the same name and no
                    way to tell them apart is how a working one got unpaired by mistake, and an
                    unpaired token cannot be restored: the machine has to pair again. */}
                <span
                  data-testid="peer-last-seen"
                  className={`shrink-0 text-[10px] ${p.last_seen_at ? 'text-textMuted' : 'text-amber-500'}`}
                  title={
                    p.last_seen_at
                      ? new Date(p.last_seen_at * 1000).toLocaleString()
                      : t('nodePairing.neverUsedHint')
                  }
                >
                  {p.last_seen_at ? t('nodePairing.lastSeen') : t('nodePairing.neverUsed')}
                </span>
                <button
                  type="button"
                  title={t('nodePairing.revoke')}
                  aria-label={t('nodePairing.revoke')}
                  onClick={() => void nodesAPI.revokePeer(p.id).then(load).catch(() => undefined)}
                  className="shrink-0 rounded-md p-0.5 text-textMuted hover:text-rose-500"
                >
                  <Trash2 size={12} />
                </button>
              </div>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
};

export default NodePairingPanel;
