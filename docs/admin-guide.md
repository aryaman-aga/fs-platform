# Administrator guide

## 1. Single-node installation (Mode A, ADR-0002)

**Host:** Ubuntu 24.04 LTS, 8 vCPU, 16 GB RAM, 1 TB SSD (RAID-1), UPS, on its own VLAN; clock synchronised to
the NIC / NPL time servers (`ops/ntp/chrony.conf`). Use `ops/iac/ansible/site.yml` or the steps below.

```
sudo apt install -y docker.io docker-compose-v2 git chrony      # or Docker's own packages (ansible role "docker")
sudo git clone <release repository> /opt/fs-platform && cd /opt/fs-platform
sudo git checkout v1.0.0                                          # tagged releases only (E-ITGC-01)
sudo cp ops/.env.example ops/.env && sudo chmod 600 ops/.env     # fill every CHANGE_ME (openssl rand -base64 …)
sudo docker compose --env-file ops/.env -f ops/docker-compose.yml --profile monitoring up -d --build
sudo docker compose --env-file ops/.env -f ops/docker-compose.yml exec api \
     python -m fsweb.manage bootstrap --tenant "Example Firm" --admin-email admin@example-firm.in
```

`bootstrap` prints a one-time password and a TOTP secret for the first Firm Admin; enrol the authenticator
immediately and store nothing else. Then configure **Cloudflare Tunnel** (`ops/cloudflared/config.yml.example`,
profile `tunnel`) and stop publishing ports 80/443 to the internet.

Install the backup timers (`ops/backup/README.md`) and take the first full backup before any user logs in.

## 2. HA profile (E-ARCH-01)

k3s (3 server nodes) + `ops/helm/fs-platform`; PostgreSQL HA (CloudNativePG or Patroni), MinIO distributed or
India-region S3, Redis Sentinel — see `ops/helm/fs-platform/README.md`. Run the node-kill and failover drills and
record them before declaring HA.

## 3. Windows render worker

`fsrender-win/README.md` and `ops/runbooks/restore-render-worker.md`: licensed Excel with shared-computer activation
(or Office LTSC), dedicated service account, pinned Excel build, token file readable by the service account only.

## 4. Offline installation (no internet on the server)

Build a **wheelhouse** on a connected build machine and carry it over:

```
pip download -r requirements.lock -d wheelhouse --require-hashes      # lock generated with pip-compile --generate-hashes
pip install --no-index --find-links wheelhouse --require-hashes -r requirements.lock
```

Container images: `docker save` on the build machine, `docker load` on the server, verify digests and cosign
signatures. The UI is built into the edge image; no npm access is needed on the server.

## 5. Secrets and keyring

- Server secrets live in `ops/.env` (0600) or Vault / cloud KMS; never in Git or the database.
- The CLI (`fscheck review-ai`) reads AI credentials from the OS keyring:
  `python -m keyring set fs-platform anthropic` (Windows Credential Manager / macOS Keychain).
- Backup keys and the KEK are split 2-of-3 among partners (`docs/security/key-management.md`).

## 6. AI configuration (ADR-0008)

Disabled by default. To enable after partner approval of `docs/ai-policy.md`: set `FSWEB_AI_ENABLED=true`, the
provider credentials **for the ai-gateway service only**, the provider and the **mandatory region** in *Admin → AI
settings*, budgets (E-AI-04), and the per-engagement switch. Only the ai-gateway has egress.

## 7. Backups and restores

See `ops/backup/README.md` (schedule, retention, 3-2-1-1-0), `ops/runbooks/restore-engagement.md` and
`ops/runbooks/restore-full.md`. The monthly restore drill writes evidence to `/var/lib/fs-platform/evidence`.

## 8. Upgrades

1. Release ticket approved (ops/itgc/change-management.md); read the release notes.
2. **Pre-migration backup:** `ops/backup/pgbackrest-backup.sh incr` (it must succeed).
3. `git fetch --tags && git checkout vX.Y.Z`, rebuild: `docker compose … build && docker compose … up -d`
   (the `migrate` one-shot service runs Alembic with the owner role before the API starts).
4. Verify: `/api/v1/ready`, `python -m fsweb.manage verify-audit-chain`, smoke test; record the release.
5. Rollback: previous tag + images; restore the pre-migration backup only if a migration is not backward-compatible.

## 9. Routine administration

| Task | Where |
|---|---|
| Users, roles, assignments, invitations | Admin → Users |
| Quarterly access review (C14) | opens automatically each quarter; Admin → Access reviews; `python -m fsweb.manage access-review-report` |
| CERT-In deadline reminders (E-SEC-05) | every 15 minutes (`incident-check` timer / CronJob) |
| DSR requests, incidents, compliance contacts | Admin → Compliance |
| Audit-chain verification | nightly timer; `python -m fsweb.manage verify-audit-chain` |
| Quarterly evidence pack | `python -m fsweb.manage evidence-pack --quarter 2026Q4 --out /var/lib/fs-platform/evidence/2026Q4` |
| Monitoring and alerts | `https://<site>/grafana/` (office networks), runbooks in `ops/runbooks/alerts/` |
| Monthly operations report | `ops/itgc/monthly-ops-report-template.md` |

## 10. Mode C laptops

Distribute the signed offline build through device management; require full-disk encryption; register each
laptop's package-signing key on the server (`fsoffline pubkey`) and trust the server's key on the laptop
(`fsoffline trust <key>`). See `fsoffline/README.md`.
