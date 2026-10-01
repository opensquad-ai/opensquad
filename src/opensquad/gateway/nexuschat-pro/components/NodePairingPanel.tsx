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
import { KeyRound, ShieldCheck, Trash2 } from 'lucide-react';

import { nodesAPI } from '../services/api';

interface Pending {
  id: string;
  name: string;
}

interface Peer {
  id: string;
  name: string;
  scopes: string[];
  revoked: boolean;
}

export const NodePairingPanel: React.FC = () => {
  const { t } = useTranslation();
  const [code, setCode] = useState('');
  const [pending, setPending] = useState<Pending[]>([]);
  const [paired, setPaired] = useState<Peer[]>([]);
  const [busy, setBusy] = useState('');

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
