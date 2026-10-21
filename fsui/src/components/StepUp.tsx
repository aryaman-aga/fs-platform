/**
 * Privileged actions (E-SEC-03): the server needs a fresh confirmation within
 * the step-up window — the authenticator (TOTP) code, or the password where the
 * installation requires no authenticator — and a reason in the X-Action-Reason
 * header. This provider collects both in one dialog, calls /auth/step-up when
 * needed, then runs the action with the reason.
 */
import { useQueryClient } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { createContext, useCallback, useContext, useMemo, useState } from "react";

import { ApiError, api } from "@/api/client";
import { qk } from "@/api/hooks";
import type { StepUpIn } from "@/api/types";
import { useAuth } from "@/auth/AuthContext";
import { mfaRequired } from "@/auth/permissions";

import { Modal } from "./Modal";
import { Button, ErrorNotice, TextInput } from "./ui";

interface Pending {
  title: string;
  description?: string;
  action: (reason: string) => Promise<unknown>;
  resolve: (v: unknown) => void;
  reject: (e: unknown) => void;
}

interface StepUpApi {
  /** Ask for a reason (+ the code or password if the step-up window has lapsed), then run the action. */
  run: <T>(title: string, action: (reason: string) => Promise<T>, description?: string) => Promise<T>;
}

const ENTER_PASSWORD = "Enter your password to continue.";

const Ctx = createContext<StepUpApi | null>(null);

export function stepUpValid(validUntil: string | null | undefined, now: Date = new Date()): boolean {
  if (!validUntil) return false;
  const t = new Date(validUntil).getTime();
  return Number.isFinite(t) && t - now.getTime() > 15_000;
}

export function StepUpProvider({ children }: { children: ReactNode }) {
  const { me } = useAuth();
  const qc = useQueryClient();
  // no authenticator on this installation: "Confirm it's you" asks for the password instead of a code
  const withPassword = !!me && !mfaRequired(me);
  const [pending, setPending] = useState<Pending | null>(null);
  const [reason, setReason] = useState("");
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [needConfirm, setNeedConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const run = useCallback(
    <T,>(title: string, action: (reason: string) => Promise<T>, description?: string) =>
      new Promise<T>((resolve, reject) => {
        setReason("");
        setCode("");
        setPassword("");
        setError(null);
        setNeedConfirm(!stepUpValid(me?.session.stepup_valid_until ?? null));
        setPending({ title, description, action: action as (r: string) => Promise<unknown>, resolve: resolve as (v: unknown) => void, reject });
      }),
    [me],
  );

  const close = () => {
    pending?.reject(new Error("cancelled"));
    setPending(null);
  };

  const submit = async () => {
    if (!pending) return;
    if (reason.trim().length < 5) {
      setError(new Error("Give a reason of at least 5 characters (it is recorded in the audit trail)."));
      return;
    }
    if (needConfirm && withPassword && !password) {
      setError(new Error(ENTER_PASSWORD));
      return;
    }
    setBusy(true);
    setError(null);
    try {
      if (needConfirm) {
        const body: StepUpIn = withPassword ? { password } : { code: code.trim() };
        await api("/auth/step-up", { method: "POST", body });
        await qc.invalidateQueries({ queryKey: qk.me });
      }
      const out = await pending.action(reason.trim());
      pending.resolve(out);
      setPending(null);
    } catch (e) {
      if (e instanceof ApiError && e.code === "step_up_required") {
        setNeedConfirm(true);
        setError(new Error(withPassword ? ENTER_PASSWORD : "Enter the current code from your authenticator app to confirm."));
      } else if (e instanceof ApiError && e.code === "invalid_password") {
        setPassword("");
        setError(new Error("That password isn't right. Try again."));
      } else if (e instanceof ApiError && e.code === "password_required") {
        setError(new Error(ENTER_PASSWORD));
      } else {
        setError(e);
      }
    } finally {
      setBusy(false);
    }
  };

  const value = useMemo(() => ({ run }), [run]);
  return (
    <Ctx.Provider value={value}>
      {children}
      {pending ? (
        <Modal
          title={pending.title}
          onClose={close}
          footer={
            <>
              <Button onClick={close}>Cancel</Button>
              <Button variant="primary" busy={busy} onClick={() => void submit()}>
                Confirm
              </Button>
            </>
          }
        >
          {pending.description ? <p className="muted">{pending.description}</p> : null}
          <TextInput
            label="Reason (recorded in the audit trail)"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="e.g. leaver per HR ticket 1234"
          />
          {needConfirm && withPassword ? (
            <TextInput
              label="Your password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              hint={`Confirm it's you. ${ENTER_PASSWORD}`}
            />
          ) : needConfirm ? (
            <TextInput
              label="Authenticator code"
              inputMode="numeric"
              autoComplete="one-time-code"
              className="otp-input"
              maxLength={8}
              value={code}
              onChange={(e) => setCode(e.target.value.replace(/\s/g, ""))}
              hint="Administrative actions need a fresh MFA confirmation (step-up)."
            />
          ) : withPassword ? (
            <p className="small muted">You confirmed it's you a moment ago, so your password isn't needed again yet.</p>
          ) : (
            <p className="small muted">Your MFA step-up is still valid; no code needed.</p>
          )}
          <ErrorNotice error={error} title="Not done" />
        </Modal>
      ) : null}
    </Ctx.Provider>
  );
}

export function useStepUp(): StepUpApi {
  const v = useContext(Ctx);
  if (!v) throw new Error("useStepUp outside StepUpProvider");
  return v;
}
