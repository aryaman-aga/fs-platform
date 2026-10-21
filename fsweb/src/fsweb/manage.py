"""Admin CLI: ``python -m fsweb.manage <command>``.

Runs with the owner database role (migrations, nightly jobs, drills). Every
command that changes state writes an audit event.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
import tempfile
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from fsweb import audit
from fsweb.db import SYSTEM, utcnow
from fsweb.models import BackupLog, Client, Engagement, Entity, FindingRow, Signatory, Tenant, User
from fsweb.settings import Settings
from fsweb.state import AppState, build_state

SYSTEM_ACTOR_NAME = "system:cli"


def _state(component: str = "cli") -> AppState:
    return build_state(Settings.from_env(), component=component)


def _tenants(s: Session, tenant: str | None) -> list[Tenant]:
    stmt = select(Tenant)
    if tenant:
        stmt = stmt.where((Tenant.id == tenant) | (Tenant.name == tenant))
    return list(s.execute(stmt).scalars())


def _actor(tenant_id: str) -> audit.Actor:
    return audit.Actor(tenant_id, None, SYSTEM_ACTOR_NAME, None, "fsweb-manage", None)


# ------------------------------------------------------------------ schema


def migrations_dir() -> Path:
    """Alembic scripts ship inside the package (works from a wheel); FSWEB_ALEMBIC_DIR overrides."""
    import os
    from importlib import resources

    override = os.environ.get("FSWEB_ALEMBIC_DIR")
    if override:
        return Path(override)
    return Path(str(resources.files("fsweb").joinpath("migrations")))


def run_migrations(database_url: str, revision: str = "head") -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", str(migrations_dir()))
    cfg.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(cfg, revision)


def cmd_migrate(args: argparse.Namespace) -> int:
    st = _state()
    if st.db.is_postgres:
        run_migrations(st.settings.database_url)
    else:
        st.db.create_all()
    print("database schema is up to date")
    return 0


def cmd_openapi(args: argparse.Namespace) -> int:
    from fsweb.main import create_app

    with tempfile.TemporaryDirectory() as td:
        s = Settings(env="test", database_url=f"sqlite:///{td}/o.db", storage=f"local:{td}/o", work_dir=f"{td}/w", engine="fake")
        app = create_app(s)
        spec = app.openapi()
    out = Path(args.out)
    out.write_text(json.dumps(spec, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    print(f"wrote {out} ({len(spec['paths'])} paths)")
    return 0


# ------------------------------------------------------------------ tenants & users


def cmd_bootstrap(args: argparse.Namespace) -> int:
    from fsweb.services.seed import bootstrap

    st = _state()
    with st.db.owner_session(SYSTEM) as s:
        if s.execute(select(User).where(User.email == args.admin_email.lower())).scalar_one_or_none():
            print("a user with this e-mail already exists", file=sys.stderr)
            return 1
        res = bootstrap(s, st.cipher, args.tenant, args.admin_email, args.admin_name)
        s.commit()
    print(json.dumps({"tenant_id": res.tenant_id, "admin_email": res.admin_email, "one_time_password": res.one_time_password,
                      "totp_secret": res.totp_secret, "note": "change the password at first sign-in; store the TOTP secret in an authenticator"}, indent=2))
    return 0


def cmd_create_breakglass(args: argparse.Namespace) -> int:
    """Sealed break-glass account (E-SEC-03): System Admin role, alert on every use."""
    from fsweb.security import totp
    from fsweb.services.seed import assign, make_user, one_time_password

    st = _state()
    with st.db.owner_session(SYSTEM) as s:
        t = _tenants(s, args.tenant)
        if len(t) != 1:
            print("specify --tenant", file=sys.stderr)
            return 1
        from fsweb.models import Role

        pw, secret = one_time_password(), totp.new_secret()
        u = make_user(s, st.cipher, t[0].id, args.email, "Break-glass (sealed)", password=pw, totp_secret=secret, created_by="cli")
        u.is_break_glass = True
        role = s.execute(select(Role).where(Role.tenant_id == t[0].id, Role.code == "system_admin")).scalar_one()
        assign(s, t[0].id, u, role, None, "cli")
        audit.append(s, _actor(t[0].id), "user.breakglass_created", object_type="user", object_id=u.id)
        s.commit()
    print(json.dumps({"email": args.email, "password": pw, "totp_secret": secret,
                      "note": "print, seal in an envelope, store in the safe; every use raises an alert"}, indent=2))
    return 0


# ------------------------------------------------------------------ audit


def cmd_verify_audit_chain(args: argparse.Namespace) -> int:
    st = _state()
    ok = True
    with st.db.owner_session(SYSTEM) as s:
        for t in _tenants(s, args.tenant):
            rep = audit.verify_chain(s, t.id)
            print(json.dumps(rep.as_dict()))
            ok = ok and rep.ok
    return 0 if ok else 1


def cmd_audit_head(args: argparse.Namespace) -> int:
    """Nightly: verify the chain, write the head hash to a write-once file and e-mail the Firm Admins."""
    from fsweb.services.common import notify, users_with_role

    st = _state()
    rc = 0
    with st.db.owner_session(SYSTEM) as s:
        for t in _tenants(s, args.tenant):
            rep = audit.verify_chain(s, t.id)
            try:
                path = audit.write_head_file(args.out_dir or st.settings.audit_head_dir, rep, args.day)
            except FileExistsError:
                print(f"{t.id}: head file for the day already written (write-once)", file=sys.stderr)
                rc = 1
                continue
            for admin in users_with_role(s, t.id, "firm_admin"):
                notify(s, t.id, admin.email, f"Audit-chain head {rep.head_seq}",
                       f"Chain verified: {rep.ok}. Head seq {rep.head_seq}, hash {rep.head_hash}.", "audit_head")
            audit.append(s, _actor(t.id), "audit.head_written", details={"seq": rep.head_seq, "hash": rep.head_hash, "ok": rep.ok, "file": str(path)})
            s.commit()
            print(f"{t.id}: {path}")
            rc = rc or (0 if rep.ok else 1)
    return rc


# ------------------------------------------------------------------ packages & drills


def cmd_export(args: argparse.Namespace) -> int:
    from fsweb.services import fspkg

    st = _state()
    with st.db.owner_session(SYSTEM) as s:
        e = s.get(Engagement, args.id)
        if e is None:
            print("engagement not found", file=sys.stderr)
            return 1
        data = fspkg.export_engagement(s, st, e)
        audit.append(s, _actor(e.tenant_id), "engagement.exported", client_id=e.client_id, engagement_id=e.id, after=hashlib.sha256(data).hexdigest())
        s.commit()
    Path(args.out).write_bytes(data)
    print(f"wrote {args.out} ({len(data)} bytes)")
    return 0


def cmd_import(args: argparse.Namespace) -> int:
    from fsweb.services import fspkg

    st = _state()
    data = Path(args.file).read_bytes()
    with st.db.owner_session(SYSTEM) as s:
        try:
            pkg = fspkg.read_package(data, st.settings)
            e = fspkg.import_engagement(s, st, data, _actor(pkg.manifest["tenant_id"]), tenant_id=pkg.manifest["tenant_id"])
        except fspkg.PackageError as exc:
            print(f"rejected: {exc}", file=sys.stderr)
            return 1
        s.commit()
        print(json.dumps({"engagement_id": e.id, "quarantine": e.is_quarantine, "merge_required": e.merge_required}))
    return 0


def restore_drill(st: AppState, *, tenant: str | None = None, engagement_id: str | None = None, seed: int | None = None) -> dict[str, Any]:
    """C13 monthly drill: export a random engagement, import into quarantine, compare hashes, re-cast."""
    from fsweb.services import fspkg, workflow
    from fsweb.services.engagements import config_for

    rng = random.Random(seed)
    report: dict[str, Any] = {"started": utcnow().isoformat(), "ok": False, "steps": []}
    with st.db.owner_session(SYSTEM) as s:
        stmt = select(Engagement).where(Engagement.is_quarantine.is_(False))
        if engagement_id:
            stmt = stmt.where(Engagement.id == engagement_id)
        if tenant:
            stmt = stmt.where(Engagement.tenant_id == tenant)
        candidates = list(s.execute(stmt).scalars())
        if not candidates:
            report["steps"].append({"step": "select", "ok": False, "detail": "no engagement to drill"})
            return report
        src = rng.choice(candidates)
        report["source_engagement_id"] = src.id
        data = fspkg.export_engagement(s, st, src)
        report["steps"].append({"step": "export", "ok": True, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
        try:
            restored = fspkg.import_engagement(s, st, data, _actor(src.tenant_id), tenant_id=src.tenant_id)
            s.flush()
            report["steps"].append({"step": "import_quarantine", "ok": True, "engagement_id": restored.id})
        except fspkg.PackageError as exc:
            report["steps"].append({"step": "import_quarantine", "ok": False, "detail": str(exc)})
            restored = None
        if restored is not None:
            same = fspkg.version_hashes(s, src.id) == fspkg.version_hashes(s, restored.id)
            report["steps"].append({"step": "compare_hashes", "ok": same, "versions": len(fspkg.version_hashes(s, src.id))})
            wb = workflow.latest_workbook_version(s, restored)
            cast_ok = True
            detail = "no workbook to re-cast"
            if wb is not None:
                with tempfile.TemporaryDirectory(dir=st.settings.work_path()) as td:
                    path = st.storage.download_to(wb.object_key, Path(td) / ("drill" + Path(wb.original_name).suffix))
                    try:
                        res = st.engine.check_workbook(path, config_for(s, restored))
                        cast_now = sum(1 for f in res.findings if f.rule_id.startswith(("CAST", "PLUG")))
                        orig_wb = workflow.latest_workbook_version(s, src)
                        orig = workflow.latest_run(s, src, "check", version_id=orig_wb.id) if orig_wb else None
                        if orig is not None:
                            rule_ids = s.execute(select(FindingRow.rule_id).where(FindingRow.run_id == orig.id)).scalars()
                            before = sum(1 for rid in rule_ids if rid.startswith(("CAST", "PLUG")))
                            cast_ok = before == cast_now
                            detail = f"CAST/PLUG findings: original {before}, restored {cast_now}"
                        else:
                            detail = f"CAST/PLUG findings on restored copy: {cast_now}"
                    except Exception as exc:
                        cast_ok = False
                        detail = f"re-cast failed: {type(exc).__name__}: {exc}"
            report["steps"].append({"step": "recast", "ok": cast_ok, "detail": detail})
        report["ok"] = all(step["ok"] for step in report["steps"])
        report["finished"] = utcnow().isoformat()
        key = f"t/{src.tenant_id}/drills/restore-{utcnow():%Y%m%d-%H%M%S}.json"
        st.storage.put_bytes(key, json.dumps(report, indent=2).encode(), "application/json")
        s.add(BackupLog(tenant_id=src.tenant_id, kind="restore_drill", status="ok" if report["ok"] else "failed", verified=bool(report["ok"]),
                        location=key, finished=utcnow(), details_json=report, created_by="cli"))
        audit.append(s, _actor(src.tenant_id), "backup.restore_drill", engagement_id=src.id, client_id=src.client_id, outcome="ok" if report["ok"] else "failed",
                     details={"ok": report["ok"], "report": key})
        s.commit()
    return report


def cmd_restore_drill(args: argparse.Namespace) -> int:
    st = _state()
    report = restore_drill(st, tenant=args.tenant, engagement_id=args.engagement, seed=args.seed)
    print(json.dumps(report, indent=2, default=str))
    return 0 if report.get("ok") else 1


# ------------------------------------------------------------------ compliance


def cmd_access_review_report(args: argparse.Namespace) -> int:
    from fsweb.services import compliance

    st = _state()
    with st.db.owner_session(SYSTEM) as s:
        for t in _tenants(s, args.tenant):
            print(json.dumps({"tenant_id": t.id, **compliance.access_report(s, t.id, args.period)}, indent=2))
    return 0


def cmd_start_access_review(args: argparse.Namespace) -> int:
    """Open the quarter's access-review campaign (C14); the reviewers decide in the admin console."""
    from fsweb.services import compliance

    today = utcnow().date()
    period = args.period or f"{today.year}Q{(today.month - 1) // 3 + 1}"
    st = _state()
    with st.db.owner_session(SYSTEM) as s:
        for t in _tenants(s, args.tenant):
            n = compliance.start_access_review(s, t.id, period, _actor(t.id))
            print(f"{t.id}: {n} assignment(s) to review for {period}")
        s.commit()
    return 0


