/** Sign-in, MFA verification / enrolment, password reset, the forced change of a one-time password and invitation
 * acceptance (05 §1). */
import { useQueryClient } from "@tanstack/react-query";
import type { FormEvent } from "react";
import { useState } from "react";
import { Link, Navigate, useLocation, useNavigate, useSearchParams } from "react-router-dom";

import { ApiError, api, setCsrfToken } from "@/api/client";
import { qk, useAuthOptions } from "@/api/hooks";
import type { AuthOkOut, LoginOut, MfaEnrollOut, OkOut, RecoveryCodesOut } from "@/api/types";
import { useAuth } from "@/auth/AuthContext";
import { mustChangePassword } from "@/auth/permissions";
import { Button, ErrorNotice, Notice, Spinner, TextInput } from "@/components/ui";
import { AuthLayout } from "@/layout/AuthLayout";

const PASSWORD_HINT = "At least 12 characters; common or breached passwords, and ones containing your name, are refused.";

function useAfterLogin() {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const location = useLocation();
  const from = (location.state as { from?: string } | null)?.from;
  return async () => {
    await qc.invalidateQueries({ queryKey: qk.me });
    await qc.refetchQueries({ queryKey: qk.me });
    navigate(from && !from.startsWith("/login") && !from.startsWith("/mfa") ? from : "/", { replace: true });
  };
}

export function LoginPage() {
  const { status, notice, setNotice, me } = useAuth();
  const options = useAuthOptions();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const location = useLocation();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  if (status === "ready") return <Navigate to="/" replace />;
  if (status === "mfa" && me) return <Navigate to={me.user.mfa_enrolled_at ? "/mfa" : "/mfa/enroll"} replace />;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const out = await api<LoginOut>("/auth/login", { method: "POST", body: { email, password }, quiet401: true });
      setCsrfToken(out.csrf_token);
      setNotice(null);
      await qc.refetchQueries({ queryKey: qk.me });
      navigate(out.status === "mfa_required" ? "/mfa" : "/mfa/enroll", { replace: true, state: location.state });
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };

  return (
    <AuthLayout title="FS-Platform" tag="Ind AS financial statements — preparation and review">
      {notice ? <Notice tone="warn">{notice}</Notice> : null}
      <form className="stack" onSubmit={(e) => void submit(e)} aria-label="Sign in">
        <TextInput label="E-mail" type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} autoFocus />
        <TextInput label="Password" type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
        <ErrorNotice error={error} title="Sign-in failed" />
        <Button type="submit" variant="primary" busy={busy}>
          Sign in
        </Button>
      </form>
      <div className="row between small">
        <Link to="/forgot-password">Forgot password?</Link>
        {options.data?.sso ? <a href="/api/v1/auth/oidc/login">Firm single sign-on</a> : null}
      </div>
      <p className="small faint">Multi-factor authentication is required for every account. Activity is recorded in the audit trail.</p>
    </AuthLayout>
  );
}

export function MfaPage() {
  const { status, me, signOut } = useAuth();
  const after = useAfterLogin();
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  if (status === "anonymous") return <Navigate to="/login" replace />;
  if (status === "ready") return <Navigate to="/" replace />;
  if (me && !me.user.mfa_enrolled_at) return <Navigate to="/mfa/enroll" replace />;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const out = await api<AuthOkOut>("/auth/mfa/verify", { method: "POST", body: { code: code.trim() }, quiet401: true });
      setCsrfToken(out.csrf_token);
      await after();
    } catch (err) {
      setError(err);
      if (err instanceof ApiError && err.status === 401 && err.code !== "mfa_invalid") await signOut();
    } finally {
      setBusy(false);
    }
  };

  return (
    <AuthLayout title="Verify it's you" tag={me ? `Signed in as ${me.user.email}` : undefined}>
      <form className="stack" onSubmit={(e) => void submit(e)} aria-label="Multi-factor verification">
        <TextInput
          label="Authenticator code"
          inputMode="numeric"
          autoComplete="one-time-code"
          className="otp-input"
          maxLength={20}
          value={code}
          onChange={(e) => setCode(e.target.value.replace(/\s/g, ""))}
          hint="Enter the 6-digit code from your authenticator app, or one of your recovery codes."
          autoFocus
          required
        />
        <ErrorNotice error={error} title="Not verified" />
        <Button type="submit" variant="primary" busy={busy}>
          Verify
        </Button>
      </form>
      <Button variant="ghost" onClick={() => void signOut()}>
        Use a different account
      </Button>
    </AuthLayout>
  );
}

