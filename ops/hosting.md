# Hosting (04 §8, ADR-0002, ADR-0004)

**Decision for go-live:** single-node Docker Compose on an office Linux server (Mode A), remote
access through Cloudflare Tunnel (no inbound ports on the office router), off-site encrypted backups
in an India region. The HA profile (`ops/helm/fs-platform`) is ready for phase 9 / growth; the India
cloud layout (`ops/iac/terraform/azure-centralindia`) doubles as the DR target.

## Options compared

| | 1. Office server (chosen) | 2. India-region cloud | 3. Hybrid |
|---|---|---|---|
| Where data lives | firm premises | Azure Central India / AWS Mumbai / GCP Mumbai | web + DB in cloud, Excel render on office Windows box |
| Hardware | Linux host 8 vCPU / 16 GB / 1 TB SSD RAID-1 + Windows Server VM, UPS, NAS | 2 Linux VMs + 1 Windows VM, object storage with versioning | as 2, plus site-to-site VPN |
| Remote access | Cloudflare Tunnel or WireGuard VPN | private endpoints + tunnel / VPN | same |
| Strengths | lowest running cost; data never leaves premises | no hardware; easy DR; managed backups | Excel licences stay on-prem |
| Weaknesses | single site (mitigated by off-site backups, RTO 4 h) | monthly cost; cloud key management | most moving parts |
| Same containers? | yes | yes | yes |

## Sizing (04 §9 and 12 §4)

| Load | Single node | HA profile |
|---|---|---|
| Users | 50 concurrent | 200 concurrent |
| Engagements / year | 300 | 2,000 |
| Workbook | 60 MB, 2 M cells, 60 print sheets | 100 MB, 5 M cells |
| Workers | 2–4 check/fix workers, 1–2 Excel render workers | autoscaled API 2–6, workers N, render pool ≥ 2 |
| Storage growth | ≈ 200 GB / year incl. versions and PDFs | same per firm |

Peak season (September–November): add worker replicas and a second render worker; load-test the
peak plan beforehand (E-OPS-05, k6/Locust: 50 users, 10 parallel checks, 60 MB workbooks, 8-hour soak).

## Microsoft Excel on a server — licensing and support risk (11 §3)

- Server-side automation of Office is **not supported by Microsoft** for unattended services; Excel can
  hang on dialogs or leak processes. Mitigations: dedicated worker VM, one Excel per job, `DisplayAlerts`
  off, watchdog kill of only the worker's own processes, queue retry on another worker, LibreOffice
  fallback, and the mandatory PDF-vs-workbook verification (PDF-01) on every render (`fsrender-win`).
- Licensing: Microsoft 365 Apps on a server / RDS need **shared computer activation** with an eligible plan
  (e.g. Microsoft 365 Business Premium, E3, E5); volume-licensed Office LTSC is the alternative. Confirm
  with the firm's licensing partner before go-live and record the outcome here:

  | Item | Decision (to be confirmed by the firm) |
  |---|---|
  | Excel licence for render workers | _M365 Apps with shared computer activation / Office LTSC_ |
  | Number of render workers | 1 at go-live, 2 for E-ARCH-03 (T2) |
  | Excel build pinning owner | IT lead |
  | Mode B (RDS RemoteApp) licences | deferred to T2 (RDS CALs + Office on RDS) |

## Mode B (Excel on the server, RemoteApp) — T2

Deferred (ADR-0004). When enabled: RD Gateway with MFA; clipboard, drive, printer and USB redirection
disabled by policy; session time-outs; optional session recording for client-data sessions; Excel opened
read-only unless the user has edit rights; saves arrive as new versions through the file-watcher with the
user recorded (E-SEC-09). Group-policy export to be added under `ops/` with the hardening work.

## Mode C (offline laptops)

`fsoffline/` — loopback-only server, SQLite, signed/encrypted `.fspkg` exchange only. Laptops require
full-disk encryption and are managed by the firm's device-management tool.

## Network summary

- Only Caddy (443/80) is published — or nothing at all with Cloudflare Tunnel.
- API, workers, PostgreSQL, MinIO and Redis live on an internal Docker network **without internet route**.
- The AI gateway is the only egress path to AI providers (ADR-0020); ClamAV signature updates and the
  backup container reach the internet for their single purpose.
- Render workers reach `/api/v1/internal/*` from the render subnet only (Caddy allow-list + token).
