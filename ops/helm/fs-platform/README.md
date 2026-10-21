# fs-platform Helm chart (HA profile, ADR-0002, 12 §3)

Stateless application tier for k3s / Kubernetes: API (2–6 replicas, HPA, PDB, zero-downtime rolling
updates), job workers, the optional AI egress gateway, migration hook and the audit-chain / restore-drill
CronJobs. Default-deny NetworkPolicies allow only DNS, the data namespace and — for the AI gateway alone —
HTTPS to approved provider ranges.

## Stateful dependencies (install first, in namespace `fs-platform-data`)

| Need (12 §3) | Suggested | Notes |
|---|---|---|
| PostgreSQL 16 primary + synchronous standby, automatic failover | CloudNativePG or Zalando postgres-operator (Patroni) | create roles `fsweb_owner` / `fsweb_app` as in `ops/postgres/initdb/01-roles.sh`; enable WAL archiving to the backup bucket (barman-cloud / pgBackRest) |
| Object storage with versioning + object lock | MinIO operator (4+ drives on 2+ nodes) or S3 in `ap-south-1` | buckets as in `ops/minio/minio-init.sh` |
| Redis with Sentinel (3) | bitnami/redis `sentinel.enabled=true` | rate limits and short-lived locks |
| Windows render pool (2+) | `fsrender-win` VMs outside the cluster | reach the API through the ingress `/api/v1/internal/*` (allow-listed) |

## Install

```
kubectl create namespace fs-platform
kubectl -n fs-platform create secret generic fs-platform-secrets --from-env-file=secrets.env   # or external-secrets / Vault
helm upgrade --install fs ops/helm/fs-platform -n fs-platform -f my-values.yaml
```

Pin images by digest in `my-values.yaml` (`image.api.digest`, `image.worker.digest`). The SPA is served by
the edge image (`ops/docker/ui.Dockerfile`) or any static host in front of the ingress.

## Acceptance (E-ARCH-01/02)

- `kubectl drain` one node during a k6/Locust run: no failed requests (maxUnavailable 0, PDB, preStop drain).
- Kill the PostgreSQL primary: failover < 30 s, no committed transaction lost (synchronous standby).
- Record both drills in the operations report (E-ITGC-04).

`helm lint` / `helm template` must pass in CI before any chart change is released (E-ITGC-01).
