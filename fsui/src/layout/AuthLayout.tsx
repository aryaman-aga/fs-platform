import type { ReactNode } from "react";

export function AuthLayout({ title, tag, children }: { title: string; tag?: string; children: ReactNode }) {
  return (
    <div className="auth-wrap">
      <main className="card auth-card" aria-labelledby="auth-title">
        <div className="card-body stack">
          <div className="brand">
            <img src="/favicon.svg" alt="" />
            <span id="auth-title">{title}</span>
          </div>
          {tag ? <div className="tag">{tag}</div> : null}
          {children}
        </div>
      </main>
    </div>
  );
}
