/**
 * BrowserPanel — the browser tab of the right-hand panel.
 *
 * Two renderers, because this app runs both ways:
 *
 * * **Desktop (Electron)**: a real `<webview>` — an actual browser, with cookies, and it
 *   can display sites that refuse to be framed. Enabled by `webviewTag: true` plus a
 *   `will-attach-webview` guard in `electron/main.ts`.
 * * **Browser / LAN client**: a sandboxed `<iframe>`. It is interactive for sites that
 *   permit framing, and blank for the many that send `X-Frame-Options: DENY`. That is a
 *   property of the site, not something this app can override, so the panel says so and
 *   offers to open the page in the system browser instead.
 *
 * A URL on this app's own origin is never framed: with `allow-same-origin` a same-origin
 * guest could reach the parent, so those go straight to the system browser.
 *
 * The second mode is a **read-only view of the agent's browser** (`agent-<id>`, the session
 * the agent's `browser_*` tools and the Playwright MCP drive — see `opensquad/tools/browser.py`).
 * It polls `/browser/frame`, so what the agent or the plugin does shows up here a poll later.
 * It is deliberately a *picture*: an earlier version forwarded clicks to that session, which
 * reached a window on this machine and trailed the 700 ms poll, so it was reverted. Watching
 * is the feature; driving stays with the agent.
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ArrowLeft, ArrowRight, ExternalLink, Globe, Home, MonitorPlay, RotateCw } from 'lucide-react';

import { browserAPI } from '../../services/api';

export interface BrowserPanelProps {
  /** Quick links, e.g. the local services of this deployment. */
  suggestions?: string[];
  /** The agent whose built-in browser the read-only preview mirrors. Omitted → no preview tab. */
  agentId?: string;
}

const DEFAULT_URL = 'http://localhost:9555';
const QUICK_LINKS = [
  { url: 'http://localhost:9555', label: 'gateway :9555' },
  { url: 'http://localhost:5173', label: 'dev ui :5173' },
  { url: 'http://localhost:9001', label: 'websearch :9001' },
];

/** How often the preview asks for the last captured frame. It is a cached PNG, not a render,
 *  so the tick is cheap; 700 ms is what the reverted version used and reads as "live enough". */
const AGENT_POLL_MS = 700;

export const normalizeUrl = (raw: string): string => {
  const value = String(raw || '').trim();
  if (!value) return '';
  if (/^[a-z]+:\/\//i.test(value)) return value;
  if (/^(localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\])(:\d+)?(\/|$)/i.test(value)) return `http://${value}`;
  if (/^\d{1,3}(\.\d{1,3}){3}(:\d+)?(\/|$)/.test(value)) return `http://${value}`;
  return `https://${value}`;
};

/** True when the URL points at this app itself (must not be framed with same-origin). */
export const isSelfOrigin = (url: string, base?: string): boolean => {
  try {
    const origin = base || window.location.origin;
    return new URL(url, origin).origin === new URL(origin, origin).origin;
  } catch {
    return false;
  }
};

/** The session id the agent's `browser_*` tools default to — `agent-<dir_name>`, see
 *  `opensquad/tools/browser.py`. Guessing it here is what makes the two one browser. */
export const agentBrowserSessionId = (agentId: string): string => `agent-${String(agentId || '').trim()}`;

interface ElectronWebview extends HTMLElement {
  goBack?: () => void;
  goForward?: () => void;
  reload?: () => void;
  canGoBack?: () => boolean;
  canGoForward?: () => boolean;
  src?: string;
}

type PanelMode = 'mine' | 'agent';

