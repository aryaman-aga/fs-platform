"""Tenant bootstrap: roles, permission matrix, policies, checklist template, first admin."""

from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from fsweb import audit
from fsweb.authz import abac
from fsweb.authz.permissions import ALL_PERMISSIONS, ROLES
from fsweb.db import utcnow
from fsweb.models import Permission, Policy, Role, RolePermission, Tenant, User, UserClientRole
from fsweb.security import passwords, totp
from fsweb.security.crypto import FieldCipher

AI_POLICY_V1 = """FS-Platform AI use policy (version 1) — approved by the partners.

Allowed uses: (1) triage of findings (genuine error / presentation-only / false positive),
(2) review of narrative notes against the statements, (3) cross-reference suggestions,
(4) plain-language explanations of mismatches for reviewers.

Prohibited uses: deciding or changing any figure; editing workbooks; signing or approving
statements; sending whole workbooks; sending unredacted names, DIN, CIN, PAN or contact data.

Data rules: AI is off unless enabled for the firm and the engagement. Every payload is
pseudonymised, shown in full to the requester, approved by a different person, bound to its
SHA-256 hash, and sent only through the AI gateway to the approved provider and region.
AI output is advisory: every number it cites is re-verified by the engine and nothing
changes a file without a user's action.
"""

DISCLOSURE_ITEMS: list[tuple[str, str]] = [
    ("GC-ASSESS", "Going concern assessment documented and the note agrees with the statements (Ind AS 1.25–26, SA 570)"),
    ("RPT-COMPLETE", "Related-party note complete: parties, relationships, transactions and balances (Ind AS 24, SA 550)"),
    ("MSME", "MSME dues and interest disclosed (MSMED Act 2006, Schedule III)"),
    ("CSR", "CSR obligation and spend disclosed where Section 135 applies"),
    ("RATIOS", "Schedule III ratios presented; variances above 25 % explained"),
    ("CONTINGENT", "Contingent liabilities and commitments disclosed (Ind AS 37)"),
    ("SUBSEQUENT", "Events after the reporting period considered (Ind AS 10)"),
    ("SEGMENT", "Segment information (Ind AS 108) or a statement that it is not applicable"),
    ("EPS", "Basic and diluted EPS presented (Ind AS 33)"),
    ("TAX", "Current and deferred tax with the tax-rate reconciliation (Ind AS 12)"),
    ("LEASES", "Lease disclosures: ROU assets, lease liabilities, maturity (Ind AS 116)"),
    ("EMPLOYEE", "Employee benefits incl. actuarial assumptions and sensitivity (Ind AS 19)"),
    ("FIN-INSTR", "Financial instruments: categories, fair-value hierarchy, risk management (Ind AS 107/109/113)"),
    ("CAPITAL", "Capital management disclosure (Ind AS 1.134)"),
    ("ARI", "Additional regulatory information of Schedule III (title deeds, benami, wilful defaulter, struck-off companies, crypto)"),
    ("SIGNATORIES", "Signature blocks complete: names, DIN / membership no., FRN, place and date"),
]


@dataclass
class BootstrapResult:
    tenant_id: str
    admin_user_id: str
    admin_email: str
    one_time_password: str
    totp_secret: str
    users: list[dict[str, Any]] = field(default_factory=list)


def ensure_permissions(db: Session) -> None:
    existing = {p for (p,) in db.execute(select(Permission.code)).all()}
    for label, code, _ in ALL_PERMISSIONS:
        if code not in existing:
            db.add(Permission(code=code, description=label))
    db.flush()


