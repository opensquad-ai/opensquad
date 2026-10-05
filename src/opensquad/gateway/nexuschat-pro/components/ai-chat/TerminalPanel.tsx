/**
 * TerminalPanel — an interactive shell in the workspace, hosted by the launcher.
 *
 * Deliberately **not** the agent: a terminal is a workspace tool, so it must work with the
 * agent stopped, and it must not depend on an agent-side release. The shell runs in the
 * launcher process (`opensquad/terminal_session.py`, served by
 * `launcher/management_api/_filesystem.py` and proxied through the gateway).
 *
 * The launcher has no push channel to the browser, so output is *polled*: every few hundred
 * milliseconds the panel asks for everything after the character offset it last saw. Characters
 * (not timestamps) means a missed poll costs nothing — the next read returns the gap.
 *
 * What it is not: a TTY. There is no pty in this repository, so full-screen programs (vim/top),
 * colour escapes and interactive password prompts do not work. The panel says so rather than
 * letting the user conclude it is broken.
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Ban, Eraser, Loader2, RotateCw, Terminal as TerminalIcon } from 'lucide-react';

import { terminalAPI, type TerminalShellProfile } from '../../services/api';
import { OpenSquadLoader } from '../OpenSquadLoader';

/** Trim the local scrollback so a long-running shell cannot grow without bound. */
const MAX_CHARS = 200_000;
const POLL_MS = 400;
const PROMPT = '$ ';

export interface TerminalPanelProps {
  /** The agent whose workspace this is — the shell starts in that directory. */
  agentId: string;
  /** The workspace directory the shell starts in (the panel's project). */
  rootPath?: string;
  sessionId?: string;
}

