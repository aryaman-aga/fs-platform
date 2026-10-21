/** Client & entity master (08 §3 screen 3): entities, signatory master, group tree, engagements. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { FormEvent } from "react";
import { useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { api } from "@/api/client";
import { qk, useClients, useEngagements } from "@/api/hooks";
import type { ClientOut, EngagementOut, EntityOut, SignatoryOut } from "@/api/types";
import { useAuth } from "@/auth/AuthContext";
import { hasNoRole } from "@/auth/permissions";
import { DataGrid } from "@/components/DataGrid";
import { Modal } from "@/components/Modal";
import { NoRoleYet } from "@/components/NoRoleYet";
import { useToast } from "@/components/Toast";
import { Button, Card, Chip, EmptyState, EngagementStatusChip, ErrorNotice, KeyValue, PageHeader, SelectInput, Spinner, TextInput } from "@/components/ui";
import { formatDate, titleCase } from "@/lib/format";

export function ClientsPage() {
  const { me, can } = useAuth();
  const clients = useClients();
  const [creating, setCreating] = useState(false);
  return (
    <div className="stack">
      <PageHeader
        title="Clients & entities"
        sub="Master data used by every engagement. PAN / CIN are encrypted and shown masked."
        actions={can("client.manage") ? <Button variant="primary" onClick={() => setCreating(true)}>New client</Button> : undefined}
      />
      <Card bodyClass="">
        {clients.isLoading ? (
          <div className="card-body">
            <Spinner />
          </div>
        ) : clients.error ? (
          <div className="card-body">
            <ErrorNotice error={clients.error} />
          </div>
        ) : (
          <DataGrid
            label="Clients"
            rows={clients.data ?? []}
            rowKey={(c) => c.id}
            empty={hasNoRole(me) ? <NoRoleYet /> : "No clients yet."}
            columns={[
              { key: "name", header: "Client", render: (c) => <Link to={`/clients/${c.id}`}>{c.name}</Link>, sortValue: (c) => c.name },
              { key: "group", header: "Group", render: (c) => c.group_name ?? "—", sortValue: (c) => c.group_name ?? "" },
              { key: "pan", header: "PAN", render: (c) => <span className="mono">{c.pan_masked ?? "—"}</span> },
              { key: "cin", header: "CIN", render: (c) => <span className="mono">{c.cin_masked ?? "—"}</span> },
              { key: "office", header: "Office", render: (c) => c.office ?? "—" },
              { key: "class", header: "Classification", render: (c) => <Chip>{c.classification}</Chip> },
              { key: "status", header: "Status", render: (c) => <Chip tone={c.status === "active" ? "ok" : undefined}>{c.status}</Chip> },
            ]}
          />
        )}
      </Card>
      {creating ? <ClientForm onClose={() => setCreating(false)} /> : null}
    </div>
  );
}

function ClientForm({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const navigate = useNavigate();
  const { refresh } = useAuth();
  const [f, setF] = useState({ name: "", pan: "", cin: "", group_name: "", office: "", classification: "confidential" });
  const m = useMutation({
    mutationFn: () =>
      api<ClientOut>("/clients", {
        method: "POST",
        body: {
          name: f.name,
          pan: f.pan || null,
          cin: f.cin || null,
          group_name: f.group_name || null,
          office: f.office || null,
          classification: f.classification,
        },
      }),
    onSuccess: async (c) => {
      // the creator may now be Partner on the new client (works on engagements): reload the permissions first
      await refresh();
      await qc.invalidateQueries({ queryKey: qk.clients });
      toast.ok(`Client ${c.name} created`);
      onClose();
      navigate(`/clients/${c.id}`);
    },
  });
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF((x) => ({ ...x, [k]: e.target.value }));
  const submit = (e: FormEvent) => {
    e.preventDefault();
    m.mutate();
  };
  return (
    <Modal
      title="New client"
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" busy={m.isPending} onClick={() => m.mutate()} disabled={!f.name}>
            Create
          </Button>
        </>
      }
    >
      <form className="stack" onSubmit={submit}>
        <TextInput label="Name" required value={f.name} onChange={set("name")} />
        <div className="grid-2">
          <TextInput label="PAN" value={f.pan} onChange={(e) => setF((x) => ({ ...x, pan: e.target.value.toUpperCase() }))} placeholder="AAAAA9999A" maxLength={10} />
          <TextInput label="CIN" value={f.cin} onChange={(e) => setF((x) => ({ ...x, cin: e.target.value.toUpperCase() }))} placeholder="U12345KA2015PTC012345" maxLength={21} />
        </div>
        <div className="grid-2">
          <TextInput label="Group" value={f.group_name} onChange={set("group_name")} />
          <TextInput label="Office" value={f.office} onChange={set("office")} />
        </div>
        <SelectInput label="Data classification" value={f.classification} onChange={set("classification")} options={["confidential", "restricted", "internal"]} />
        <ErrorNotice error={m.error} />
      </form>
    </Modal>
  );
}

interface TreeNode {
  e: EntityOut;
  children: TreeNode[];
}

function buildTree(entities: EntityOut[]): TreeNode[] {
  const byId = new Map(entities.map((e) => [e.id, { e, children: [] as TreeNode[] }]));
  const roots: TreeNode[] = [];
  for (const n of byId.values()) {
    const parent = n.e.parent_entity_id ? byId.get(n.e.parent_entity_id) : undefined;
    if (parent) parent.children.push(n);
    else roots.push(n);
  }
  return roots;
}

function Tree({ nodes }: { nodes: TreeNode[] }) {
  return (
    <ul>
      {nodes.map((n) => (
        <li key={n.e.id}>
          <strong>{n.e.name}</strong>{" "}
          <span className="small muted">
            {n.e.country} · {n.e.functional_ccy}
            {n.e.presentation_ccy !== n.e.functional_ccy ? ` → ${n.e.presentation_ccy}` : ""}
          </span>
          {n.children.length ? <Tree nodes={n.children} /> : null}
        </li>
      ))}
    </ul>
  );
}

export function ClientDetailPage() {
  const { clientId = "" } = useParams();
  const { can } = useAuth();
  const client = useQuery({ queryKey: qk.client(clientId), queryFn: () => api<ClientOut>(`/clients/${clientId}`) });
  const entities = useQuery({ queryKey: ["client", clientId, "entities"], queryFn: () => api<EntityOut[]>(`/clients/${clientId}/entities`) });
  const engagements = useEngagements({ client_id: clientId });
  const [entityForm, setEntityForm] = useState(false);
  const [engForm, setEngForm] = useState(false);
  const tree = useMemo(() => buildTree(entities.data ?? []), [entities.data]);
  if (client.isLoading) return <Spinner />;
  if (client.error) return <ErrorNotice error={client.error} />;
  const c = client.data;
  if (!c) return null;
  return (
    <div className="stack">
      <PageHeader
        title={c.name}
        sub={<Link to="/clients">← All clients</Link>}
        actions={
          can("client.manage", c.id) ? (
            <>
              <Button onClick={() => setEntityForm(true)}>Add entity</Button>
              <Button variant="primary" onClick={() => setEngForm(true)} disabled={!entities.data?.length}>
                New engagement
              </Button>
            </>
          ) : undefined
        }
      />
      <div className="grid-2">
        <Card title="Client">
          <KeyValue
            items={[
              ["Group", c.group_name],
              ["PAN", <span className="mono" key="pan">{c.pan_masked ?? "—"}</span>],
              ["CIN", <span className="mono" key="cin">{c.cin_masked ?? "—"}</span>],
              ["Office", c.office],
              ["Classification", c.classification],
              ["Status", c.status],
            ]}
          />
        </Card>
        <Card title="Group structure">
          {entities.isLoading ? <Spinner /> : tree.length ? <div className="tree"><Tree nodes={tree} /></div> : <EmptyState>No entities yet.</EmptyState>}
        </Card>
      </div>
      <Card title="Engagements" bodyClass="">
        <DataGrid
          label="Engagements of this client"
          rows={engagements.data ?? []}
          rowKey={(e) => e.id}
          empty="No engagements yet."
          columns={[
            {
              key: "name",
              header: "Engagement",
              // admins set engagements up but do not open them (no engagement.view)
              render: (e: EngagementOut) => (can("engagement.view", c.id) ? <Link to={`/engagements/${e.id}`}>{e.name}</Link> : e.name),
            },
            { key: "entity", header: "Entity", render: (e) => e.entity_name ?? "—" },
            { key: "period", header: "Period end", render: (e) => formatDate(e.period_end) },
            { key: "type", header: "Type", render: (e) => titleCase(e.type) },
            { key: "status", header: "Status", render: (e) => <EngagementStatusChip status={e.status} /> },
          ]}
        />
      </Card>
      {(entities.data ?? []).map((e) => (
        <SignatoryCard key={e.id} entity={e} canEdit={can("client.manage", c.id)} />
      ))}
      {entityForm ? <EntityForm clientId={c.id} entities={entities.data ?? []} onClose={() => setEntityForm(false)} /> : null}
      {engForm ? <EngagementForm entities={entities.data ?? []} onClose={() => setEngForm(false)} /> : null}
    </div>
  );
}

function SignatoryCard({ entity, canEdit }: { entity: EntityOut; canEdit: boolean }) {
  const qc = useQueryClient();
  const toast = useToast();
  const key = ["entity", entity.id, "signatories"];
  const sigs = useQuery({ queryKey: key, queryFn: () => api<SignatoryOut[]>(`/entities/${entity.id}/signatories`) });
  const [f, setF] = useState({ name: "", role: "Director", din: "", membership_no: "", frn: "" });
  const add = useMutation({
    mutationFn: () =>
      api<SignatoryOut>(`/entities/${entity.id}/signatories`, {
        method: "POST",
        body: { name: f.name, role: f.role, din: f.din || null, membership_no: f.membership_no || null, frn: f.frn || null },
      }),
    onSuccess: async () => {
      setF({ name: "", role: "Director", din: "", membership_no: "", frn: "" });
      await qc.invalidateQueries({ queryKey: key });
      toast.ok("Signatory added");
    },
  });
  const del = useMutation({
    mutationFn: (id: string) => api(`/signatories/${id}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: key }),
    onError: (e) => toast.error(e),
  });
  return (
    <Card title={`Signatory master — ${entity.name}`} bodyClass="card-body stack">
      <DataGrid
        label={`Signatories of ${entity.name}`}
        rows={sigs.data ?? []}
        rowKey={(s) => s.id}
        empty="No signatories recorded; TXT-01 builds the master from the Balance Sheet signature block."
        columns={[
          { key: "name", header: "Name", render: (s) => s.name },
          { key: "role", header: "Role", render: (s) => s.role },
          { key: "din", header: "DIN", render: (s) => <span className="mono">{s.din_masked ?? "—"}</span> },
          { key: "mem", header: "Membership no.", render: (s) => s.membership_no ?? "—" },
          { key: "frn", header: "FRN", render: (s) => s.frn ?? "—" },
          { key: "valid", header: "Valid", render: (s) => (s.valid_from || s.valid_to ? `${formatDate(s.valid_from)} – ${formatDate(s.valid_to)}` : "—") },
          {
            key: "x",
            header: "",
            render: (s) =>
              canEdit ? (
                <Button size="sm" variant="ghost" onClick={() => del.mutate(s.id)} aria-label={`Remove ${s.name}`}>
                  Remove
                </Button>
              ) : null,
          },
        ]}
      />
      {canEdit ? (
        <form
          className="row"
          style={{ alignItems: "flex-end" }}
          onSubmit={(e) => {
            e.preventDefault();
            add.mutate();
          }}
        >
          <TextInput label="Name" value={f.name} onChange={(e) => setF((x) => ({ ...x, name: e.target.value }))} required />
          <SelectInput label="Role" value={f.role} onChange={(e) => setF((x) => ({ ...x, role: e.target.value }))} options={["Director", "Managing Director", "Chief Financial Officer", "Company Secretary", "Partner", "Proprietor"]} />
          <TextInput label="DIN" value={f.din} onChange={(e) => setF((x) => ({ ...x, din: e.target.value.replace(/\D/g, "") }))} maxLength={8} placeholder="8 digits" />
          <TextInput label="Membership no." value={f.membership_no} onChange={(e) => setF((x) => ({ ...x, membership_no: e.target.value.replace(/\D/g, "") }))} maxLength={6} />
          <TextInput label="FRN" value={f.frn} onChange={(e) => setF((x) => ({ ...x, frn: e.target.value.toUpperCase() }))} maxLength={7} />
          <Button type="submit" busy={add.isPending} disabled={!f.name}>
            Add
          </Button>
        </form>
      ) : null}
      <ErrorNotice error={add.error} />
    </Card>
  );
}

function EntityForm({ clientId, entities, onClose }: { clientId: string; entities: EntityOut[]; onClose: () => void }) {
  const qc = useQueryClient();
  const [f, setF] = useState({ name: "", functional_ccy: "INR", presentation_ccy: "INR", country: "IN", parent_entity_id: "" });
  const m = useMutation({
    mutationFn: () => api<EntityOut>(`/clients/${clientId}/entities`, { method: "POST", body: { ...f, parent_entity_id: f.parent_entity_id || null } }),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ["client", clientId, "entities"] });
      onClose();
    },
  });
  return (
    <Modal
      title="Add entity"
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" busy={m.isPending} disabled={!f.name} onClick={() => m.mutate()}>
            Add
          </Button>
        </>
      }
    >
      <TextInput label="Name" value={f.name} onChange={(e) => setF((x) => ({ ...x, name: e.target.value }))} />
      <div className="grid-3">
        <TextInput label="Functional currency" value={f.functional_ccy} maxLength={3} onChange={(e) => setF((x) => ({ ...x, functional_ccy: e.target.value.toUpperCase() }))} />
        <TextInput label="Presentation currency" value={f.presentation_ccy} maxLength={3} onChange={(e) => setF((x) => ({ ...x, presentation_ccy: e.target.value.toUpperCase() }))} />
        <TextInput label="Country" value={f.country} maxLength={2} onChange={(e) => setF((x) => ({ ...x, country: e.target.value.toUpperCase() }))} />
      </div>
      <SelectInput
        label="Parent entity (group structure)"
        value={f.parent_entity_id}
        onChange={(e) => setF((x) => ({ ...x, parent_entity_id: e.target.value }))}
        options={[{ value: "", label: "— none (top of the group) —" }, ...entities.map((e) => ({ value: e.id, label: e.name }))]}
      />
      <ErrorNotice error={m.error} />
    </Modal>
  );
}

function EngagementForm({ entities, onClose }: { entities: EntityOut[]; onClose: () => void }) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const { can } = useAuth();
  const [f, setF] = useState({ entity_id: entities[0]?.id ?? "", name: "", type: "annual", period_end: "2026-03-31", due_date: "" });
  const m = useMutation({
    mutationFn: () => api<EngagementOut>("/engagements", { method: "POST", body: { ...f, due_date: f.due_date || null } }),
    onSuccess: async (e) => {
      await qc.invalidateQueries({ queryKey: ["engagements"] });
      onClose();
      if (can("engagement.view", e.client_id)) navigate(`/engagements/${e.id}`);
    },
  });
  return (
    <Modal
      title="New engagement"
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" busy={m.isPending} disabled={!f.name || !f.entity_id} onClick={() => m.mutate()}>
            Create
          </Button>
        </>
      }
    >
      <SelectInput label="Entity" value={f.entity_id} onChange={(e) => setF((x) => ({ ...x, entity_id: e.target.value }))} options={entities.map((e) => ({ value: e.id, label: e.name }))} />
      <TextInput label="Name" value={f.name} onChange={(e) => setF((x) => ({ ...x, name: e.target.value }))} placeholder="FY 2025-26 statutory accounts" />
      <div className="grid-3">
        <SelectInput label="Type" value={f.type} onChange={(e) => setF((x) => ({ ...x, type: e.target.value }))} options={[{ value: "annual", label: "Annual" }, { value: "interim", label: "Interim" }, { value: "spfs", label: "SPFS (quarterly)" }]} />
        <TextInput label="Period end" type="date" value={f.period_end} onChange={(e) => setF((x) => ({ ...x, period_end: e.target.value }))} />
        <TextInput label="Due date" type="date" value={f.due_date} onChange={(e) => setF((x) => ({ ...x, due_date: e.target.value }))} />
      </div>
      <ErrorNotice error={m.error} />
    </Modal>
  );
}