export function MfaEnrollPage() {
  const { status, me, signOut } = useAuth();
  const after = useAfterLogin();
  const [enrol, setEnrol] = useState<MfaEnrollOut | null>(null);
  const [codes, setCodes] = useState<string[] | null>(null);
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [saved, setSaved] = useState(false);
  if (status === "anonymous") return <Navigate to="/login" replace />;
  if (status === "ready" && !codes) return <Navigate to="/" replace />;

  const start = async () => {
    setBusy(true);
    setError(null);
    try {
      setEnrol(await api<MfaEnrollOut>("/auth/mfa/enroll", { method: "POST", quiet401: true }));
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };
  const confirm = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const out = await api<RecoveryCodesOut>("/auth/mfa/enroll/confirm", { method: "POST", body: { code: code.trim() }, quiet401: true });
      setCsrfToken(out.csrf_token);
      setCodes(out.recovery_codes);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };
  const download = () => {
    const blob = new Blob([`FS-Platform recovery codes for ${me?.user.email ?? ""}\n\n${(codes ?? []).join("\n")}\n`], { type: "text/plain" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "fs-platform-recovery-codes.txt";
    a.click();
    URL.revokeObjectURL(url);
  };

  if (codes) {
    return (
      <AuthLayout title="Save your recovery codes" tag="They are shown only once.">
        <Notice tone="warn">Each code works once if you lose your authenticator. Store them in your password manager.</Notice>
        <div className="codes" data-testid="recovery-codes">
          {codes.map((c) => (
            <span key={c}>{c}</span>
          ))}
        </div>
        <div className="row">
          <Button onClick={download}>Download as text</Button>
        </div>
        <label className="checkbox">
          <input type="checkbox" checked={saved} onChange={(e) => setSaved(e.target.checked)} />
          <span>I have stored these codes safely</span>
        </label>
        <Button variant="primary" disabled={!saved} onClick={() => void after()}>
          Continue
        </Button>
      </AuthLayout>
    );
  }

  return (
    <AuthLayout title="Set up your authenticator" tag={me ? `Account ${me.user.email}` : undefined}>
      {!enrol ? (
        <>
          <p>Multi-factor authentication is mandatory. You need an authenticator app (Microsoft or Google Authenticator, or similar).</p>
          <ErrorNotice error={error} />
          <Button variant="primary" busy={busy} onClick={() => void start()}>
            Start set-up
          </Button>
        </>
      ) : (
        <form className="stack" onSubmit={(e) => void confirm(e)}>
          <p className="small">Scan this code with your authenticator app, then enter the 6-digit code it shows.</p>
          <div className="qr">
            <img src={enrol.qr_svg_data_uri} alt="QR code for the authenticator app" />
          </div>
          <details>
            <summary className="small">Can't scan? Enter the key manually</summary>
            <code className="mono" style={{ wordBreak: "break-all" }}>
              {enrol.secret}
            </code>
          </details>
          <TextInput label="Code from the app" inputMode="numeric" autoComplete="one-time-code" className="otp-input" maxLength={8} value={code} onChange={(e) => setCode(e.target.value.replace(/\s/g, ""))} required />
          <ErrorNotice error={error} title="Not confirmed" />
          <Button type="submit" variant="primary" busy={busy}>
            Confirm
          </Button>
        </form>
      )}
      <Button variant="ghost" onClick={() => void signOut()}>
        Cancel and sign out
      </Button>
    </AuthLayout>
  );
}

export function ForgotPasswordPage() {
  const options = useAuthOptions();
  const [email, setEmail] = useState("");
  const [done, setDone] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  if (options.isLoading) {
    return (
      <AuthLayout title="Reset your password">
        <Spinner />
      </AuthLayout>
    );
  }
  if (options.data && !options.data.self_service_reset) {
    // no e-mail (desktop edition, or no mail server): an administrator issues a reset link instead
    return (
      <AuthLayout title="Reset your password">
        <Notice>Ask your administrator to reset your password.</Notice>
        <p className="small muted">
          A Firm Admin creates a one-time reset link under Administration → Users → Reset password and gives it to you
          {options.data.edition === "desktop" ? "; it opens on this PC." : "."} If you also lost your authenticator app, ask them to reset MFA too.
        </p>
        <Link to="/login" className="small">
          Back to sign in
        </Link>
      </AuthLayout>
    );
  }
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const out = await api<OkOut>("/auth/password/forgot", { method: "POST", body: { email }, quiet401: true });
      setDone(out.message);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };
  return (
    <AuthLayout title="Reset your password">
      {done ? (
        <Notice tone="ok">{done}</Notice>
      ) : (
        <form className="stack" onSubmit={(e) => void submit(e)}>
          <TextInput label="E-mail" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} autoFocus />
          <ErrorNotice error={error} />
          <Button type="submit" variant="primary" busy={busy}>
            Send reset link
          </Button>
        </form>
      )}
      <Link to="/login" className="small">
        Back to sign in
      </Link>
    </AuthLayout>
  );
}

