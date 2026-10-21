import { Component } from "react";
import type { ReactNode } from "react";

import { Button } from "./ui";

interface State {
  error: Error | null;
}

/**
 * Keeps the shell (navigation, sign-out) usable when one screen throws while
 * rendering. AppShell keys it by route, so navigating away resets it. Only the
 * message is shown: component stacks can quote client data held in props.
 */
export class ErrorBoundary extends Component<{ children: ReactNode }, State> {
  override state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  override render(): ReactNode {
    const { error } = this.state;
    if (!error) return this.props.children;
    return (
      <div className="notice error" role="alert" data-testid="screen-error">
        <strong>This screen hit an error and was stopped.</strong>
        <div className="small">{error.message}</div>
        <div className="row" style={{ marginTop: 8 }}>
          <Button onClick={() => window.location.reload()}>Reload</Button>
          <Button variant="ghost" onClick={() => this.setState({ error: null })}>
            Try again
          </Button>
        </div>
      </div>
    );
  }
}
