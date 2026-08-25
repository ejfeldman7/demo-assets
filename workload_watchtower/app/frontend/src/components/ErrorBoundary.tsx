import { Component, type ReactNode } from "react";

interface Props {
  children: ReactNode;
}
interface State {
  error: Error | null;
}

/**
 * Catches render/runtime errors in the wrapped view so a single failure shows a
 * recoverable fallback instead of unmounting the whole app (blank screen).
 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error) {
    // eslint-disable-next-line no-console
    console.error("View render error:", error);
  }

  render() {
    if (this.state.error) {
      return (
        <div style={{ padding: 40, maxWidth: 640 }}>
          <h2 style={{ color: "var(--text, #E8EBF0)", fontWeight: 400, fontSize: 20, marginBottom: 8 }}>
            This view hit an error.
          </h2>
          <p style={{ color: "var(--muted, #97A0AF)", fontSize: 13, marginBottom: 20 }}>
            {String(this.state.error.message || this.state.error)}
          </p>
          <button
            onClick={() => this.setState({ error: null })}
            style={{
              padding: "8px 16px",
              borderRadius: 8,
              background: "#2272EB",
              color: "#fff",
              border: "none",
              cursor: "pointer",
              fontSize: 13,
            }}
          >
            Retry
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