def cmd_auto_revoke(args: argparse.Namespace) -> int:
    from fsweb.services import compliance

    st = _state()
    with st.db.owner_session(SYSTEM) as s:
        for t in _tenants(s, args.tenant):
            n = compliance.auto_revoke(s, t.id, _actor(t.id))
            print(f"{t.id}: {n} revoked")
        s.commit()
    return 0


def cmd_incident_check(args: argparse.Namespace) -> int:
    """CERT-In: alert when a reportable incident is due within the hour or overdue (6-hour rule)."""
    from fsweb.services import compliance
    from fsweb.services.common import notify, users_with_role

    st = _state()
    due_total = 0
    with st.db.owner_session(SYSTEM) as s:
        for t in _tenants(s, args.tenant):
            due = compliance.overdue_incidents(s, t.id)
            due_total += len(due)
            for i in due:
                for u in users_with_role(s, t.id, "firm_admin") + users_with_role(s, t.id, "system_admin"):
                    notify(s, t.id, u.email, "CERT-In deadline", f"Incident '{i.title}' is due for CERT-In reporting at {i.report_due_at:%d-%b-%Y %H:%M} UTC.", "incident")
        s.commit()
    print(f"{due_total} incident(s) due or overdue")
    return 0


def cmd_evidence_pack(args: argparse.Namespace) -> int:
    from fsweb.services import compliance

    st = _state()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    with st.db.owner_session(SYSTEM) as s:
        for t in _tenants(s, args.tenant):
            chain = audit.verify_chain(s, t.id)
            data = compliance.build_evidence_pack(s, t.id, args.quarter, chain)
            p = out / f"evidence-pack-{t.id[:8]}-{args.quarter}.zip"
            p.write_bytes(data)
            audit.append(s, _actor(t.id), "evidence_pack.exported", details={"quarter": args.quarter, "chain_ok": chain.ok})
            print(p)
        s.commit()
    return 0


