import { useQuery, useQueryClient } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { ApiError, api, setCsrfToken, setSessionEndHandler } from "@/api/client";
import { qk } from "@/api/hooks";
import type { MeOut } from "@/api/types";

import { can as canFn, isClientUser, needsMfa } from "./permissions";

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
  const status: AuthStatus = meQuery.isLoading ? "loading" : !me ? "anonymous" : needsMfa(me) ? "mfa" : "ready";

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

  const refresh = useCallback(async () => {
    await qc.invalidateQueries({ queryKey: qk.me });
    await qc.refetchQueries({ queryKey: qk.me });
  }, [qc]);

  const signOut = useCallback(async () => {
    try {
      await api("/auth/logout", { method: "POST", quiet401: true });
    } catch {
      // already signed out server-side
    }
    setCsrfToken(null);
    qc.clear();
    qc.setQueryData(qk.me, null);
    navigate("/login", { replace: true });
  }, [qc, navigate]);

  const value = useMemo<AuthState>(
    () => ({
      me,
      status,
      notice,
      setNotice,
      refresh,
      can: (permission: string, clientId?: string | null) => canFn(me, permission, clientId),
      signOut,
    }),
    [me, status, notice, refresh, signOut],
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
