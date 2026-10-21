"""Engagement-level services: configuration, checklist seeding, the data-change rule."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from fscore.config import EngagementConfig, PeriodSpec, dump_config
from fsweb import audit
from fsweb.models import (
    Client,
    DisclosureChecklistItem,
    Engagement,
    EngagementConfigRow,
    Entity,
    FileVersion,
    Run,
    Signatory,
)
from fsweb.services import workflow
from fsweb.services.common import create_run
from fsweb.services.seed import DISCLOSURE_ITEMS

if TYPE_CHECKING:
    from fsweb.security.crypto import FieldCipher


def default_config(db: Session, eng: Engagement) -> EngagementConfig:
    client = db.get(Client, eng.client_id)
    entity = db.get(Entity, eng.entity_id)
    periods = [PeriodSpec.model_validate(p) for p in (eng.periods_in_scope_json or [])]
    units = "millions" if eng.type == "spfs" else "lakhs"
    return EngagementConfig(
        client=client.name if client else "Client",
        entity=entity.name if entity else None,
        year_end=eng.period_end,
        period_type=eng.type,  # type: ignore[arg-type]
        units=units,  # type: ignore[arg-type]
        functional_currency=entity.functional_ccy if entity else None,
        periods_in_scope=periods,
    )


def config_for(db: Session, eng: Engagement) -> EngagementConfig:
    row = latest_config_row(db, eng)
    if row is None:
        return default_config(db, eng)
    data: Any = yaml.safe_load(row.yaml_text) or {}
    return EngagementConfig.model_validate(data)


def signatory_master(db: Session, cipher: FieldCipher, eng: Engagement) -> list[dict[str, str]]:
    """The entity's signatories still in office at the period end, as the engine's master file (control C8)."""
    out: list[dict[str, str]] = []
    for s in db.execute(select(Signatory).where(Signatory.entity_id == eng.entity_id).order_by(Signatory.name)).scalars():
        if s.valid_to is not None and s.valid_to < eng.period_end:
            continue
        rec = {"name": s.name, "role": s.role}
        din = cipher.decrypt(db, eng.tenant_id, s.din_enc)
        if din:
            rec["din"] = din
        if s.membership_no:
            rec["membership_no"] = s.membership_no
        if s.frn:
            rec["frn"] = s.frn
        out.append(rec)
    return out


def latest_config_row(db: Session, eng: Engagement) -> EngagementConfigRow | None:
    return db.execute(
        select(EngagementConfigRow).where(EngagementConfigRow.engagement_id == eng.id).order_by(EngagementConfigRow.version.desc())
    ).scalars().first()


def save_config(db: Session, eng: Engagement, yaml_text: str, created_by: str) -> EngagementConfigRow:
    data: Any = yaml.safe_load(yaml_text) or {}
    EngagementConfig.model_validate(data)  # raises on invalid configuration
    prev = latest_config_row(db, eng)
    row = EngagementConfigRow(tenant_id=eng.tenant_id, client_id=eng.client_id, engagement_id=eng.id, yaml_text=yaml_text,
                              version=(prev.version + 1) if prev else 1, created_by=created_by)
    db.add(row)
    db.flush()
    return row


def seed_engagement(db: Session, eng: Engagement, created_by: str) -> None:
    for code, question in DISCLOSURE_ITEMS:
        db.add(DisclosureChecklistItem(tenant_id=eng.tenant_id, client_id=eng.client_id, engagement_id=eng.id, item_code=code,
                                       question=question, created_by=created_by))
    save_config(db, eng, dump_config(default_config(db, eng)), created_by)


def data_changed(db: Session, eng: Engagement, actor: audit.Actor, new_version: FileVersion | None, *, reason: str) -> Run | None:
    """06 §1: any data change after FIXED_VERIFIED → CHECKED, and checks re-run automatically."""
    if eng.status not in workflow.POST_VERIFY:
        return None
    workflow.set_status(db, eng, "CHECKED", actor, reason=reason)
    version = new_version or workflow.latest_workbook_version(db, eng)
    if version is None:
        return None
    run, _ = create_run(db, eng, "check", created_by=actor.user_id or "system", input_versions=[version],
                        params={"auto": True, "reason": reason}, correlation_id=actor.correlation_id, actor=actor)
    return run
