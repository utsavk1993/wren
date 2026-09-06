import { Component, type ReactNode } from "react";

/**
 * Shows what went wrong instead of a blank page.
 *
 * A React component that throws while rendering takes the whole page down and
 * leaves an empty white screen, with the reason only in the browser console.
 * Anyone looking at it just sees nothing and has no way to say what happened.
 */
export class ErrorBoundary extends Component<
  { children: ReactNode },
  { error: Error | null }
> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="app">
        <h1>Something broke</h1>
        <p className="error">{this.state.error.message}</p>
        <pre className="crash">{this.state.error.stack}</pre>
        <button type="button" onClick={() => location.reload()}>
          Reload
        </button>
      </div>
    );
  }
}
