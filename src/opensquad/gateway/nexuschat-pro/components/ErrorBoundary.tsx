/**
 * ErrorBoundary — keep one broken surface from taking the whole app down.
 *
 * React unmounts the entire root when an error escapes render, so before this
 * existed a single bad field on a single task blanked the whole product: the
 * sidebar, the chat, the composer, everything. The offending read was
 * `plan.budget.max_tokens` on a task whose `plan` was the empty placeholder
 * object. The data bug is fixed; this is the reason it cannot recur as a
 * *blank screen* — the failure is contained to the pane that produced it and
 * the user keeps a working app around it.
 *
 * Two mount levels, deliberately:
 *   · pane (`full={false}`, in WorkspacePaneShell) — a tasks / scheduled-tasks
 *     panel dies alone, with the rest of the workspace intact.
 *   · app (`full={true}`, in index.tsx) — last resort for anything outside a
 *     pane, offering a page reload because broken app-level state rarely
 *     recovers from a re-render.
 */
import React from 'react';
import { AlertTriangle, RotateCcw } from 'lucide-react';
import i18n from '../i18n';

interface Props {
  children: React.ReactNode;
  /** Identifies the guarded surface in the fallback text and the log. */
  label: string;
  /** Render a page-reload action too; for the outermost boundary only. */
  full?: boolean;
  /**
   * Reset the boundary when this changes. Switching to a different task (or
   * agent) should get a fresh render instead of a stuck error card — the
   * previous input is what failed, not the component itself.
   */
  resetKey?: string | number | null;
}

interface State {
  error: Error | null;
  /** Which component threw — the message alone rarely identifies the surface. */
  stack: string;
}

export class ErrorBoundary extends React.Component<Props, State> {
  declare props: Props;
  declare state: State;

  constructor(props: Props) {
    super(props);
    this.state = { error: null, stack: '' };
  }

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    // Keep the raw error reachable: the fallback is deliberately terse.
    console.error(`[ErrorBoundary:${this.props.label}]`, error, info.componentStack);
    this.setState({ stack: (info.componentStack || '').trim() });
  }

  componentDidUpdate(prev: Props) {
    if (this.state.error && prev.resetKey !== this.props.resetKey) {
      this.setState({ error: null, stack: '' });
    }
  }

  private retry = () => {
    this.setState({ error: null, stack: '' });
  };

  render() {
    const { error, stack } = this.state;
    if (!error) return this.props.children;

    // The label is rendered, not just logged: a screenshot of the card has to
    // say *which* surface died, and a stale tab has to be distinguishable from
    // a live bug. The reload button is therefore always available — a stale
    // module in an open tab is otherwise unrecoverable from the UI.
    return (
      <div
        role="alert"
        className="flex-1 min-h-0 h-full w-full flex flex-col items-center justify-center gap-3 p-6 text-center"
      >
        <AlertTriangle size={28} className="text-amber-500 shrink-0" />
        <div className="text-[13px] font-medium">
          {i18n.t('errorBoundary.title')} · <span className="font-mono">{this.props.label}</span>
        </div>
        <div className="text-[11px] text-textMuted max-w-md">
          {i18n.t('errorBoundary.hint')}
        </div>
        <pre className="max-w-full max-h-40 overflow-auto rounded-lg bg-black/[0.04] dark:bg-white/[0.06] px-3 py-2 text-[10px] text-left whitespace-pre-wrap break-words">
          {error.message}
        </pre>
        {stack && (
          <details className="max-w-full text-left">
            <summary className="cursor-pointer text-[10px] text-textMuted">
              {i18n.t('errorBoundary.detail')}
            </summary>
            <pre className="max-h-32 overflow-auto rounded-lg bg-black/[0.04] dark:bg-white/[0.06] px-3 py-2 text-[10px] whitespace-pre-wrap break-words">
              {stack}
            </pre>
          </details>
        )}
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={this.retry}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[11px] font-medium border border-border hover:bg-black/5 dark:hover:bg-white/10"
          >
            <RotateCcw size={12} />
            {i18n.t('errorBoundary.retry')}
          </button>
          <button
            type="button"
            onClick={() => window.location.reload()}
            className="px-3 py-1.5 rounded-lg text-[11px] font-medium bg-sky-500 text-white hover:bg-sky-600"
          >
            {i18n.t('errorBoundary.reload')}
          </button>
        </div>
      </div>
    );
  }
}
