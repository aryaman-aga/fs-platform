# ops — deployment, backups, monitoring, runbooks, IaC and ITGC

| Path | What | Requirements |
|---|---|---|
| `docker-compose.yml` | single-node profile: Caddy, API, workers, AI gateway, PostgreSQL + pgBackRest, MinIO, optional Redis / ClamAV / monitoring / backup / tunnel | 04 §2–§3, E-ARCH-02/05, ADR-0002 |
| `docker-compose.ha.yml` | HA rehearsal overlay (2 API replicas + Redis) | E-ARCH-01/02 |
| `helm/fs-platform/` | HA profile for k3s / Kubernetes | E-ARCH-01/04 |
| `docker/` | images (Python api/worker with LibreOffice, Caddy + SPA, PostgreSQL + pgBackRest, backup tooling) with whitelist ignore files | E-SEC-12 |
| `caddy/Caddyfile` | TLS, HSTS, CSP, rate limits, office / render-pool allow-lists, SPA + API | 05 §1, §5 |
| `cloudflared/` | tunnel configuration (no inbound ports) | ADR-0002 |
| `.env.example` | every setting and secret placeholder | 09 §2 |
| `postgres/`, `minio/` | first-start roles (RLS runtime role) and buckets (versioning, object lock) | 05 §3, 06 §1 |
| `backup/` | pgBackRest, restic (NAS + object-locked off-site), metrics, restore drill, audit-chain job, timers | 07, C13, E-BCP-02 |
| `monitoring/` | Prometheus, SLO recording rules, alerts, Alertmanager, Grafana, Loki (180 d), Alloy, Tempo, OTel | E-OPS-01/02/04, E-SEC-05 |
| `runbooks/` | restore, DR, incident response (CERT-In 6 h, DPDP), key recovery, one runbook per alert | 07 §5, E-OPS-04 |
| `ntp/chrony.conf` | NIC / NPL time synchronisation | E-SEC-05 |
| `iac/` | Terraform (Azure Central India) and Ansible skeletons | E-ARCH-04 |
| `hosting.md`, `bcp.md`, `tabletop-exercises.md` | hosting decision and Excel licensing, BCP, exercises | 04 §8, E-BCP-01/04 |
| `itgc/` | change, rule-regression, access and operations procedures, templates | E-ITGC-01…05 |
| `tests/` | configuration tests (YAML, compose network segregation, alert → runbook links, script safety, docs coverage) | — |

Quick start (single node): see `docs/admin-guide.md`.
