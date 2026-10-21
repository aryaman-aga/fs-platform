import { useState } from "react";
import { Link, NavLink, Navigate, Outlet, useLocation } from "react-router-dom";

import { useAuth } from "@/auth/AuthContext";
import { mustChangePassword, navItems, roleCodes } from "@/auth/permissions";
import { ErrorBoundary } from "@/components/ErrorBoundary";
import { usePrefs } from "@/components/Prefs";
import { Button, Spinner } from "@/components/ui";
import { titleCase } from "@/lib/format";
import { helpFor } from "@/lib/help";

const ICONS: Record<string, string> = {
  home: "⌂",
  clients: "▤",
  portal: "⇄",
  audit: "⛓",
  rules: "≡",
  admin: "⚙",
  help: "?",
  notes: "✎",
};

function HelpDrawer({ onClose }: { onClose: () => void }) {
  const { pathname } = useLocation();
  const h = helpFor(pathname);
  return (
    <aside className="help-drawer" aria-label="Help for this screen">
      <div className="card-header">
        <h2>{h.title}</h2>
        <Button variant="ghost" className="icon-btn" aria-label="Close help" onClick={onClose}>
          ✕
        </Button>
      </div>
      <div className="body">
        <p>{h.summary}</p>
        {h.steps?.length ? (
          <>
            <h3>How to</h3>
            <ul>
              {h.steps.map((s) => (
                <li key={s}>{s}</li>
              ))}
            </ul>
          </>
        ) : null}
        {h.controls?.length ? (
          <>
            <h3>Controls applied</h3>
            <ul>
              {h.controls.map((s) => (
                <li key={s}>{s}</li>
              ))}
            </ul>
          </>
        ) : null}
        <p className="small muted" style={{ marginTop: 16 }}>
          More in the <Link to="/help">user guide</Link> and the <Link to="/release-notes">release notes</Link>.
        </p>
      </div>
    </aside>
  );
}

export function AppShell() {
  const { me, status, signOut } = useAuth();
  const { grouping, setGrouping, theme, setTheme } = usePrefs();
  const [help, setHelp] = useState(false);
  const location = useLocation();

  if (status === "loading") {
    return (
      <div className="auth-wrap">
        <Spinner label="Loading FS-Platform" />
      </div>
    );
  }
  if (status === "anonymous") return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />;
  if (status === "mfa") return <Navigate to={me?.user.mfa_enrolled_at ? "/mfa" : "/mfa/enroll"} replace />;
  if (mustChangePassword(me)) return <Navigate to="/change-password" replace />;

  const items = navItems(me);
  const work = items.filter((i) => i.section === "work");
  const admin = items.filter((i) => i.section === "admin");
  const helpItems = items.filter((i) => i.section === "help");
  const roles = roleCodes(me).map((r) => titleCase(r)).join(", ");

  return (
    <div className="shell">
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <nav className="sidebar" aria-label="Main">
        <Link to="/" className="brand" style={{ textDecoration: "none" }}>
          <img src="/favicon.svg" alt="" />
          <span>
            FS-Platform
            <small>Ind AS preparation & review</small>
          </span>
        </Link>
        <div className="nav-section">Work</div>
        <div className="nav">
          {work.map((i) => (
            <NavLink key={i.to} to={i.to} end={i.to === "/"} className={({ isActive }) => (isActive ? "active" : undefined)}>
              <span className="nav-icon" aria-hidden="true">
                {ICONS[i.icon]}
              </span>
              {i.label}
            </NavLink>
          ))}
        </div>
        {admin.length ? (
          <>
            <div className="nav-section">Firm</div>
            <div className="nav">
              {admin.map((i) => (
                <NavLink key={i.to} to={i.to} className={({ isActive }) => (isActive ? "active" : undefined)}>
                  <span className="nav-icon" aria-hidden="true">
                    {ICONS[i.icon]}
                  </span>
                  {i.label}
                </NavLink>
              ))}
            </div>
          </>
        ) : null}
        <div className="nav-section">Help</div>
        <div className="nav">
          {helpItems.map((i) => (
            <NavLink key={i.to} to={i.to} className={({ isActive }) => (isActive ? "active" : undefined)}>
              <span className="nav-icon" aria-hidden="true">
                {ICONS[i.icon]}
              </span>
              {i.label}
            </NavLink>
          ))}
        </div>
      </nav>
      <div className="main">
        <header className="topbar">
          <span className="tenant">{me?.tenant_name}</span>
          <span className="spacer" />
          <label className="row small" title="Number grouping">
            <span className="muted">Amounts</span>
            <select value={grouping} onChange={(e) => setGrouping(e.target.value === "western" ? "western" : "indian")} aria-label="Number grouping" style={{ width: "auto", minHeight: 28 }}>
              <option value="indian">Lakh / crore (12,34,567)</option>
              <option value="western">Million (1,234,567)</option>
            </select>
          </label>
          <label className="row small" title="Theme">
            <span className="muted">Theme</span>
            <select value={theme} onChange={(e) => setTheme(e.target.value as typeof theme)} aria-label="Colour theme" style={{ width: "auto", minHeight: 28 }}>
              <option value="system">System</option>
              <option value="light">Light</option>
              <option value="dark">Dark</option>
            </select>
          </label>
          <Button variant="ghost" onClick={() => setHelp((h) => !h)} aria-expanded={help} aria-label="Help for this screen">
            ? Help
          </Button>
          <Link to="/profile" className="small" title={roles}>
            {me?.user.name}
          </Link>
          <Button size="sm" onClick={() => void signOut()} data-testid="sign-out">
            Sign out
          </Button>
        </header>
        <main id="main" className="content" tabIndex={-1}>
          <ErrorBoundary key={location.pathname}>
            <Outlet />
          </ErrorBoundary>
        </main>
      </div>
      {help ? <HelpDrawer onClose={() => setHelp(false)} /> : null}
    </div>
  );
}
