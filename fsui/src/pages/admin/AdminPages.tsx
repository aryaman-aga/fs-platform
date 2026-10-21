/** Administration (08 §3 screen 6). Privileged changes go through the step-up dialog (reason + a fresh confirmation:
 * the MFA code, or the password where the installation requires no authenticator). */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { useState } from "react";
import { Link, NavLink, Navigate, useParams, useSearchParams } from "react-router-dom";

import { api, apiBlob, saveFile } from "@/api/client";
import { useAuthOptions, useClients } from "@/api/hooks";
import type {
  AccessReviewOut,
  AIPolicyOut,
  BackupLogOut,
  BackupSettingsOut,
  DsrOut,
  ElevationOut,
  HealthOut,
  IncidentOut,
  InviteOut,
  MatrixOut,
  PasswordResetLinkOut,
  PolicyOut,
  ReleaseRecordOut,
  RoleAssignmentOut,
  RoleOut,
  TenantSettingsOut,
  UsageOut,
  UserOut,
} from "@/api/types";
import { useAuth } from "@/auth/AuthContext";
import { adminSections, canAny, mfaRequired, roleInForce } from "@/auth/permissions";
import type { Column } from "@/components/DataGrid";
import { DataGrid } from "@/components/DataGrid";
import { Modal } from "@/components/Modal";
import { useStepUp } from "@/components/StepUp";
import { useToast } from "@/components/Toast";
import { Button, Card, Checkbox, Chip, EmptyState, ErrorNotice, KeyValue, Notice, PageHeader, SelectInput, Spinner, TextArea, TextInput } from "@/components/ui";
import { formatBytes, formatDate, formatDateTime, relativeTime, shortHash, titleCase } from "@/lib/format";

function useAdminAction() {
  const stepUp = useStepUp();
  const toast = useToast();
  const qc = useQueryClient();
  return async (title: string, fn: (reason: string) => Promise<unknown>, invalidate: unknown[][], okText = "Done") => {
    try {
      await stepUp.run(title, fn);
      toast.ok(okText);
      await Promise.all(invalidate.map((k) => qc.invalidateQueries({ queryKey: k })));
    } catch (e) {
      if ((e as Error).message !== "cancelled") toast.error(e);
    }
  };
}

export function AdminPage() {
  const { section } = useParams();
  const { me } = useAuth();
  const sections = adminSections(me);
  if (!sections.length) return <Notice tone="warn">You do not have administrative permissions.</Notice>;
  const active = sections.find((s) => s.key === section);
  if (!active) return <Navigate to={`/admin/${sections[0]?.key}`} replace />;
  const body: Record<string, ReactNode> = {
    users: <Users />,
    firm: <FirmSettings />,
    roles: <Roles />,
    assignments: <Assignments />,
    elevations: <Elevations />,
    "access-reviews": <AccessReviews />,
    "ai-policy": <AiPolicyAdmin />,
    abac: <AbacPolicy />,
    backups: <Backups />,
    incidents: <Incidents />,
    dsr: <Dsr />,
    releases: <Releases />,
    usage: <Usage />,
    health: <Health />,
    evidence: <Evidence />,
  };
  return (
    <div className="stack">
      <PageHeader title="Administration" sub="Users, access, compliance and operations. Every change is in the hash-chained audit trail." />
      <div className="tabs" role="navigation" aria-label="Administration sections">
        {sections.map((s) => (
          <NavLink key={s.key} to={`/admin/${s.key}`} className="tab" aria-current={s.key === active.key ? "page" : undefined} style={s.key === active.key ? { color: "var(--accent)", borderBottomColor: "var(--accent)" } : undefined}>
            {s.label}
          </NavLink>
        ))}
      </div>
      {body[active.key]}
    </div>
  );
}

// ------------------------------------------------------------------ users

/** A link an administrator hands over in person (desktop edition: no e-mail; it opens on this PC). */
function HandOverLink({ intro, link, expiresAt, testId }: { intro: string; link: string; expiresAt: string; testId: string }) {
  const toast = useToast();
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(link);
      toast.ok("Link copied");
    } catch {
      toast.error(new Error("Could not copy: select the link and copy it."));
    }
  };
  return (
    <div className="card-body" data-testid={testId}>
      <Notice>
        <div className="stack" style={{ gap: 6 }}>
          <span>
            {intro}; valid until {formatDateTime(expiresAt)}:
          </span>
          <code className="mono" style={{ wordBreak: "break-all" }}>
            {link}
          </code>
          <span className="row">
            <Button size="sm" onClick={() => void copy()}>
              Copy link
            </Button>
          </span>
        </div>
      </Notice>
    </div>
  );
}

