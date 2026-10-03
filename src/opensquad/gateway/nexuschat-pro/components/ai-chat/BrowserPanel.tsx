/**
 * BrowserPanel — a view onto the built-in browser.
 *
 * Not an iframe and not a `<webview>`: the page is rendered by a Playwright session in the
 * launcher (`opensquad/browser_session.py`), which is also what the agent's `browser_*` tools
 * drive. That is the whole point — the page the agent clicks is the page you are looking at —
 * and it is why sites that refuse to be framed (Baidu, GitHub) display here too: nothing is
 * framed, the launcher just paints and we show the frame.
 *
 * Preferred shape (option A): the session opens a REAL window on the launcher's machine, with a
 * persistent profile, so you get a browser you can actually use — hover, caret, selection,
 * right-click, logins that survive a restart — while the agent drives that same window. This
 * panel is then the remote control plus a preview; on a machine with no desktop the session says
 * so and stays headless, and the preview is all there is.
 *
 * The preview is a *view*: it polls `frame` (the last capture, no re-render per tick), maps a
 * click on the image back to viewport coordinates, and forwards typing to whatever the page has
 * focused. The session id is the same default the tools use (`agent-<id>`), so an agent asking
 * for the built-in browser lands on this very page.
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ArrowLeft, ExternalLink, Globe, Loader2, MonitorUp, RotateCw, Send } from 'lucide-react';

import { browserAPI } from '../../services/api';

/** Must match the session's viewport (browser_session.VIEWPORT) — clicks are in its pixels. */
const VIEWPORT = { width: 1280, height: 800 };
const POLL_MS = 700;

export interface BrowserPanelProps {
  /** The agent whose browser this is; the session id is `agent-<id>`, shared with its tools. */
  agentId: string;
}

export const normalizeUrl = (raw: string): string => {
  const value = String(raw || '').trim();
  if (!value) return '';
  if (/^[a-z]+:\/\//i.test(value)) return value;
  if (/^(localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\])(:\d+)?(\/|$)/i.test(value)) return `http://${value}`;
  if (/^\d{1,3}(\.\d{1,3}){3}(:\d+)?(\/|$)/.test(value)) return `http://${value}`;
  return `https://${value}`;
};

export const browserSessionId = (agentId: string): string =>
  `agent-${String(agentId || '').trim() || 'agent'}`;

