/**
 * "Create an account" (desktop edition, where the installation allows it): a colleague enters their name, e-mail and a
 * password and is signed in at once. The new account has no role, so it sees no client until an administrator gives it
 * one (Administration → Assignments). The people signing up are not IT staff: every message is in plain words.
 */
import { useQueryClient } from "@tanstack/react-query";
import type { ChangeEvent, FormEvent } from "react";
import { useState } from "react";
import { Link, Navigate, useNavigate } from "react-router-dom";

import { ApiError, api, setCsrfToken } from "@/api/client";
import { qk, useAuthOptions } from "@/api/hooks";
import type { LoginOut, SignupIn } from "@/api/types";
import { useAuth } from "@/auth/AuthContext";
import { Button, ErrorNotice, Notice, Spinner, TextInput } from "@/components/ui";
import { AuthLayout } from "@/layout/AuthLayout";

const MIN_PASSWORD = 12;
const PASSWORD_HINT = "At least 12 characters — a short sentence works well, e.g. ‘Blue tea cups at noon’";

/** "password must not contain your name" → "Password must not contain your name." */
function sentence(s: string): string {
  const t = s.trim();
  if (!t) return t;
  return `${t.charAt(0).toUpperCase()}${t.slice(1)}${/[.!?]$/.test(t) ? "" : "."}`;
}

/** The server's refusals in plain words; anything unexpected keeps its code and reference for support. */
function SignupError({ error, email }: { error: unknown; email: string }) {
  if (!error) return null;
  const code = error instanceof ApiError ? error.code : null;
  if (code === "email_taken") {
    return (
      <Notice tone="error">
        There is already an account with this e-mail address.{" "}
        <Link to="/login" state={{ email }}>
          Sign in instead
        </Link>
      </Notice>
    );
  }
  if (code === "signup_disabled") return <Notice tone="error">You can't create an account yourself here. Ask your administrator to create your account.</Notice>;
  if (code === "rate_limited") return <Notice tone="error">That was a lot of tries in a short time. Wait a minute, then try again.</Notice>;
  return <ErrorNotice error={error} title="Your account was not created" />;
}

export function SignupPage() {
  const options = useAuthOptions();
  const { status, me, setNotice } = useAuth();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [f, setF] = useState({ name: "", email: "", password: "", confirm: "" });
  const [tried, setTried] = useState(false);
  const [refused, setRefused] = useState<string | null>(null); // the server's password rule, shown under the field
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  if (options.isLoading || status === "loading") {
    return (
      <AuthLayout title="Create your account">
        <Spinner />
      </AuthLayout>
    );
  }
  if (options.data?.setup_required) return <Navigate to="/setup" replace />;
  if (status === "ready") return <Navigate to="/" replace />;
  if (status === "mfa" && me) return <Navigate to={me.user.mfa_enrolled_at ? "/mfa" : "/mfa/enroll"} replace />;
  if (!options.data?.signup) return <Navigate to="/login" replace />;

  const set = (k: keyof typeof f) => (e: ChangeEvent<HTMLInputElement>) => {
    const v = e.target.value;
    setF((x) => ({ ...x, [k]: v }));
    if (k === "password") setRefused(null);
  };
  const short = f.password.length < MIN_PASSWORD;
  // a typo in the repeat shows at once; a repeat still being typed only once "Create account" was pressed
  const mismatch = (f.confirm.length > 0 && !f.password.startsWith(f.confirm)) || (tried && f.confirm !== f.password);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setTried(true);
    if (!f.name.trim() || !f.email.trim() || short || f.confirm !== f.password) return;
    setBusy(true);
    setError(null);
    try {
      const body: SignupIn = { name: f.name.trim(), email: f.email.trim(), password: f.password };
      const out = await api<LoginOut>("/auth/signup", { method: "POST", body, quiet401: true });
      setCsrfToken(out.csrf_token);
      setNotice(null);
      await qc.invalidateQueries({ queryKey: qk.me });
      await qc.refetchQueries({ queryKey: qk.me });
      // signed in; where the installation also requires an authenticator, it is set up first
      navigate(out.status === "ok" ? "/" : out.status === "mfa_required" ? "/mfa" : "/mfa/enroll", { replace: true });
    } catch (err) {
      if (err instanceof ApiError && err.code === "setup_required") {
        // no firm yet: the first start sets it up
        await qc.invalidateQueries({ queryKey: qk.options });
        navigate("/setup", { replace: true });
      } else if (err instanceof ApiError && err.code === "password_policy") {
        setRefused(sentence(err.message));
      } else {
        setError(err);
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <AuthLayout title="Create your account">
      <p className="small">Use your own e-mail address. Once your account is ready, your FS-Platform administrator gives you access to your clients.</p>
      <form className="stack" onSubmit={(e) => void submit(e)} aria-label="Create your account">
        <TextInput label="Full name" required autoComplete="name" value={f.name} onChange={set("name")} autoFocus />
        <TextInput label="E-mail" type="email" required autoComplete="email" value={f.email} onChange={set("email")} />
        <TextInput
          label="Password"
          type="password"
          required
          autoComplete="new-password"
          value={f.password}
          onChange={set("password")}
          hint={PASSWORD_HINT}
          error={refused ?? (tried && short ? "Use at least 12 characters." : undefined)}
        />
        <TextInput
          label="Confirm password"
          type="password"
          required
          autoComplete="new-password"
          value={f.confirm}
          onChange={set("confirm")}
          error={mismatch ? "The passwords don't match." : undefined}
        />
        <SignupError error={error} email={f.email.trim()} />
        <Button type="submit" variant="primary" busy={busy}>
          Create account
        </Button>
      </form>
      <p className="small">
        Already have an account? <Link to="/login">Sign in</Link>
      </p>
    </AuthLayout>
  );
}
