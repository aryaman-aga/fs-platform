/**
 * Per-viewer conveniences (theme, number grouping). Stored in localStorage when
 * available; everything works without it (private windows, blocked storage).
 */
import type { Grouping } from "./format";

export type Theme = "light" | "dark" | "system";

export interface Prefs {
  theme: Theme;
  grouping: Grouping;
}

const KEY = "fsui.prefs.v1";
export const DEFAULT_PREFS: Prefs = { theme: "system", grouping: "indian" };

export function loadPrefs(): Prefs {
  try {
    const raw = window.localStorage.getItem(KEY);
    if (!raw) return { ...DEFAULT_PREFS };
    const p = JSON.parse(raw) as Partial<Prefs>;
    return {
      theme: p.theme === "light" || p.theme === "dark" || p.theme === "system" ? p.theme : DEFAULT_PREFS.theme,
      grouping: p.grouping === "western" ? "western" : "indian",
    };
  } catch {
    return { ...DEFAULT_PREFS };
  }
}

export function savePrefs(p: Prefs): void {
  try {
    window.localStorage.setItem(KEY, JSON.stringify(p));
  } catch {
    // storage unavailable: preferences last for this page only
  }
}

export function applyTheme(theme: Theme): void {
  const root = document.documentElement;
  if (theme === "system") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", theme);
}
