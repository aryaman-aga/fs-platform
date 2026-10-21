# Monitoring, SLOs and alerting (E-OPS-01, E-OPS-02, E-OPS-04, 07 §6)

Start with the compose profile: `docker compose --env-file ops/.env -f ops/docker-compose.yml --profile monitoring up -d`.
Grafana is published at `https://<site>/grafana/` for office networks only (Caddy allow-list).

| Component | Purpose | Config |
|---|---|---|
| Prometheus | metrics, SLI recording rules, alert rules | `prometheus.yml`, `recording-rules.yml`, `alerts.yml` |
| Alertmanager | e-mail + webhook relay (WhatsApp Business / Teams) | `alertmanager.yml` (mount `smtp.pass` at `/etc/alertmanager/smtp.pass`) |
| node_exporter | host metrics, clock sync, **textfile** metrics from the backup scripts | compose |
| postgres_exporter | DB size, WAL archiver failures | compose |
| Loki | logs, **180-day retention in India** (CERT-In) | `loki-config.yml` |
| Grafana Alloy | ships container logs to Loki (Promtail is EOL; `promtail-config.yml` kept for migration) | `alloy/config.alloy` |
| Tempo + OTel Collector | traces UI → API → worker → render; scrubs DB statements, URLs, cookies | `tempo.yml`, `otel-collector.yml` |
| Grafana | overview dashboard (SLOs, jobs, backups, audit chain, clock, disk) | `grafana/` |

## SLOs (12 §4)

| SLI | Target | Recording rule / alert |
|---|---|---|
| Availability | 99.5 % (99.9 % business hours in filing season) | `fs:api_availability:ratio30d`, `ErrorBudgetBurn` |
| API latency | p95 < 300 ms | `fs:api_request_duration_seconds:p95_5m`, `ApiLatencyP95High` |
| Check run | p95 < 60 s | `fs:run_duration_seconds:p95_1h{kind="check"}`, `CheckRunP95High` |
| Render | p95 < 90 s | `fs:run_duration_seconds:p95_1h{kind="render"}`, `RenderP95High` |

Error budgets drive release pace: while `ErrorBudgetBurn` is firing, only fixes and emergency
changes ship (E-ITGC-01).

## Metric contract

The API (`fsweb`) exposes on `/metrics` (internal network only; Caddy returns 404 for `/metrics`):

| Metric | Type | Labels |
|---|---|---|
| `fsweb_http_requests_total` | counter | `method`, `route` (template, not raw path), `status` |
| `fsweb_http_request_seconds` | histogram | `route` |
| `fsweb_job_queue_depth` | gauge | `queue` |
| `fsweb_run_duration_seconds` | histogram | `kind` = check, fix, render, ai_review, prepare, diff |
| `fsweb_render_worker_last_heartbeat_timestamp_seconds` | gauge | `worker_id` |

The backup and audit scripts (`ops/backup/*.sh`) write textfile metrics:
`fs_backup_last_run_status{kind}`, `fs_backup_last_success_timestamp_seconds{kind}`,
`fs_backup_pgbackrest_last_backup_timestamp_seconds{type}`, `fs_backup_wal_last_archived_timestamp_seconds`,
`fs_backup_wal_pending_segments`, `fs_backup_snapshot_last_timestamp_seconds{target}`,
`fs_restore_drill_last_success_timestamp_seconds`, `fs_audit_chain_verify_status`,
`fs_audit_chain_last_verify_timestamp_seconds`.

Labels never contain client names, file names or figures — route templates and IDs only.

## Alerts → runbooks

Every alert in `alerts.yml` has a `runbook` annotation pointing at `ops/runbooks/alerts/<AlertName>.md`
(enforced by `ops/tests/test_ops_configs.py`) and a `runbook_url` built from the
`runbook_base` external label, so the on-call person (even a rota of one, E-OPS-04) always has a
documented first response.