def cmd_rotate_kek(args: argparse.Namespace) -> int:
    """Re-wrap every data key with the active KEK (add the new key last in FSWEB_KMS_MASTER_KEY first)."""
    st = _state()
    with st.db.owner_session(SYSTEM) as s:
        n = st.cipher.rotate_kek(s)
        for t in _tenants(s, None):
            audit.append(s, _actor(t.id), "kms.kek_rotated", details={"rewrapped": n, "active_kek": st.cipher.kms.active_kek_id})
        s.commit()
    print(f"re-wrapped {n} data key(s) under {st.cipher.kms.active_kek_id}")
    return 0


def cmd_send_mail(args: argparse.Namespace) -> int:
    """Flush the notification outbox via SMTP (FSWEB_SMTP_HOST); without SMTP, list what is queued."""
    import os
    import smtplib
    from email.message import EmailMessage

    from fsweb.models import EmailOutbox

    st = _state()
    host = os.environ.get("FSWEB_SMTP_HOST")
    sent = 0
    with st.db.owner_session(SYSTEM) as s:
        for m in s.execute(select(EmailOutbox).where(EmailOutbox.sent_at.is_(None))).scalars():
            if not host:
                print(f"queued: {m.kind} -> {m.to_addr}: {m.subject}")
                continue
            msg = EmailMessage()
            msg["From"] = os.environ.get("FSWEB_SMTP_FROM", "fs-platform@localhost")
            msg["To"] = m.to_addr
            msg["Subject"] = m.subject
            msg.set_content(m.body)
            try:
                with smtplib.SMTP(host, int(os.environ.get("FSWEB_SMTP_PORT", "587")), timeout=20) as smtp:
                    smtp.starttls()
                    if os.environ.get("FSWEB_SMTP_USER"):
                        smtp.login(os.environ["FSWEB_SMTP_USER"], os.environ.get("FSWEB_SMTP_PASSWORD", ""))
                    smtp.send_message(msg)
                m.sent_at = utcnow()
                sent += 1
            except OSError as exc:
                m.error = str(exc)[:500]
        s.commit()
    print(f"sent {sent}")
    return 0


