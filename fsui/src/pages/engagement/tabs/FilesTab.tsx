/** Files: drag-and-drop upload, immutable versions, check-out lock, diff, restore, watermarked download. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { DragEvent } from "react";
import { useRef, useState } from "react";
import { Link } from "react-router-dom";

import { ApiError, api, apiBlob, saveFile } from "@/api/client";
import { qk, useFiles } from "@/api/hooks";
import type { CheckoutOut, DiffOut, FileOut, FileVersionOut, ShareLinkOut, UploadOut } from "@/api/types";
import type { ChangeRow, PrintedMove } from "@/api/types";
import { useAuth } from "@/auth/AuthContext";
import { DataGrid } from "@/components/DataGrid";
import { Modal } from "@/components/Modal";
import { RunWatcher } from "@/components/RunWatcher";
import { useToast } from "@/components/Toast";
import { Button, Card, Chip, EmptyState, ErrorNotice, Notice, SelectInput, Spinner } from "@/components/ui";
import { cellRef } from "@/lib/cells";
import { formatBytes, formatDateTime, relativeTime, shortHash, titleCase } from "@/lib/format";

import type { TabProps } from "../tabKeys";

const KINDS = [
  { value: "workbook", label: "Statements workbook (.xlsx / .xlsm)" },
  { value: "tb", label: "Trial balance" },
  { value: "printed_pdf", label: "Printed PDF (for staleness check)" },
  { value: "prior_year", label: "Prior-year signed statements" },
  { value: "other", label: "Other supporting file" },
];

export function FilesTab({ eng, can }: TabProps) {
  const qc = useQueryClient();
  const toast = useToast();
  const files = useFiles(eng.id);
  const [kind, setKind] = useState("workbook");
  const [over, setOver] = useState(false);
  const [autoRun, setAutoRun] = useState<string | null>(null);
  const [stored, setStored] = useState<UploadOut | null>(null);
  const [openFile, setOpenFile] = useState<FileOut | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const upload = useMutation({
    mutationFn: async (file: File) => {
      const form = new FormData();
      form.append("file", file);
      form.append("kind", kind);
      return api<UploadOut>(`/engagements/${eng.id}/files`, { method: "POST", form });
    },
    onSuccess: async (out) => {
      toast.ok(`Uploaded ${out.version.original_name} as version ${out.version.version_no}`);
      setStored(out);
      setAutoRun(out.auto_check_run_id ?? null);
      await qc.invalidateQueries({ queryKey: ["engagement", eng.id] });
    },
    onError: (e) => toast.error(e, "Upload refused"),
  });

  const onDrop = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setOver(false);
    const f = e.dataTransfer.files?.[0];
    if (f) upload.mutate(f);
  };

  return (
    <div className="stack">
      {can("file.upload") ? (
        <Card title="Upload">
          <div className="row" style={{ alignItems: "flex-end" }}>
            <div style={{ minWidth: 320 }}>
              <SelectInput label="File type" value={kind} onChange={(e) => setKind(e.target.value)} options={KINDS} />
            </div>
            <span className="small muted">Accepted: .xlsx, .xlsm (macros are stored, never run), .pdf, .csv. Files with external links are refused.</span>
          </div>
          <div
            className={`dropzone ${over ? "over" : ""}`}
            style={{ marginTop: 10 }}
            onDragOver={(e) => {
              e.preventDefault();
              setOver(true);
            }}
            onDragLeave={() => setOver(false)}
            onDrop={onDrop}
          >
            <p>Drag a file here, or</p>
            <input
              ref={inputRef}
              type="file"
              data-testid="file-input"
              accept=".xlsx,.xlsm,.pdf,.csv"
              aria-label="Choose a file to upload"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) upload.mutate(f);
                e.target.value = "";
              }}
            />
            {upload.isPending ? <Spinner label="Uploading and scanning" /> : null}
          </div>
          {autoRun ? (
            <div style={{ marginTop: 10 }}>
              <RunWatcher
                runId={autoRun}
                title="Checks started automatically on the new version"
                onDone={() => {
                  void qc.invalidateQueries({ queryKey: ["engagement", eng.id] });
                }}
              />
            </div>
          ) : stored?.file.kind === "workbook" && can("run.check") ? (
            <div style={{ marginTop: 10 }}>
              <Notice>
                Version {stored.version.version_no} is stored. Checks re-run by themselves only after the statements are verified;{" "}
                <Link to={`/engagements/${eng.id}/checks`}>run checks on it now</Link>.
              </Notice>
            </div>
          ) : null}
        </Card>
      ) : null}
      <Card title="Files" bodyClass="">
        {files.isLoading ? (
          <div className="card-body">
            <Spinner />
          </div>
        ) : files.error ? (
          <div className="card-body">
            <ErrorNotice error={files.error} />
          </div>
        ) : (
          <DataGrid
            label="Files"
            rows={files.data ?? []}
            rowKey={(f) => f.id}
            onOpen={(f) => setOpenFile(f)}
            selectedKey={openFile?.id}
            empty={<EmptyState>No files yet. Upload the statements workbook to start.</EmptyState>}
            columns={[
              { key: "title", header: "File", render: (f) => <strong>{f.title}</strong>, sortValue: (f) => f.title },
              { key: "kind", header: "Type", render: (f) => titleCase(f.kind) },
              { key: "ver", header: "Latest", render: (f) => (f.latest_version ? `v${f.latest_version.version_no} · ${f.latest_version.original_name}` : "—") },
              { key: "size", header: "Size", className: "num", render: (f) => formatBytes(f.latest_version?.size) },
              { key: "when", header: "Uploaded", render: (f) => formatDateTime(f.latest_version?.created_at) },
              {
                key: "lock",
                header: "Lock",
                render: (f) => (f.checked_out_by ? <Chip tone="warn">🔒 checked out</Chip> : <Chip>available</Chip>),
              },
            ]}
          />
        )}
      </Card>
      {openFile ? <FileDetail eng={eng} can={can} file={openFile} onClose={() => setOpenFile(null)} /> : null}
    </div>
  );
}

function FileDetail({ eng, can, file, onClose }: TabProps & { file: FileOut; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const { me } = useAuth();
  const versions = useQuery({ queryKey: qk.versions(file.id), queryFn: () => api<FileVersionOut[]>(`/files/${file.id}/versions`) });
  const [a, setA] = useState<string>("");
  const [b, setB] = useState<string>("");
  const [diff, setDiff] = useState<DiffOut | null>(null);
  const [share, setShare] = useState<ShareLinkOut | null>(null);
  const lockedByMe = file.checked_out_by === me?.user.id;

  const refresh = async () => {
    await qc.invalidateQueries({ queryKey: qk.files(eng.id) });
    await qc.invalidateQueries({ queryKey: qk.versions(file.id) });
  };
  const checkout = useMutation({
    mutationFn: () => api<CheckoutOut>(`/files/${file.id}/checkout`, { method: "POST", query: { minutes: 120 } }),
    onSuccess: async (c) => {
      toast.ok(`Checked out until ${formatDateTime(c.expires_at)} (${relativeTime(c.expires_at)})`);
      await refresh();
      onClose();
    },
    onError: (e) => toast.error(e),
  });
  const checkin = useMutation({
    mutationFn: () => api(`/files/${file.id}/checkout`, { method: "DELETE" }),
    onSuccess: async () => {
      toast.ok("Lock released");
      await refresh();
      onClose();
    },
    onError: (e) => toast.error(e),
  });
  const restore = useMutation({
    mutationFn: (vid: string) => api<FileVersionOut>(`/versions/${vid}/restore`, { method: "POST" }),
    onSuccess: async (v) => {
      toast.ok(`Restored as new version v${v.version_no} (nothing was overwritten)`);
      await refresh();
      await qc.invalidateQueries({ queryKey: ["engagement", eng.id] });
    },
    onError: (e) => toast.error(e),
  });
  const runDiff = useMutation({
    mutationFn: () => api<DiffOut>(`/engagements/${eng.id}/diff`, { query: { a, b } }),
    onSuccess: (d) => setDiff(d),
  });
  const download = async (v: FileVersionOut) => {
    try {
      const f = await apiBlob(`/versions/${v.id}/download`, {}, v.original_name);
      saveFile(f);
      toast.info(v.content_type === "application/pdf" ? "Downloaded a copy watermarked with your name and the time." : "Downloaded; the download is recorded in the audit trail.");
    } catch (e) {
      toast.error(e, "Download refused");
    }
  };
  const makeShare = async (v: FileVersionOut) => {
    try {
      setShare(await api<ShareLinkOut>(`/versions/${v.id}/share-links`, { method: "POST", body: { hours: 24 } }));
    } catch (e) {
      toast.error(e);
    }
  };

  const list = versions.data ?? [];
  const changes = ((diff?.diff?.changes as ChangeRow[] | undefined) ?? []).slice(0, 500);
  const moves = ((diff?.diff?.printed_moves as PrintedMove[] | undefined) ?? []).slice(0, 500);
  const hidden = (diff?.diff?.hidden_changes as string[] | undefined) ?? [];

  return (
    <Modal title={file.title} onClose={onClose} wide>
      <div className="row between">
        <span className="small muted">{titleCase(file.kind)} · {list.length} version(s) · versions are immutable (C1)</span>
        <div className="row">
          {can("file.upload") && !file.checked_out_by ? (
            <Button onClick={() => checkout.mutate()} busy={checkout.isPending}>
              Check out for editing
            </Button>
          ) : null}
          {file.checked_out_by && (lockedByMe || can("fix.approve")) ? (
            <Button onClick={() => checkin.mutate()} busy={checkin.isPending}>
              {lockedByMe ? "Check in (release lock)" : "Break lock"}
            </Button>
          ) : null}
        </div>
      </div>
      {file.checked_out_by && !lockedByMe ? <Notice tone="warn">Checked out by another user — uploads of new versions are blocked until the lock is released or expires (E-DATA-03).</Notice> : null}
      {versions.isLoading ? (
        <Spinner />
      ) : (
        <DataGrid
          label="Versions"
          rows={list}
          rowKey={(v) => v.id}
          columns={[
            { key: "no", header: "Version", render: (v) => `v${v.version_no}${v.version_line > 1 ? ` (line ${v.version_line})` : ""}` },
            { key: "name", header: "Name", render: (v) => v.original_name },
            { key: "src", header: "Source", render: (v) => String(v.change_summary_json?.source ?? (v.produced_by_run_id ? "run" : "upload")) },
            { key: "sha", header: "SHA-256", render: (v) => <span className="mono" title={v.sha256}>{shortHash(v.sha256)}</span> },
            { key: "when", header: "Created", render: (v) => formatDateTime(v.created_at) },
            { key: "flags", header: "", render: (v) => <span className="row">{v.has_macros ? <Chip tone="warn">macros (not run)</Chip> : null}{v.worm_until ? <Chip tone="ok">WORM</Chip> : null}</span> },
            {
              key: "act",
              header: "",
              render: (v) => (
                <span className="row">
                  {can("file.download") ? <Button size="sm" onClick={() => void download(v)}>Download</Button> : null}
                  {can("file.upload") && v.id !== list[0]?.id ? <Button size="sm" onClick={() => restore.mutate(v.id)}>Restore as new</Button> : null}
                  {can("release.client") ? <Button size="sm" variant="ghost" onClick={() => void makeShare(v)}>Share link</Button> : null}
                </span>
              ),
            },
          ]}
        />
      )}
      {share ? (
        <Notice tone="info">
          Single-use link, expires {formatDateTime(share.expires_at)}: <code className="mono">{share.url}</code>
        </Notice>
      ) : null}
      {list.length >= 2 && (file.kind === "workbook" || list[0]?.original_name.endsWith(".xlsx")) ? (
        <div className="stack">
          <h3>Compare versions</h3>
          <div className="row" style={{ alignItems: "flex-end" }}>
            <SelectInput label="From" value={a} onChange={(e) => setA(e.target.value)} options={[{ value: "", label: "—" }, ...list.map((v) => ({ value: v.id, label: `v${v.version_no}` }))]} />
            <SelectInput label="To" value={b} onChange={(e) => setB(e.target.value)} options={[{ value: "", label: "—" }, ...list.map((v) => ({ value: v.id, label: `v${v.version_no}` }))]} />
            <Button onClick={() => runDiff.mutate()} disabled={!a || !b || a === b} busy={runDiff.isPending}>
              Show differences
            </Button>
          </div>
          <ErrorNotice error={runDiff.error} />
          {diff ? (
            <div className="stack">
              <div className="row small">
                <Chip>{changes.length} cell change(s)</Chip>
                <Chip tone={moves.length ? "warn" : "ok"}>{moves.length} printed figure(s) moved</Chip>
                <Chip>{hidden.length} hidden row/column change(s)</Chip>
              </div>
              {moves.length ? (
                <DataGrid
                  label="Printed figures moved"
                  rows={moves}
                  rowKey={(m) => `${m.sheet}!${m.cell}`}
                  rowClassName={(m) => (m.is_py ? "highlight-py" : m.is_key ? "highlight-key" : undefined)}
                  columns={[
                    { key: "cell", header: "Cell", render: (m) => <span className="mono">{cellRef(m.sheet, m.cell)}</span> },
                    { key: "cap", header: "Caption", render: (m) => m.caption ?? "" },
                    { key: "b", header: "Before", className: "num", render: (m) => <span className="change-before">{m.before}</span> },
                    { key: "a", header: "After", className: "num", render: (m) => <span className="change-after">{m.after}</span> },
                    { key: "f", header: "", render: (m) => <span className="row">{m.is_py ? <Chip tone="warn">PY</Chip> : null}{m.is_key ? <Chip tone="accent">key</Chip> : null}</span> },
                  ]}
                />
              ) : null}
              {changes.length ? (
                <DataGrid
                  label="Cell changes"
                  rows={changes}
                  rowKey={(c) => `${c.sheet}!${c.cell}:${c.kind}`}
                  maxHeight={300}
                  columns={[
                    { key: "cell", header: "Cell", render: (c) => <span className="mono">{cellRef(c.sheet, c.cell)}</span> },
                    { key: "kind", header: "Change", render: (c) => c.kind },
                    { key: "b", header: "Before", render: (c) => <span className="formula">{c.before}</span> },
                    { key: "a", header: "After", render: (c) => <span className="formula">{c.after}</span> },
                  ]}
                />
              ) : null}
            </div>
          ) : null}
        </div>
      ) : null}
      {checkout.error instanceof ApiError ? <ErrorNotice error={checkout.error} /> : null}
    </Modal>
  );
}
