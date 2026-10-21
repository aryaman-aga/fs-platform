"""Client, entity and signatory master data (08 §3 screen 3). Identifiers are
field-level encrypted (E-SEC-07) and only shown masked."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from fsweb import audit, schemas
from fsweb.authz.deps import Principal, authorize, check_if_match, etag, get_db, get_state, require_principal
from fsweb.errors import bad_request, not_found
from fsweb.models import Client, Entity, Role, Signatory, User
from fsweb.services.seed import assign

router = APIRouter(tags=["clients"])


def client_out(request: Request, db: Session, c: Client) -> schemas.ClientOut:
    cipher = get_state(request).cipher
    pan = cipher.decrypt(db, c.tenant_id, c.pan_enc)
    cin = cipher.decrypt(db, c.tenant_id, c.cin_enc)
    return schemas.ClientOut(id=c.id, name=c.name, pan_masked=cipher.mask(pan), cin_masked=cipher.mask(cin, 6), group_name=c.group_name,
                             status=c.status, office=c.office, classification=c.classification, version=c.version, created_at=c.created_at)


def visible_client_ids(p: Principal) -> list[str] | None:
    """None = all clients of the tenant (Firm Admin)."""
    return None if p.is_firm_admin else p.client_ids


@router.get("/clients", response_model=list[schemas.ClientOut])
def list_clients(request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> list[schemas.ClientOut]:
    stmt = select(Client).where(Client.tenant_id == p.tenant_id).order_by(Client.name)
    ids = visible_client_ids(p)
    if ids is not None:
        stmt = stmt.where(Client.id.in_(ids))
    return [client_out(request, db, c) for c in db.execute(stmt).scalars()]


@router.post("/clients", response_model=schemas.ClientOut, status_code=201)
def create_client(body: schemas.ClientIn, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> schemas.ClientOut:
    authorize(request, db, p, "client.manage")
    cipher = get_state(request).cipher
    c = Client(tenant_id=p.tenant_id, name=body.name, group_name=body.group_name, office=body.office, classification=body.classification,
               created_by=p.user_id)
    c.pan_enc = cipher.encrypt(db, p.tenant_id, body.pan)
    c.cin_enc = cipher.encrypt(db, p.tenant_id, body.cin)
    db.add(c)
    db.flush()
    # The creating Partner / Manager is assigned to the new client so they can work on it.
    if not p.is_firm_admin:
        mine = p.all_roles & {"partner", "manager"}
        if mine:
            code = "partner" if "partner" in mine else "manager"
            role = db.execute(select(Role).where(Role.tenant_id == p.tenant_id, Role.code == code)).scalar_one()
            user = db.get(User, p.user_id)
            assert user is not None
            assign(db, p.tenant_id, user, role, c.id, p.user_id)
    audit.append(db, p.actor(), "client.created", client_id=c.id, object_type="client", object_id=c.id, details={"office": c.office})
    db.commit()
    return client_out(request, db, c)


def _client(request: Request, db: Session, p: Principal, client_id: str) -> Client:
    c = db.get(Client, client_id)
    if c is None or c.tenant_id != p.tenant_id or (not p.is_firm_admin and not p.is_member(client_id)):
        raise not_found("client")
    return c


@router.get("/clients/{client_id}", response_model=schemas.ClientOut)
def get_client(client_id: str, request: Request, response: Response, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> schemas.ClientOut:
    c = _client(request, db, p, client_id)
    response.headers["ETag"] = etag(c.version)
    return client_out(request, db, c)


@router.patch("/clients/{client_id}", response_model=schemas.ClientOut)
def patch_client(client_id: str, body: schemas.ClientPatchIn, request: Request, response: Response, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> schemas.ClientOut:
    c = _client(request, db, p, client_id)
    authorize(request, db, p, "client.manage", client_id=c.id)
    check_if_match(request, c.version)
    before = {"name": c.name, "group_name": c.group_name, "office": c.office, "status": c.status, "classification": c.classification}
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(c, k, v)
    audit.append(db, p.actor(), "client.updated", client_id=c.id, object_type="client", object_id=c.id, before=before, after=body.model_dump(exclude_none=True))
    db.commit()
    response.headers["ETag"] = etag(c.version)
    return client_out(request, db, c)


@router.get("/clients/{client_id}/entities", response_model=list[schemas.EntityOut])
def list_entities(client_id: str, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> list[Entity]:
    _client(request, db, p, client_id)
    return list(db.execute(select(Entity).where(Entity.client_id == client_id).order_by(Entity.name)).scalars())


@router.post("/clients/{client_id}/entities", response_model=schemas.EntityOut, status_code=201)
def create_entity(client_id: str, body: schemas.EntityIn, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> Entity:
    c = _client(request, db, p, client_id)
    authorize(request, db, p, "client.manage", client_id=c.id)
    if body.parent_entity_id:
        parent = db.get(Entity, body.parent_entity_id)
        if parent is None or parent.client_id != c.id:
            raise bad_request("invalid_parent", "parent entity must belong to the same client")
    e = Entity(tenant_id=p.tenant_id, client_id=c.id, created_by=p.user_id, **body.model_dump())
    db.add(e)
    db.flush()
    audit.append(db, p.actor(), "entity.created", client_id=c.id, object_type="entity", object_id=e.id)
    db.commit()
    return e


def _entity(request: Request, db: Session, p: Principal, entity_id: str) -> Entity:
    e = db.get(Entity, entity_id)
    if e is None or e.tenant_id != p.tenant_id:
        raise not_found("entity")
    _client(request, db, p, e.client_id)
    return e


def signatory_out(request: Request, db: Session, s: Signatory) -> schemas.SignatoryOut:
    cipher = get_state(request).cipher
    din = cipher.decrypt(db, s.tenant_id, s.din_enc)
    return schemas.SignatoryOut(id=s.id, entity_id=s.entity_id, name=s.name, role=s.role, din_masked=cipher.mask(din), membership_no=s.membership_no,
                                frn=s.frn, valid_from=s.valid_from, valid_to=s.valid_to)


@router.get("/entities/{entity_id}/signatories", response_model=list[schemas.SignatoryOut])
def list_signatories(entity_id: str, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> list[schemas.SignatoryOut]:
    e = _entity(request, db, p, entity_id)
    return [signatory_out(request, db, s) for s in db.execute(select(Signatory).where(Signatory.entity_id == e.id).order_by(Signatory.name)).scalars()]


@router.post("/entities/{entity_id}/signatories", response_model=schemas.SignatoryOut, status_code=201)
def create_signatory(entity_id: str, body: schemas.SignatoryIn, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> schemas.SignatoryOut:
    e = _entity(request, db, p, entity_id)
    authorize(request, db, p, "client.manage", client_id=e.client_id)
    cipher = get_state(request).cipher
    s = Signatory(tenant_id=p.tenant_id, client_id=e.client_id, entity_id=e.id, name=body.name, role=body.role,
                  membership_no=body.membership_no, frn=body.frn, valid_from=body.valid_from, valid_to=body.valid_to, created_by=p.user_id)
    s.din_enc = cipher.encrypt(db, p.tenant_id, body.din)
    db.add(s)
    db.flush()
    audit.append(db, p.actor(), "signatory.created", client_id=e.client_id, object_type="signatory", object_id=s.id)
    db.commit()
    return signatory_out(request, db, s)


@router.delete("/signatories/{signatory_id}", response_model=schemas.OkOut)
def delete_signatory(signatory_id: str, request: Request, p: Principal = Depends(require_principal), db: Session = Depends(get_db)) -> schemas.OkOut:
    s = db.get(Signatory, signatory_id)
    if s is None or s.tenant_id != p.tenant_id:
        raise not_found("signatory")
    _client(request, db, p, s.client_id)
    authorize(request, db, p, "client.manage", client_id=s.client_id)
    from fsweb.db import utcnow

    # Signatories are end-dated, never deleted (history of who signed what is kept).
    s.valid_to = utcnow().date()
    audit.append(db, p.actor(), "signatory.end_dated", client_id=s.client_id, object_type="signatory", object_id=s.id)
    db.commit()
    return schemas.OkOut(message="signatory end-dated")