function Users() {
  const qc = useQueryClient();
  const toast = useToast();
  const stepUp = useStepUp();
  const act = useAdminAction();
  const options = useAuthOptions();
  const { me, can } = useAuth();
  const mfa = mfaRequired(me); // no authenticator on this installation: nothing to show or reset about one
  const users = useQuery({ queryKey: ["admin", "users"], queryFn: () => api<UserOut[]>("/admin/users") });
  const roles = useQuery({ queryKey: ["admin", "roles"], queryFn: () => api<RoleOut[]>("/admin/roles") });
  // the same list as Assignments ("All"): who has a role, so that new accounts without one stand out
  const assignments = useQuery({
    queryKey: ["admin", "assignments", ""],
    queryFn: () => api<RoleAssignmentOut[]>("/admin/assignments"),
    enabled: can("engagement.assign"),
  });
  const clients = useClients();
  const [invite, setInvite] = useState(false);
  const [f, setF] = useState({ email: "", name: "", is_firm_user: true, role_code: "staff", client_id: "" });
  const [link, setLink] = useState<InviteOut | null>(null);
  const [reset, setReset] = useState<(PasswordResetLinkOut & { user: UserOut }) | null>(null);
  const onThisPc = options.data?.edition === "desktop" ? " — it opens on this PC" : "";
  const create = useMutation({
    mutationFn: () => api<InviteOut>("/admin/users/invite", { method: "POST", body: { ...f, client_id: f.client_id || null, role_code: f.role_code || null } }),
    onSuccess: async (o) => {
      setLink(o);
      setReset(null);
      setInvite(false);
      toast.ok(o.invite_link ? `Invitation created for ${o.user.email}` : `Invitation sent to ${o.user.email}`);
      await qc.invalidateQueries({ queryKey: ["admin", "users"] });
    },
  });
  const resetPassword = async (u: UserOut) => {
    try {
      const out = await stepUp.run(
        `Reset password for ${u.email}`,
        (reason) => api<PasswordResetLinkOut>(`/admin/users/${u.id}/password-reset-link`, { method: "POST", reason }),
        `Creates a one-time link on which they choose a new password. Using it signs them out everywhere${mfa ? "; their authenticator stays enrolled" : ""}.`,
      );
      setReset({ ...out, user: u });
      setLink(null);
      toast.ok(`Password reset link created for ${u.email}`);
    } catch (e) {
      if ((e as Error).message !== "cancelled") toast.error(e);
    }
  };
  const role = roles.data?.find((r) => r.code === f.role_code);
  // people nobody has given a role yet (e.g. they created their own account): they see no client until one is assigned
  const withRole = new Set((assignments.data ?? []).filter((a) => roleInForce(a)).map((a) => a.user_id));
  const noRole = (u: UserOut) => !!assignments.data && u.status !== "disabled" && !withRole.has(u.id);
  const waiting = (users.data ?? []).filter(noRole);
  const mfaColumn: Column<UserOut>[] = mfa
    ? [{ key: "m", header: "MFA", render: (u) => (u.mfa_enrolled_at ? `${u.mfa_type} · ${formatDate(u.mfa_enrolled_at)}` : <Chip tone="warn">not enrolled</Chip>) }]
    : [];
  return (
    <Card title="Users" actions={<Button variant="primary" onClick={() => setInvite(true)}>Invite user</Button>} bodyClass="">
      {link?.invite_link ? (
        <HandOverLink testId="invite-link" intro={`Give this link to your colleague${onThisPc}`} link={link.invite_link} expiresAt={link.expires_at} />
      ) : null}
      {reset ? (
        <HandOverLink testId="reset-link" intro={`Password reset for ${reset.user.name}: give them this link${onThisPc}`} link={reset.link} expiresAt={reset.expires_at} />
      ) : null}
      {waiting.length ? (
        <div className="card-body" data-testid="no-role-hint">
          <Notice tone="warn">
            {waiting.length === 1 ? `${waiting[0]?.name} has no role yet` : `${waiting.length} people have no role yet`}, so they cannot see any client.{" "}
            <Link to={waiting.length === 1 ? `/admin/assignments?user=${waiting[0]?.id}` : "/admin/assignments"}>Give them a role under Assignments</Link>.
          </Notice>
        </div>
      ) : null}
      {users.isLoading ? <div className="card-body"><Spinner /></div> : null}
      <ErrorNotice error={users.error} />
      <DataGrid
        label="Users"
        rows={users.data ?? []}
        rowKey={(u) => u.id}
        columns={[
          { key: "n", header: "Name", render: (u) => u.name, sortValue: (u) => u.name },
          { key: "e", header: "E-mail", render: (u) => u.email, sortValue: (u) => u.email },
          { key: "t", header: "Type", render: (u) => (u.is_firm_user ? "Firm" : "Client") },
          {
            key: "s",
            header: "Status",
            render: (u) => (
              <span className="row">
                <Chip tone={u.status === "active" ? "ok" : u.status === "disabled" ? "danger" : "warn"}>{u.status}</Chip>
                {u.must_change_password ? <Chip tone="warn" title="Signed in with a one-time password; must choose their own">one-time password</Chip> : null}
                {noRole(u) ? <Chip tone="warn" title="Sees no client until you give them a role under Assignments">No role yet</Chip> : null}
              </span>
            ),
          },
          ...mfaColumn,
          { key: "l", header: "Last sign-in", render: (u) => formatDateTime(u.last_login_at) || "—" },
          {
            key: "a",
            header: "",
            render: (u) => (
              <span className="row">
                {u.status === "disabled" ? (
                  <Button size="sm" onClick={() => void act(`Enable ${u.email}`, (reason) => api(`/admin/users/${u.id}/enable`, { method: "POST", reason }), [["admin", "users"]], "User enabled")}>
                    Enable
                  </Button>
                ) : (
                  <Button size="sm" variant="danger" onClick={() => void act(`Disable ${u.email}`, (reason) => api(`/admin/users/${u.id}/disable`, { method: "POST", reason }), [["admin", "users"]], "User disabled; sessions revoked")}>
                    Disable
                  </Button>
                )}
                {u.status !== "disabled" ? (
                  <Button size="sm" variant="ghost" onClick={() => void resetPassword(u)}>
                    Reset password
                  </Button>
                ) : null}
                {mfa ? (
                  <Button size="sm" variant="ghost" onClick={() => void act(`Reset MFA for ${u.email}`, (reason) => api(`/admin/users/${u.id}/mfa-reset`, { method: "POST", reason }), [["admin", "users"]], "MFA reset; the user enrols again at next sign-in")}>
                    Reset MFA
                  </Button>
                ) : null}
                {noRole(u) ? (
                  <Link className="btn sm" to={`/admin/assignments?user=${u.id}`}>
                    Give a role
                  </Link>
                ) : null}
              </span>
            ),
          },
        ]}
      />
      {invite ? (
        <Modal
          title="Invite user"
          onClose={() => setInvite(false)}
          footer={
            <>
              <Button onClick={() => setInvite(false)}>Cancel</Button>
              <Button variant="primary" busy={create.isPending} disabled={!f.email || !f.name} onClick={() => create.mutate()}>
                Send invitation
              </Button>
            </>
          }
        >
          <TextInput label="E-mail" type="email" value={f.email} onChange={(e) => setF((x) => ({ ...x, email: e.target.value }))} />
          <TextInput label="Name" value={f.name} onChange={(e) => setF((x) => ({ ...x, name: e.target.value }))} />
          <Checkbox label="Firm user (uncheck for a client user)" checked={f.is_firm_user} onChange={(e) => setF((x) => ({ ...x, is_firm_user: e.target.checked, role_code: e.target.checked ? "staff" : "client_viewer" }))} />
          <SelectInput label="Role" value={f.role_code} onChange={(e) => setF((x) => ({ ...x, role_code: e.target.value }))} options={(roles.data ?? []).filter((r) => r.is_client_role !== f.is_firm_user).map((r) => ({ value: r.code, label: r.name }))} />
          {role?.scope === "client" ? (
            <SelectInput label="Client" value={f.client_id} onChange={(e) => setF((x) => ({ ...x, client_id: e.target.value }))} options={[{ value: "", label: "— choose —" }, ...(clients.data ?? []).map((c) => ({ value: c.id, label: c.name }))]} />
          ) : null}
          <ErrorNotice error={create.error} />
        </Modal>
      ) : null}
    </Card>
  );
}

