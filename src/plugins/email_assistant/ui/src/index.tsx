import React, { useState, useEffect, useCallback } from 'react';
import { createRoot } from 'react-dom/client';
import {
  Mail, RefreshCw, Loader2, AlertCircle, Send,
  ChevronLeft, Search, Inbox, X,
} from 'lucide-react';

// ---------------------------------------------------------------------------
// API helpers
// ---------------------------------------------------------------------------

const PLUGIN_ID = 'email_assistant';

function authHeaders(): Record<string, string> {
  const token = localStorage.getItem('chat_token');
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function apiGet(params: Record<string, string>) {
  const qs = new URLSearchParams(params).toString();
  const resp = await fetch(`/api/ai-web/admin/plugins/${PLUGIN_ID}/data?${qs}`, {
    headers: authHeaders(),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.error || `HTTP ${resp.status}`);
  }
  return resp.json();
}

async function apiAction(action: string, data: Record<string, any>) {
  const resp = await fetch(`/api/ai-web/admin/plugins/${PLUGIN_ID}/action`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({ action, data }),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.error || `HTTP ${resp.status}`);
  }
  return resp.json();
}

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface EmailSummary {
  id: number;
  msg_id: string;
  subject: string;
  sender: string;
  date_str: string;
  received_at: number;
}

interface EmailDetail extends EmailSummary {
  recipients: string;
  body: string;
}

// ---------------------------------------------------------------------------
// Compose modal
// ---------------------------------------------------------------------------

