/**
 * Privileged actions (E-SEC-03): the server needs a TOTP re-verification within
 * the step-up window and a reason in the X-Action-Reason header. This provider
 * collects both in one dialog, calls /auth/step-up when needed, then runs the
 * action with the reason.
 */
import { useQueryClient } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { createContext, useCallback, useContext, useMemo, useState } from "react";

import { ApiError, api } from "@/api/client";
import { qk } from "@/api/hooks";
import { useAuth } from "@/auth/AuthContext";

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
  /** Ask for a reason (+ TOTP if the step-up window has lapsed), then run the action. */
  run: <T>(title: string, action: (reason: string) => Promise<T>, description?: string) => Promise<T>;
}

const Ctx = createContext<StepUpApi | null>(null);

export function stepUpValid(validUntil: string | null | undefined, now: Date = new Date()): boolean {
  if (!validUntil) return false;
  const t = new Date(validUntil).getTime();
  return Number.isFinite(t) && t - now.getTime() > 15_000;
}

export function StepUpProvider({ children }: { children: ReactNode }) {
  const { me } = useAuth();
  const qc = useQueryClient();
  const [pending, setPending] = useState<Pending | null>(null);
  const [reason, setReason] = useState("");
  const [code, setCode] = useState("");
  const [needCode, setNeedCode] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const run = useCallback(
    <T,>(title: string, action: (reason: string) => Promise<T>, description?: string) =>
      new Promise<T>((resolve, reject) => {
        setReason("");
        setCode("");
        setError(null);
        setNeedCode(!stepUpValid(me?.session.stepup_valid_until ?? null));
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
    setBusy(true);
    setError(null);
    try {
      if (needCode) {
        await api("/auth/step-up", { method: "POST", body: { code: code.trim() } });
        await qc.invalidateQueries({ queryKey: qk.me });
      }
      const out = await pending.action(reason.trim());
      pending.resolve(out);
      setPending(null);
    } catch (e) {
      if (e instanceof ApiError && e.code === "step_up_required") {
        setNeedCode(true);
        setError(new Error("Enter the current code from your authenticator app to confirm."));
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
          {needCode ? (
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
