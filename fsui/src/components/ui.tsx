/** Small in-house component set (no UI kit): buttons, cards, fields, badges, notices. */
import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode, SelectHTMLAttributes, TextareaHTMLAttributes } from "react";
import { useId } from "react";

import { describeError } from "@/api/client";
import { titleCase } from "@/lib/format";

type BtnVariant = "default" | "primary" | "danger" | "ghost";

export function Button({
  variant = "default",
  size,
  busy,
  children,
  className,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: BtnVariant; size?: "sm"; busy?: boolean }) {
  const cls = ["btn", variant !== "default" ? variant : "", size ?? "", className ?? ""].filter(Boolean).join(" ");
  return (
    <button type="button" className={cls} {...rest} disabled={rest.disabled || busy} aria-busy={busy || undefined}>
      {busy ? <span className="spinner" aria-hidden="true" /> : null}
      {children}
    </button>
  );
}

export function Card({
  title,
  actions,
  children,
  className,
  bodyClass,
}: {
  title?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClass?: string;
}) {
  return (
    <section className={`card ${className ?? ""}`}>
      {title || actions ? (
        <header className="card-header">
          {typeof title === "string" ? <h2>{title}</h2> : title}
          {actions ? <div className="row">{actions}</div> : null}
        </header>
      ) : null}
      <div className={bodyClass ?? "card-body"}>{children}</div>
    </section>
  );
}

export function PageHeader({ title, sub, actions, children }: { title: ReactNode; sub?: ReactNode; actions?: ReactNode; children?: ReactNode }) {
  return (
    <div className="page-header">
      <div>
        <h1>{title}</h1>
        {sub ? <div className="sub">{sub}</div> : null}
        {children}
      </div>
      {actions ? <div className="row">{actions}</div> : null}
    </div>
  );
}

export function Field({ label, hint, error, children, htmlFor }: { label: ReactNode; hint?: ReactNode; error?: ReactNode; children: ReactNode; htmlFor?: string }) {
  return (
    <div className="field">
      <label htmlFor={htmlFor}>{label}</label>
      {children}
      {hint ? <span className="hint">{hint}</span> : null}
      {error ? (
        <span className="error-text" role="alert">
          {error}
        </span>
      ) : null}
    </div>
  );
}

export function TextInput({ label, hint, error, ...rest }: InputHTMLAttributes<HTMLInputElement> & { label: ReactNode; hint?: ReactNode; error?: ReactNode }) {
  const id = useId();
  return (
    <Field label={label} hint={hint} error={error} htmlFor={rest.id ?? id}>
      <input type="text" id={rest.id ?? id} aria-invalid={error ? true : undefined} {...rest} />
    </Field>
  );
}

export function SelectInput({
  label,
  hint,
  options,
  ...rest
}: SelectHTMLAttributes<HTMLSelectElement> & { label: ReactNode; hint?: ReactNode; options: (string | { value: string; label: string })[] }) {
  const id = useId();
  return (
    <Field label={label} hint={hint} htmlFor={rest.id ?? id}>
      <select id={rest.id ?? id} {...rest}>
        {options.map((o) =>
          typeof o === "string" ? (
            <option key={o} value={o}>
              {o}
            </option>
          ) : (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ),
        )}
      </select>
    </Field>
  );
}

export function TextArea({ label, hint, error, ...rest }: TextareaHTMLAttributes<HTMLTextAreaElement> & { label: ReactNode; hint?: ReactNode; error?: ReactNode }) {
  const id = useId();
  return (
    <Field label={label} hint={hint} error={error} htmlFor={rest.id ?? id}>
      <textarea id={rest.id ?? id} aria-invalid={error ? true : undefined} {...rest} />
    </Field>
  );
}

export function Checkbox({ label, ...rest }: InputHTMLAttributes<HTMLInputElement> & { label: ReactNode }) {
  return (
    <label className="checkbox">
      <input type="checkbox" {...rest} />
      <span>{label}</span>
    </label>
  );
}

const SEV_ICON: Record<string, string> = { High: "▲", Medium: "◆", Low: "●", Info: "ℹ" };