export const BrowserPanel: React.FC<BrowserPanelProps> = ({ agentId }) => {
  const { t } = useTranslation();
  const session = useMemo(() => browserSessionId(agentId), [agentId]);
  const [address, setAddress] = useState('');
  const [url, setUrl] = useState('');
  const [title, setTitle] = useState('');
  const [frame, setFrame] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [text, setText] = useState('');
  // Which half of the deal this session got: a real window on the launcher's machine, or only
  // this preview (no desktop there, or the caller asked for headless).
  const [headed, setHeaded] = useState(false);
  const [windowNote, setWindowNote] = useState('');
  const imgRef = useRef<HTMLImageElement>(null);
  const typeRef = useRef<HTMLInputElement>(null);
  const busyRef = useRef(false);
  busyRef.current = busy;

  /** Start (or reuse) the session so there is something to show. */
  useEffect(() => {
    if (!agentId) return;
    let alive = true;
    void browserAPI
      .open(agentId, session)
      .then((res) => {
        if (!alive) return;
        if (!res?.ok) setError(String(res?.error || t('aiChat.browser.startFailed')));
        setHeaded(Boolean(res?.headed));
        setWindowNote(String(res?.window_note || ''));
      })
      .catch((e: any) => alive && setError(String(e?.message || e)));
    return () => {
      alive = false;
    };
  }, [agentId, session, t]);

  /** Poll the frame — the launcher cannot push, and only the frame changes. */
  useEffect(() => {
    if (!agentId) return;
    let alive = true;
    const tick = async () => {
      if (busyRef.current) return; // an action is in flight; its own reply refreshes us
      try {
        const res = await browserAPI.frame(agentId, session);
        if (!alive || !res) return;
        if (res.ok === false) {
          setError(String(res.error || ''));
          return;
        }
        if (res.png) setFrame((prev) => (res.png === prev ? prev : String(res.png)));
        if (res.url) {
          setUrl(String(res.url));
          setAddress((prev) => (prev && busyRef.current ? prev : String(res.url)));
        }
        if (res.title) setTitle(String(res.title));
        if (typeof res.headed === 'boolean') setHeaded(res.headed);
        if (res.window_note !== undefined) setWindowNote(String(res.window_note || ''));
        setError('');
      } catch {
        /* a failed poll is not fatal: the next one retries */
      }
    };
    const timer = window.setInterval(() => void tick(), POLL_MS);
    void tick();
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, [agentId, session]);

  const run = useCallback(
    async (fn: () => Promise<any>) => {
      setBusy(true);
      setError('');
      try {
        const res = await fn();
        if (res && res.ok === false) setError(String(res.error || t('aiChat.browser.actionFailed')));
        if (res?.url) setUrl(String(res.url));
        if (res?.title) setTitle(String(res.title));
      } catch (e: any) {
        setError(String(e?.message || e));
      } finally {
        setBusy(false);
      }
    },
    [t],
  );

  /** Swap this headless session for a real window on this machine. The page restarts — that is
   *  the price of changing how it renders — so it is a button, not something done behind you. */
  const showWindow = () => {
    void (async () => {
      setBusy(true);
      setError('');
      try {
        await browserAPI.close(agentId, session);
        const res = await browserAPI.open(agentId, session, false);
        setFrame('');
        setUrl('');
        setAddress('');
        setTitle('');
        setHeaded(Boolean(res?.headed));
        setWindowNote(String(res?.window_note || ''));
        if (!res?.ok) setError(String(res?.error || t('aiChat.browser.startFailed')));
        else if (!res?.headed) setError(String(res?.window_note || t('aiChat.browser.previewOnly')));
      } catch (e: any) {
        setError(String(e?.message || e));
      } finally {
        setBusy(false);
      }
    })();
  };

  const go = (raw?: string) => {
    const next = normalizeUrl(raw !== undefined ? raw : address);
    if (!next) return;
    setAddress(next);
    void run(() => browserAPI.navigate(agentId, session, next));
  };

  /** A click on the image, in the session's own pixels. */
  const onPageClick = (event: React.MouseEvent<HTMLImageElement>) => {
    const el = imgRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    const x = Math.round(((event.clientX - rect.left) / rect.width) * VIEWPORT.width);
    const y = Math.round(((event.clientY - rect.top) / rect.height) * VIEWPORT.height);
    // Move the caret to the field below: the page has focus now (on the field that was clicked),
    // so whatever is typed next goes there. The image cannot show a caret, and without this the
    // natural next move — start typing — would go nowhere.
    typeRef.current?.focus();
    void run(() => browserAPI.click(agentId, session, x, y));
  };

  /** Wheel scrolls the page, not the image: the view cannot move a page it does not render. */
  const onWheel = (event: React.WheelEvent) => {
    if (busyRef.current) return;
    void run(() => browserAPI.press(agentId, session, event.deltaY > 0 ? 'PageDown' : 'PageUp'));
  };

  const sendText = () => {
    const value = text;
    if (!value) return;
    setText('');
    // Typing goes to whatever the page has focused (click the field first), and Enter submits.
    void run(() => browserAPI.type(agentId, session, value, true));
  };

  return (
    <div className="flex-1 min-h-0 flex flex-col" data-testid="browser-panel">
      <div className="flex-shrink-0 flex items-center gap-1 border-b border-border px-1.5 py-1">
        <button
          type="button"
          onClick={() => void run(() => browserAPI.back(agentId, session))}
          disabled={!url}
          className="shrink-0 rounded p-1 text-textMuted hover:bg-primary/10 disabled:opacity-40"
          title={t('aiChat.browser.back')}
          data-testid="browser-back"
        >
          <ArrowLeft size={12} />
        </button>
        <button
          type="button"
          onClick={() => url && go(url)}
          disabled={!url}
          className="shrink-0 rounded p-1 text-textMuted hover:bg-primary/10 disabled:opacity-40"
          title={t('aiChat.browser.reload')}
          data-testid="browser-reload"
        >
          <RotateCw size={12} />
        </button>
        <input
          value={address}
          onChange={(e) => setAddress(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault();
              go();
            }
          }}
          spellCheck={false}
          autoComplete="off"
          placeholder={t('aiChat.browser.placeholder')}
          data-testid="browser-address"
          className="min-w-0 flex-1 rounded-md border border-border bg-bgLight px-2 py-0.5 font-mono text-[11px] text-textMain outline-none focus:border-primary"
        />
        <button
          type="button"
          onClick={() => go()}
          className="shrink-0 rounded p-1 text-textMuted hover:bg-primary/10"
          title={t('aiChat.browser.go')}
          data-testid="browser-go"
        >
          {busy ? <Loader2 size={12} className="animate-spin" /> : <Globe size={12} />}
        </button>
        <button
          type="button"
          onClick={() => url && window.open(url, '_blank', 'noopener,noreferrer')}
          disabled={!url}
          className="shrink-0 rounded p-1 text-textMuted hover:bg-primary/10 disabled:opacity-40"
          title={t('aiChat.browser.openExternal')}
          data-testid="browser-open-external"
        >
          <ExternalLink size={12} />
        </button>
      </div>

      <div
        className="flex-1 min-h-0 overflow-auto bg-white dark:bg-neutral-900"
        data-testid="browser-stage"
        onWheel={onWheel}
      >
        {frame ? (
          <img
            ref={imgRef}
            src={`data:image/png;base64,${frame}`}
            alt={title || url}
            onClick={onPageClick}
            data-testid="browser-frame"
            // Natural size, not squeezed into the rail: a 1280px page scaled to ~300px is
            // unreadable. The stage scrolls instead.
            className="block max-w-none cursor-pointer select-none"
            draggable={false}
          />
        ) : (
          <div className="h-full flex flex-col items-center justify-center gap-1 px-4 text-center">
            <Globe size={20} className="text-textMuted/50" />
            <div className="text-[11px] text-textMuted">{error || t('aiChat.browser.empty')}</div>
          </div>
        )}
      </div>

      <div className="flex-shrink-0 flex items-center gap-1.5 border-t border-border px-2 py-1">
        <input
          ref={typeRef}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault();
              sendText();
            }
          }}
          spellCheck={false}
          autoComplete="off"
          placeholder={t('aiChat.browser.typePlaceholder')}
          data-testid="browser-type"
          className="min-w-0 flex-1 rounded-md border border-border bg-bgLight px-2 py-0.5 text-[11px] text-textMain outline-none focus:border-primary"
        />
        <button
          type="button"
          onClick={sendText}
          disabled={!text}
          className="shrink-0 rounded p-1 text-textMuted hover:bg-primary/10 disabled:opacity-40"
          title={t('aiChat.browser.sendText')}
          data-testid="browser-send-text"
        >
          <Send size={12} />
        </button>
      </div>

      <div className="flex-shrink-0 flex flex-wrap items-center gap-x-1.5 px-2 py-0.5 text-[10px]">
        {headed ? (
          <span className="text-emerald-600 dark:text-emerald-400" data-testid="browser-headed">
            {t('aiChat.browser.windowOpen')}
          </span>
        ) : (
          <>
            <span className="text-textMuted/80">{windowNote || t('aiChat.browser.previewOnly')}</span>
            <button
              type="button"
              onClick={showWindow}
              className="inline-flex items-center gap-0.5 text-primary hover:underline"
              data-testid="browser-show-window"
            >
              <MonitorUp size={10} />
              {t('aiChat.browser.showWindow')}
            </button>
          </>
        )}
      </div>

      <div className="flex-shrink-0 px-2 pb-0.5 text-[10px] leading-snug text-textMuted/70">
        {error ? <span className="text-rose-500">{error} · </span> : null}
        {t('aiChat.browser.sharedNote')}
      </div>
    </div>
  );
};

export default BrowserPanel;
