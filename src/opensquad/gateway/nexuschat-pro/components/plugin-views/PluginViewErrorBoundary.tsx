import React from 'react';
import { AlertCircle, ArrowLeft } from 'lucide-react';
import { pluginAPI } from '../../services/api';
import i18n from '../../i18n';

interface State {
  hasError: boolean;
  error: Error | null;
}

interface Props {
  children: React.ReactNode;
  onBack: () => void;
  viewKey: string;
}

/**
 * Error boundary that wraps lazily-loaded plugin view components.
 *
 * If the plugin component throws during render (or fails to load), this
 * boundary catches the error and shows a safe fallback instead of crashing
 * the entire PluginManagerPage.
 */
export class PluginViewErrorBoundary extends React.Component<Props, State> {
  declare props: Props;
  declare state: State;

  constructor(props: Props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error(
      `[PluginView] Error in plugin view "${this.props.viewKey}":`,
      error,
      info.componentStack
    );
    // Report to backend so the agent can read the log and self-correct
    const pluginName = this.props.viewKey.split(':')[0];
    pluginAPI.reportPluginViewError(pluginName, this.props.viewKey, error.message, info.componentStack ?? '')
      .catch(e => console.warn('[PluginView] Failed to report error to backend:', e));
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="flex-1 h-full bg-bgLight flex flex-col overflow-hidden">
          <div className="px-6 py-4 border-b border-border bg-panel flex items-center gap-4">
            <button
              onClick={this.props.onBack}
              className="p-2 hover:bg-garyWrapper rounded-lg transition-colors"
            >
              <ArrowLeft size={20} />
            </button>
            <h1 className="text-lg font-semibold text-content">{i18n.t('pluginManager.viewError')}</h1>
          </div>
          <div className="flex flex-col items-center justify-center flex-1 p-8 gap-4">
            <AlertCircle className="text-red-500" size={48} />
            <p className="text-content font-medium">{i18n.t('pluginManager.viewLoadFailed')}</p>
            <p className="text-graySub text-sm text-center max-w-md">
              {i18n.t('pluginManager.viewErrorMsg', { viewKey: this.props.viewKey })}
            </p>
            {this.state.error && (
              <pre className="bg-bgContent text-red-400 text-xs p-4 rounded-lg max-w-full overflow-auto max-h-48">
                {this.state.error.message}
              </pre>
            )}
            <button
              onClick={this.props.onBack}
              className="mt-2 px-4 py-2 bg-primary text-white rounded-lg hover:opacity-90 transition-opacity"
            >
              {i18n.t('pluginManager.backToList')}
            </button>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}
