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
 */
import React, { useCallback, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ArrowLeft, ArrowRight, ExternalLink, Globe, Home, RotateCw } from 'lucide-react';

export interface BrowserPanelProps {
  /** Quick links, e.g. the local services of this deployment. */
  suggestions?: string[];
}

const DEFAULT_URL = 'http://localhost:9555';
const QUICK_LINKS = [
  { url: 'http://localhost:9555', label: 'gateway :9555' },
  { url: 'http://localhost:5173', label: 'dev ui :5173' },
  { url: 'http://localhost:9001', label: 'websearch :9001' },
];

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

interface ElectronWebview extends HTMLElement {
  goBack?: () => void;
  goForward?: () => void;
  reload?: () => void;
  canGoBack?: () => boolean;
  canGoForward?: () => boolean;
  src?: string;
}

export const BrowserPanel: React.FC<BrowserPanelProps> = ({ suggestions }) => {
  const { t } = useTranslation();
  const [address, setAddress] = useState('');
  const [url, setUrl] = useState('');
  const [nonce, setNonce] = useState(0);
  const [refused, setRefused] = useState(false);
  const viewRef = useRef<ElectronWebview | null>(null);

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

  const webview = () => {
    const el = viewRef.current;
    return el && typeof el.reload === 'function' ? el : null;
  };

  return (
    <div className="flex-1 min-h-0 flex flex-col" data-testid="browser-panel">
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
    </div>
  );
};

export default BrowserPanel;
