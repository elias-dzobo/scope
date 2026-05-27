import React from 'react';

interface State {
  hasError: boolean;
  message: string;
}

export class ErrorBoundary extends React.Component<React.PropsWithChildren<{ fallback?: React.ReactNode }>, State> {
  constructor(props: React.PropsWithChildren<{ fallback?: React.ReactNode }>) {
    super(props);
    this.state = { hasError: false, message: '' };
  }

  static getDerivedStateFromError(error: unknown): State {
    const message = error instanceof Error ? error.message : String(error);
    return { hasError: true, message };
  }

  componentDidCatch(error: unknown, info: React.ErrorInfo) {
    console.error('[ErrorBoundary] Caught render error:', error, info.componentStack);
  }

  handleReset = () => {
    this.setState({ hasError: false, message: '' });
  };

  render() {
    if (this.state.hasError) {
      if (this.props.fallback) return this.props.fallback;
      return (
        <div className="flex h-full flex-col items-center justify-center px-6 py-16 text-center">
          <p className="text-[12px] uppercase tracking-[0.18em] text-text-muted">Something went wrong</p>
          <p className="mt-4 max-w-[480px] text-[15px] leading-7 text-text-secondary">
            {this.state.message || 'An unexpected error occurred. Please try again.'}
          </p>
          <button
            onClick={this.handleReset}
            className="mt-8 border border-line-subtle px-5 py-3 text-[11px] font-semibold uppercase tracking-[0.14em] text-text-secondary transition hover:border-text-primary hover:text-text-primary"
          >
            Try again
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