# ------------------------------------------------------------------ demo


DEMO_INJECTIONS = ("subtotal_not_cast", "plug_total_minus", "din_mismatch", "gc_stale_figure", "rpt_omitted_balance", "hidden_row_in_total")


def _demo_workbook(path: Path) -> str:
    """A synthetic workbook for the demo: the engine's generator when available, else a small stand-in."""
    try:  # the engine's generator: a full Ind AS statement set with typical review errors injected
        from fscore.synthetic.generator import make_workbook

        make_workbook(path, seed=7, inject=list(DEMO_INJECTIONS))
        if path.exists():
            return "fscore.synthetic.generator.make_workbook"
    except Exception:  # noqa: S110 - fall back to the simpler candidates below
        pass
    candidates: list[tuple[str, str]] = [
        ("fscore.synthetic", "generate_workbook"),
        ("fscore.synthetic", "make_synthetic_workbook"),
        ("fscore.synthetic.generator", "generate_workbook"),
        ("fscore.synthetic.generator", "make_synthetic_workbook"),
    ]
    import importlib

    for mod_name, fn_name in candidates:
        try:
            mod = importlib.import_module(mod_name)
            fn: Callable[..., Any] | None = getattr(mod, fn_name, None)
        except ImportError:
            fn = None
        if fn is not None:
            try:
                fn(path)
                if path.exists():
                    return f"{mod_name}.{fn_name}"
            except TypeError:
                try:
                    fn(out=path)
                    if path.exists():
                        return f"{mod_name}.{fn_name}"
                except Exception:  # noqa: S112
                    continue
            except Exception:  # noqa: S112
                continue
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "BS"
    fmt = '_(* #,##0.00_);_(* \\(#,##0.00\\);_(* "-"??_);_(@_)'
    rows = [("Synthetic Demo Private Limited", None, None), ("Balance Sheet as at March 31, 2026", None, None), ("(All amounts in ₹ lakhs)", None, None),
            ("Particulars", "March 31, 2026", "March 31, 2025"), ("Property, plant and equipment", 120.25, 110.10), ("Trade receivables", 80.40, 75.35),
            ("Cash and cash equivalents", 45.10, 30.05), ("Total assets", "=SUM(B5:B7)", "=SUM(C5:C7)"), ("Equity share capital", 100.00, 100.00),
            ("Other equity", 95.75, 70.50), ("Trade payables", 50.00, 45.00), ("Total equity and liabilities", "=SUM(B9:B11)", "=SUM(C9:C11)")]
    for r in rows:
        ws.append(list(r))
    for row in ws.iter_rows(min_row=5, max_row=12, min_col=2, max_col=3):
        for c in row:
            c.number_format = fmt
    wb.save(path)
    return "stand-in"


