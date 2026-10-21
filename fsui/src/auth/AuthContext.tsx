import { useQuery, useQueryClient } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { ApiError, api, setCsrfToken, setPasswordChangeHandler, setSessionEndHandler } from "@/api/client";
import { qk } from "@/api/hooks";
import type { MeOut } from "@/api/types";

import { can as canFn, isClientUser, mfaRequired, needsMfa } from "./permissions";

/** "mfa": the password is verified, the authenticator code is still due — never where no authenticator is required, so
 * no guard sends anyone to /mfa or /mfa/enroll there. */
export type AuthStatus = "loading" | "anonymous" | "mfa" | "ready";

export interface AuthState {
  me: MeOut | null;
  status: AuthStatus;
  /** Why the user was sent back to the sign-in page (idle timeout, signed out …). */
  notice: string | null;
  setNotice: (s: string | null) => void;
  refresh: () => Promise<void>;
  can: (permission: string, clientId?: string | null) => boolean;
  signOut: () => Promise<void>;
  /** Sign out and forget the user's cached data without leaving the page (a colleague takes over this PC). */
  endSession: () => Promise<void>;
}

const Ctx = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [notice, setNotice] = useState<string | null>(null);
  const meQuery = useQuery({
    queryKey: qk.me,
    queryFn: async () => {
      try {
        return await api<MeOut>("/auth/me", { quiet401: true });
      } catch (e) {
        if (e instanceof ApiError && e.status === 401) return null;
        throw e;
      }
    },
    staleTime: 60_000,
    retry: false,
  });
  const me = meQuery.data ?? null;
  // Without an authenticator there is no code step: a half-finished sign-in from before it was switched off signs in again.
  const status: AuthStatus = meQuery.isLoading ? "loading" : !me ? "anonymous" : needsMfa(me) ? (mfaRequired(me) ? "mfa" : "anonymous") : "ready";

  useEffect(() => {
    setSessionEndHandler((err) => {
      const msg =
        err.code === "session_idle_timeout"
          ? "You were signed out after a period of inactivity."
          : err.code === "account_disabled"
            ? "Your account is disabled. Contact your Firm Admin."
            : "Your session has ended. Sign in again.";
      setNotice(msg);
      setCsrfToken(null);
      qc.setQueryData(qk.me, null);
      qc.removeQueries({ predicate: (q) => q.queryKey[0] !== "me" });
      navigate("/login", { replace: true });
    });
    return () => setSessionEndHandler(null);
  }, [qc, navigate]);

  useEffect(() => {
    // a one-time password: the server refuses everything but the change, so send the user to the form
    setPasswordChangeHandler(() => {
      void qc.invalidateQueries({ queryKey: qk.me });
      navigate("/change-password", { replace: true });
    });
    return () => setPasswordChangeHandler(null);
  }, [qc, navigate]);

  const refresh = useCallback(async () => {
    await qc.invalidateQueries({ queryKey: qk.me });
    await qc.refetchQueries({ queryKey: qk.me });
  }, [qc]);

  const endSession = useCallback(async () => {
    try {
      await api("/auth/logout", { method: "POST", quiet401: true });
    } catch {
      // already signed out server-side
    }
    setCsrfToken(null);
    // nothing of the previous user stays in memory; the public sign-in options do
    qc.removeQueries({ predicate: (q) => q.queryKey[0] !== qk.options[0] });
    qc.setQueryData(qk.me, null);
  }, [qc]);

  const signOut = useCallback(async () => {
    await endSession();
    navigate("/login", { replace: true });
  }, [endSession, navigate]);

  const value = useMemo<AuthState>(
    () => ({
      me,
      status,
      notice,
      setNotice,
      refresh,
      can: (permission: string, clientId?: string | null) => canFn(me, permission, clientId),
      signOut,
      endSession,
    }),
    [me, status, notice, refresh, signOut, endSession],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth(): AuthState {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAuth outside AuthProvider");
  return v;
}

export function useIsClientUser(): boolean {
  return isClientUser(useAuth().me);
}
