"""User, role and access administration (05 §2–§3, E-SEC-02/03, E-ITGC-03)."""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, Request
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from fsweb import audit, schemas
from fsweb.authz.abac import PolicyDoc
from fsweb.authz.deps import (
    Principal,
    authorize,
    get_db,
    get_state,
    invalidate_matrix,
    require_principal,
    require_stepup,
)
from fsweb.authz.permissions import ALL_PERMISSIONS, DENY, MATRIX_ROWS, STRENGTH
from fsweb.db import utcnow
from fsweb.errors import bad_request, conflict, forbidden, not_found
from fsweb.models import (
    Client,
    Elevation,
    Invite,
    Policy,
    RecoveryCode,
    Role,
    RolePermission,
    User,
    UserClientRole,
)
from fsweb.security import sessions
from fsweb.security.crypto import hash_token, new_token
from fsweb.services.common import notify
from fsweb.services.seed import assign

router = APIRouter(tags=["admin"])


def _role(db: Session, tenant_id: str, code: str) -> Role:
    r = db.execute(select(Role).where(Role.tenant_id == tenant_id, Role.code == code)).scalar_one_or_none()
    if r is None:
        raise bad_request("unknown_role", f"unknown role {code}")
    return r


@router.get("/admin/users", response_model=list[schemas.UserOut])
def list_users(request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> list[User]:
    authorize(request, db, p, "users.manage")
    return list(db.execute(select(User).where(User.tenant_id == p.tenant_id).order_by(User.email)).scalars())


@router.post("/admin/users/invite", response_model=schemas.InviteOut, status_code=201)
def invite_user(body: schemas.InviteIn, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> schemas.InviteOut:
    st = get_state(request)
    authorize(request, db, p, "users.manage")
    if db.execute(select(User).where(User.email == body.email.lower())).scalar_one_or_none() is not None:
        raise conflict("user_exists", "a user with this e-mail already exists")
    u = User(tenant_id=p.tenant_id, email=body.email.lower(), name=body.name, status="invited", is_firm_user=body.is_firm_user, created_by=p.user_id)
    db.add(u)
    db.flush()
    if body.role_code:
        role = _role(db, p.tenant_id, body.role_code)
        if role.scope == "client" and not body.client_id:
            raise bad_request("client_required", "this role is assigned per client")
        if role.is_client_role and body.is_firm_user:
            raise bad_request("role_mismatch", "client roles are for client users")
        if body.client_id and db.get(Client, body.client_id) is None:
            raise not_found("client")
        assign(db, p.tenant_id, u, role, body.client_id if role.scope == "client" else None, p.user_id)
    token = new_token()
    inv = Invite(tenant_id=p.tenant_id, user_id=u.id, token_hash=hash_token(token), expires_at=utcnow() + timedelta(hours=st.settings.invite_hours), created_by=p.user_id)
    db.add(inv)
    link = f"{st.settings.public_base_url}/invite?token={token}"
    notify(db, p.tenant_id, u.email, "You are invited to FS-Platform", f"Set your password within {st.settings.invite_hours} hours: {link}", "invite")
    audit.append(db, p.actor(), "user.invited", client_id=body.client_id, object_type="user", object_id=u.id,
                 details={"role": body.role_code, "firm_user": body.is_firm_user})
    db.commit()
    return schemas.InviteOut(user=schemas.UserOut.model_validate(u), invite_link=None if st.settings.production_like else link, expires_at=inv.expires_at)


@router.get("/admin/users/{user_id}", response_model=schemas.UserOut)
def get_user(user_id: str, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> User:
    authorize(request, db, p, "users.manage")
    u = db.get(User, user_id)
    if u is None or u.tenant_id != p.tenant_id:
        raise not_found("user")
    return u


@router.patch("/admin/users/{user_id}", response_model=schemas.UserOut)
def patch_user(user_id: str, body: schemas.UserPatchIn, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> User:
    authorize(request, db, p, "users.manage")
    u = db.get(User, user_id)
    if u is None or u.tenant_id != p.tenant_id:
        raise not_found("user")
    before = {"name": u.name, "download_allowed": u.download_allowed}
    if body.name is not None:
        u.name = body.name
    if body.download_allowed is not None:
        u.download_allowed = body.download_allowed
    audit.append(db, p.actor(), "user.updated", object_type="user", object_id=u.id, before=before,
                 after={"name": u.name, "download_allowed": u.download_allowed})
    db.commit()
    return u


@router.post("/admin/users/{user_id}/disable", response_model=schemas.UserOut)
def disable_user(user_id: str, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> User:
    """Offboarding: disabling revokes every session immediately; history is kept (05 §1)."""
    authorize(request, db, p, "users.manage")
    reason = require_stepup(request, p)
    u = db.get(User, user_id)
    if u is None or u.tenant_id != p.tenant_id:
        raise not_found("user")
    if u.id == p.user_id:
        raise conflict("self_disable", "you cannot disable your own account")
    u.status = "disabled"
    n = sessions.revoke_all(db, u.id)
    audit.append(db, p.actor(), "user.disabled", object_type="user", object_id=u.id, details={"reason": reason, "sessions_revoked": n})
    db.commit()
    return u


@router.post("/admin/users/{user_id}/enable", response_model=schemas.UserOut)
def enable_user(user_id: str, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> User:
    authorize(request, db, p, "users.manage")
    reason = require_stepup(request, p)
    u = db.get(User, user_id)
    if u is None or u.tenant_id != p.tenant_id:
        raise not_found("user")
    u.status = "active" if u.password_hash else "invited"
    u.locked_until = None
    u.failed_logins = 0
    audit.append(db, p.actor(), "user.enabled", object_type="user", object_id=u.id, details={"reason": reason})
    db.commit()
    return u


@router.post("/admin/users/{user_id}/mfa-reset", response_model=schemas.OkOut)
def mfa_reset(user_id: str, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> schemas.OkOut:
    """MFA reset is admin-only (08 §3), with step-up and a reason."""
    authorize(request, db, p, "users.manage")
    reason = require_stepup(request, p)
    u = db.get(User, user_id)
    if u is None or u.tenant_id != p.tenant_id:
        raise not_found("user")
    u.totp_secret_enc = None
    u.mfa_type = "none"
    u.mfa_enrolled_at = None
    u.last_totp_step = None
    for rc in db.execute(select(RecoveryCode).where(RecoveryCode.user_id == u.id)).scalars():
        db.delete(rc)
    n = sessions.revoke_all(db, u.id)
    audit.append(db, p.actor(), "user.mfa_reset", object_type="user", object_id=u.id, details={"reason": reason, "sessions_revoked": n})
    db.commit()
    return schemas.OkOut(message="MFA reset; the user enrols a new authenticator at next sign-in")


# ------------------------------------------------------------------ roles & matrix


@router.get("/admin/roles", response_model=list[schemas.RoleOut])
def list_roles(request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> list[Role]:
    if not (p.is_firm_admin or p.is_system_admin):
        authorize(request, db, p, "users.manage")
    return list(db.execute(select(Role).where(Role.tenant_id == p.tenant_id).order_by(Role.name)).scalars())


@router.get("/admin/permission-matrix", response_model=schemas.MatrixOut)
def permission_matrix(request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> schemas.MatrixOut:
    authorize(request, db, p, "users.manage")
    roles = list(db.execute(select(Role).where(Role.tenant_id == p.tenant_id)).scalars())
    by_role = {r.id: r.code for r in roles}
    values: dict[str, dict[str, str]] = {}
    for rp in db.execute(select(RolePermission).where(RolePermission.tenant_id == p.tenant_id)).scalars():
        values.setdefault(rp.permission_code, {})[by_role[rp.role_id]] = rp.value
    in_matrix = {code for _, code, _ in MATRIX_ROWS}
    rows = [schemas.MatrixRowOut(label=label, permission=code, values=values.get(code, {}), in_05_matrix=code in in_matrix) for label, code, _ in ALL_PERMISSIONS]
    return schemas.MatrixOut(roles=[schemas.RoleOut.model_validate(r) for r in roles], rows=rows)


@router.put("/admin/roles/{role_code}/permissions", response_model=schemas.OkOut)
def set_role_permissions(role_code: str, body: schemas.RolePermissionsIn, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> schemas.OkOut:
    """Edit the matrix (05 §2: editable by Firm Admin) — step-up + reason, fully audited."""
    authorize(request, db, p, "users.manage")
    reason = require_stepup(request, p)
    role = _role(db, p.tenant_id, role_code)
    known = {code for _, code, _ in ALL_PERMISSIONS}
    for perm, value in body.values.items():
        if perm not in known or value not in STRENGTH or value == DENY:
            raise bad_request("invalid_matrix", f"invalid entry {perm}={value}")
    if role.code == "system_admin" and any(v for k, v in body.values.items() if k in {"finding.view", "file.download", "file.view"}):
        raise forbidden("System Admin cannot be given client-data permissions (E-SEC-03)", "sod_violation")
    before = {rp.permission_code: rp.value for rp in db.execute(select(RolePermission).where(RolePermission.role_id == role.id)).scalars()}
    for rp in db.execute(select(RolePermission).where(RolePermission.role_id == role.id)).scalars():
        db.delete(rp)
    db.flush()
    for perm, value in body.values.items():
        db.add(RolePermission(role_id=role.id, permission_code=perm, tenant_id=p.tenant_id, value=value))
    if body.download_allowed is not None:
        role.download_allowed = body.download_allowed
    audit.append(db, p.actor(), "role.permissions_changed", object_type="role", object_id=role.id, before=before, after=body.values,
                 details={"role": role.code, "reason": reason})
    db.commit()
    invalidate_matrix(p.tenant_id)
    return schemas.OkOut(message=f"permissions of {role.code} updated")


# ------------------------------------------------------------------ assignments


@router.get("/admin/assignments", response_model=list[schemas.RoleAssignmentOut])
def list_assignments(request: Request, client_id: str | None = None, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> list[schemas.RoleAssignmentOut]:
    authorize(request, db, p, "engagement.assign", client_id=client_id)
    stmt = (select(UserClientRole, Role, Client.name).join(Role, Role.id == UserClientRole.role_id)
            .outerjoin(Client, Client.id == UserClientRole.client_id)
            .where(UserClientRole.tenant_id == p.tenant_id, UserClientRole.revoked_at.is_(None)))
    if client_id:
        stmt = stmt.where(UserClientRole.client_id == client_id)
    out = []
    for a, r, cname in db.execute(stmt).all():
        out.append(schemas.RoleAssignmentOut(id=a.id, user_id=a.user_id, client_id=a.client_id, client_name=cname, role_code=r.code,
                                             role_name=r.name, expires_at=a.expires_at, revoked_at=a.revoked_at, created_at=a.created_at))
    return out


@router.post("/admin/assignments", response_model=schemas.RoleAssignmentOut, status_code=201)
def create_assignment(body: schemas.AssignmentIn, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> schemas.RoleAssignmentOut:
    role = _role(db, p.tenant_id, body.role_code)
    if role.scope == "tenant":
        authorize(request, db, p, "users.manage")
        require_stepup(request, p)
        client_id = None
    else:
        if not body.client_id:
            raise bad_request("client_required", "this role is assigned per client")
        if db.get(Client, body.client_id) is None:
            raise not_found("client")
        authorize(request, db, p, "engagement.assign", client_id=body.client_id)
        client_id = body.client_id
    user = db.get(User, body.user_id)
    if user is None or user.tenant_id != p.tenant_id:
        raise not_found("user")
    if role.is_client_role == user.is_firm_user:
        raise bad_request("role_mismatch", "client roles are for client users and firm roles for firm users")
    _check_rank(p, role.code, client_id, "grant")
    now = utcnow()
    dup = db.execute(select(UserClientRole).where(UserClientRole.user_id == user.id, UserClientRole.role_id == role.id,
                                                  UserClientRole.client_id.is_(None) if client_id is None else UserClientRole.client_id == client_id,
                                                  UserClientRole.revoked_at.is_(None),
                                                  or_(UserClientRole.expires_at.is_(None), UserClientRole.expires_at > now))).scalars().first()
    if dup is not None:
        raise conflict("already_assigned", "the user already holds this role here")
    a = assign(db, p.tenant_id, user, role, client_id, p.user_id)
    audit.append(db, p.actor(), "assignment.created", client_id=client_id, object_type="assignment", object_id=a.id,
                 details={"user_id": user.id, "role": role.code})
    db.commit()
    cname = db.get(Client, client_id).name if client_id else None  # type: ignore[union-attr]
    return schemas.RoleAssignmentOut(id=a.id, user_id=a.user_id, client_id=a.client_id, client_name=cname, role_code=role.code,
                                     role_name=role.name, expires_at=a.expires_at, revoked_at=None, created_at=a.created_at)


def _check_rank(p: Principal, role_code: str, client_id: str | None, verb: str) -> None:
    """Who may grant a role — and so revoke it (05 §2): Firm Admin only by a Firm Admin; Partner and Manager by a
    Firm Admin or a Partner of the client."""
    if role_code == "firm_admin" and not p.is_firm_admin:
        raise forbidden(f"only a Firm Admin can {verb} Firm Admin")
    if role_code in ("partner", "manager") and not (p.is_firm_admin or "partner" in p.roles_for(client_id)):
        raise forbidden(f"only a Firm Admin or a Partner can {'assign' if verb == 'grant' else verb} a {role_code.title()}")


@router.delete("/admin/assignments/{assignment_id}", response_model=schemas.OkOut)
def revoke_assignment(assignment_id: str, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> schemas.OkOut:
    a = db.get(UserClientRole, assignment_id)
    if a is None or a.tenant_id != p.tenant_id:
        raise not_found("assignment")
    if a.client_id is None:
        authorize(request, db, p, "users.manage")
        require_stepup(request, p)
    else:
        authorize(request, db, p, "engagement.assign", client_id=a.client_id)
    role = db.get(Role, a.role_id)
    _check_rank(p, role.code if role is not None else "", a.client_id, "revoke")
    if a.revoked_at is not None:
        return schemas.OkOut(message="assignment already revoked")  # keep the original revocation time and reason
    a.revoked_at = utcnow()
    a.revoked_reason = "revoked by administrator"
    audit.append(db, p.actor(), "assignment.revoked", client_id=a.client_id, object_type="assignment", object_id=a.id, details={"user_id": a.user_id})
    db.commit()
    return schemas.OkOut(message="assignment revoked")


# ------------------------------------------------------------------ JIT elevation


@router.post("/admin/elevations", response_model=schemas.ElevationOut, status_code=201)
def request_elevation(body: schemas.ElevationIn, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> Elevation:
    if not p.is_firm_user:
        raise forbidden("only firm users can request elevation")
    role = _role(db, p.tenant_id, body.role_code)
    if role.is_client_role:
        raise bad_request("invalid_role", "client roles cannot be elevated to")
    e = Elevation(tenant_id=p.tenant_id, user_id=p.user_id, role_id=role.id, client_id=body.client_id, reason=body.reason,
                  duration_minutes=body.duration_minutes, created_by=p.user_id)
    db.add(e)
    db.flush()
    audit.append(db, p.actor(), "elevation.requested", client_id=body.client_id, object_type="elevation", object_id=e.id,
                 details={"role": role.code, "minutes": body.duration_minutes, "reason": body.reason})
    db.commit()
    return e


@router.get("/admin/elevations", response_model=list[schemas.ElevationOut])
def list_elevations(request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> list[Elevation]:
    if p.is_firm_admin:
        return list(db.execute(select(Elevation).where(Elevation.tenant_id == p.tenant_id).order_by(Elevation.created_at.desc())).scalars())
    return list(db.execute(select(Elevation).where(Elevation.user_id == p.user_id).order_by(Elevation.created_at.desc())).scalars())


@router.post("/admin/elevations/{elevation_id}/approve", response_model=schemas.ElevationOut)
def approve_elevation(elevation_id: str, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> Elevation:
    """Time-boxed elevation approved by a *different* admin; alerts on use (E-SEC-03)."""
    authorize(request, db, p, "users.manage")
    require_stepup(request, p)
    e = db.get(Elevation, elevation_id)
    if e is None or e.tenant_id != p.tenant_id:
        raise not_found("elevation")
    if e.user_id == p.user_id:
        raise forbidden("segregation of duties: you cannot approve your own elevation", "segregation_of_duties")
    if e.status != "pending":
        raise conflict("not_pending", "elevation is not pending")
    user = db.get(User, e.user_id)
    role = db.get(Role, e.role_id)
    assert user is not None
    assert role is not None
    a = assign(db, p.tenant_id, user, role, e.client_id if role.scope == "client" else None, p.user_id)
    e.status = "active"
    e.approved_by = p.user_id
    e.approved_at = utcnow()
    e.expires_at = utcnow() + timedelta(minutes=e.duration_minutes)
    a.expires_at = e.expires_at
    e.assignment_id = a.id
    audit.append(db, p.actor(), "elevation.approved", client_id=e.client_id, object_type="elevation", object_id=e.id,
                 details={"user_id": e.user_id, "role": role.code, "expires_at": e.expires_at.isoformat(), "severity": "alert"})
    db.commit()
    return e


# ------------------------------------------------------------------ ABAC policies


@router.get("/admin/policies/{name}", response_model=list[schemas.PolicyOut])
def list_policies(name: str, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> list[Policy]:
    authorize(request, db, p, "users.manage" if name == "abac" else "ai.policy")
    return list(db.execute(select(Policy).where(Policy.tenant_id == p.tenant_id, Policy.name == name).order_by(Policy.version.desc())).scalars())


@router.put("/admin/policies/abac", response_model=schemas.PolicyOut)
def publish_abac(body: schemas.PolicyIn, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> Policy:
    """Publish a new ABAC policy version (policies are versioned data, E-SEC-02)."""
    authorize(request, db, p, "users.manage")
    reason = require_stepup(request, p)
    try:
        doc = PolicyDoc.parse(body.body)
    except ValueError as exc:
        raise bad_request("invalid_policy", str(exc)) from exc
    current = db.execute(select(Policy).where(Policy.tenant_id == p.tenant_id, Policy.name == "abac").order_by(Policy.version.desc())).scalars().first()
    version = (current.version if current else 0) + 1
    for old in db.execute(select(Policy).where(Policy.tenant_id == p.tenant_id, Policy.name == "abac", Policy.active.is_(True))).scalars():
        old.active = False
    pol = Policy(tenant_id=p.tenant_id, name="abac", version=version, body=body.body, active=True, approved_by=p.user_id, created_by=p.user_id)
    db.add(pol)
    audit.append(db, p.actor(), "policy.published", object_type="policy", object_id=f"abac:{version}", after=body.body,
                 details={"rules": len(doc.rules), "reason": reason})
    db.commit()
    return pol