export const TerminalPanel: React.FC<TerminalPanelProps> = ({ agentId, rootPath = '' }) => {
  const { t } = useTranslation();
  // Minted once per mount: the launcher keys the shell on it, so a second panel is a second
  // shell rather than a shared one.
  const terminalId = useMemo(
    () => `t${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`,
    [],
  );
  const [output, setOutput] = useState('');
  const [input, setInput] = useState('');
  const [history, setHistory] = useState<string[]>([]);
  const [historyIndex, setHistoryIndex] = useState(-1);
  const [state, setState] = useState<'starting' | 'running' | 'exited' | 'failed'>('starting');
  const [shell, setShell] = useState('');
  const [cwd, setCwd] = useState('');
  const [error, setError] = useState('');
  /** Which shells this machine has, and which one this terminal runs. */
  const [shells, setShells] = useState<TerminalShellProfile[]>([]);
  const [shellId, setShellId] = useState('');
  const offsetRef = useRef(0);
  const aliveRef = useRef(true);
  const scrollerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  /** Ask what this machine has, so the picker offers real shells (cmd / pwsh / bash / wsl…). */
  useEffect(() => {
    if (!agentId) return;
    let alive = true;
    void terminalAPI
      .shells(agentId)
      .then((res) => {
        if (!alive) return;
        const list = Array.isArray(res?.shells) ? res.shells : [];
        setShells(list);
        setShellId((current) => current || String(res?.default || list[0]?.id || ''));
      })
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [agentId]);

  /** Start (or restart) a shell in this workspace. */
  const open = useCallback(async (overrideShell?: string) => {
    if (!agentId) return;
    setOutput('');
    setError('');
    setState('starting');
    offsetRef.current = 0;
    try {
      const wanted = overrideShell ?? shellId;
      const res = await terminalAPI.open(
        agentId,
        terminalId,
        rootPath || undefined,
        undefined,
        wanted || undefined,
      );
      if (!aliveRef.current) return;
      if (!res?.ok) {
        setState('failed');
        setError(String(res?.error || t('aiChat.terminal.openFailed')));
        return;
      }
      setShell(String(res.shell || ''));
      setCwd(String(res.cwd || rootPath || ''));
      setState('running');
      window.setTimeout(() => inputRef.current?.focus(), 0);
    } catch (e: any) {
      if (!aliveRef.current) return;
      setState('failed');
      setError(String(e?.message || e));
    }
  }, [agentId, rootPath, shellId, terminalId, t]);

  useEffect(() => {
    aliveRef.current = true;
    void open();
    return () => {
      aliveRef.current = false;
      // Stop the shell when the panel goes away (fire and forget: the tab may be closing).
      void terminalAPI.close(agentId, terminalId).catch(() => undefined);
    };
  }, [agentId, terminalId, open]);

  /** Poll for output — the launcher cannot push it to us. */
  useEffect(() => {
    if (!agentId || (state !== 'running' && state !== 'starting')) return;
    let stopped = false;
    let inFlight = false;
    const tick = async () => {
      // Polls must not overlap. Two responses can come back out of order, and the older one
      // carries a smaller offset; taking it made the next poll ask for text the panel had already
      // shown, so the same output was appended again and again.
      if (inFlight) return;
      inFlight = true;
      try {
        const res = await terminalAPI.read(agentId, terminalId, offsetRef.current);
        if (stopped || !aliveRef.current) return;
        const nextOffset = Number(res?.offset);
        if (res?.chunk && !(Number.isFinite(nextOffset) && nextOffset <= offsetRef.current)) {
          if (Number.isFinite(nextOffset)) offsetRef.current = nextOffset;
          setOutput((prev) => (prev + res.chunk).slice(-MAX_CHARS));
          setState((s) => (s === 'starting' ? 'running' : s));
        }
        if (res && res.running === false && res.offset !== undefined) {
          if (Number.isFinite(nextOffset)) {
            offsetRef.current = Math.max(offsetRef.current, nextOffset);
          }
          setState('exited');
        }
      } catch {
        /* a failed poll is not fatal: the next one retries */
      } finally {
        inFlight = false;
      }
    };
    const timer = window.setInterval(() => void tick(), POLL_MS);
    void tick();
    return () => {
      stopped = true;
      window.clearInterval(timer);
    };
  }, [agentId, terminalId, state]);

  /** Keep the newest output in view, the way a terminal does. */
  useEffect(() => {
    const el = scrollerRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [output, state]);

  const submit = () => {
    const line = input;
    if (!agentId) return;
    // The shell does not echo for us (cmd is switched to `@echo off`), so the typed line is
    // echoed here — exactly once, on both platforms.
    setOutput((prev) => `${prev}${PROMPT}${line}\n`.slice(-MAX_CHARS));
    void terminalAPI.write(agentId, terminalId, `${line}\n`).catch((e) => setError(String(e?.message || e)));
    if (line.trim()) setHistory((h) => [line, ...h].slice(0, 100));
    setHistoryIndex(-1);
    setInput('');
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      submit();
      return;
    }
    if (e.key === 'ArrowUp' && history.length) {
      e.preventDefault();
      const next = Math.min(historyIndex + 1, history.length - 1);
      setHistoryIndex(next);
      setInput(history[next] ?? '');
      return;
    }
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      const next = historyIndex - 1;
      setHistoryIndex(next);
      setInput(next >= 0 ? history[next] ?? '' : '');
      return;
    }
    if (e.key === 'c' && e.ctrlKey) {
      e.preventDefault();
      void terminalAPI.interrupt(agentId, terminalId).catch(() => undefined);
    }
  };

  const restart = () => {
    void terminalAPI.close(agentId, terminalId).catch(() => undefined).then(() => open());
  };

  const status =
    state === 'exited'
      ? t('aiChat.terminal.exited')
      : state === 'failed'
        ? t('aiChat.terminal.failed')
        : state === 'starting'
          ? t('aiChat.terminal.connecting')
          : t('aiChat.terminal.running');

  return (
    <div className="flex-1 min-h-0 flex flex-col" data-testid="terminal-panel">
      <div className="px-2 py-1 border-b border-border flex-shrink-0 flex items-center gap-2 text-[10px] text-textMuted">
        <TerminalIcon size={11} className="shrink-0" />
        <span className="truncate font-mono" title={cwd || rootPath || undefined}>
          {shell ? `${shell} · ` : ''}
          {cwd || rootPath || t('aiChat.terminal.workspaceRoot')}
        </span>
        <span className="ml-auto shrink-0">{status}</span>
        {/* Which shell runs here: the machines's own list (cmd / PowerShell / pwsh / Git Bash /
            WSL / zsh / fish), never a hard-coded single default. Switching restarts the shell. */}
        {shells.length ? (
          <select
            value={shellId}
            onChange={(e) => {
              const next = e.target.value;
              setShellId(next);
              void terminalAPI
                .close(agentId, terminalId)
                .catch(() => undefined)
                .then(() => open(next));
            }}
            data-testid="terminal-shell"
            title={t('aiChat.terminal.shell')}
            className="shrink-0 max-w-[8.5rem] rounded border border-border bg-bgLight px-1 py-0.5 font-mono text-[10px] text-textMuted outline-none"
          >
            {shells.map((s) => (
              <option key={s.id} value={s.id}>
                {s.label}
              </option>
            ))}
          </select>
        ) : null}
        <button
          type="button"
          onClick={() => void terminalAPI.interrupt(agentId, terminalId).catch(() => undefined)}
          className="shrink-0 rounded p-1 hover:bg-primary/10"
          title={t('aiChat.terminal.interrupt')}
          data-testid="terminal-interrupt"
        >
          <Ban size={11} />
        </button>
        <button
          type="button"
          onClick={() => setOutput('')}
          className="shrink-0 rounded p-1 hover:bg-primary/10"
          title={t('aiChat.terminal.clear')}
          data-testid="terminal-clear"
        >
          <Eraser size={11} />
        </button>
        <button
          type="button"
          onClick={restart}
          className="shrink-0 rounded p-1 hover:bg-primary/10"
          title={t('aiChat.terminal.restart')}
          data-testid="terminal-restart"
        >
          <RotateCw size={11} />
        </button>
      </div>

      <div
        ref={scrollerRef}
        data-testid="terminal-output"
        onClick={() => inputRef.current?.focus()}
        className="flex-1 min-h-0 overflow-auto bg-neutral-950/95 px-2 py-1.5 font-mono text-[11.5px] leading-[1.45] text-neutral-200"
      >
        <div className="whitespace-pre-wrap break-words">{output}</div>
        {!output && state !== 'running' ? (
          <div className="text-neutral-500">
            {state === 'failed'
              ? error || t('aiChat.terminal.openFailed')
              : state === 'starting'
                ? t('aiChat.terminal.connecting')
                : t('aiChat.terminal.ready')}
          </div>
        ) : null}
        {/* The prompt line is IN the terminal — click anywhere in the surface and type here,
            the way a real terminal works, instead of a separate box along the bottom. */}
        <div className="flex items-center gap-1.5">
          <span className="shrink-0 text-neutral-400">{PROMPT}</span>
          <input
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKeyDown}
            spellCheck={false}
            autoComplete="off"
            data-testid="terminal-input"
            disabled={state === 'failed'}
            placeholder={state === 'running' ? '' : t('aiChat.terminal.placeholder')}
            className="min-w-0 flex-1 bg-transparent font-mono text-[11.5px] text-neutral-100 caret-neutral-100 outline-none placeholder:text-neutral-600 disabled:opacity-50"
          />
          {state === 'starting' ? <OpenSquadLoader size={11} className="shrink-0" /> : null}
        </div>
      </div>

      <div className="flex-shrink-0 px-2 pb-1 text-[10px] leading-snug text-textMuted/70">
        {t('aiChat.terminal.noTtyNote')}
      </div>
    </div>
  );
};

export default TerminalPanel;
