"""``python -m fsoffline [run|init|export|import|pubkey|trust|env|where]``."""

from __future__ import annotations

import argparse
import logging
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
    command = args.command or "run"
    if command == "run" and not hasattr(args, "host"):
        args.host, args.port, args.no_browser = "127.0.0.1", 0, False
    try:
        settings = _settings(args)
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
    except (OfflineConfigError, ValueError, launcher.InstanceLockError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
