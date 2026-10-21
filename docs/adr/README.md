# Architecture Decision Records

Decisions taken in phase 0 for the autonomous build (the user asked for the whole platform to be built
without stopping at the ★ reviews), recorded with the recommended answers from 11 §2 and 12 §7.
Items marked *confirm with the firm* in `docs/phase0-decisions.md` stay open for the partners.

| ADR | Decision | Status |
|---|---|---|
| [ADR-0001](0001-record-architecture-decisions.md) | Record architecture decisions | Accepted |
| [ADR-0002](0002-hosting-single-node-compose-cloudflare-tunnel.md) | Hosting: single-node Docker Compose on an office server, Cloudflare Tunnel, HA profile ready | Accepted |
| [ADR-0003](0003-identity-local-totp-and-oidc-sso.md) | Identity: local accounts with mandatory TOTP, OIDC SSO for firm users | Accepted |
| [ADR-0004](0004-render-engines-excel-libreoffice-native.md) | PDF render engines: Windows Excel pool, LibreOffice fallback, native renderer for drafts | Accepted |
| [ADR-0005](0005-job-queue-postgres-outbox.md) | Job queue: PostgreSQL transactional outbox with SKIP LOCKED; Redis optional | Accepted |
| [ADR-0006](0006-ooxml-surgical-patching.md) | Workbook writes by surgical OOXML patching with strict XML validation | Accepted |
| [ADR-0007](0007-object-storage-minio-s3.md) | Object storage: MinIO / S3 with versioning and object lock; local adapter for Mode C | Accepted |
| [ADR-0008](0008-ai-provider-gateway.md) | AI reviewer: off by default; Anthropic API or Amazon Bedrock (ap-south-1); approval token bound to the payload hash | Accepted |
| [ADR-0009](0009-retention-seven-years-legal-hold.md) | Retention: 7 years from the report date, configurable, with legal hold | Accepted |
| [ADR-0010](0010-house-style-pack-v1.md) | House style: built-in Big Four-style template pack v1 | Accepted |
| [ADR-0011](0011-tb-formats-netsuite-generic-first.md) | Trial-balance formats: NetSuite layout and generic CSV/XLSX first | Accepted |
| [ADR-0012](0012-backups-pgbackrest-restic-shamir.md) | Backups: pgBackRest + restic to NAS and India-region object storage with object lock; Shamir 2-of-3 key escrow | Accepted |
| [ADR-0013](0013-billing-per-client-month.md) | Billing: usage per client per month; invoicing outside the app | Accepted |
| [ADR-0014](0014-assurance-iso27001-aligned.md) | Assurance target: ISO 27001-aligned controls with SOC 2-ready evidence | Accepted |
| [ADR-0015](0015-frameworks-year-one.md) | Frameworks in year 1: Ind AS Division II standalone and SPFS; consolidation and other frameworks T2 | Accepted |
| [ADR-0016](0016-signing-dsc-udin.md) | Signing: partner DSC tokens outside the platform; UDIN captured | Accepted |
| [ADR-0017](0017-disaster-recovery-year-one.md) | Disaster recovery: off-site backups and documented rebuild in year 1; warm DR site in T2 | Accepted |
| [ADR-0018](0018-accounting-presentation-choices.md) | Accounting-presentation choices log | Accepted |
| [ADR-0019](0019-runtimes.md) | Runtimes: Python 3.11+ (tested on 3.13), Node LTS, React + TypeScript + Vite | Accepted |
| [ADR-0020](0020-egress-only-via-ai-gateway.md) | Network egress only through the AI gateway | Accepted |
| [ADR-0021](0021-public-repo-hygiene.md) | Repository hygiene: synthetic fixtures only; build pack and real workbooks never committed | Accepted |
