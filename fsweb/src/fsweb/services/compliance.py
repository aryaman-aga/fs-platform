"""Compliance services shared by the admin API and the CLI: access recertification
(C14 / E-SEC-04), DPDP requests (E-SEC-06), CERT-In incident deadlines (E-SEC-05),
and the quarterly evidence pack (E-ITGC-06)."""

from __future__ import annotations

import csv
import io
import json
import zipfile
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from fsweb import audit
from fsweb.db import utcnow
from fsweb.models import (
    AccessReview,
    AuditEvent,
    BackupLog,
    Client,
    DsrRequest,
    Incident,
    Release,
    Role,
    RuleRow,
    RuleVersion,
    Run,
    User,
    UserClientRole,
    UserSession,
)
from fsweb.services.common import notify, users_with_role

CERTIN_HOURS = 6
REVIEW_DAYS = 14


def quarter_bounds(period: str) -> tuple[datetime, datetime]:
    year, q = int(period[:4]), int(period[-1])
    start = datetime(year, 3 * (q - 1) + 1, 1, tzinfo=utcnow().tzinfo)
    end = datetime(year + (1 if q == 4 else 0), 1 if q == 4 else 3 * q + 1, 1, tzinfo=utcnow().tzinfo)
    return start, end


def start_access_review(db: Session, tenant_id: str, period: str, actor: audit.Actor) -> int:
    existing = int(db.execute(select(func.count()).select_from(AccessReview).where(AccessReview.tenant_id == tenant_id, AccessReview.period == period)).scalar_one())
    if existing:
        return 0
    admins = users_with_role(db, tenant_id, "firm_admin")
    n = 0
    now = utcnow()
    for a in db.execute(select(UserClientRole).where(UserClientRole.tenant_id == tenant_id, UserClientRole.revoked_at.is_(None))).scalars():
        if a.expires_at is not None and a.expires_at <= now:
            continue
        reviewer = None
        if a.client_id:
            partners = [u for u in users_with_role(db, tenant_id, "partner", a.client_id) if u.id != a.user_id]
            reviewer = partners[0].id if partners else None
        if reviewer is None:
            cands = [u for u in admins if u.id != a.user_id]
            reviewer = cands[0].id if cands else None
        db.add(AccessReview(tenant_id=tenant_id, client_id=a.client_id, period=period, reviewer_id=reviewer, user_id=a.user_id, role_id=a.role_id,
                            assignment_id=a.id, due_at=now + timedelta(days=REVIEW_DAYS), created_by=actor.user_id))
        n += 1
    audit.append(db, actor, "access_review.started", object_type="access_review", object_id=period, details={"items": n})
    for u in admins:
        notify(db, tenant_id, u.email, f"Access review {period} started", f"{n} assignments to certify within {REVIEW_DAYS} days.", "access_review")
    return n


def decide_access(db: Session, item: AccessReview, decision: str, actor: audit.Actor) -> None:
    item.decision = decision
    item.decided_at = utcnow()
    item.decided_by = actor.user_id
    if decision == "revoke":
        a = db.get(UserClientRole, item.assignment_id)
        if a is not None and a.revoked_at is None:
            a.revoked_at = utcnow()
            a.revoked_reason = f"access review {item.period}"
    audit.append(db, actor, "access_review.decided", client_id=item.client_id, object_type="access_review", object_id=item.id,
                 details={"decision": decision, "user_id": item.user_id})


def overdue_emergency_reviews(db: Session, tenant_id: str | None = None, now: datetime | None = None) -> list[Release]:
    """Emergency changes deployed more than 48 hours ago and still not approved (E-ITGC-01)."""
    now = now or utcnow()
    stmt = select(Release).where(Release.emergency.is_(True), Release.deployed_at.is_not(None), Release.approved_at.is_(None),
                                 Release.deployed_at < now - Release.EMERGENCY_REVIEW)
    if tenant_id is not None:
        stmt = stmt.where(Release.tenant_id == tenant_id)
    return list(db.execute(stmt).scalars())


def auto_revoke(db: Session, tenant_id: str, actor: audit.Actor, now: datetime | None = None) -> int:
    """Unanswered items are revoked automatically after 14 days (E-SEC-04)."""
    now = now or utcnow()
    n = 0
    roles = {r.id: r.code for r in db.execute(select(Role).where(Role.tenant_id == tenant_id)).scalars()}
    for item in db.execute(select(AccessReview).where(AccessReview.tenant_id == tenant_id, AccessReview.decision == "pending", AccessReview.due_at < now)).scalars():
        a = db.get(UserClientRole, item.assignment_id)
        if a is not None and a.revoked_at is None:
            code = roles.get(a.role_id, "")
            if code in {"firm_admin", "system_admin"} and _other_active_holders(db, a, now) == 0:
                # Never lock the firm out: the last administrator is escalated, not revoked.
                audit.append(db, actor, "access_review.escalated", object_type="access_review", object_id=item.id,
                             details={"role": code, "user_id": a.user_id})
                continue
            a.revoked_at = now
            a.revoked_reason = f"auto-revoked: access review {item.period} unanswered"
        item.decision = "auto_revoked"
        item.decided_at = now
        n += 1
    if n:
        audit.append(db, actor, "access_review.auto_revoked", details={"items": n})
    return n


