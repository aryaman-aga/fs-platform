# Key management (E-SEC-07, E-BCP-02)

## Key inventory

| Key | Protects | Where it lives | Rotation | Escrow |
|---|---|---|---|---|
| KEK `FSWEB_KMS_MASTER_KEY` (AES-256) | wraps the data-encryption keys used for field-level encryption (PAN, DIN, CIN, client contacts) | single node: `ops/.env` (0600, root) or Vault; cloud: Key Vault / KMS in an India region (HSM-backed) | yearly, or on suspicion | Shamir 2-of-3 |
| DEKs (per tenant / record type) | encrypted columns | database, wrapped by the KEK with a key ID | re-wrapped on KEK rotation | via KEK |
| `FSWEB_SECRET_KEY` | session and CSRF signing | secret store | yearly; rotation signs everyone out | not needed (regenerate) |
| pgBackRest cipher pass | database backups | secret store / postgres container env | never casually — old backups need the old pass; new repo on rotation | Shamir 2-of-3 |
| restic NAS / off-site passwords | object, config and DB-repository backups | backup container secret files | add a new key with `restic key add`, remove the old after verification | Shamir 2-of-3 |
| `FSWEB_PKG_SIGNING_KEY` (Ed25519) | engagement packages (.fspkg) | secret store; laptops have their own keys | yearly; publish the new public key to trusted lists first | Shamir 2-of-3 (server key) |
| `FSWEB_WORKER_TOKEN` | render-worker API | secret store + token file on each render host | yearly or when a host is rebuilt | not needed |
| MinIO SSE key | object store at rest | MinIO KMS secret / external KMS | per MinIO procedure | Shamir 2-of-3 |
| AI provider credentials | AI gateway only | secret store / OS keyring for the CLI | per provider policy | not needed |
| TLS certificates | edge | Caddy (ACME) or Cloudflare origin certificate | automatic | not needed |

## Envelope encryption

`ciphertext = AES-256-GCM(DEK, plaintext, aad = table.column.row-id)`; the DEK is stored wrapped:
`AES-256-GCM(KEK_version_n, DEK)` with the KEK ID. Decryption picks the KEK by ID, so several KEK versions
can coexist during rotation (`FSWEB_KMS_MASTER_KEY="k1:<old>,k2:<new>"`, last = active).

### KEK rotation without downtime (test: E-SEC-07 acceptance)

1. Generate the new KEK (`openssl rand -base64 32`) in the secret store; split it with `tools/shamir.py`.
2. Deploy with both keys (`k1:<old>,k2:<new>`): new writes use k2, reads accept both.
3. Run the re-wrap job (all DEKs re-wrapped with k2); verify a sample of encrypted fields decrypts.
4. Remove k1 from the configuration in the next release; destroy k1 after the backup retention of the
   previous configuration has expired (or keep it escrowed if old backups must stay readable).

## Escrow and recovery (E-BCP-02)

Every backup-related secret and the KEK are split **2-of-3** among three partners with Shamir's Secret
Sharing (`tools/shamir.py`, GF(256), integrity digest so a wrong or tampered share is detected). Shares are
stored separately (sealed envelope, safe deposit, hardware token), never photographed or e-mailed. The
key-recovery drill (`ops/runbooks/key-recovery-drill.md`) proves recovery twice a year without exposing the
secret. When a key holder leaves, reconstruct and **re-split** (new set ID invalidates old shares), and rotate
the key if compromise is possible.

## Rules

- Secrets never in Git, the database, logs, tickets or chat; `ops/.env` is 0600 and git-ignored.
- Production refuses to start with development secrets (settings validation fails closed).
- Separate keys per environment; production keys never in dev or staging (E-ITGC-05).
