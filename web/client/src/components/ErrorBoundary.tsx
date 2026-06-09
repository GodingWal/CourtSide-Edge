import { Component, type ComponentType, type ErrorInfo, type ReactNode } from 'react';
import { AlertTriangle } from 'lucide-react';

interface ErrorBoundaryProps {
  children: ReactNode;
  fallback?: ReactNode;
}

interface ErrorBoundaryState {
  hasError: boolean;
  error: Error | null;
}

export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  constructor(props: ErrorBoundaryProps) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo): void {
    console.error('[ErrorBoundary] Caught error:', error, errorInfo);
  }

  handleReload = (): void => {
    window.location.reload();
  };

  render(): ReactNode {
    if (this.state.hasError) {
      if (this.props.fallback) {
        return this.props.fallback;
      }

      return (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-cs-black/90 p-4">
          <div className="cs-card w-full max-w-md animate-fade-in p-8 text-center">
            {/* Icon */}
            <div className="mx-auto mb-6 flex h-16 w-16 items-center justify-center rounded-full bg-cs-red/10 ring-1 ring-cs-red/30">
              <AlertTriangle className="h-8 w-8 text-cs-red-bright" />
            </div>

            {/* Title */}
            <h2 className="mb-2 font-inter text-xl font-bold text-white">
              Something went wrong
            </h2>

            {/* Error message */}
            <p className="mb-2 text-sm text-cs-muted">
              An unexpected error occurred in the application.
            </p>
            <div className="mb-6 rounded-lg bg-cs-dark/80 p-3 text-left">
              <code className="font-mono text-xs text-cs-red-bright/80 break-all">
                {this.state.error?.message || 'Unknown error'}
              </code>
            </div>

            {/* Reload button */}
            <button
              onClick={this.handleReload}
              className="cs-btn-primary w-full"
            >
              Reload Application
            </button>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}

export function withErrorBoundary<P extends object>(
  WrappedComponent: ComponentType<P>,
  fallback?: ReactNode
) {
  const displayName =
    WrappedComponent.displayName || WrappedComponent.name || 'Component';

  const WithErrorBoundary = (props: P) => (
    <ErrorBoundary fallback={fallback}>
      <WrappedComponent {...props} />
    </ErrorBoundary>
  );

  WithErrorBoundary.displayName = `withErrorBoundary(${displayName})`;

  return WithErrorBoundary;
}

export default ErrorBoundary;
