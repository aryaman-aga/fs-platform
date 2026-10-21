/** Firm-wide audit search, rule registry, profile, help, release notes, not found. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { useState } from "react";
import { Link } from "react-router-dom";

import { api, apiBlob, saveFile } from "@/api/client";
import type { AuditEventOut, Page, RuleOut, SessionInfo } from "@/api/types";
import { useAuth } from "@/auth/AuthContext";
import { hasNoRole, mfaRequired, roleCodes } from "@/auth/permissions";
import { DataGrid } from "@/components/DataGrid";
import { useToast } from "@/components/Toast";
import { Button, Card, Chip, ErrorNotice, KeyValue, Notice, PageHeader, Spinner, TextInput } from "@/components/ui";
import { formatDateTime, shortHash, titleCase } from "@/lib/format";

import { ChainBadge } from "./engagement/tabs/AuditTab";

export function AuditSearchPage() {
  const { can } = useAuth();
  const toast = useToast();
  const [action, setAction] = useState("");
  const [since, setSince] = useState("");
  const [offset, setOffset] = useState(0);
  const limit = 100;
  const events = useQuery({
    queryKey: ["audit", "search", action, since, offset],
    queryFn: () => api<Page>("/audit", { query: { action: action || undefined, since: since ? `${since}T00:00:00` : undefined, limit, offset } }),
  });
  const items = (events.data?.items ?? []) as AuditEventOut[];
  return (
    <div className="stack">
      <PageHeader title="Audit trail" sub="Append-only and hash-chained: every change, approval, download and denial." actions={<ChainBadge enabled={can("users.manage")} />} />
      <Card bodyClass="card-body stack">
        <div className="row" style={{ alignItems: "flex-end" }}>
          <TextInput label="Action starts with" value={action} onChange={(e) => { setAction(e.target.value); setOffset(0); }} placeholder="e.g. fixplan, auth., file.download" />
          <TextInput label="Since" type="date" value={since} onChange={(e) => { setSince(e.target.value); setOffset(0); }} />
          <Button onClick={async () => { try { saveFile(await apiBlob("/audit/export.csv", { query: { action: action || undefined } }, "audit-trail.csv")); } catch (e) { toast.error(e); } }}>⤓ CSV</Button>
        </div>
        {events.isLoading ? <Spinner /> : null}
        <ErrorNotice error={events.error} />
        <DataGrid
          label="Audit events"
          rows={items}
          rowKey={(e) => String(e.id)}
          columns={[
            { key: "seq", header: "#", className: "num", render: (e) => e.seq },
            { key: "ts", header: "When", render: (e) => formatDateTime(e.ts) },
            { key: "a", header: "Action", render: (e) => <span className="mono">{e.action}</span> },
            { key: "r", header: "Role", render: (e) => e.role_at_time ?? "" },
            { key: "ip", header: "IP", render: (e) => e.ip ?? "" },
            { key: "o", header: "Outcome", render: (e) => <Chip tone={e.outcome === "ok" ? undefined : "danger"}>{e.outcome}</Chip> },
            { key: "h", header: "Hash", render: (e) => <span className="mono small">{shortHash(e.event_hash, 10)}</span> },
          ]}
        />
        <div className="row between small">
          <span>{events.data ? `${offset + 1}–${offset + items.length} of ${events.data.total}` : ""}</span>
          <span className="row">
            <Button size="sm" disabled={offset === 0} onClick={() => setOffset((o) => Math.max(0, o - limit))}>Previous</Button>
            <Button size="sm" disabled={!events.data || offset + limit >= events.data.total} onClick={() => setOffset((o) => o + limit)}>Next</Button>
          </span>
        </div>
      </Card>
    </div>
  );
}

export function RulesPage() {
  const rules = useQuery({ queryKey: ["rules"], queryFn: () => api<RuleOut>("/rules") });
  const quality = useQuery({ queryKey: ["rules", "quality"], queryFn: () => api<Record<string, unknown>[]>("/analytics/rule-quality") });
  const [family, setFamily] = useState("");
  const list = (rules.data?.rules ?? []) as Record<string, unknown>[];
  const fams = [...new Set(list.map((r) => String(r.family ?? "")))].sort();
  const q = new Map((quality.data ?? []).map((r) => [String(r.rule_id), r]));
  return (
    <div className="stack">
      <PageHeader title="Rule registry" sub={rules.data ? `Rule set ${rules.data.rule_set_version} · hash ${shortHash(rules.data.rule_set_hash, 16)} — every run records the version it used (E-RULE-01/03)` : undefined} />
      <ErrorNotice error={rules.error} />
      <Card
        bodyClass=""
        actions={
          <select aria-label="Family" value={family} onChange={(e) => setFamily(e.target.value)} style={{ width: "auto" }}>
            <option value="">All families</option>
            {fams.map((f) => (
              <option key={f} value={f}>
                {titleCase(f)}
              </option>
            ))}
          </select>
        }
        title={`${list.length} rules`}
      >
        <DataGrid
          label="Rules"
          rows={list.filter((r) => !family || r.family === family)}
          rowKey={(r) => String(r.id)}
          columns={[
            { key: "id", header: "Rule", render: (r) => <span className="mono">{String(r.id)}</span>, sortValue: (r) => String(r.id) },
            { key: "t", header: "Title", className: "wrap", render: (r) => String(r.title ?? "") },
            { key: "f", header: "Family", render: (r) => titleCase(String(r.family ?? "")) },
            { key: "s", header: "Severity", render: (r) => String(r.severity ?? "") },
            { key: "c", header: "Certainty", render: (r) => String(r.certainty ?? "") },
            { key: "k", header: "Fix", render: (r) => String(r.fix_kind ?? "") },
            { key: "v", header: "Version", render: (r) => String(r.version ?? "") },
            {
              key: "q",
              header: "False-positive rate",
              className: "num",
              render: (r) => {
                const x = q.get(String(r.id));
                if (!x) return "—";
                const rate = Number(x.false_positive_rate ?? 0);
                return <Chip tone={x.flag_for_tuning ? "danger" : undefined}>{(rate * 100).toFixed(1)}% of {String(x.findings)}</Chip>;
              },
            },
          ]}
        />
      </Card>
    </div>
  );
}

export function ProfilePage() {
  const { me, signOut } = useAuth();
  const qc = useQueryClient();
  const toast = useToast();
  const sessions = useQuery({ queryKey: ["sessions"], queryFn: () => api<SessionInfo[]>("/auth/sessions") });
  const [pw, setPw] = useState({ current_password: "", new_password: "" });
  const change = useMutation({
    mutationFn: () => api("/auth/password/change", { method: "POST", body: pw }),
    onSuccess: () => {
      setPw({ current_password: "", new_password: "" });
      toast.ok("Password changed; your other sessions were signed out");
    },
  });
  const revoke = useMutation({
    mutationFn: (id: string) => api(`/auth/sessions/${id}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["sessions"] }),
  });
  const logoutAll = async () => {
    try {
      await api("/auth/logout-all", { method: "POST" });
    } finally {
      await signOut();
    }
  };
  // no authenticator on this installation: nothing to show or manage about one
  const mfa: [ReactNode, ReactNode][] = mfaRequired(me)
    ? [["MFA", me?.user.mfa_enrolled_at ? `${me.user.mfa_type} since ${formatDateTime(me.user.mfa_enrolled_at)}` : "not enrolled"]]
    : [];
  return (
    <div className="stack">
      <PageHeader title="Your account" actions={<Button variant="danger" onClick={() => void logoutAll()}>Sign out of all devices</Button>} />
      <div className="grid-2">
        <Card title="Profile">
          <KeyValue
            items={[
              ["Name", me?.user.name],
              ["E-mail", me?.user.email],
              ["Firm", me?.tenant_name],
              ["Roles", hasNoRole(me) ? "None yet — ask your FS-Platform administrator to give you a role" : roleCodes(me).map(titleCase).join(", ")],
              ...mfa,
              ["AI policy", me?.ai_policy_version ? (me.ai_policy_accepted ? `accepted (v${me.ai_policy_version})` : `not yet accepted (v${me.ai_policy_version})`) : "—"],
              ["Session", me ? `${me.session.auth_method} · idle limit ${me.session.idle_minutes} min · ends ${formatDateTime(me.session.expires_at)}` : ""],
            ]}
          />
        </Card>
        <Card title="Change password">
          <form className="stack" onSubmit={(e) => { e.preventDefault(); change.mutate(); }}>
            <TextInput label="Current password" type="password" autoComplete="current-password" value={pw.current_password} onChange={(e) => setPw((x) => ({ ...x, current_password: e.target.value }))} />
            <TextInput label="New password" type="password" autoComplete="new-password" minLength={12} value={pw.new_password} onChange={(e) => setPw((x) => ({ ...x, new_password: e.target.value }))} hint="At least 12 characters." />
            <ErrorNotice error={change.error} />
            <Button type="submit" variant="primary" busy={change.isPending} disabled={pw.new_password.length < 12 || !pw.current_password}>
              Change password
            </Button>
          </form>
        </Card>
      </div>
      <Card title="Active sessions" bodyClass="">
        <DataGrid
          label="Active sessions"
          rows={sessions.data ?? []}
          rowKey={(s) => s.id}
          columns={[
            { key: "m", header: "Method", render: (s) => s.auth_method },
            { key: "c", header: "Started", render: (s) => formatDateTime(s.created_at) },
            { key: "l", header: "Last seen", render: (s) => formatDateTime(s.last_seen_at) },
            { key: "ip", header: "IP", render: (s) => s.ip ?? "" },
            { key: "ua", header: "Device", className: "wrap", render: (s) => <span className="small">{s.user_agent ?? ""}</span> },
            { key: "x", header: "", render: (s) => (s.current ? <Chip tone="accent">this session</Chip> : <Button size="sm" onClick={() => revoke.mutate(s.id)}>Sign out</Button>) },
          ]}
        />
      </Card>
      <p className="small muted">
        Signed in with <code className="mono">{me?.user.email}</code>. <Link to="/help">User guide</Link>
      </p>
    </div>
  );
}

const GUIDE: { role: string; points: string[] }[] = [
  { role: "Staff / Article", points: ["Upload the client's workbook and trial balance (Files).", "Run checks, work through findings, mark 'Not an issue' with a reason.", "Propose a fix plan; a Manager approves and applies it.", "Correct narrative notes as typed text; a Manager approves your edits."] },
  { role: "Manager", points: ["Approve fix plans you did not propose and select approval items.", "Approve narrative edits and AI payloads raised by others.", "Release drafts to the client from REVIEW; manage client queries and representations."] },
  { role: "Partner", points: ["Check balancing adjustments approved by a Manager.", "Review key figures and exceptions; render the FINAL PDF when the gates pass.", "Mark FINAL and lock; request an unlock with a reason if ever needed."] },
  { role: "Reviewer / EQCR", points: ["Read everything, raise and clear review notes; no edits."] },
  { role: "Client users", points: ["See released drafts, answer queries, upload requested files and confirm management representations in the portal."] },
  { role: "Firm Admin", points: ["Invite users, assign roles per client, run quarterly access reviews.", "Publish the AI policy, track incidents (CERT-In 6 hours) and data-principal requests.", "Download the quarterly control evidence pack."] },
];

export function HelpPage() {
  return (
    <div className="stack">
      <PageHeader title="User guide" sub="Press “? Help” on any screen for help about that screen." />
      <div className="grid-2">
        {GUIDE.map((g) => (
          <Card key={g.role} title={g.role}>
            <ul style={{ margin: 0, paddingLeft: 18 }}>
              {g.points.map((p) => (
                <li key={p}>{p}</li>
              ))}
            </ul>
          </Card>
        ))}
      </div>
      <Card title="Keyboard">
        <ul style={{ margin: 0, paddingLeft: 18 }}>
          <li>Tables: ↑ / ↓ move between rows, Home / End jump, Enter opens the row.</li>
          <li>Tabs: ← / → switch tabs. Dialogs: Esc closes.</li>
          <li>“Skip to content” is the first item when you press Tab.</li>
        </ul>
      </Card>
    </div>
  );
}

export function ReleaseNotesPage() {
  const { me } = useAuth();
  return (
    <div className="stack">
      <PageHeader title="Release notes" />
      <Card title="1.0.0">
        <ul style={{ margin: 0, paddingLeft: 18 }}>
          <li>Engagement workspace: files and versions, checks with live progress, fix plans with maker-checker, narrative, related parties, PDF and release, review notes, client items, disclosure checklist, AI review, audit timeline, settings.</li>
          <li>Client portal for released drafts, queries, uploads and management representations.</li>
          <li>Administration: users, permission matrix, assignments, elevation, access reviews, AI policy, ABAC policies, backups, incidents, DPDP requests, change log, usage, health and evidence pack.</li>
          <li>Lakh / crore or million grouping, light and dark themes, keyboard-navigable tables, contextual help.</li>
        </ul>
      </Card>
      <Notice>
        Security: sessions are HttpOnly cookies with CSRF protection; administrative actions need a reason and{" "}
        {mfaRequired(me) ? "a fresh MFA code." : "a fresh confirmation with your password."}
      </Notice>
    </div>
  );
}

export function NotFoundPage() {
  return (
    <div className="stack">
      <PageHeader title="Page not found" />
      <Link to="/">Go to the dashboard</Link>
    </div>
  );
}
