# fsoffline — FS-Platform offline edition (Mode C)

For laptops at client sites without connectivity (04 §2, mode C). It is the **same**
web UI and engine as the firm server, packaged for one PC:

| Mode A (firm server) | Mode C (this package) |
|---|---|
| PostgreSQL 16 with RLS | SQLite file in the user's data directory |
| MinIO / S3 object store | local object directory |
| separate job workers | worker thread inside the launcher |
| Caddy + TLS on the firm domain | `http://127.0.0.1:<port>` — **loopback only** |
| AI gateway optional | AI disabled (offline by default, 03 §3.7) |

The launcher refuses to bind anything except `127.0.0.1` / `::1`; a laptop must never
serve client data to its network. Remote work is what Mode A is for.

## Use

```
fsoffline init --tenant "Example Firm" --admin-email you@example-firm.in   # first run only
fsoffline                      # start; opens the browser at http://127.0.0.1:<free port>/
fsoffline run --port 8765 --no-browser
fsoffline where                # data directory
fsoffline env                  # FSWEB_* environment, secrets redacted
```

Data directory (override with `--data-dir` or `FSOFFLINE_DATA_DIR`):
Windows `%LOCALAPPDATA%\FSPlatform\offline`, macOS
`~/Library/Application Support/FSPlatform/offline`, Linux
`$XDG_DATA_HOME/fsplatform/offline`. It holds the SQLite database, object store, work
directory and a `secrets/` folder (session key, KMS master key, worker token,
package-signing key) created on first start with owner-only permissions. Use full-disk
encryption (BitLocker / FileVault) on every laptop that runs Mode C.

Only one instance may run per data directory (a lock file prevents two processes from
sharing the SQLite database; a stale lock from a crash is cleared automatically).

## Synchronising with the firm server — packages only

Mode C never synchronises databases. Work moves as **signed, encrypted engagement
packages** (`.fspkg`, 07 §4, E-DATA-05):

1. **Pair once.** On the laptop run `fsoffline pubkey` and give the key to the Firm Admin,
   who adds it to the server's trusted package keys (`FSWEB_PKG_TRUSTED_KEYS`). Import the
   server's public key on the laptop with `fsoffline trust <base64-key>`.
2. **Take an engagement out.** On the server: *Engagement → Export* (or
   `python -m fsweb.manage export-engagement --id ID --out E.fspkg`). Copy the file to the
   laptop (encrypted USB or the firm's file share — never personal e-mail or cloud drives)
   and run `fsoffline import --file E.fspkg`.
3. **Work offline.** Uploads, checks, fix plans and approvals are recorded in the local
   hash-chained audit trail like on the server.
4. **Bring it back.** `fsoffline export --id ID --out E-back.fspkg`, copy it to the office
   and import it on the server. The server verifies the Ed25519 signature, the audit hash
   chain and the **base version**: if the engagement changed on the server in the meantime,
   the package is imported as a **branch** that a Manager must merge — never a silent
   overwrite. A tampered package is rejected.
5. Delete the local copy when the engagement is back on the server and the import is
   confirmed (data minimisation; the server is the system of record).

## Build (PyInstaller)

```
cd fsui && npm ci && npm run build && cd ..
pip install -e fscore -e fsweb -e fsoffline pyinstaller
pyinstaller fsoffline/fsoffline.spec --noconfirm
```

The spec bundles `fsui/dist`, the engine's package data and the fsweb Alembic scripts.
Sign the executable with the firm's code-signing certificate and record the release in the
ITGC change log (E-ITGC-01). Distribute through the firm's device-management tool only.

## Tests

```
.venv/bin/python -m pytest fsoffline/tests -q
```

They cover loopback-only binding, per-OS data directories, secret creation and reuse
(mode 0600), the environment handed to fsweb, free-port selection, the single-instance
lock, in-process `fsweb.manage` invocation and the worker entry-point resolution — without
needing fsweb installed.
