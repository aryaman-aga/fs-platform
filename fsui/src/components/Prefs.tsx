import type { ReactNode } from "react";
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

import type { AmountOptions, Grouping } from "@/lib/format";
import { formatAmount } from "@/lib/format";
import type { Prefs, Theme } from "@/lib/prefs";
import { applyTheme, loadPrefs, savePrefs } from "@/lib/prefs";

interface PrefsApi extends Prefs {
  setTheme: (t: Theme) => void;
  setGrouping: (g: Grouping) => void;
}

const Ctx = createContext<PrefsApi | null>(null);

export function PrefsProvider({ children }: { children: ReactNode }) {
  const [prefs, setPrefs] = useState<Prefs>(() => loadPrefs());
  useEffect(() => {
    applyTheme(prefs.theme);
    savePrefs(prefs);
  }, [prefs]);
  const setTheme = useCallback((theme: Theme) => setPrefs((p) => ({ ...p, theme })), []);
  const setGrouping = useCallback((grouping: Grouping) => setPrefs((p) => ({ ...p, grouping })), []);
  const value = useMemo(() => ({ ...prefs, setTheme, setGrouping }), [prefs, setTheme, setGrouping]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function usePrefs(): PrefsApi {
  const v = useContext(Ctx);
  if (!v) throw new Error("usePrefs outside PrefsProvider");
  return v;
}

/** An amount in the user's chosen grouping (lakh/crore or million). */
export function Amount({ value, ...opts }: { value: number | string | null | undefined } & Omit<AmountOptions, "grouping">) {
  const { grouping } = usePrefs();
  return <span className="num">{formatAmount(value, { grouping, ...opts })}</span>;
}