DEMO_PASSWORD = "Demo-Ledger!2026-fs"  # noqa: S105 - dev / demo only; seed-demo refuses to run in prod / offline

DEMO_USERS: list[tuple[str, str, str, bool, bool]] = [
    # email, name, role, firm user, client-scoped
    ("sysadmin@demo.fs.test", "Sam Sysadmin", "system_admin", True, False),
    ("admin@demo.fs.test", "Asha Admin", "firm_admin", True, False),
    ("partner@demo.fs.test", "Priya Partner", "partner", True, True),
    ("manager@demo.fs.test", "Manoj Manager", "manager", True, True),
    ("staff@demo.fs.test", "Sunil Staff", "staff", True, True),
    ("reviewer@demo.fs.test", "Rhea Reviewer", "reviewer", True, True),
    ("uploader@client.demo.fs.test", "Uma Uploader", "client_uploader", False, True),
    ("cfo@client.demo.fs.test", "Chetan CFO", "client_viewer", False, True),
]


def seed_demo(st: AppState) -> dict[str, Any]:
    from fsweb.models import File as FileModel
    from fsweb.models import Role
    from fsweb.services.common import store_version
    from fsweb.services.engagements import seed_engagement
    from fsweb.services.seed import assign, create_tenant, demo_totp_secret, make_user

    if st.settings.production_like:
        raise RuntimeError("seed-demo is for dev / demo environments only")
    with st.db.owner_session(SYSTEM) as s:
        existing = s.execute(select(Tenant).where(Tenant.name == "Demo Firm (synthetic)")).scalar_one_or_none()
        if existing is not None:
            raise RuntimeError("the demo tenant already exists")
        t = create_tenant(s, "Demo Firm (synthetic)", settings={"firm_admin_may_approve_ai": True, "ai_enabled": True})
        roles = {r.code: r for r in s.execute(select(Role).where(Role.tenant_id == t.id)).scalars()}
        client = Client(tenant_id=t.id, name="Synthetic Demo Private Limited", group_name="Synthetic Group", office="Head office", created_by="seed")
        client.pan_enc = st.cipher.encrypt(s, t.id, "AAACS1234Z")
        client.cin_enc = st.cipher.encrypt(s, t.id, "U72900KA2015PTC012345")
        s.add(client)
        s.flush()
        entity = Entity(tenant_id=t.id, client_id=client.id, name="Synthetic Demo Private Limited", created_by="seed")
        s.add(entity)
        s.flush()
        for name, role, din in (("Director Alpha", "Director", "01234567"), ("Director Beta", "Director", "07654321")):
            sig = Signatory(tenant_id=t.id, client_id=client.id, entity_id=entity.id, name=name, role=role, created_by="seed")
            sig.din_enc = st.cipher.encrypt(s, t.id, din)
            s.add(sig)
        users: list[dict[str, Any]] = []
        ids: dict[str, str] = {}
        for email, name, role, firm, scoped in DEMO_USERS:
            secret = demo_totp_secret(email)
            u = make_user(s, st.cipher, t.id, email, name, password=DEMO_PASSWORD, is_firm_user=firm, totp_secret=secret, created_by="seed")
            assign(s, t.id, u, roles[role], client.id if scoped else None, "seed")
            ids[role] = u.id
            users.append({"email": email, "role": role, "password": DEMO_PASSWORD, "totp_secret": secret})
        eng = Engagement(tenant_id=t.id, client_id=client.id, entity_id=entity.id, name="FY 2025-26 statutory accounts", type="annual",
                         period_end=date(2026, 3, 31), partner_id=ids["partner"], manager_id=ids["manager"], created_by="seed")
        s.add(eng)
        s.flush()
        seed_engagement(s, eng, "seed")
        uploaded = None
        with tempfile.TemporaryDirectory() as td:
            wb_path = Path(td) / "Synthetic FS FY26.xlsx"
            source = _demo_workbook(wb_path)
            f = FileModel(tenant_id=t.id, client_id=client.id, engagement_id=eng.id, kind="workbook", title="Statements workbook", created_by=ids["staff"])
            s.add(f)
            s.flush()
            v = store_version(s, st.storage, eng, f, wb_path.read_bytes(), original_name=wb_path.name, created_by=ids["staff"],
                              change_summary={"source": f"seed-demo ({source})"})
            eng.status = "DATA_RECEIVED"
            uploaded = {"version_id": v.id, "generator": source}
        audit.append(s, audit.Actor(t.id, None, "seed"), "tenant.demo_seeded", object_type="tenant", object_id=t.id)
        s.commit()
        return {"tenant_id": t.id, "client_id": client.id, "engagement_id": eng.id, "workbook": uploaded, "users": users}