def seed_roles(db: Session, tenant_id: str) -> dict[str, Role]:
    ensure_permissions(db)
    roles: dict[str, Role] = {r.code: r for r in db.execute(select(Role).where(Role.tenant_id == tenant_id)).scalars()}
    for rd in ROLES:
        if rd.code not in roles:
            role = Role(tenant_id=tenant_id, code=rd.code, name=rd.name, is_system=True, scope=rd.scope,
                        is_client_role=rd.is_client_role, download_allowed=rd.download_allowed, description=rd.description)
            db.add(role)
            db.flush()
            roles[rd.code] = role
    have = {(rp.role_id, rp.permission_code) for rp in db.execute(select(RolePermission).where(RolePermission.tenant_id == tenant_id)).scalars()}
    for _, code, cells in ALL_PERMISSIONS:
        for role_code, value in cells.items():
            rid = roles[role_code].id
            if (rid, code) not in have:
                db.add(RolePermission(role_id=rid, permission_code=code, tenant_id=tenant_id, value=value))
    db.flush()
    return roles


def seed_policies(db: Session, tenant_id: str) -> None:
    for name, body in (("abac", abac.default_policy_text()), ("ai_policy", AI_POLICY_V1)):
        exists = db.execute(select(Policy).where(Policy.tenant_id == tenant_id, Policy.name == name)).scalars().first()
        if exists is None:
            db.add(Policy(tenant_id=tenant_id, name=name, version=1, body=body, active=True, approved_by="seed"))
    db.flush()


def create_tenant(db: Session, name: str, *, settings: dict[str, Any] | None = None) -> Tenant:
    t = Tenant(name=name, settings_json=settings or {"firm_admin_may_approve_ai": True, "ai_enabled": False})
    db.add(t)
    db.flush()
    audit.ensure_head(db, t.id)
    seed_roles(db, t.id)
    seed_policies(db, t.id)
    return t


def assign(db: Session, tenant_id: str, user: User, role: Role, client_id: str | None, by: str | None) -> UserClientRole:
    a = UserClientRole(tenant_id=tenant_id, user_id=user.id, client_id=client_id, role_id=role.id, created_by=by)
    db.add(a)
    db.flush()
    return a


def make_user(
    db: Session,
    cipher: FieldCipher,
    tenant_id: str,
    email: str,
    name: str,
    *,
    password: str | None,
    is_firm_user: bool = True,
    totp_secret: str | None = None,
    status: str = "active",
    created_by: str | None = None,
) -> User:
    u = User(
        tenant_id=tenant_id,
        email=email.strip().lower(),
        name=name,
        status=status,
        is_firm_user=is_firm_user,
        password_hash=passwords.hash_password(password) if password else None,
        password_changed_at=utcnow() if password else None,
        created_by=created_by,
    )
    if totp_secret:
        u.totp_secret_enc = cipher.encrypt(db, tenant_id, totp_secret)
        u.mfa_type = "totp"
        u.mfa_enrolled_at = utcnow()
    db.add(u)
    db.flush()
    return u


def one_time_password() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
    while True:
        pw = "-".join("".join(secrets.choice(alphabet) for _ in range(5)) for _ in range(4))
        try:
            passwords.check_policy(pw)
            return pw
        except passwords.PasswordPolicyError:
            continue


def demo_totp_secret(email: str) -> str:
    """Deterministic TOTP secret for *demo* users only (seed-demo refuses to run in prod)."""
    return base64.b32encode(hashlib.sha256(("fs-demo-totp:" + email).encode()).digest()).decode()[:32]


def bootstrap(db: Session, cipher: FieldCipher, tenant_name: str, admin_email: str, admin_name: str = "Firm Admin") -> BootstrapResult:
    t = create_tenant(db, tenant_name)
    roles = {r.code: r for r in db.execute(select(Role).where(Role.tenant_id == t.id)).scalars()}
    pw = one_time_password()
    secret = totp.new_secret()
    u = make_user(db, cipher, t.id, admin_email, admin_name, password=pw, totp_secret=secret, created_by="bootstrap")
    assign(db, t.id, u, roles["firm_admin"], None, "bootstrap")
    audit.append(db, audit.Actor(t.id, None, "bootstrap"), "tenant.bootstrapped", object_type="tenant", object_id=t.id,
                 details={"admin_user_id": u.id})
    return BootstrapResult(t.id, u.id, u.email, pw, secret)
