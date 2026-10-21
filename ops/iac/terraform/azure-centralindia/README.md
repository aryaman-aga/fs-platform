# Terraform — Azure Central India (skeleton, E-ARCH-04)

Provisions the cloud variant of the single-node layout (04 §8 option 2) or the DR target (E-BCP-03):
app VM (Caddy, API, workers), data VM (PostgreSQL, MinIO, 1 TB data disk), Windows render VM, three
private subnets with NSGs (no public IPs; ingress through Cloudflare Tunnel), a ZRS backup storage
account with versioning + immutability for the off-site restic repository, a premium Key Vault
(HSM-backed KEK) and a Log Analytics workspace with ≥ 180-day retention (CERT-In).

```
terraform init -backend-config=backend.hcl        # state in an India-region storage account
terraform plan -var-file=prod.tfvars -out plan.out
terraform apply plan.out
```

| Variable | Meaning |
|---|---|
| `prefix` | resource name prefix |
| `location` | India regions only (validated) |
| `admin_ssh_public_key`, `admin_cidrs` | administrator access from office/VPN networks only |
| `app_vm_size`, `data_vm_size`, `render_vm_size` | sizing per 04 §9 / 12 §4 |
| `windows_admin_password` | initial render VM password (sensitive; rotate after domain join) |
| `backup_immutability_days` | object-lock period of the off-site backups (default 30) |
| `log_retention_days` | ≥ 180 (validated) |

After `apply`: run the Ansible playbook (`ops/iac/ansible`) for the Linux hosts, follow
`ops/runbooks/restore-render-worker.md` for the Windows host, then **lock** the storage immutability
policy once the first backup is verified. Fresh environment from code in under 2 hours (E-ARCH-04).
Run `terraform validate` and `tflint` in CI before any change (E-ITGC-01).