export const BrowserPanel: React.FC<BrowserPanelProps> = ({ suggestions, agentId }) => {
  const { t } = useTranslation();
  const [mode, setMode] = useState<PanelMode>('mine');
  const [address, setAddress] = useState('');
  const [url, setUrl] = useState('');
  const [nonce, setNonce] = useState(0);
  const [refused, setRefused] = useState(false);
  const viewRef = useRef<ElectronWebview | null>(null);

  // The agent preview: a base64 PNG plus what the page currently is.
  const [frame, setFrame] = useState('');
  const [agentUrl, setAgentUrl] = useState('');
  const [agentTitle, setAgentTitle] = useState('');
  const [agentHeaded, setAgentHeaded] = useState(false);
  const [agentNote, setAgentNote] = useState('');
  const [agentError, setAgentError] = useState('');
  const [refreshKey, setRefreshKey] = useState(0);
  const lastFrameRef = useRef('');

  const sessionId = useMemo(() => agentBrowserSessionId(agentId || ''), [agentId]);

  const isElectron = useMemo(() => {
    try {
      return !!window.electronEnv?.isElectron;
    } catch {
      return false;
    }
  }, []);

  const links = useMemo(() => {
    const extra = (suggestions || []).filter(Boolean).map((u) => ({ url: u, label: u }));
    return [...QUICK_LINKS, ...extra];
  }, [suggestions]);

  const go = useCallback(
    (raw?: string) => {
      const next = normalizeUrl(raw !== undefined ? raw : address);
      if (!next) return;
      setAddress(next);
      if (!isElectron && isSelfOrigin(next)) {
        // Same-origin content must not be framed with allow-same-origin.
        setRefused(true);
        setUrl('');
        return;
      }
      setRefused(false);
      setUrl(next);
      setNonce((n) => n + 1);
    },
    [address, isElectron],
  );

  const external = () => {
    if (!url) return;
    window.open(url, '_blank', 'noopener,noreferrer');
  };

  const externalAgentUrl = () => {
    if (!agentUrl) return;
    window.open(agentUrl, '_blank', 'noopener,noreferrer');
  };

  const webview = () => {
    const el = viewRef.current;
    return el && typeof el.reload === 'function' ? el : null;
  };

  // Start (or reuse) the agent's browser once per visit. Headless is requested so watching
  // never puts a window on this machine's desktop; an already-open session keeps the shape it
  // has, which is why the note below is read from the reply rather than assumed.
  useEffect(() => {
    if (mode !== 'agent' || !agentId) return;
    let alive = true;
    void (async () => {
      try {
        const res = await browserAPI.open(agentId, sessionId, true);
        if (!alive || res.ok) return;
        setAgentError(res.error || t('aiChat.browser.startFailed'));
      } catch (err) {
        if (alive) setAgentError(String(err));
      }
    })();
    return () => {
      alive = false;
    };
  }, [mode, agentId, sessionId, t]);

  // The poll. Only state that actually moved is written: a 700 ms tick that re-set an
  // identical image would restart the <img> decode for nothing.
  useEffect(() => {
    if (mode !== 'agent' || !agentId) return;
    let alive = true;
    let timer: number | undefined;

    const tick = async () => {
      try {
        const res = await browserAPI.frame(agentId, sessionId);
        if (!alive) return;
        if (res.ok && res.png) {
          if (res.png !== lastFrameRef.current) {
            lastFrameRef.current = res.png;
            setFrame(res.png);
          }
          setAgentUrl(res.url || '');
          setAgentTitle(res.title || '');
          setAgentHeaded(!!res.headed);
          setAgentNote(res.headed ? t('aiChat.browser.windowOpen') : res.window_note || t('aiChat.browser.previewOnly'));
          setAgentError('');
        } else {
          setAgentError(res.error || t('aiChat.browser.actionFailed'));
        }
      } catch (err) {
        if (alive) setAgentError(String(err));
      } finally {
        if (alive) timer = window.setTimeout(tick, AGENT_POLL_MS);
      }
    };
    void tick();

    return () => {
      alive = false;
      if (timer) window.clearTimeout(timer);
    };
  }, [mode, agentId, sessionId, refreshKey, t]);

  const modeButton = (value: PanelMode, key: string, testId: string) => (
    <button
      key={value}
      type="button"
      onClick={() => setMode(value)}
      data-testid={testId}
      className={`shrink-0 rounded border px-1.5 py-0.5 text-[10px] ${
        mode === value
          ? 'border-primary bg-primary/10 text-textMain'
          : 'border-border/60 text-textMuted hover:bg-primary/10 hover:text-textMain'
      }`}
    >
      {t(key)}
    </button>
  );

  return (
    <div className="flex-1 min-h-0 flex flex-col" data-testid="browser-panel">
      {agentId ? (
        <div className="flex-shrink-0 flex items-center gap-1 border-b border-border px-1.5 py-1">
          {modeButton('mine', 'aiChat.browser.modeMine', 'browser-mode-mine')}
          {modeButton('agent', 'aiChat.browser.modeAgent', 'browser-mode-agent')}
        </div>
      ) : null}

      {mode === 'agent' ? (
        <>
          <div className="flex-shrink-0 flex items-center gap-1 border-b border-border px-1.5 py-1">
            <button
              type="button"
              onClick={() => setRefreshKey((n) => n + 1)}
              className="shrink-0 rounded p-1 text-textMuted hover:bg-primary/10"
              title={t('aiChat.browser.reload')}
              data-testid="browser-agent-refresh"
            >
              <RotateCw size={12} />
            </button>
            <span
              className="min-w-0 flex-1 truncate font-mono text-[11px] text-textMuted"
              data-testid="browser-agent-url"
              title={agentUrl}
            >
              {agentUrl || t('aiChat.browser.agentIdle')}
            </span>
            <button
              type="button"
              onClick={externalAgentUrl}
              disabled={!agentUrl}
              className="shrink-0 rounded p-1 text-textMuted hover:bg-primary/10 disabled:opacity-40"
              title={t('aiChat.browser.openExternal')}
              data-testid="browser-agent-external"
            >
              <ExternalLink size={12} />
            </button>
          </div>

          <div className="flex-1 min-h-0 overflow-auto bg-white dark:bg-neutral-900">
            {frame ? (
              // A picture, never a control surface: pointer-events off and no handler, so a
              // click here cannot reach the session the agent is driving.
              <img
                src={`data:image/png;base64,${frame}`}
                alt={agentTitle || agentUrl || 'agent browser'}
                data-testid="browser-agent-frame"
                draggable={false}
                className="block w-full select-none pointer-events-none"
              />
            ) : (
              <div className="h-full flex flex-col items-center justify-center gap-1 px-4 text-center">
                <MonitorPlay size={20} className="text-textMuted/50" />
                <div className="text-[11px] text-textMuted">
                  {agentError || t('aiChat.browser.agentIdle')}
                </div>
              </div>
            )}
          </div>

          <div className="flex-shrink-0 px-2 py-0.5 text-[10px] leading-snug text-textMuted/70">
            {agentError ? (
              <span className="text-amber-500">{agentError}</span>
            ) : (
              <>
                {agentNote}
                {agentTitle ? ` · ${agentTitle}` : ''}
              </>
            )}
          </div>
        </>
      ) : (
        <>
          <div className="flex-shrink-0 flex items-center gap-1 border-b border-border px-1.5 py-1">
            <button
              type="button"
              onClick={() => webview()?.goBack?.()}
              disabled={!isElectron || !url}
              className="shrink-0 rounded p-1 text-textMuted hover:bg-primary/10 disabled:opacity-40"
              title={t('aiChat.browser.back')}
            >
              <ArrowLeft size={12} />
            </button>
            <button
              type="button"
              onClick={() => webview()?.goForward?.()}
              disabled={!isElectron || !url}
              className="shrink-0 rounded p-1 text-textMuted hover:bg-primary/10 disabled:opacity-40"
              title={t('aiChat.browser.forward')}
            >
              <ArrowRight size={12} />
            </button>
            <button
              type="button"
              onClick={() => {
                if (isElectron && url) webview()?.reload?.();
                else if (url) setNonce((n) => n + 1);
              }}
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
              <Globe size={12} />
            </button>
            <button
              type="button"
              onClick={external}
              disabled={!url}
              className="shrink-0 rounded p-1 text-textMuted hover:bg-primary/10 disabled:opacity-40"
              title={t('aiChat.browser.openExternal')}
              data-testid="browser-open-external"
            >
              <ExternalLink size={12} />
            </button>
          </div>

          {links.length ? (
            <div className="flex-shrink-0 flex flex-wrap items-center gap-1 border-b border-border/60 px-1.5 py-1">
              <Home size={10} className="text-textMuted" />
              {links.map((l) => (
                <button
                  key={l.url}
                  type="button"
                  onClick={() => go(l.url)}
                  className="rounded border border-border/60 px-1.5 py-0.5 font-mono text-[10px] text-textMuted hover:bg-primary/10 hover:text-textMain"
                >
                  {l.label}
                </button>
              ))}
            </div>
          ) : null}

          {!isElectron && url ? (
            // In the browser build some sites cannot be embedded at all (X-Frame-Options, or a page
            // script that refuses to be framed — Baidu does the latter, so the area just stays blank
            // with no clue why). Say it where it happens, with the way out next to it.
            <div className="flex-shrink-0 flex items-center gap-1.5 border-b border-border/60 bg-amber-500/[0.07] px-2 py-0.5 text-[10px] text-textMuted">
              <span className="min-w-0 flex-1 truncate">{t('aiChat.browser.framingNote')}</span>
              <button
                type="button"
                onClick={external}
                data-testid="browser-iframe-hint-external"
                className="shrink-0 rounded border border-border px-1.5 py-0.5 text-textMain hover:bg-primary/10"
              >
                {t('aiChat.browser.openExternal')}
              </button>
            </div>
          ) : null}

          <div className="flex-1 min-h-0 relative bg-white dark:bg-neutral-900">
            {!url ? (
              <div className="h-full flex flex-col items-center justify-center gap-1 px-4 text-center">
                <Globe size={20} className="text-textMuted/50" />
                <div className="text-[11px] text-textMuted">
                  {t('aiChat.browser.empty', { defaultValue: '输入网址后回车，或用上面的本地服务快捷入口。' })}
                </div>
                <div className="text-[10px] text-textMuted/70 max-w-[22rem]">{t('aiChat.browser.framingNote')}</div>
              </div>
            ) : refused ? (
              <div className="h-full flex flex-col items-center justify-center gap-1 px-4 text-center">
                <div className="text-[11px] text-amber-500">{t('aiChat.browser.selfOrigin')}</div>
                <button
                  type="button"
                  onClick={external}
                  className="rounded border border-border px-2 py-0.5 text-[11px] text-textMain hover:bg-primary/10"
                >
                  {t('aiChat.browser.openExternal')}
                </button>
              </div>
            ) : isElectron ? (
              React.createElement('webview', {
                // Electron-only element: a real browser tab, so sites that refuse framing work.
                key: `${url}#${nonce}`,
                ref: (node: ElectronWebview | null) => {
                  viewRef.current = node;
                },
                src: url,
                partition: 'persist:opensquad-browser',
                allowpopups: 'true',
                style: { width: '100%', height: '100%', border: '0' },
                'data-testid': 'browser-webview',
              })
            ) : (
              <iframe
                key={`${url}#${nonce}`}
                src={url}
                title={url}
                data-testid="browser-iframe"
                // no allow-same-origin: a cross-origin guest gets storage, but a same-origin
                // one could reach the parent — and those are refused above.
                sandbox="allow-scripts allow-forms allow-popups allow-modals"
                referrerPolicy="no-referrer"
                className="h-full w-full border-0"
              />
            )}
          </div>

          <div className="flex-shrink-0 px-2 py-0.5 text-[10px] leading-snug text-textMuted/70">
            {isElectron ? t('aiChat.browser.electronNote') : t('aiChat.browser.framingNote')}
          </div>
        </>
      )}
    </div>
  );
};

export default BrowserPanel;
