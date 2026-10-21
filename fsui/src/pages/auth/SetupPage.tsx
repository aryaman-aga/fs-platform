/**
 * First run (desktop edition): the firm and its first administrator are created here, on the PC where FS-Platform
 * runs — no command line, nothing printed. The launcher opens `/setup#token=…` with a one-time token; the fragment
 * never leaves the browser except in the setup request itself. The administrator then signs in and, where the
 * installation requires one, enrols an authenticator app with a QR code (the usual MFA enrolment); otherwise the
 * e-mail and password are all they need.
 */
import { useQueryClient } from "@tanstack/react-query";
import type { FormEvent } from "react";
import { useMemo, useState } from "react";
import { Link, useLocation } from "react-router-dom";

import { api } from "@/api/client";
import { qk, useAuthOptions } from "@/api/hooks";
import type { AuthOptionsOut, SetupIn, SetupOut } from "@/api/types";
import { mfaRequired } from "@/auth/permissions";
import { Button, Checkbox, ErrorNotice, Notice, Spinner, TextInput } from "@/components/ui";
import { AuthLayout } from "@/layout/AuthLayout";

/** The launcher's one-time token from the URL fragment (`#token=…`). */
function setupTokenFrom(hash: string): string | null {
  const token = new URLSearchParams(hash.replace(/^#/, "")).get("token");
  return token && token.trim() ? token.trim() : null;
}

export function SetupPage() {
  const options = useAuthOptions();
  const qc = useQueryClient();
  const { hash } = useLocation();
  const token = useMemo(() => setupTokenFrom(hash), [hash]);
  // works_on_engagements stays null until the box is clicked: see `works` below
  const [f, setF] = useState({ firm_name: "", admin_name: "", admin_email: "", password: "", confirm: "", works_on_engagements: null as boolean | null });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [done, setDone] = useState<SetupOut | null>(null);
  const desktop = options.data?.edition === "desktop";
  const mfa = mfaRequired(options.data);
  const signup = !!options.data?.signup;

  if (done) {
    return (
      <AuthLayout title="Your firm is ready" tag={desktop ? "Desktop edition · this PC" : undefined}>
        <Notice tone="ok">{done.message || "FS-Platform is set up for your firm."}</Notice>
        <ol className="small stack" style={{ paddingLeft: 18, margin: 0 }}>
          <li>
            Sign in as <strong>{done.admin_email}</strong> with the password you chose.
          </li>
          {mfa ? (
            <>
              <li>Scan the QR code with an authenticator app (Microsoft or Google Authenticator) and keep the recovery codes safe.</li>
              <li>Invite your colleagues under Administration → Users; each one sets a password and enrols their own authenticator.</li>
            </>
          ) : signup ? (
            <li>
              Colleagues create their own account with “Create an account” on the sign-in page. Then give each of them a role under
              Administration → Assignments, so that they see their clients.
            </li>
          ) : (
            <li>Invite your colleagues under Administration → Users; each one chooses their own password.</li>
          )}
        </ol>
        <Link to="/login" state={{ email: done.admin_email }} className="btn primary" style={{ textAlign: "center" }}>
          Sign in
        </Link>
      </AuthLayout>
    );
  }
  if (options.isLoading) {
    return (
      <AuthLayout title="Set up FS-Platform">
        <Spinner />
      </AuthLayout>
    );
  }
  if (options.data && !options.data.setup_required) {
    return (
      <AuthLayout title="FS-Platform is already set up">
        <p>Your firm already exists. Sign in with your account, or ask your administrator to invite you.</p>
        <Link to="/login">Sign in</Link>
      </AuthLayout>
    );
  }

  const set = <K extends keyof typeof f>(k: K, v: (typeof f)[K]) => setF((x) => ({ ...x, [k]: v }));
  // The desktop edition starts ticked: a small firm's administrator usually prepares or reviews the statements too, and
  // unticked they would create a client they cannot work on. Elsewhere it starts unticked.
  const works = f.works_on_engagements ?? desktop;
  const mismatch = f.confirm.length > 0 && f.confirm !== f.password;
  const ready = f.firm_name.trim() && f.admin_name.trim() && f.admin_email.trim() && f.password.length >= 12 && f.confirm === f.password;
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!ready) return;
    setBusy(true);
    setError(null);
    try {
      const body: SetupIn = {
        firm_name: f.firm_name.trim(),
        admin_name: f.admin_name.trim(),
        admin_email: f.admin_email.trim(),
        password: f.password,
        works_on_engagements: works,
        setup_token: token,
      };
      const out = await api<SetupOut>("/setup", { method: "POST", body, quiet401: true });
      qc.setQueryData<AuthOptionsOut | undefined>(qk.options, (o) => (o ? { ...o, setup_required: false } : o));
      setDone(out);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };

  return (
    <AuthLayout title="Set up FS-Platform" tag={desktop ? "Desktop edition · runs on this PC only" : "First run"}>
      <p className="small">
        Create your firm and its administrator account.{" "}
        {signup ? "Colleagues then create their own account on the sign-in page." : "Colleagues are invited afterwards and get their own sign-in."}
      </p>
      <form className="stack" onSubmit={(e) => void submit(e)} aria-label="Set up the firm">
        <TextInput label="Firm name" required value={f.firm_name} onChange={(e) => set("firm_name", e.target.value)} autoComplete="organization" autoFocus />
        <TextInput label="Your name" required value={f.admin_name} onChange={(e) => set("admin_name", e.target.value)} autoComplete="name" />
        <TextInput label="Your e-mail" type="email" required value={f.admin_email} onChange={(e) => set("admin_email", e.target.value)} autoComplete="username" hint="You sign in with it. No e-mail is sent." />
        <TextInput
          label="Password"
          type="password"
          required
          minLength={12}
          autoComplete="new-password"
          value={f.password}
          onChange={(e) => set("password", e.target.value)}
          hint="At least 12 characters; common or breached passwords, and ones containing your name or e-mail name, are refused."
        />
        <TextInput label="Repeat password" type="password" required autoComplete="new-password" value={f.confirm} onChange={(e) => set("confirm", e.target.value)} error={mismatch ? "The passwords differ." : undefined} />
        <Checkbox label="I also work on engagements (prepare or review statements)" checked={works} onChange={(e) => set("works_on_engagements", e.target.checked)} />
        <p className="small muted" style={{ marginTop: -4 }}>
          You are then made Partner on every client you create. That weakens segregation of duties, so each such assignment is recorded in
          the audit trail and flagged in access reviews. Change it later under Administration → Firm settings.
        </p>
        <ErrorNotice error={error} title="Not set up" />
        <Button type="submit" variant="primary" busy={busy} disabled={!ready}>
          Create the firm
        </Button>
      </form>
      <p className="small faint">
        {mfa
          ? "Next: sign in and scan a QR code with your authenticator app. Multi-factor authentication is required for every account."
          : "Next: sign in with your e-mail and the password you choose here."}
      </p>
    </AuthLayout>
  );
}
