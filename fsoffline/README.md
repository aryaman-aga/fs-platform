# fsoffline — FS-Platform on one PC (desktop edition, Mode C)

The **same** web UI and engine as the firm server, packaged for one PC: a client office, or a laptop at a client
site without connectivity (04 §2, mode C).

| Mode A (firm server) | Mode C (this package) |
|---|---|
| PostgreSQL 16 with RLS | SQLite file in the data folder (upgraded in place by Alembic) |
| MinIO / S3 object store | local object folder |
| separate job workers | worker threads inside the app |
| Caddy + TLS on the firm domain | `http://127.0.0.1:8746` — **loopback only** |
| AI gateway optional | AI off until an administrator turns it on |

The app refuses to bind anything except `127.0.0.1` / `::1`: a PC must never serve client data to its network.
Remote work is what Mode A is for.

## Windows installer

`FS-Platform-Setup-<version>.exe` installs everything — the Python runtime and libraries, the web UI, the
Visual C++ runtime and LibreOffice — so the PC needs nothing else and no internet. The client-facing guide is
[docs/user-guide/windows-install.md](../docs/user-guide/windows-install.md). In short:

- program files in `C:\Program Files\FS-Platform`, data in `C:\ProgramData\FSPlatform` (database, files, keys, logs,
  backups), readable only by SYSTEM, Administrators and the **FS-Platform Users** group;
- a tray icon (Open · Back up now · Open data folder · Quit); one server per PC, shared by every Windows account
  that may use it; the first start opens the setup page (firm + first administrator, authenticator by QR code);
- PDFs through Microsoft Excel when installed, otherwise the bundled LibreOffice; drafts can also use the built-in
  renderer;
- daily backups (keep 14, files mirrored incrementally), optionally copied to a second folder set on
  Admin → Backups; **FS-Platform Recovery** (Start menu, administrator) resets an administrator's password or
  authenticator, allows a Windows account, restores a backup and saves diagnostics.

### Build the installer

```
make windows-installer          # = python fsoffline/packaging/build_windows.py
```

Runs on macOS, Linux or Windows with CPython 3.13, Node.js 22 and `makensis` (NSIS). Nothing is frozen: the bundle
is the official embeddable CPython plus the win_amd64 wheels pinned (with hashes) in
`packaging/requirements-windows.lock`, FS-Platform's own wheels and the built UI; `packaging/installer.nsi` is the
NSIS script. After changing dependencies run `make windows-lock`. The `windows` CI workflow builds the installer on
macOS, installs it on a clean Windows runner and runs `packaging/smoke_test.py` end to end (setup, a second user,
upload, checks, fix plan approved by the other user, apply and verify, LibreOffice PDF, backup, upgrade, uninstall).
Sign the installer with the firm's code-signing certificate before distributing it, and record the release in the
ITGC change log (E-ITGC-01).

## Commands

```
fsoffline                       # installed: the desktop app (tray); source checkout: the console server (`run`)
fsoffline desktop [--no-tray]   # the installed app
fsoffline serve --no-browser    # the same, no tray (CI, troubleshooting)
fsoffline status | stop         # is a server running (any Windows account) / ask it to stop
fsoffline backup                # back up now
fsoffline restore --backup DIR  # put a backup back (stops the server first)
fsoffline recover               # FS-Platform Recovery (interactive; or reset-password / reset-mfa / allow-user / diagnostics)
fsoffline where | env           # data folder / FSWEB_* environment with secrets redacted
fsoffline run --port 8765       # developer console mode
```

Data folder: `--data-dir` or `FSOFFLINE_DATA_DIR`; an installed copy uses the folder in its `fsplatform.ini`
(`C:\ProgramData\FSPlatform`); a source checkout uses `%LOCALAPPDATA%\FSPlatform\offline` on Windows,
`~/Library/Application Support/FSPlatform/offline` on macOS and `$XDG_DATA_HOME/fsplatform/offline` on Linux. It holds
the SQLite database, object store, work folder, logs, backups and a `secrets/` folder (session key, KMS master key,
worker token, package-signing key) created on first start. Use full-disk encryption (BitLocker / FileVault).

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

## Tests

```
.venv/bin/python -m pytest fsoffline/tests -q
```

They cover loopback-only binding, per-OS data directories, secret creation and reuse
(mode 0600), the environment handed to fsweb, free-port selection, the single-instance
lock, in-process `fsweb.manage` invocation and the worker entry-point resolution — without
needing fsweb installed.
