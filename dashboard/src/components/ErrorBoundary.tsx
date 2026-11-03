import { Component, type ReactNode } from "react";

/** A page that crashes shows what went wrong (and a way back) instead of a blank screen. */
export class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error) {
    console.error("page crashed:", error);
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div role="alert" className="card card-alarm p-6">
        <p className="label mb-2">something broke on this page</p>
        <p className="text-sm">{this.state.error.message}</p>
        <button className="btn mt-4" onClick={() => this.setState({ error: null })}>try again</button>
      </div>
    );
  }
}