// ------------------------------------------------------------------ firm settings

function FirmSettings() {
  const qc = useQueryClient();
  const toast = useToast();
  const stepUp = useStepUp();
  const settings = useQuery({ queryKey: ["admin", "tenant-settings"], queryFn: () => api<TenantSettingsOut>("/admin/tenant-settings") });
  const [works, setWorks] = useState<boolean | null>(null);
  const current = settings.data?.works_on_engagements ?? false;
  const value = works ?? current;
  const save = async () => {
    try {
      await stepUp.run(value ? "Let Firm Admins work on engagements" : "Stop assigning Firm Admins to new clients", (reason) =>
        api<TenantSettingsOut>("/admin/tenant-settings", { method: "PUT", body: { works_on_engagements: value }, reason }),
      );
      toast.ok("Firm settings saved");
      await qc.invalidateQueries({ queryKey: ["admin", "tenant-settings"] });
      setWorks(null);
    } catch (e) {
      if ((e as Error).message !== "cancelled") toast.error(e);
    }
  };
  return (
    <Card title="Firm settings">
      {settings.isLoading ? <Spinner /> : null}
      <ErrorNotice error={settings.error} />
      {settings.data ? (
        <div className="stack">
          <Checkbox label="Firm Admins also work on engagements" checked={value} onChange={(e) => setWorks(e.target.checked)} />
          <p className="small muted">
            When on, a Firm Admin who creates a client is made its Partner automatically, so a small office can prepare and sign off
            statements without a separate partner account. This weakens segregation of duties: every such assignment is recorded in the
            audit trail and flagged in access reviews, where another administrator certifies it.
          </p>
          <span className="row">
            <Button variant="primary" disabled={value === current} onClick={() => void save()}>
              Save
            </Button>
          </span>
        </div>
      ) : null}
    </Card>
  );
}

const VALUE_LABEL: Record<string, string> = {
  allow: "✓",
  maker_checker: "M",
  not_own: "✓ not own",
  shared_only: "shared only",
  role_setting: "role setting",
  policy: "✓ (policy)",
  own_clients: "own clients",
  dual: "✓ + dual",
  approve_restore: "approve restore",
};

