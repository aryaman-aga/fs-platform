"""``python -m fsoffline [desktop|serve|run|stop|status|backup|restore|recover|init|export|import|pubkey|trust|env|where]``.

``desktop`` is the installed app (tray icon, no console); ``serve`` is the same without the tray; ``run`` is the
developer console mode."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from fsoffline.settings import (
    OfflineConfigError,
    OfflineSettings,
    build_env,
    default_data_dir,
    ensure_layout,
    package_public_key,
    redacted,
)


def _settings(args: argparse.Namespace) -> OfflineSettings:
    return OfflineSettings(
        data_dir=Path(args.data_dir) if args.data_dir else default_data_dir(),
        host=getattr(args, "host", "127.0.0.1"),
        port=getattr(args, "port", 0) or 0,
        open_browser=not getattr(args, "no_browser", False),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fsoffline", description="FS-Platform offline edition (Mode C)")
    parser.add_argument("--data-dir", help="data directory (default: per-user application data)")
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", help="start the offline edition (default)")
    run.add_argument("--host", default="127.0.0.1", help="loopback address only: 127.0.0.1 or ::1")
    run.add_argument("--port", type=int, default=0, help="port (default: a free port)")
    run.add_argument("--no-browser", action="store_true", help="do not open the browser")

    init = sub.add_parser("init", help="first run: create the local tenant and administrator")
    init.add_argument("--tenant", required=True)
    init.add_argument("--admin-email", required=True)

    export = sub.add_parser("export", help="export an engagement as a signed, encrypted .fspkg")
    export.add_argument("--id", required=True, help="engagement id")
    export.add_argument("--out", required=True, help="output .fspkg path")

    imp = sub.add_parser("import", help="import a .fspkg from the firm server")
    imp.add_argument("--file", required=True)
    imp.add_argument("--quarantine", action="store_true", help="import into a quarantine engagement")

    desktop = sub.add_parser("desktop", help="the installed app: server on 127.0.0.1 with a tray icon (default when installed)")
    desktop.add_argument("--no-browser", action="store_true", help="do not open the browser")
    desktop.add_argument("--no-tray", action="store_true", help="no tray icon")
    serve = sub.add_parser("serve", help="the installed app without a tray icon (CI, troubleshooting)")
    serve.add_argument("--no-browser", action="store_true", help="do not open the browser")
    serve.add_argument("--port", type=int, default=0, help="preferred port (default: the installed port, 8746)")
    sub.add_parser("stop", help="stop a running server (also one started by another Windows account)")
    sub.add_parser("status", help="print whether a server is running, and where")
    sub.add_parser("backup", help="back up now (database, keys, files)")
    restore = sub.add_parser("restore", help="restore a backup (stops the server first)")
    restore.add_argument("--backup", required=True, help="a dated folder in <data>\\backups")
    recover = sub.add_parser("recover", help="FS-Platform Recovery (interactive, or one action)")
    recover_sub = recover.add_subparsers(dest="action")
    rp = recover_sub.add_parser("reset-password", help="one-time link to set a new password")
    rp.add_argument("--email", required=True)
    rm = recover_sub.add_parser("reset-mfa", help="remove an account's authenticator (it enrols again)")
    rm.add_argument("--email", required=True)
    au = recover_sub.add_parser("allow-user", help="allow another Windows account to use FS-Platform")
    au.add_argument("--account", required=True)
    dg = recover_sub.add_parser("diagnostics", help="save a support zip (logs, versions; no client data)")
    dg.add_argument("--out", required=True)

    sub.add_parser("pubkey", help="print this installation's package-signing public key")
    trust = sub.add_parser("trust", help="trust a server's package-signing public key")
    trust.add_argument("public_key", help="base64 Ed25519 public key from the firm server")
    sub.add_parser("env", help="print the FSWEB_* environment (secrets redacted)")
    sub.add_parser("where", help="print the data directory")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    from fsoffline import launcher

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    args = build_parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    from fsoffline.install import load_install_config

    command = args.command or ("desktop" if load_install_config().installed else "run")
    if command in ("run", "desktop") and not hasattr(args, "host"):
        args.host, args.port = "127.0.0.1", getattr(args, "port", 0)
        args.no_browser = getattr(args, "no_browser", False)
    try:
        settings = _settings(args)
        if command in ("desktop", "serve", "stop", "status", "backup", "restore", "recover"):
            return _desktop_command(command, args, settings)
        if command == "where":
            print(settings.data_dir)
            return 0
        if command == "env":
            ensure_layout(settings)
            for key, value in sorted(redacted(build_env(settings, settings.port or 0)).items()):
                print(f"{key}={value}")
            return 0
        if command == "pubkey":
            ensure_layout(settings)
            print(package_public_key(settings))
            return 0
        if command == "trust":
            import base64

            raw = base64.b64decode(args.public_key, validate=True)
            if len(raw) != 32:
                raise OfflineConfigError("not an Ed25519 public key (expected 32 bytes)")
            ensure_layout(settings)
            with settings.trusted_keys_path.open("a", encoding="ascii") as handle:
                handle.write(args.public_key.strip() + "\n")
            print("trusted; packages signed with this key can now be imported")
            return 0

        if command == "init":
            return launcher.bootstrap(settings, args.tenant, args.admin_email)
        if command == "export":
            return launcher.manage_passthrough(settings, ["export-engagement", "--id", args.id, "--out", args.out])
        if command == "import":
            extra = ["--quarantine"] if args.quarantine else []
            return launcher.manage_passthrough(settings, ["import-engagement", "--file", args.file, *extra])
        return launcher.run(settings)
    except (OfflineConfigError, ValueError, launcher.InstanceLockError, RuntimeError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def _desktop_command(command: str, args: argparse.Namespace, settings: OfflineSettings) -> int:
    from fsoffline import backup, desktop, recovery

    if command == "desktop":
        return desktop.run_desktop(settings, tray=not args.no_tray, open_browser=not args.no_browser)
    if command == "serve":
        return desktop.run_desktop(settings, tray=False, open_browser=not args.no_browser)
    if command == "stop":
        stopped = desktop.request_stop(settings)
        print("stopped" if stopped else "FS-Platform did not stop in time")
        return 0 if stopped else 1
    if command == "status":
        rt = desktop.read_runtime(settings)
        print(f"running at {rt['url']} (pid {rt['pid']}, version {rt['version']})" if rt else "not running")
        return 0
    if command == "backup":
        ensure_layout(settings)
        result = backup.run_backup(settings, reason="manual")
        print(f"backup {'saved' if result.ok else 'FAILED'}: {result.path}" + (f" ({result.error})" if result.error else ""))
        if result.copy_error:
            print(result.copy_error)
        return 0 if result.ok and not result.copy_error else 1
    if command == "restore":
        aside = recovery.restore(settings, Path(args.backup))
        print(f"restored; the data it replaced is kept in {aside}")
        return 0
    # recover
    action = getattr(args, "action", None)
    if action is None:
        if os.name == "nt" and not recovery.is_admin():  # pragma: no cover - Windows
            return 0 if recovery.relaunch_elevated(["recover"]) else 1
        return recovery.interactive(settings)
    if action == "reset-password":
        link, expires = recovery.reset_password(settings, args.email)
        print(f"{link}\nvalid until {expires.isoformat()}")
    elif action == "reset-mfa":
        recovery.reset_mfa(settings, args.email)
        print("authenticator removed; it is enrolled again at the next sign-in")
    elif action == "allow-user":
        recovery.allow_windows_account(args.account)
        print(f"{args.account} may use FS-Platform after signing in to Windows again")
    elif action == "diagnostics":
        print(recovery.diagnostics(settings, Path(args.out)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
