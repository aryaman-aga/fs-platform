# Key-recovery drill (E-BCP-02, part of the restore drills, control C13)

**Purpose:** prove that the backup and KMS keys can be rebuilt from any 2 of the 3 partner shares,
without anyone ever holding the whole key alone, and that the shares are intact.
**Frequency:** twice a year, and whenever a key holder joins or leaves.

## Keys in escrow

| Secret | Used by | Split |
|---|---|---|
| restic NAS repository password | ops/backup | 2-of-3 |
| restic off-site repository password | ops/backup | 2-of-3 |
| pgBackRest cipher pass | PostgreSQL backups | 2-of-3 |
| `FSWEB_KMS_MASTER_KEY` (KEK) | field-level encryption | 2-of-3 |
| `.fspkg` signing seed | engagement packages | 2-of-3 |

## Drill (≈ 30 minutes, two partners + IT)

1. In a room without cameras, on the offline drill laptop (no network), each of two partners enters or
   inserts their share file. Use a *different pair* than in the previous drill.
2. Verify without revealing the secret:
   ```
   tools/shamir.py verify share-A.txt share-B.txt
   ```
   "OK" means the shares reconstruct a secret whose embedded SHA-256 digest matches.
3. Functional check (still offline): reconstruct to a RAM disk and open a **canary** — e.g. `restic cat config`
   against a copy of the repository on the drill disk, or decrypt a canary file encrypted with the KMS key.
   ```
   tools/shamir.py combine share-A.txt share-B.txt --out /Volumes/RAMDisk/secret.bin
   ```
   Wipe the RAM disk immediately afterwards.
4. Record in the evidence pack: date, participants (names only), shares used (indices), result, any share
   re-issued. Never record the secret or the share text.

## Re-issuing shares (a holder leaves, or a share is lost or suspected compromised)

Reconstruct with two remaining holders, then **split again** (`tools/shamir.py split --k 2 --n 3`): a new
split produces a new set ID, so old shares can no longer be combined with new ones. For a suspected
compromise, rotate the underlying key (docs/security/key-management.md) rather than only re-splitting.