def _other_active_holders(db: Session, a: UserClientRole, now: datetime) -> int:
    """People other than ``a``'s holder who can still use its role: assignment neither revoked nor expired (a lapsed
    JIT elevation grants nothing) and account active (a disabled leaver cannot sign in)."""
    return int(db.execute(
        select(func.count()).select_from(UserClientRole).join(User, User.id == UserClientRole.user_id)
        .where(UserClientRole.role_id == a.role_id, UserClientRole.user_id != a.user_id, UserClientRole.revoked_at.is_(None),
               or_(UserClientRole.expires_at.is_(None), UserClientRole.expires_at > now), User.status == "active")
    ).scalar_one())


def access_report(db: Session, tenant_id: str, period: str | None = None) -> dict[str, Any]:
    stmt = select(AccessReview).where(AccessReview.tenant_id == tenant_id)
    if period:
        stmt = stmt.where(AccessReview.period == period)
    rows = list(db.execute(stmt).scalars())
    users = {u.id: u.email for u in db.execute(select(User).where(User.tenant_id == tenant_id)).scalars()}
    roles = {r.id: r.code for r in db.execute(select(Role).where(Role.tenant_id == tenant_id)).scalars()}
    clients = {c.id: c.name for c in db.execute(select(Client).where(Client.tenant_id == tenant_id)).scalars()}
    summary: dict[str, int] = {}
    items = []
    for r in rows:
        summary[r.decision] = summary.get(r.decision, 0) + 1
        items.append({"period": r.period, "user": users.get(r.user_id, r.user_id), "role": roles.get(r.role_id, r.role_id),
                      "client": clients.get(r.client_id or "", "(tenant-wide)"), "reviewer": users.get(r.reviewer_id or "", ""),
                      "decision": r.decision, "decided_at": r.decided_at.isoformat() if r.decided_at else None, "due_at": r.due_at.isoformat()})
    return {"period": period, "summary": summary, "items": items}


def incident_due(detected_at: datetime, certin: bool) -> datetime | None:
    return detected_at + timedelta(hours=CERTIN_HOURS) if certin else None


def overdue_incidents(db: Session, tenant_id: str, now: datetime | None = None) -> list[Incident]:
    """CERT-In reportable incidents not yet reported that are due within the hour or overdue."""
    now = now or utcnow()
    stmt = select(Incident).where(Incident.tenant_id == tenant_id, Incident.certin_reportable.is_(True), Incident.reported_at.is_(None))
    return [i for i in db.execute(stmt).scalars() if i.report_due_at is not None and i.report_due_at - timedelta(hours=1) <= now]


