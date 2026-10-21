import type { KeyboardEvent, ReactNode } from "react";
import { useRef } from "react";

export interface TabDef {
  key: string;
  label: ReactNode;
  count?: number | null;
  hidden?: boolean;
}

/** Accessible tab list (WAI-ARIA tabs pattern): arrow keys, Home / End. */
export function Tabs({ tabs, active, onChange, label }: { tabs: TabDef[]; active: string; onChange: (key: string) => void; label: string }) {
  const visible = tabs.filter((t) => !t.hidden);
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const onKey = (e: KeyboardEvent<HTMLButtonElement>, i: number) => {
    let next = -1;
    if (e.key === "ArrowRight") next = (i + 1) % visible.length;
    else if (e.key === "ArrowLeft") next = (i - 1 + visible.length) % visible.length;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = visible.length - 1;
    if (next >= 0) {
      e.preventDefault();
      const t = visible[next];
      if (t) {
        onChange(t.key);
        refs.current[next]?.focus();
      }
    }
  };
  return (
    <div className="tabs" role="tablist" aria-label={label}>
      {visible.map((t, i) => (
        <button
          key={t.key}
          ref={(el) => {
            refs.current[i] = el;
          }}
          type="button"
          role="tab"
          id={`tab-${t.key}`}
          aria-selected={t.key === active}
          aria-controls={`panel-${t.key}`}
          tabIndex={t.key === active ? 0 : -1}
          className="tab"
          onClick={() => onChange(t.key)}
          onKeyDown={(e) => onKey(e, i)}
        >
          {t.label}
          {t.count !== undefined && t.count !== null ? <span className="chip count">{t.count}</span> : null}
        </button>
      ))}
    </div>
  );
}

export function TabPanel({ id, children }: { id: string; children: ReactNode }) {
  return (
    <div role="tabpanel" id={`panel-${id}`} aria-labelledby={`tab-${id}`}>
      {children}
    </div>
  );
}