def cmd_seed_demo(args: argparse.Namespace) -> int:
    st = _state()
    try:
        out = seed_demo(st)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(out, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="fsweb.manage", description="FS-Platform admin CLI")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate").set_defaults(fn=cmd_migrate)
    p = sub.add_parser("openapi")
    p.add_argument("--out", default=str(Path(__file__).resolve().parents[2] / "openapi.json"))
    p.set_defaults(fn=cmd_openapi)
    p = sub.add_parser("bootstrap")
    p.add_argument("--tenant", required=True)
    p.add_argument("--admin-email", required=True)
    p.add_argument("--admin-name", default="Firm Admin")
    p.set_defaults(fn=cmd_bootstrap)
    p = sub.add_parser("create-breakglass")
    p.add_argument("--tenant", required=True)
    p.add_argument("--email", required=True)
    p.set_defaults(fn=cmd_create_breakglass)
    for name, fn in (("verify-audit-chain", cmd_verify_audit_chain), ("access-review-report", cmd_access_review_report),
                     ("start-access-review", cmd_start_access_review), ("auto-revoke", cmd_auto_revoke), ("incident-check", cmd_incident_check)):
        p = sub.add_parser(name)
        p.add_argument("--tenant")
        if name in ("access-review-report", "start-access-review"):
            p.add_argument("--period")
        p.set_defaults(fn=fn)
    p = sub.add_parser("audit-head")
    p.add_argument("--tenant")
    p.add_argument("--out-dir")
    p.add_argument("--day")
    p.set_defaults(fn=cmd_audit_head)
    p = sub.add_parser("export-engagement")
    p.add_argument("--id", required=True)
    p.add_argument("--out", required=True)
    p.set_defaults(fn=cmd_export)
    p = sub.add_parser("import-engagement")
    p.add_argument("--file", required=True)
    p.add_argument("--quarantine", action="store_true", default=True, help="always on: imports land in quarantine")
    p.set_defaults(fn=cmd_import)
    p = sub.add_parser("restore-drill")
    p.add_argument("--tenant")
    p.add_argument("--engagement")
    p.add_argument("--seed", type=int)
    p.set_defaults(fn=cmd_restore_drill)
    p = sub.add_parser("evidence-pack")
    p.add_argument("--quarter", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--tenant")
    p.set_defaults(fn=cmd_evidence_pack)
    sub.add_parser("rotate-kek").set_defaults(fn=cmd_rotate_kek)
    sub.add_parser("send-mail").set_defaults(fn=cmd_send_mail)
    sub.add_parser("seed-demo").set_defaults(fn=cmd_seed_demo)
    args = ap.parse_args(argv)
    fn: Callable[[argparse.Namespace], int] = args.fn
    return fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