def dsr_export(db: Session, user: User) -> dict[str, Any]:
    """Personal data held about a firm or client user (DPDP right of access)."""
    sessions = db.execute(select(UserSession).where(UserSession.user_id == user.id)).scalars()
    events = db.execute(select(AuditEvent).where(AuditEvent.user_id == user.id).order_by(AuditEvent.seq)).scalars()
    return {
        "profile": {"email": user.email, "name": user.name, "status": user.status, "firm_user": user.is_firm_user, "created_at": user.created_at.isoformat(),
                    "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None, "mfa": user.mfa_type},
        "sessions": [{"created_at": s.created_at.isoformat(), "ip": s.ip, "user_agent": s.user_agent} for s in sessions],
        "activity": [{"ts": e.ts.isoformat(), "action": e.action, "object_type": e.object_type} for e in events],
    }


def dsr_erase(db: Session, user: User, retention_years: int) -> str:
    """Erasure subject to legal retention: identity is pseudonymised; audit records keep only the user id."""
    user.name = "Erased user"
    user.email = f"erased-{user.id}@invalid.example"
    user.phone_enc = None
    user.status = "disabled"
    user.totp_secret_enc = None
    user.password_hash = None
    user.erased_at = utcnow()
    for s in db.execute(select(UserSession).where(UserSession.user_id == user.id)).scalars():
        s.revoked_at = s.revoked_at or utcnow()
        s.ip = None
        s.user_agent = None
    return (f"Identity pseudonymised. Engagement records and the audit trail are retained for {retention_years} years under SA 230 / "
            "the firm's retention policy (legal obligation); they reference only an opaque user id.")


def build_evidence_pack(db: Session, tenant_id: str, quarter: str, chain: audit.ChainReport) -> bytes:
    """One-click quarterly evidence pack (E-ITGC-06) mapped to C1–C14 / ITGC."""
    start, end = quarter_bounds(quarter)
    buf = io.BytesIO()

    def table(rows: list[dict[str, Any]]) -> str:
        if not rows:
            return ""
        s = io.StringIO()
        w = csv.DictWriter(s, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
        return s.getvalue()

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        manifest = {
            "tenant_id": tenant_id,
            "quarter": quarter,
            "generated_at": utcnow().isoformat(),
            "controls": {
                "C1/C5/C7 (immutability, verification, PDF match)": "runs.csv",
                "C10/C14/E-SEC-04 (segregation, access review)": "access_review.csv, users_roles.csv",
                "C13 (backups and restore drills)": "backups.csv",
                "E-ITGC-01/02 (change management, rule regression)": "releases.csv, rule_versions.csv",
                "E-SEC-05 (CERT-In incidents)": "incidents.csv",
                "E-SEC-06 (DPDP requests)": "dsr.csv",
                "06 §4 (audit chain)": "audit_chain.json",
            },
        }
        z.writestr("manifest.json", json.dumps(manifest, indent=2))
        z.writestr("audit_chain.json", json.dumps(chain.as_dict(), indent=2))
        z.writestr("access_review.csv", table(access_report(db, tenant_id, quarter)["items"]))
        users = {u.id: u for u in db.execute(select(User).where(User.tenant_id == tenant_id)).scalars()}
        roles = {r.id: r.code for r in db.execute(select(Role).where(Role.tenant_id == tenant_id)).scalars()}
        ur = [{"user": users[a.user_id].email if a.user_id in users else a.user_id, "status": users[a.user_id].status if a.user_id in users else "",
               "role": roles.get(a.role_id, a.role_id), "client_id": a.client_id or "(tenant)", "granted_at": a.created_at.isoformat(),
               "revoked_at": a.revoked_at.isoformat() if a.revoked_at else "", "expires_at": a.expires_at.isoformat() if a.expires_at else ""}
              for a in db.execute(select(UserClientRole).where(UserClientRole.tenant_id == tenant_id)).scalars()]
        z.writestr("users_roles.csv", table(ur))
        z.writestr("backups.csv", table([{"kind": b.kind, "started": b.started.isoformat(), "status": b.status, "verified": b.verified, "size": b.size,
                                          "location": b.location} for b in db.execute(select(BackupLog).where(BackupLog.tenant_id == tenant_id,
                                          BackupLog.started >= start, BackupLog.started < end)).scalars()]))
        z.writestr("releases.csv", table([{"version": r.version, "ticket": r.ticket_ref, "risk": r.risk, "developer": r.created_by, "approver": r.approver_id,
                                           "approved_at": r.approved_at.isoformat() if r.approved_at else "", "deployed_at": r.deployed_at.isoformat() if r.deployed_at else "",
                                           "rule_change": r.rule_change, "regression_evidence": r.regression_evidence or "", "emergency": r.emergency,
                                           "review_due_at": r.review_due_at.isoformat() if r.review_due_at else "", "review_overdue": r.review_overdue}
                                          for r in db.execute(select(Release).where(Release.tenant_id == tenant_id)).scalars()]))
        codes = {r.id: r.code for r in db.execute(select(RuleRow).where(RuleRow.tenant_id == tenant_id)).scalars()}
        z.writestr("rule_versions.csv", table([{"rule": codes.get(v.rule_id, v.rule_id), "version": v.version, "severity": v.severity,
                                                "effective_from": v.effective_from.isoformat() if v.effective_from else "",
                                                "effective_to": v.effective_to.isoformat() if v.effective_to else "",
                                                "rule_set_hash": (v.params_json or {}).get("rule_set_hash", ""), "approved_by": v.approved_by,
                                                "change_record": v.regression_run_id}
                                               for v in db.execute(select(RuleVersion).where(RuleVersion.tenant_id == tenant_id)
                                                                   .order_by(RuleVersion.created_at)).scalars()]))
        z.writestr("incidents.csv", table([{"severity": i.severity, "title": i.title, "detected_at": i.detected_at.isoformat(), "certin": i.certin_reportable,
                                            "due": i.report_due_at.isoformat() if i.report_due_at else "", "reported_at": i.reported_at.isoformat() if i.reported_at else "",
                                            "status": i.status} for i in db.execute(select(Incident).where(Incident.tenant_id == tenant_id)).scalars()]))
        z.writestr("dsr.csv", table([{"kind": d.kind, "status": d.status, "due_at": d.due_at.isoformat(), "completed_at": d.completed_at.isoformat() if d.completed_at else ""}
                                     for d in db.execute(select(DsrRequest).where(DsrRequest.tenant_id == tenant_id)).scalars()]))
        z.writestr("runs.csv", table([{"run_id": r.id, "kind": r.kind, "status": r.status, "engine": r.engine_version, "rule_set": r.rule_set_version,
                                       "created_at": r.created_at.isoformat()} for r in db.execute(select(Run).where(Run.tenant_id == tenant_id, Run.created_at >= start,
                                       Run.created_at < end)).scalars()]))
    return buf.getvalue()
