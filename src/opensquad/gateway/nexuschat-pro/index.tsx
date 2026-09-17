import React from 'react';
import ReactDOM from 'react-dom/client';
import './index.css';
import './i18n';
import App from './App';
import { ErrorBoundary } from './components/ErrorBoundary';
import { initTheme } from './utils/themeStore';
import { hydrateHostUiPrefs } from './utils/hostUiPrefs';

const rootElement = document.getElementById('root');
if (!rootElement) {
  throw new Error("Could not find root element to mount to");
}

const root = ReactDOM.createRoot(rootElement);

async function boot() {
  // Apply this origin's cache immediately, then overlay host prefs so
  // packaged :9555 matches Vite :5173 even though localStorage is isolated.
  initTheme();
  const ctrl = new AbortController();
  const timer = window.setTimeout(() => ctrl.abort(), 800);
  try {
    await hydrateHostUiPrefs(ctrl.signal);
  } catch {
    /* offline / timeout — keep local theme */
  } finally {
    window.clearTimeout(timer);
  }
  root.render(
    <React.StrictMode>
      {/* Outermost guard: without it any render throw unmounts the entire root
          and the user gets a blank page with no way forward. */}
      <ErrorBoundary label="app" full>
        <App />
      </ErrorBoundary>
    </React.StrictMode>
  );
}

void boot();
