import type { ReactNode } from "react";
import { createContext, useCallback, useContext, useMemo, useState } from "react";

import { describeError } from "@/api/client";

type Tone = "ok" | "error" | "info";

interface ToastItem {
  id: number;
  tone: Tone;
  text: string;
  cid?: string | null;
}

interface ToastApi {
  ok: (text: string) => void;
  info: (text: string) => void;
  error: (err: unknown, prefix?: string) => void;
}

const Ctx = createContext<ToastApi | null>(null);
let seq = 0;

/** Toasts announce results politely (aria-live); errors carry the correlation id. */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const push = useCallback((t: Omit<ToastItem, "id">) => {
    const id = ++seq;
    setItems((xs) => [...xs, { ...t, id }].slice(-5));
    window.setTimeout(() => setItems((xs) => xs.filter((x) => x.id !== id)), t.tone === "error" ? 12000 : 5000);
  }, []);
  const api = useMemo<ToastApi>(
    () => ({
      ok: (text) => push({ tone: "ok", text }),
      info: (text) => push({ tone: "info", text }),
      error: (err, prefix) => {
        const d = describeError(err);
        push({ tone: "error", text: prefix ? `${prefix}: ${d.message}` : d.message, cid: d.correlationId });
      },
    }),
    [push],
  );
  return (
    <Ctx.Provider value={api}>
      {children}
      <div className="toasts" aria-live="polite" aria-atomic="false">
        {items.map((t) => (
          <div key={t.id} className={`toast ${t.tone}`} role={t.tone === "error" ? "alert" : "status"}>
            <div>{t.text}</div>
            {t.cid ? <div className="cid">ref {t.cid}</div> : null}
          </div>
        ))}
      </div>
    </Ctx.Provider>
  );
}

export function useToast(): ToastApi {
  const v = useContext(Ctx);
  if (!v) throw new Error("useToast outside ToastProvider");
  return v;
}
