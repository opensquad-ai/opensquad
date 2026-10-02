/**
 * TerminalPanel — the interactive shell tab of the right-hand panel.
 *
 * It drives a real shell on the agent's machine: the id is minted here, sent with every
 * command, and the output comes back on the websocket events the app already uses for job
 * output (`job_stdout` / `job_status`, keyed by `terminal:<id>`). Nothing new was needed on
 * the protocol or the gateway for that — which is why this panel is small.
 *
 * What it is not: a TTY. There is no pty in this repository, so full-screen programs
 * (vim/top), colour escapes and interactive password prompts do not work. The panel says so
 * rather than letting the user conclude it is broken.
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Ban, Eraser, RotateCw, Terminal as TerminalIcon } from 'lucide-react';

import { getAiWsService } from '../../services/aiWebSocket';

const ID_PREFIX = 'terminal:';
/** Trim the local scrollback so a long-running shell cannot grow without bound. */
const MAX_CHARS = 200_000;
const PROMPT = '$ ';

export interface TerminalPanelProps {
  agentId: string;
  /** The workspace directory the shell starts in (the panel's project). */
  rootPath?: string;
  sessionId?: string;
}

interface StreamPayload {
  job_id?: string;
  chunk?: string;
  state?: string;
  reason?: string;
  return_code?: number | null;
}

export const TerminalPanel: React.FC<TerminalPanelProps> = ({ agentId, rootPath = '', sessionId = '' }) => {
  const { t } = useTranslation();
  // Minted once per mount: the agent keys its output on this, so a second panel is a second
  // shell rather than a shared one.
  const terminalId = useMemo(
    () => `t${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`,
    [],
  );
  const tunnel = `${ID_PREFIX}${terminalId}`;
  const [output, setOutput] = useState('');
  const [input, setInput] = useState('');
  const [history, setHistory] = useState<string[]>([]);
  const [historyIndex, setHistoryIndex] = useState(-1);
  const [state, setState] = useState<'connecting' | 'running' | 'done'>('connecting');
  const [connected, setConnected] = useState<boolean>(() => {
    try {
      return getAiWsService(agentId).isConnected;
    } catch {
      return false;
    }
  });
  const scrollerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  /** Stream this terminal's output (only this terminal's — one panel per shell). */
  useEffect(() => {
    if (!agentId) return;
    const svc = getAiWsService(agentId);
    setConnected(svc.isConnected);
    const read = (msg: unknown): StreamPayload => {
      const raw = msg as { content?: unknown; data?: unknown } | null;
      const body = (raw?.content ?? raw?.data ?? {}) as Record<string, unknown>;
      return body as StreamPayload;
    };
    const offOut = svc.on('job_stdout', (msg) => {
      const d = read(msg);
      if (String(d.job_id || '') !== tunnel) return;
      const chunk = typeof d.chunk === 'string' ? d.chunk : '';
      if (!chunk) return;
      setState((s) => (s === 'connecting' ? 'running' : s));
      setOutput((prev) => (prev + chunk).slice(-MAX_CHARS));
    });
    const offStatus = svc.on('job_status', (msg) => {
      const d = read(msg);
      if (String(d.job_id || '') !== tunnel) return;
      const next = String(d.state || '');
      if (next === 'running') setState('running');
      else if (next === 'done' || next === 'aborted' || next === 'error') setState('done');
    });
    return () => {
      offOut();
      offStatus();
    };
  }, [agentId, tunnel]);

  const open = useCallback(() => {
    if (!agentId) return;
    setOutput('');
    setState('connecting');
    const svc = getAiWsService(agentId);
    setConnected(svc.isConnected);
    svc.openTerminal(terminalId, rootPath || undefined, sessionId || undefined);
    window.setTimeout(() => inputRef.current?.focus(), 0);
  }, [agentId, rootPath, sessionId, terminalId]);

  /** Open on mount; stop the shell when the panel goes away. */
  useEffect(() => {
    if (!agentId) return;
    open();
    return () => {
      try {
        getAiWsService(agentId).closeTerminal(terminalId);
      } catch {
        /* the socket may already be gone; the agent drops the shell when it exits */
      }
    };
  }, [agentId, terminalId, open]);

  /** Keep the newest output in view, the way a terminal does. */
  useEffect(() => {
    const el = scrollerRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [output, state]);

  const submit = () => {
    const line = input;
    if (!agentId) return;
    // A piped shell does not echo for us (cmd is switched to `@echo off`), so the typed
    // line is echoed here — exactly once, on both platforms.
    setOutput((prev) => `${prev}${PROMPT}${line}\n`.slice(-MAX_CHARS));
    getAiWsService(agentId).writeTerminal(terminalId, `${line}\n`);
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
      getAiWsService(agentId).interruptTerminal(terminalId);
    }
  };

  const restart = () => {
    getAiWsService(agentId).closeTerminal(terminalId);
    open();
  };

  return (
    <div className="flex-1 min-h-0 flex flex-col" data-testid="terminal-panel">
      <div className="px-2 py-1 border-b border-border flex-shrink-0 flex items-center gap-2 text-[10px] text-textMuted">
        <TerminalIcon size={11} className="shrink-0" />
        <span className="truncate font-mono" title={rootPath || undefined}>
          {rootPath || t('aiChat.terminal.workspaceRoot')}
        </span>
        <span className="ml-auto shrink-0">
          {state === 'done'
            ? t('aiChat.terminal.exited')
            : connected
              ? t('aiChat.terminal.running')
              : t('aiChat.terminal.disconnected')}
        </span>
        <button
          type="button"
          onClick={() => getAiWsService(agentId).interruptTerminal(terminalId)}
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
        className="flex-1 min-h-0 overflow-auto bg-neutral-950/95 px-2 py-1.5 font-mono text-[11.5px] leading-[1.45] text-neutral-200 whitespace-pre-wrap break-words"
      >
        {output || (
          <span className="text-neutral-500">
            {state === 'connecting' ? t('aiChat.terminal.connecting') : t('aiChat.terminal.ready')}
          </span>
        )}
      </div>

      <div className="flex-shrink-0 flex items-center gap-1.5 border-t border-border px-2 py-1">
        <span className="font-mono text-[11px] text-textMuted">{PROMPT}</span>
        <input
          ref={inputRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={onKeyDown}
          spellCheck={false}
          autoComplete="off"
          data-testid="terminal-input"
          placeholder={t('aiChat.terminal.placeholder')}
          className="min-w-0 flex-1 bg-transparent font-mono text-[11.5px] text-textMain outline-none"
        />
      </div>

      <div className="flex-shrink-0 px-2 pb-1 text-[10px] leading-snug text-textMuted/70">
        {t('aiChat.terminal.noTtyNote')}
      </div>
    </div>
  );
};

export default TerminalPanel;