/** Severity carries an icon and a word, not only a colour (WCAG 1.4.1). */
export function SeverityBadge({ severity }: { severity: string }) {
  return (
    <span className={`badge sev-${severity}`}>
      <span aria-hidden="true">{SEV_ICON[severity] ?? "•"}</span>
      {severity}
    </span>
  );
}

const STATUS_TONE: Record<string, string> = {
  DRAFT_SETUP: "",
  DATA_RECEIVED: "accent",
  CHECKED: "warn",
  FIX_PLANNED: "warn",
  FIX_APPROVED: "warn",
  FIXED_VERIFIED: "ok",
  REVIEW: "accent",
  DRAFT_RELEASED_TO_CLIENT: "accent",
  MGMT_CONFIRMED: "ok",
  FINAL_LOCKED: "ok",
  ARCHIVED: "",
};

export function EngagementStatusChip({ status }: { status: string }) {
  const locked = status === "FINAL_LOCKED" || status === "ARCHIVED";
  return (
    <span className={`chip ${STATUS_TONE[status] ?? ""}`} title={status}>
      {locked ? <span aria-hidden="true">🔒</span> : <span className="dot" aria-hidden="true" />}
      {titleCase(status)}
    </span>
  );
}

const RUN_TONE: Record<string, string> = { succeeded: "ok", failed: "danger", running: "accent", queued: "", retrying: "warn" };

export function RunStatusChip({ status }: { status: string }) {
  return <span className={`chip ${RUN_TONE[status] ?? ""}`}>{status}</span>;
}

export function Chip({ tone, children, title }: { tone?: "ok" | "warn" | "danger" | "accent"; children: ReactNode; title?: string }) {
  return (
    <span className={`chip ${tone ?? ""}`} title={title}>
      {children}
    </span>
  );
}

export function Spinner({ label = "Loading" }: { label?: string }) {
  return (
    <span role="status" className="row muted">
      <span className="spinner" aria-hidden="true" /> {label}…
    </span>
  );
}

export function EmptyState({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

export function Notice({ tone = "info", children }: { tone?: "info" | "ok" | "warn" | "error"; children: ReactNode }) {
  return (
    <div className={`notice ${tone}`} role={tone === "error" ? "alert" : "status"}>
      {children}
    </div>
  );
}

/** API errors show the message, the error code and the correlation id — never a stack trace (08 §4). */
export function ErrorNotice({ error, title }: { error: unknown; title?: string }) {
  if (!error) return null;
  const d = describeError(error);
  return (
    <div className="notice error" role="alert">
      <strong>{title ?? "Something went wrong"}:</strong> {d.message}
      {d.code || d.correlationId ? (
        <span className="cid">
          {d.code ? `code ${d.code}` : ""}
          {d.code && d.correlationId ? " · " : ""}
          {d.correlationId ? `ref ${d.correlationId}` : ""}
        </span>
      ) : null}
    </div>
  );
}

export function KeyValue({ items }: { items: [ReactNode, ReactNode][] }) {
  return (
    <dl className="kv">
      {items.map(([k, v], i) => (
        <div key={i} style={{ display: "contents" }}>
          <dt>{k}</dt>
          <dd>{v ?? "—"}</dd>
        </div>
      ))}
    </dl>
  );
}

export function ProgressBar({ value, label }: { value: number; label?: string }) {
  const pct = Math.max(0, Math.min(100, Math.round(value * 100)));
  return (
    <div className="stack" style={{ gap: 4 }}>
      <div className="progress" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct} aria-label={label ?? "Progress"}>
        <span style={{ width: `${pct}%` }} />
      </div>
      {label ? <span className="small muted">{label}</span> : null}
    </div>
  );
}

export function Stat({ label, value, tone }: { label: string; value: ReactNode; tone?: "ok" | "warn" | "danger" }) {
  return (
    <div className="card stat">
      <div className="label">{label}</div>
      <div className="value" style={tone ? { color: `var(--${tone})` } : undefined}>
        {value}
      </div>
    </div>
  );
}