function PasswordForm({ endpoint, title, submitLabel }: { endpoint: string; title: string; submitLabel: string }) {
  const { me, endSession } = useAuth();
  const [params] = useSearchParams();
  const token = params.get("token") ?? "";
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [done, setDone] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const mismatch = confirm.length > 0 && confirm !== password;
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (mismatch || password.length < 12) return;
    setBusy(true);
    setError(null);
    try {
      const out = await api<OkOut>(endpoint, { method: "POST", body: { token, password }, quiet401: true });
      // A shared PC: whoever handed over the link may still be signed in here; the new password's owner signs in next.
      if (me) await endSession();
      setDone(out.message);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };
  return (
    <AuthLayout title={title}>
      {!token ? <Notice tone="error">This link is incomplete. Open the whole link you were given again.</Notice> : null}
      {me && !done ? (
        <Notice tone="warn">
          {me.user.email} is signed in on this browser. Setting the password signs them out, so that the account the link is for can sign in.
        </Notice>
      ) : null}
      {done ? (
        <>
          <Notice tone="ok">{done}</Notice>
          <Link to="/login">Sign in</Link>
        </>
      ) : (
        <form className="stack" onSubmit={(e) => void submit(e)}>
          <TextInput label="New password" type="password" autoComplete="new-password" required minLength={12} value={password} onChange={(e) => setPassword(e.target.value)} hint={PASSWORD_HINT} />
          <TextInput label="Repeat password" type="password" autoComplete="new-password" required value={confirm} onChange={(e) => setConfirm(e.target.value)} error={mismatch ? "The passwords differ." : undefined} />
          <ErrorNotice error={error} />
          <Button type="submit" variant="primary" busy={busy} disabled={!token || mismatch || password.length < 12}>
            {submitLabel}
          </Button>
        </form>
      )}
    </AuthLayout>
  );
}

export function ResetPasswordPage() {
  return <PasswordForm endpoint="/auth/password/reset" title="Choose a new password" submitLabel="Change password" />;
}

export function AcceptInvitePage() {
  return <PasswordForm endpoint="/auth/invite/accept" title="Welcome to FS-Platform" submitLabel="Set password" />;
}

/** Signed in with a one-time password (printed by the CLI, or generated): the server serves nothing else until the
 * user chooses their own password, so every screen leads here. */
export function ChangePasswordPage() {
  const { status, me, refresh, signOut } = useAuth();
  const navigate = useNavigate();
  const [current, setCurrent] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  if (status === "loading") {
    return (
      <div className="auth-wrap">
        <Spinner />
      </div>
    );
  }
  if (status === "anonymous" || !me) return <Navigate to="/login" replace />;
  if (status === "mfa") return <Navigate to={me.user.mfa_enrolled_at ? "/mfa" : "/mfa/enroll"} replace />;
  if (!mustChangePassword(me)) return <Navigate to="/" replace />;

  const mismatch = confirm.length > 0 && confirm !== password;
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (mismatch || password.length < 12 || !current) return;
    setBusy(true);
    setError(null);
    try {
      await api<OkOut>("/auth/password/change", { method: "POST", body: { current_password: current, new_password: password } });
      await refresh();
      navigate("/", { replace: true });
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };
  return (
    <AuthLayout title="Choose your own password" tag={`Signed in as ${me.user.email}`}>
      <Notice tone="warn">You signed in with a one-time password. Choose your own password before you continue.</Notice>
      <form className="stack" onSubmit={(e) => void submit(e)} aria-label="Change password">
        <TextInput label="One-time password" type="password" autoComplete="current-password" required value={current} onChange={(e) => setCurrent(e.target.value)} />
        <TextInput label="New password" type="password" autoComplete="new-password" required minLength={12} value={password} onChange={(e) => setPassword(e.target.value)} hint={PASSWORD_HINT} />
        <TextInput label="Repeat new password" type="password" autoComplete="new-password" required value={confirm} onChange={(e) => setConfirm(e.target.value)} error={mismatch ? "The passwords differ." : undefined} />
        <ErrorNotice error={error} title="Not changed" />
        <Button type="submit" variant="primary" busy={busy} disabled={!current || mismatch || password.length < 12}>
          Change password
        </Button>
      </form>
      <Button variant="ghost" onClick={() => void signOut()}>
        Sign out
      </Button>
    </AuthLayout>
  );
}
