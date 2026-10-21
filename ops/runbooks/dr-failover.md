# Disaster recovery and failover (E-BCP-03, 07 §5A)

**Year 1 (ADR-0017):** off-site encrypted backups in an India region + documented rebuild — RTO 4 h,
RPO 15 min. **T2:** warm DR site (second office or India-region cloud) with async-replicated PostgreSQL
and object storage, rebuilt from IaC; failover exercised twice a year.

## Year 1: site loss

1. Incident commander declares DR (incident-response.md) and informs partners and users.
2. Provision compute in the India region from IaC: `ops/iac/terraform/azure-centralindia`
   (`terraform apply -var-file=dr.tfvars`) — two Linux VMs and one Windows VM, the backup storage account
   already exists there.
3. Run restore-full.md against the **off-site** restic repository (object-locked, unaffected by the site
   loss). Reconstruct keys with 2 partners.
4. Point DNS / Cloudflare Tunnel at the new host (tunnel credentials are part of the config backup).
5. Users continue through the browser (Mode A). Laptops with Mode C packages keep working offline and
   re-import when the server is back (packages are verified against the base version; conflicts branch).
6. Measure and record RTO / RPO; return to the primary site with the same procedure in reverse during a
   maintenance window.

## T2: warm site failover

| Step | Action | Check |
|---|---|---|
| 1 | Freeze writes at the primary if it is still reachable (maintenance mode) | no new audit events |
| 2 | Promote the DR PostgreSQL replica (`patronictl failover` or `pg_ctl promote`) | timeline increments |
| 3 | Confirm MinIO site replication caught up (`mc admin replicate status`) | no pending objects |
| 4 | Start API/workers at the DR site (Helm values `site=dr`) | `/api/v1/ready` OK |
| 5 | Switch traffic (Cloudflare Tunnel / DNS) | synthetic login succeeds |
| 6 | Verify the audit chain and compare the head with the last primary head file | heads consistent |
| 7 | Re-register render workers at the DR site | heartbeats fresh |

Exercise twice a year (outside the September–November peak); record achieved RTO/RPO and actions in the
DR exercise report.