function Roles() {
  const matrix = useQuery({ queryKey: ["admin", "matrix"], queryFn: () => api<MatrixOut>("/admin/permission-matrix") });
  if (matrix.isLoading) return <Spinner />;
  if (matrix.error) return <ErrorNotice error={matrix.error} />;
  const m = matrix.data;
  if (!m) return null;
  return (
    <Card title="Permission matrix (05 §3)" bodyClass="">
      <div className="table-wrap" style={{ border: "none", maxHeight: 640 }}>
        <table className="data" aria-label="Permission matrix">
          <thead>
            <tr>
              <th scope="col">Permission</th>
              {m.roles.map((r) => (
                <th key={r.code} scope="col" title={r.description}>
                  {r.name}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {m.rows.map((row) => (
              <tr key={row.permission}>
                <td>
                  {row.label}
                  <div className="mono faint small">
                    {row.permission}
                    {row.in_05_matrix ? "" : " · derived"}
                  </div>
                </td>
                {m.roles.map((r) => {
                  const v = row.values[r.code];
                  return (
                    <td key={r.code} className="small" style={{ textAlign: "center" }}>
                      {v ? <Chip tone={v === "allow" ? "ok" : "accent"}>{VALUE_LABEL[v] ?? v}</Chip> : <span className="faint">–</span>}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

function Assignments() {
  const act = useAdminAction();
  const clients = useClients();
  const [params] = useSearchParams();
  const [client, setClient] = useState("");
  const list = useQuery({ queryKey: ["admin", "assignments", client], queryFn: () => api<RoleAssignmentOut[]>("/admin/assignments", { query: { client_id: client || undefined } }) });
  const users = useQuery({ queryKey: ["admin", "users"], queryFn: () => api<UserOut[]>("/admin/users") });
  const roles = useQuery({ queryKey: ["admin", "roles"], queryFn: () => api<RoleOut[]>("/admin/roles") });
  // "Give a role" on Users opens this with ?user=<id> chosen
  const [f, setF] = useState({ user_id: params.get("user") ?? "", role_code: "staff", client_id: "" });
  const email = (id: string) => users.data?.find((u) => u.id === id)?.email ?? id.slice(0, 8);
  // a per-client role (Staff, Manager, Partner …) is refused without the client it is for
  const needsClient = roles.data?.find((r) => r.code === f.role_code)?.scope === "client";
  // firm people get firm roles and client people client-portal roles (the server refuses the other kind)
  const chosen = users.data?.find((u) => u.id === f.user_id);
  const roleOptions = (roles.data ?? []).filter((r) => !chosen || r.is_client_role !== chosen.is_firm_user);
  const pickUser = (id: string) => {
    const u = users.data?.find((x) => x.id === id);
    setF((x) => ({ ...x, user_id: id, role_code: u && !u.is_firm_user ? "client_viewer" : "staff" }));
  };
  return (
    <div className="stack">
      <Card title="Assign a role">
        <div className="row" style={{ alignItems: "flex-end" }}>
          <SelectInput label="User" value={f.user_id} onChange={(e) => pickUser(e.target.value)} options={[{ value: "", label: "— choose —" }, ...(users.data ?? []).map((u) => ({ value: u.id, label: u.email }))]} />
          <SelectInput label="Role" value={f.role_code} onChange={(e) => setF((x) => ({ ...x, role_code: e.target.value }))} options={roleOptions.map((r) => ({ value: r.code, label: r.name }))} />
          <SelectInput label="Client" value={f.client_id} onChange={(e) => setF((x) => ({ ...x, client_id: e.target.value }))} options={[{ value: "", label: needsClient ? "— choose —" : "Tenant-wide" }, ...(clients.data ?? []).map((c) => ({ value: c.id, label: c.name }))]} />
          <Button
            variant="primary"
            disabled={!f.user_id || (needsClient && !f.client_id)}
            onClick={() => void act("Assign role", (reason) => api("/admin/assignments", { method: "POST", body: { ...f, client_id: f.client_id || null }, reason }), [["admin", "assignments"]], "Role assigned")}
          >
            Assign
          </Button>
        </div>
      </Card>
      <Card
        title="Assignments"
        bodyClass=""
        actions={<SelectInput label="Client" value={client} onChange={(e) => setClient(e.target.value)} options={[{ value: "", label: "All" }, ...(clients.data ?? []).map((c) => ({ value: c.id, label: c.name }))]} />}
      >
        <DataGrid
          label="Role assignments"
          rows={list.data ?? []}
          rowKey={(a) => a.id}
          columns={[
            { key: "u", header: "User", render: (a) => email(a.user_id) },
            { key: "r", header: "Role", render: (a) => a.role_name },
            { key: "c", header: "Client", render: (a) => a.client_name ?? "Tenant-wide" },
            { key: "e", header: "Expires", render: (a) => (a.expires_at ? formatDateTime(a.expires_at) : "—") },
            { key: "x", header: "", render: (a) => <Button size="sm" variant="danger" onClick={() => void act("Revoke assignment", (reason) => api(`/admin/assignments/${a.id}`, { method: "DELETE", reason }), [["admin", "assignments"]], "Assignment revoked")}>Revoke</Button> },
          ]}
        />
      </Card>
    </div>
  );
}

function Elevations() {
  const qc = useQueryClient();
  const act = useAdminAction();
  const toast = useToast();
  const roles = useQuery({ queryKey: ["admin", "roles"], queryFn: () => api<RoleOut[]>("/admin/roles") });
  const list = useQuery({ queryKey: ["admin", "elevations"], queryFn: () => api<ElevationOut[]>("/admin/elevations") });
  const [f, setF] = useState({ role_code: "firm_admin", reason: "", duration_minutes: 60 });
  const request = useMutation({
    mutationFn: () => api<ElevationOut>("/admin/elevations", { method: "POST", body: f }),
    onSuccess: async () => {
      toast.ok("Elevation requested — another admin must approve it");
      await qc.invalidateQueries({ queryKey: ["admin", "elevations"] });
    },
    onError: (e) => toast.error(e),
  });
  return (
    <div className="stack">
      <Card title="Request just-in-time elevation (E-SEC-03)">
        <div className="row" style={{ alignItems: "flex-end" }}>
          <SelectInput label="Role" value={f.role_code} onChange={(e) => setF((x) => ({ ...x, role_code: e.target.value }))} options={(roles.data ?? []).map((r) => ({ value: r.code, label: r.name }))} />
          <TextInput label="Minutes" type="number" min={5} max={480} value={f.duration_minutes} onChange={(e) => setF((x) => ({ ...x, duration_minutes: Number(e.target.value) }))} />
          <TextInput label="Reason" value={f.reason} onChange={(e) => setF((x) => ({ ...x, reason: e.target.value }))} />
          <Button onClick={() => request.mutate()} busy={request.isPending} disabled={f.reason.trim().length < 5}>
            Request
          </Button>
        </div>
      </Card>
      <Card title="Elevations" bodyClass="">
        <DataGrid
          label="Elevations"
          rows={list.data ?? []}
          rowKey={(x) => x.id}
          columns={[
            { key: "w", header: "Requested", render: (x) => formatDateTime(x.created_at) },
            { key: "d", header: "Duration", render: (x) => `${x.duration_minutes} min` },
            { key: "r", header: "Reason", className: "wrap", render: (x) => x.reason },
            { key: "s", header: "Status", render: (x) => <Chip tone={x.status === "approved" ? "ok" : "warn"}>{x.status}</Chip> },
            { key: "e", header: "Expires", render: (x) => (x.expires_at ? `${formatDateTime(x.expires_at)} (${relativeTime(x.expires_at)})` : "—") },
            { key: "a", header: "", render: (x) => (x.status === "pending" ? <Button size="sm" onClick={() => void act("Approve elevation", () => api(`/admin/elevations/${x.id}/approve`, { method: "POST" }), [["admin", "elevations"]], "Elevation approved")}>Approve</Button> : null) },
          ]}
        />
      </Card>
    </div>
  );
}

// ------------------------------------------------------------------ compliance

function AccessReviews() {
  const qc = useQueryClient();
  const toast = useToast();
  const [period, setPeriod] = useState(() => {
    const d = new Date();
    return `${d.getFullYear()}Q${Math.floor(d.getMonth() / 3) + 1}`;
  });
  const list = useQuery({ queryKey: ["admin", "access-reviews", period], queryFn: () => api<AccessReviewOut[]>("/admin/access-reviews", { query: { period } }) });
  const users = useQuery({ queryKey: ["admin", "users"], queryFn: () => api<UserOut[]>("/admin/users") });
  const email = (id: string) => users.data?.find((u) => u.id === id)?.email ?? id.slice(0, 8);
  const start = useMutation({
    mutationFn: () => api("/admin/access-reviews", { method: "POST", body: { period } }),
    onSuccess: async () => {
      toast.ok(`Campaign ${period} started`);
      await qc.invalidateQueries({ queryKey: ["admin", "access-reviews"] });
    },
    onError: (e) => toast.error(e),
  });
  const decide = useMutation({
    mutationFn: ({ id, decision }: { id: string; decision: "certify" | "revoke" }) => api(`/admin/access-reviews/${id}/decide`, { method: "POST", body: { decision } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin", "access-reviews"] }),
    onError: (e) => toast.error(e),
  });
  const autoRevoke = useMutation({
    mutationFn: () => api<{ message: string }>("/admin/access-reviews/auto-revoke", { method: "POST" }),
    onSuccess: async (o) => {
      toast.ok(o.message || "Overdue items revoked");
      await qc.invalidateQueries({ queryKey: ["admin", "access-reviews"] });
    },
    onError: (e) => toast.error(e),
  });
  const items = list.data ?? [];
  return (
    <Card
      title={`Access recertification ${period} (C14, E-SEC-04)`}
      actions={
        <>
          <TextInput label="Quarter" value={period} onChange={(e) => setPeriod(e.target.value.toUpperCase())} style={{ width: 110 }} />
          <Button onClick={() => start.mutate()} busy={start.isPending}>
            Start campaign
          </Button>
          <Button variant="ghost" onClick={() => autoRevoke.mutate()}>
            Auto-revoke overdue (14 days)
          </Button>
        </>
      }
      bodyClass=""
    >
      <DataGrid
        label="Access review items"
        rows={items}
        rowKey={(x) => x.id}
        empty="No items for this quarter. Start the campaign."
        columns={[
          { key: "u", header: "User", render: (x) => email(x.user_id) },
          { key: "r", header: "Role", render: (x) => x.role_id.slice(0, 8) },
          { key: "c", header: "Client", render: (x) => x.client_id?.slice(0, 8) ?? "tenant" },
          {
            key: "sod",
            header: "Segregation of duties",
            className: "wrap",
            render: (x) =>
              x.sod_warning ? (
                <Chip tone="warn" title={x.sod_warning}>
                  ⚠ {x.sod_warning}
                </Chip>
              ) : (
                ""
              ),
          },
          { key: "d", header: "Decision", render: (x) => <Chip tone={x.decision === "certify" ? "ok" : x.decision === "revoke" ? "danger" : "warn"}>{x.decision}</Chip> },
          { key: "due", header: "Due", render: (x) => formatDate(x.due_at) },
          {
            key: "a",
            header: "",
            render: (x) =>
              x.decision === "pending" ? (
                <span className="row">
                  <Button size="sm" onClick={() => decide.mutate({ id: x.id, decision: "certify" })}>
                    Certify
                  </Button>
                  <Button size="sm" variant="danger" onClick={() => decide.mutate({ id: x.id, decision: "revoke" })}>
                    Revoke
                  </Button>
                </span>
              ) : null,
          },
        ]}
      />
    </Card>
  );
}

function AiPolicyAdmin() {
  const act = useAdminAction();
  const policy = useQuery({ queryKey: ["ai-policy"], queryFn: () => api<AIPolicyOut>("/ai/policy") });
  const [body, setBody] = useState<string | null>(null);
  const text = body ?? policy.data?.body ?? "";
  return (
    <Card title={`AI use policy${policy.data ? ` — version ${policy.data.version}` : ""} (E-AI-01)`}>
      <div className="stack">
        <p className="small muted">Publishing a new version requires every user to accept it again before requesting AI reviews.</p>
        <TextArea label="Policy text" value={text} onChange={(e) => setBody(e.target.value)} style={{ minHeight: 300 }} />
        <Button
          variant="primary"
          disabled={text.length < 50 || text === policy.data?.body}
          onClick={() => void act("Publish AI policy", (reason) => api("/admin/ai-policy", { method: "PUT", body: { body: text }, reason }), [["ai-policy"]], "New AI policy version published")}
        >
          Publish new version
        </Button>
      </div>
    </Card>
  );
}

function AbacPolicy() {
  const versions = useQuery({ queryKey: ["admin", "abac"], queryFn: () => api<PolicyOut[]>("/admin/policies/abac") });
  const active = (versions.data ?? []).find((p) => p.active) ?? versions.data?.[0];
  return (
    <Card title="Attribute-based access policies (E-SEC-02)">
      {versions.isLoading ? <Spinner /> : null}
      <ErrorNotice error={versions.error} />
      {active ? (
        <div className="stack">
          <span className="small muted">
            Active version {active.version} · {formatDateTime(active.created_at)} · {versions.data?.length ?? 0} version(s). Policies are data; the server evaluates them on every request.
          </span>
          <pre className="small card card-body" style={{ maxHeight: 420, overflow: "auto" }}>
            {active.body}
          </pre>
        </div>
      ) : (
        <EmptyState>The built-in default policy is in force.</EmptyState>
      )}
    </Card>
  );
}

function Incidents() {
  const qc = useQueryClient();
  const toast = useToast();
  const list = useQuery({ queryKey: ["admin", "incidents"], queryFn: () => api<IncidentOut[]>("/admin/incidents") });
  const [f, setF] = useState({ severity: "medium", title: "", description: "", certin_reportable: false });
  const create = useMutation({
    mutationFn: () => api<IncidentOut>("/admin/incidents", { method: "POST", body: f }),
    onSuccess: async () => {
      setF({ severity: "medium", title: "", description: "", certin_reportable: false });
      await qc.invalidateQueries({ queryKey: ["admin", "incidents"] });
    },
    onError: (e) => toast.error(e),
  });
  const patch = useMutation({
    mutationFn: ({ id, body }: { id: string; body: Record<string, unknown> }) => api(`/admin/incidents/${id}`, { method: "PATCH", body }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin", "incidents"] }),
    onError: (e) => toast.error(e),
  });
  return (
    <div className="stack">
      <Card title="Record an incident">
        <div className="grid-4">
          <SelectInput label="Severity" value={f.severity} onChange={(e) => setF((x) => ({ ...x, severity: e.target.value }))} options={["low", "medium", "high", "critical"]} />
          <TextInput label="Title" value={f.title} onChange={(e) => setF((x) => ({ ...x, title: e.target.value }))} />
          <TextInput label="Description" value={f.description} onChange={(e) => setF((x) => ({ ...x, description: e.target.value }))} />
          <Checkbox label="CERT-In reportable (6-hour clock)" checked={f.certin_reportable} onChange={(e) => setF((x) => ({ ...x, certin_reportable: e.target.checked }))} />
        </div>
        <div className="row" style={{ marginTop: 8 }}>
          <Button variant="primary" onClick={() => create.mutate()} disabled={f.title.trim().length < 3} busy={create.isPending}>
            Record
          </Button>
        </div>
      </Card>
      <Card title="Incidents" bodyClass="">
        <DataGrid
          label="Incidents"
          rows={list.data ?? []}
          rowKey={(i) => i.id}
          rowClassName={(i) => (i.overdue ? "highlight-py" : undefined)}
          columns={[
            { key: "s", header: "Severity", render: (i) => <Chip tone={i.severity === "critical" || i.severity === "high" ? "danger" : "warn"}>{i.severity}</Chip> },
            { key: "t", header: "Title", className: "wrap", render: (i) => i.title },
            { key: "d", header: "Detected", render: (i) => formatDateTime(i.detected_at) },
            {
              key: "c",
              header: "CERT-In report",
              render: (i) =>
                i.certin_reportable ? (
                  i.reported_at ? (
                    <Chip tone="ok">reported {formatDateTime(i.reported_at)}</Chip>
                  ) : (
                    <Chip tone={i.overdue ? "danger" : "warn"}>due {relativeTime(i.report_due_at)}</Chip>
                  )
                ) : (
                  "not reportable"
                ),
            },
            { key: "st", header: "Status", render: (i) => i.status },
            {
              key: "a",
              header: "",
              render: (i) => (
                <span className="row">
                  {i.certin_reportable && !i.reported_at ? <Button size="sm" onClick={() => patch.mutate({ id: i.id, body: { reported_at: new Date().toISOString() } })}>Mark reported</Button> : null}
                  {i.status !== "closed" ? <Button size="sm" variant="ghost" onClick={() => patch.mutate({ id: i.id, body: { status: i.status === "open" ? "contained" : i.status === "contained" ? "resolved" : "closed" } })}>Advance</Button> : null}
                </span>
              ),
            },
          ]}
        />
      </Card>
    </div>
  );
}

function Dsr() {
  const qc = useQueryClient();
  const toast = useToast();
  const act = useAdminAction();
  const list = useQuery({ queryKey: ["admin", "dsr"], queryFn: () => api<DsrOut[]>("/admin/dsr") });
  const [f, setF] = useState({ principal_ref: "", kind: "access", details: "" });
  const create = useMutation({
    mutationFn: () => api<DsrOut>("/admin/dsr", { method: "POST", body: f }),
    onSuccess: async () => {
      setF({ principal_ref: "", kind: "access", details: "" });
      await qc.invalidateQueries({ queryKey: ["admin", "dsr"] });
    },
    onError: (e) => toast.error(e),
  });
  return (
    <div className="stack">
      <Card title="New data-principal request (DPDP Act 2023)">
        <div className="grid-3">
          <TextInput label="Data principal (e-mail / reference)" value={f.principal_ref} onChange={(e) => setF((x) => ({ ...x, principal_ref: e.target.value }))} />
          <SelectInput label="Request" value={f.kind} onChange={(e) => setF((x) => ({ ...x, kind: e.target.value }))} options={["access", "correction", "erasure", "grievance"]} />
          <TextInput label="Details" value={f.details} onChange={(e) => setF((x) => ({ ...x, details: e.target.value }))} />
        </div>
        <div className="row" style={{ marginTop: 8 }}>
          <Button variant="primary" onClick={() => create.mutate()} disabled={f.principal_ref.length < 3} busy={create.isPending}>
            Log request
          </Button>
        </div>
      </Card>
      <Card title="Requests" bodyClass="">
        <DataGrid
          label="Data-principal requests"
          rows={list.data ?? []}
          rowKey={(d) => d.id}
          columns={[
            { key: "p", header: "Principal", render: (d) => d.principal_ref },
            { key: "k", header: "Request", render: (d) => titleCase(d.kind) },
            { key: "s", header: "Status", render: (d) => <Chip tone={d.status === "completed" ? "ok" : "warn"}>{d.status}</Chip> },
            { key: "due", header: "Due", render: (d) => `${formatDate(d.due_at)} (${relativeTime(d.due_at)})` },
            { key: "r", header: "Resolution", className: "wrap", render: (d) => d.resolution ?? "" },
            {
              key: "a",
              header: "",
              render: (d) =>
                d.status !== "completed" && d.status !== "rejected" ? (
                  <Button size="sm" onClick={() => void act(`Execute ${d.kind} request`, (reason) => api(`/admin/dsr/${d.id}/execute`, { method: "POST", reason }), [["admin", "dsr"]], "Request executed (legal retention respected)")}>
                    Execute
                  </Button>
                ) : null,
            },
          ]}
        />
      </Card>
    </div>
  );
}

// ------------------------------------------------------------------ operations

const BACKUP_KIND: Record<string, string> = {
  desktop: "Desktop backup",
  pg_full: "Database (full)",
  pg_diff: "Database (differential)",
  wal: "Database log (WAL)",
  objects: "Files (object store)",
  config: "Configuration",
  restore_drill: "Restore drill",
  key_recovery_drill: "Key recovery drill",
  restore_request: "Restore request",
};

/** Where a backup went: its file, and the second copy the desktop launcher made in the backup copy folder. */
function BackupLocation({ b }: { b: BackupLogOut }) {
  const d = b.details_json ?? {};
  const copiedTo = typeof d.copied_to === "string" ? d.copied_to : null;
  const copyError = typeof d.copy_error === "string" ? d.copy_error : null;
  return (
    <span className="stack" style={{ gap: 2 }}>
      <span className="mono small" style={{ wordBreak: "break-all" }} title={b.location}>
        {b.location || "—"}
      </span>
      {copiedTo ? (
        <span className="small muted" style={{ wordBreak: "break-all" }}>
          copy: {copiedTo}
        </span>
      ) : null}
      {copyError ? (
        <span>
          <Chip tone="danger" title={copyError}>
            copy failed
          </Chip>
        </span>
      ) : null}
    </span>
  );
}

function BackupCopyFolder() {
  const qc = useQueryClient();
  const toast = useToast();
  const stepUp = useStepUp();
  const settings = useQuery({ queryKey: ["admin", "backup-settings"], queryFn: () => api<BackupSettingsOut>("/admin/backup-settings") });
  const [folder, setFolder] = useState<string | null>(null);
  const current = settings.data?.backup_copy_dir ?? "";
  const value = folder ?? current;
  const save = async () => {
    try {
      await stepUp.run(value.trim() ? "Set the backup copy folder" : "Stop copying backups", (reason) =>
        api<BackupSettingsOut>("/admin/backup-settings", { method: "PUT", body: { backup_copy_dir: value.trim() || null }, reason }),
      );
      toast.ok(value.trim() ? "Backup copy folder saved" : "Backups are no longer copied");
      await qc.invalidateQueries({ queryKey: ["admin", "backup-settings"] });
      setFolder(null);
    } catch (e) {
      if ((e as Error).message !== "cancelled") toast.error(e);
    }
  };
  return (
    <div className="card-body stack" data-testid="backup-copy-folder">
      <ErrorNotice error={settings.error} />
      <div className="row" style={{ alignItems: "flex-end" }}>
        <TextInput
          label="Backup copy folder"
          value={value}
          onChange={(e) => setFolder(e.target.value)}
          placeholder="D:\FS-Platform backups"
          hint="Desktop edition: every backup is also copied here"
          style={{ minWidth: 360 }}
        />
        <Button variant="primary" disabled={settings.isLoading || value.trim() === current} onClick={() => void save()}>
          Save
        </Button>
      </div>
      <span className="small muted">
        Use another drive, a USB disk or a network folder, so that a failed disk does not take the backups with it. Give the full path,
        starting with the drive letter.
      </span>
      {settings.data?.backup_copy_dir && settings.data.exists === false ? (
        <Notice tone="warn">The folder is not reachable right now (drive unplugged?). Backups are copied there once it is back.</Notice>
      ) : null}
    </div>
  );
}

function Backups() {
  const act = useAdminAction();
  const { me } = useAuth();
  const list = useQuery({ queryKey: ["admin", "backups"], queryFn: () => api<BackupLogOut[]>("/admin/backups") });
  return (
    <Card title="Backups and restore drills (07, C13)" bodyClass="">
      {canAny(me, ["ops.manage", "admin.compliance"]) ? <BackupCopyFolder /> : null}
      <DataGrid
        label="Backup log"
        rows={list.data ?? []}
        rowKey={(b) => b.id}
        empty="No backup records yet. The backup jobs post their results here."
        columns={[
          { key: "k", header: "Kind", render: (b) => BACKUP_KIND[b.kind] ?? titleCase(b.kind) },
          { key: "s", header: "Status", render: (b) => <Chip tone={b.status === "ok" ? "ok" : b.status === "failed" ? "danger" : "warn"}>{b.status}</Chip> },
          { key: "w", header: "Started", render: (b) => formatDateTime(b.started) },
          { key: "z", header: "Size", className: "num", render: (b) => formatBytes(b.size) },
          { key: "l", header: "Location", className: "wrap", render: (b) => <BackupLocation b={b} /> },
          { key: "v", header: "Verified", render: (b) => (b.verified ? <Chip tone="ok">verified</Chip> : "—") },
          {
            key: "a",
            header: "",
            render: (b) =>
              b.kind === "restore_request" && b.status === "pending_approval" ? (
                <Button size="sm" onClick={() => void act("Approve restore of client data", (reason) => api(`/admin/backups/${b.id}/approve-restore`, { method: "POST", reason }), [["admin", "backups"]], "Restore approved")}>
                  Approve restore
                </Button>
              ) : null,
          },
        ]}
      />
    </Card>
  );
}

function Releases() {
  const qc = useQueryClient();
  const toast = useToast();
  const list = useQuery({ queryKey: ["admin", "releases"], queryFn: () => api<ReleaseRecordOut[]>("/admin/releases") });
  const [f, setF] = useState({ version: "", ticket_ref: "", description: "", risk: "low", test_evidence: "", rollback_plan: "", rule_change: false, emergency: false });
  const create = useMutation({
    mutationFn: () => api<ReleaseRecordOut>("/admin/releases", { method: "POST", body: f }),
    onSuccess: async () => {
      toast.ok("Release recorded; a different person approves it (E-ITGC-01)");
      await qc.invalidateQueries({ queryKey: ["admin", "releases"] });
    },
    onError: (e) => toast.error(e),
  });
  const step = useMutation({
    mutationFn: ({ id, action }: { id: string; action: "approve" | "deployed" }) => api(`/admin/releases/${id}/${action}`, { method: "POST" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin", "releases"] }),
    onError: (e) => toast.error(e),
  });
  return (
    <div className="stack">
      <Card title="Record a production change (E-ITGC-01 / 02)">
        <div className="grid-3">
          <TextInput label="Version" value={f.version} onChange={(e) => setF((x) => ({ ...x, version: e.target.value }))} />
          <TextInput label="Ticket" value={f.ticket_ref} onChange={(e) => setF((x) => ({ ...x, ticket_ref: e.target.value }))} />
          <SelectInput label="Risk" value={f.risk} onChange={(e) => setF((x) => ({ ...x, risk: e.target.value }))} options={["low", "medium", "high"]} />
          <TextInput label="Test evidence" value={f.test_evidence} onChange={(e) => setF((x) => ({ ...x, test_evidence: e.target.value }))} />
          <TextInput label="Rollback plan" value={f.rollback_plan} onChange={(e) => setF((x) => ({ ...x, rollback_plan: e.target.value }))} />
          <TextInput label="Description" value={f.description} onChange={(e) => setF((x) => ({ ...x, description: e.target.value }))} />
        </div>
        <div className="row" style={{ marginTop: 8 }}>
          <Checkbox label="Rule change (needs golden regression evidence)" checked={f.rule_change} onChange={(e) => setF((x) => ({ ...x, rule_change: e.target.checked }))} />
          <Checkbox label="Emergency change (review within 48 h)" checked={f.emergency} onChange={(e) => setF((x) => ({ ...x, emergency: e.target.checked }))} />
          <Button variant="primary" onClick={() => create.mutate()} busy={create.isPending} disabled={!f.version || !f.ticket_ref || f.test_evidence.length < 3 || f.rollback_plan.length < 3}>
            Record
          </Button>
        </div>
      </Card>
      <Card title="Change log" bodyClass="">
        <DataGrid
          label="Releases"
          rows={list.data ?? []}
          rowKey={(r) => r.id}
          columns={[
            { key: "v", header: "Version", render: (r) => r.version },
            { key: "t", header: "Ticket", render: (r) => r.ticket_ref },
            { key: "r", header: "Risk", render: (r) => r.risk },
            { key: "f", header: "", render: (r) => <span className="row">{r.rule_change ? <Chip tone="warn">rule change</Chip> : null}{r.emergency ? <Chip tone="danger">emergency</Chip> : null}</span> },
            { key: "a", header: "Approved", render: (r) => (r.approved_at ? formatDateTime(r.approved_at) : <Button size="sm" onClick={() => step.mutate({ id: r.id, action: "approve" })}>Approve</Button>) },
            { key: "d", header: "Deployed", render: (r) => (r.deployed_at ? formatDateTime(r.deployed_at) : r.approved_at ? <Button size="sm" onClick={() => step.mutate({ id: r.id, action: "deployed" })}>Mark deployed</Button> : "—") },
          ]}
        />
      </Card>
    </div>
  );
}

function Usage() {
  const [month, setMonth] = useState(() => new Date().toISOString().slice(0, 7));
  const usage = useQuery({ queryKey: ["admin", "usage", month], queryFn: () => api<UsageOut>("/admin/usage", { query: { month } }) });
  const rows = usage.data?.rows ?? [];
  const cols = rows.length ? Object.keys(rows[0] ?? {}) : [];
  return (
    <Card title="Usage per client per month (billing)" actions={<TextInput label="Month" type="text" value={month} onChange={(e) => setMonth(e.target.value)} style={{ width: 110 }} />} bodyClass="">
      {usage.isLoading ? <div className="card-body"><Spinner /></div> : null}
      <ErrorNotice error={usage.error} />
      <DataGrid
        label="Usage"
        rows={rows.map((r, i) => ({ ...r, __k: String(i) }))}
        rowKey={(r) => String(r.__k)}
        empty="No usage recorded for this month."
        columns={cols.map((c) => ({ key: c, header: titleCase(c), className: typeof rows[0]?.[c] === "number" ? "num" : undefined, render: (r: Record<string, unknown>) => String(r[c] ?? "") }))}
      />
    </Card>
  );
}

function Health() {
  const h = useQuery({ queryKey: ["admin", "health"], queryFn: () => api<HealthOut>("/admin/health"), refetchInterval: 30_000 });
  if (h.isLoading) return <Spinner />;
  if (h.error) return <ErrorNotice error={h.error} />;
  const d = h.data;
  if (!d) return null;
  return (
    <div className="grid-2">
      <Card title="Platform">
        <KeyValue
          items={[
            ["Status", <Chip key="s" tone={d.status === "ok" ? "ok" : "danger"}>{d.status}</Chip>],
            ["API version", d.version],
            ["Engine", `${d.engine} ${d.engine_version}`],
            ["Database", d.database ? "reachable" : "DOWN"],
            ["Object storage", d.storage ? "reachable" : "DOWN"],
            ["Queues", Object.entries(d.queue_depth).map(([q, n]) => `${q}: ${n}`).join(" · ") || "empty"],
          ]}
        />
      </Card>
      <Card title="Operations">
        <KeyValue
          items={[
            ["Render workers", d.render_workers.length ? d.render_workers.map((w) => String(w.worker_id ?? w.id)).join(", ") : "none registered (LibreOffice / built-in renderer)"],
            ["Last backup", d.last_backup ? `${BACKUP_KIND[String(d.last_backup.kind)] ?? String(d.last_backup.kind ?? "")} ${formatDateTime(String(d.last_backup.started ?? ""))} — ${String(d.last_backup.status ?? "")}` : "none recorded"],
            ["Last restore drill", d.last_restore_drill ? formatDateTime(String(d.last_restore_drill.started ?? "")) : "none recorded"],
            ["Audit chain head", d.audit_head ? <span className="mono" key="h">#{String(d.audit_head.seq ?? "")} {shortHash(String(d.audit_head.hash ?? d.audit_head.event_hash ?? ""))}</span> : "—"],
          ]}
        />
      </Card>
    </div>
  );
}

function Evidence() {
  const toast = useToast();
  const [quarter, setQuarter] = useState(() => {
    const d = new Date();
    return `${d.getFullYear()}Q${Math.floor(d.getMonth() / 3) + 1}`;
  });
  const [busy, setBusy] = useState(false);
  const download = async () => {
    setBusy(true);
    try {
      saveFile(await apiBlob("/admin/evidence-pack", { query: { quarter } }, `evidence-pack-${quarter}.zip`));
    } catch (e) {
      toast.error(e, "Evidence pack not produced");
    } finally {
      setBusy(false);
    }
  };
  return (
    <Card title="Control evidence pack (E-ITGC-06)">
      <div className="stack">
        <p className="small muted">One zip per quarter: audit-chain verification, access reviews, backups and restore drills, releases and rule-regression records — mapped to ISO 27001, SOC 2 and SQM 1.</p>
        <div className="row" style={{ alignItems: "flex-end" }}>
          <TextInput label="Quarter" value={quarter} onChange={(e) => setQuarter(e.target.value.toUpperCase())} style={{ width: 120 }} />
          <Button variant="primary" busy={busy} onClick={() => void download()}>
            ⤓ Download evidence pack
          </Button>
        </div>
      </div>
    </Card>
  );
}