const ComposeModal: React.FC<{ onClose: () => void }> = ({ onClose }) => {
  const [to, setTo]         = useState('');
  const [subject, setSubject] = useState('');
  const [body, setBody]     = useState('');
  const [sending, setSending] = useState(false);
  const [sent, setSent]     = useState(false);
  const [error, setError]   = useState('');

  const handleSend = async () => {
    if (!to.trim()) { setError('Recipient required'); return; }
    setSending(true);
    setError('');
    try {
      const res = await apiAction('send_email', { to: to.trim(), subject, body });
      if (res.error) throw new Error(res.error);
      setSent(true);
      setTimeout(onClose, 1200);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setSending(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
      onClick={e => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div className="bg-panel border border-border rounded-xl shadow-xl w-full max-w-lg mx-4 p-5 flex flex-col gap-3">
        <div className="flex items-center justify-between">
          <span className="font-semibold text-textMain flex items-center gap-2">
            <Send size={16} /> New Email
          </span>
          <button onClick={onClose} className="text-textMuted hover:text-textMain">
            <X size={18} />
          </button>
        </div>

        {error && <p className="text-xs text-red-400">{error}</p>}
        {sent  && <p className="text-xs text-emerald-400">Sent!</p>}

        <input
          className="bg-surface border border-border rounded-lg px-3 py-1.5 text-sm text-textMain outline-none focus:border-primary"
          placeholder="To"
          value={to}
          onChange={e => setTo(e.target.value)}
        />
        <input
          className="bg-surface border border-border rounded-lg px-3 py-1.5 text-sm text-textMain outline-none focus:border-primary"
          placeholder="Subject"
          value={subject}
          onChange={e => setSubject(e.target.value)}
        />
        <textarea
          className="bg-surface border border-border rounded-lg px-3 py-1.5 text-sm text-textMain outline-none focus:border-primary resize-none h-40"
          placeholder="Body"
          value={body}
          onChange={e => setBody(e.target.value)}
        />
        <div className="flex justify-end">
          <button
            onClick={handleSend}
            disabled={sending || sent}
            className="inline-flex items-center gap-2 px-4 py-1.5 rounded-lg bg-primary/15 text-primary text-sm font-medium hover:bg-primary/25 disabled:opacity-50 transition-colors"
          >
            {sending ? <Loader2 size={14} className="animate-spin" /> : <Send size={14} />}
            {sent ? 'Sent' : 'Send'}
          </button>
        </div>
      </div>
    </div>
  );
};

// ---------------------------------------------------------------------------
// Email detail view
// ---------------------------------------------------------------------------

const EmailDetailView: React.FC<{ emailId: number; onBack: () => void }> = ({ emailId, onBack }) => {
  const [email, setEmail]   = useState<EmailDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError]   = useState('');

  useEffect(() => {
    (async () => {
      try {
        const data = await apiGet({ action: 'read', id: String(emailId) });
        if (data.error) throw new Error(data.error);
        setEmail(data as EmailDetail);
      } catch (e: any) {
        setError(e.message);
      } finally {
        setLoading(false);
      }
    })();
  }, [emailId]);

  if (loading) return (
    <div className="flex items-center justify-center py-12">
      <Loader2 size={20} className="animate-spin text-textMuted" />
    </div>
  );
  if (error) return <p className="text-red-400 text-sm py-4">{error}</p>;
  if (!email) return null;

  return (
    <div className="flex flex-col gap-3">
      <button
        onClick={onBack}
        className="flex items-center gap-1 text-xs text-textMuted hover:text-primary transition-colors"
      >
        <ChevronLeft size={14} /> Back to inbox
      </button>
      <div className="bg-panel border border-border rounded-xl p-4 flex flex-col gap-2">
        <h2 className="text-sm font-bold text-textMain">{email.subject || '(no subject)'}</h2>
        <div className="text-xs text-textMuted flex flex-col gap-0.5">
          <span>From: {email.sender}</span>
          <span>To: {email.recipients}</span>
          <span>Date: {email.date_str}</span>
        </div>
        <hr className="border-border" />
        <pre className="text-xs text-textMain whitespace-pre-wrap font-mono leading-relaxed max-h-[50vh] overflow-y-auto">
          {email.body || '(empty body)'}
        </pre>
      </div>
    </div>
  );
};

// ---------------------------------------------------------------------------
// Main App
// ---------------------------------------------------------------------------

const App: React.FC = () => {
  const [emails, setEmails]   = useState<EmailSummary[]>([]);
  const [total, setTotal]     = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError]     = useState('');
  const [page, setPage]       = useState(0);
  const [search, setSearch]   = useState('');
  const [searchInput, setSearchInput] = useState('');
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [compose, setCompose] = useState(false);

  const LIMIT = 30;

  const fetchEmails = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      if (search.trim()) {
        const data = await apiGet({ action: 'search', q: search, limit: String(LIMIT) });
        setEmails(data.emails || []);
        setTotal(data.count || 0);
      } else {
        const data = await apiGet({ action: 'list', limit: String(LIMIT), offset: String(page * LIMIT) });
        setEmails(data.emails || []);
        setTotal(data.total || 0);
      }
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [page, search]);

  useEffect(() => { fetchEmails(); }, [fetchEmails]);

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault();
    setSearch(searchInput);
    setPage(0);
    setSelectedId(null);
  };

  const handleClearSearch = () => {
    setSearchInput('');
    setSearch('');
    setPage(0);
    setSelectedId(null);
  };

  const totalPages = Math.ceil(total / LIMIT);

  return (
    <div className="min-h-screen bg-background text-textMain p-4 font-sans">
      {compose && <ComposeModal onClose={() => { setCompose(false); fetchEmails(); }} />}

      <div className="max-w-3xl mx-auto flex flex-col gap-4">
        {/* Header */}
        <div className="flex items-center justify-between">
          <h1 className="text-base font-bold flex items-center gap-2">
            <Inbox size={18} className="text-primary" />
            Email Inbox
            <span className="text-xs text-textMuted font-normal">({total} emails)</span>
          </h1>
          <div className="flex items-center gap-2">
            <button
              onClick={fetchEmails}
              className="p-1.5 rounded-lg text-textMuted hover:text-primary hover:bg-primary/10 transition-colors"
              title="Refresh"
            >
              <RefreshCw size={15} />
            </button>
            <button
              onClick={() => setCompose(true)}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-primary/15 text-primary text-xs font-medium hover:bg-primary/25 transition-colors"
            >
              <Send size={13} /> Compose
            </button>
          </div>
        </div>

        {/* Search */}
        <form onSubmit={handleSearch} className="flex items-center gap-2">
          <div className="flex-1 relative">
            <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-textMuted pointer-events-none" />
            <input
              className="w-full bg-surface border border-border rounded-lg pl-8 pr-8 py-1.5 text-sm text-textMain outline-none focus:border-primary"
              placeholder="Search emails..."
              value={searchInput}
              onChange={e => setSearchInput(e.target.value)}
            />
            {searchInput && (
              <button
                type="button"
                onClick={handleClearSearch}
                className="absolute right-2 top-1/2 -translate-y-1/2 text-textMuted hover:text-textMain"
              >
                <X size={13} />
              </button>
            )}
          </div>
          <button
            type="submit"
            className="px-3 py-1.5 rounded-lg bg-surface border border-border text-xs text-textMuted hover:text-primary hover:border-primary transition-colors"
          >
            Search
          </button>
        </form>

        {/* Content */}
        {selectedId !== null ? (
          <EmailDetailView emailId={selectedId} onBack={() => setSelectedId(null)} />
        ) : loading ? (
          <div className="flex items-center justify-center py-12">
            <Loader2 size={20} className="animate-spin text-textMuted" />
          </div>
        ) : error ? (
          <div className="flex items-center gap-2 text-red-400 text-sm py-4">
            <AlertCircle size={16} /> {error}
          </div>
        ) : emails.length === 0 ? (
          <div className="flex flex-col items-center py-16 gap-3 text-textMuted">
            <Mail size={32} />
            <p className="text-sm">{search ? `No results for "${search}"` : 'No emails yet'}</p>
          </div>
        ) : (
          <>
            <div className="flex flex-col divide-y divide-border border border-border rounded-xl overflow-hidden">
              {emails.map(email => (
                <button
                  key={email.id}
                  onClick={() => setSelectedId(email.id)}
                  className="flex items-start gap-3 px-4 py-3 bg-panel hover:bg-surface text-left transition-colors"
                >
                  <Mail size={14} className="mt-0.5 text-primary shrink-0" />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-xs font-semibold text-textMain truncate">
                        {email.subject || '(no subject)'}
                      </span>
                      <span className="text-[10px] text-textMuted shrink-0">
                        {new Date(email.received_at * 1000).toLocaleDateString()}
                      </span>
                    </div>
                    <span className="text-xs text-textMuted truncate block">{email.sender}</span>
                  </div>
                </button>
              ))}
            </div>

            {/* Pagination */}
            {!search && totalPages > 1 && (
              <div className="flex items-center justify-center gap-3 pt-1">
                <button
                  onClick={() => setPage(p => Math.max(0, p - 1))}
                  disabled={page === 0}
                  className="text-xs text-textMuted hover:text-primary disabled:opacity-40 transition-colors"
                >
                  Previous
                </button>
                <span className="text-xs text-textMuted">
                  {page + 1} / {totalPages}
                </span>
                <button
                  onClick={() => setPage(p => Math.min(totalPages - 1, p + 1))}
                  disabled={page >= totalPages - 1}
                  className="text-xs text-textMuted hover:text-primary disabled:opacity-40 transition-colors"
                >
                  Next
                </button>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
};

// ---------------------------------------------------------------------------
// Mount
// ---------------------------------------------------------------------------

let _root: ReturnType<typeof createRoot> | null = null;

export function mount(el: HTMLElement) {
  if (!_root) _root = createRoot(el);
  _root.render(<App />);
}

export function unmount() {
  if (_root) {
    _root.unmount();
    _root = null;
  }
}
