"""Rule versions in use per tenant (E-RULE-01, E-ITGC-02).

Every check run records the engine's rule-set version and hash. The first run that uses a rule
version registers it here, closing the rule's previous version, so the evidence pack can show which
version of each rule was in force when, and the approved change record (with its golden-regression
evidence) that brought a rule change in.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from fsweb.db import utcnow
from fsweb.models import Release, RuleRow, RuleVersion, Run


def record_rule_versions(db: Session, run: Run, rules: list[dict[str, Any]], rule_set_version: str, rule_set_hash: str) -> int:
    """Register the rule versions ``run`` used that the tenant has not seen yet (a new version or a changed
    definition of a rule); returns how many were added."""
    rows = {r.code: r for r in db.execute(select(RuleRow).where(RuleRow.tenant_id == run.tenant_id)).scalars()}
    current: dict[str, RuleVersion] = {}
    for v in db.execute(select(RuleVersion).where(RuleVersion.tenant_id == run.tenant_id, RuleVersion.effective_to.is_(None))).scalars():
        current[v.rule_id] = v
    change = db.execute(
        select(Release).where(Release.tenant_id == run.tenant_id, Release.rule_change.is_(True), Release.approver_id.is_not(None))
        .order_by(Release.approved_at.desc())
    ).scalars().first()
    today = utcnow().date()
    added = 0
    for rule in rules:
        code = str(rule.get("id") or "")
        if not code:
            continue
        row = rows.get(code)
        if row is None:
            row = RuleRow(tenant_id=run.tenant_id, code=code, family=str(rule.get("family") or code.split("-")[0]), created_by=run.created_by)
            db.add(row)
            db.flush()
            rows[code] = row
        version = str(rule.get("version") or "1")
        definition = hashlib.sha256(json.dumps(rule, sort_keys=True, default=str).encode("utf-8")).hexdigest()
        prev = current.get(row.id)
        if prev is not None and prev.version == version and (prev.params_json or {}).get("definition_sha256") == definition:
            continue
        if prev is not None:
            prev.effective_to = today
        db.add(RuleVersion(
            tenant_id=run.tenant_id, rule_id=row.id, version=version, severity=str(rule.get("severity") or ""),
            params_json={"definition_sha256": definition, "rule_set_version": rule_set_version, "rule_set_hash": rule_set_hash,
                         "first_run_id": run.id},
            frameworks=list(rule.get("frameworks") or []), effective_from=today,
            # a rule change reaches production through an approved change record carrying regression evidence
            approved_by=change.approver_id if change is not None else None,
            regression_run_id=change.id if change is not None else None, created_by=run.created_by,
        ))
        added += 1
    return added
