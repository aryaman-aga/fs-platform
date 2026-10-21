"""Engagement export packages ``.fspkg`` (07 §4, E-DATA-05).

Layout: ``FSPKG1`` | u32 header length | header JSON | u16 signature length |
Ed25519 signature | AES-256-GCM ciphertext of a zip (manifest + blobs).
The signature covers magic + header + ciphertext; the header is also the AEAD
associated data, so any tampering with either is detected. Imports verify the
signature against trusted keys, decrypt, re-hash every blob, re-verify every
exported audit event and land in a **quarantine** engagement; conflicting
changes against the live engagement are flagged for a Manager merge.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import struct
import zipfile
from dataclasses import dataclass
from typing import Any

from cryptography.exceptions import InvalidSignature, InvalidTag
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import select
from sqlalchemy.orm import Session

from fsweb import audit
from fsweb.db import new_id, utcnow
from fsweb.models import (
    AuditEvent,
    Client,
    Engagement,
    File,
    FileVersion,
    FindingRow,
    ReviewNote,
    ReviewNoteMsg,
    Run,
)
from fsweb.security.crypto import derive_key
from fsweb.services import workflow
from fsweb.services.common import store_artefact
from fsweb.settings import Settings
from fsweb.state import AppState
from fsweb.storage import object_key

MAGIC = b"FSPKG1"


class PackageError(ValueError):
    pass


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def signing_key(settings: Settings) -> Ed25519PrivateKey:
    seed = base64.b64decode(settings.pkg_signing_key) if settings.pkg_signing_key else derive_key(settings.secret_key, "fspkg-sign")
    return Ed25519PrivateKey.from_private_bytes(seed[:32])


def public_key_b64(settings: Settings) -> str:
    raw = signing_key(settings).public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return _b64(raw)


def trusted_keys(settings: Settings) -> set[str]:
    return {public_key_b64(settings), *settings.pkg_trusted_keys}


def package_key(settings: Settings, key_id: str | None = None) -> tuple[str, bytes]:
    keys = settings.master_keys()
    kid = key_id or list(keys)[-1]
    if kid not in keys:
        raise PackageError(f"this node does not hold package key {kid}")
    return kid, derive_key(base64.b64encode(keys[kid]).decode(), "fspkg-enc")


def _event_payload(e: AuditEvent) -> dict[str, Any]:
    return {**audit.payload_of(e), "prev_event_hash": e.prev_event_hash, "event_hash": e.event_hash}


def export_engagement(db: Session, state: AppState, eng: Engagement) -> bytes:
    files = list(db.execute(select(File).where(File.engagement_id == eng.id)).scalars())
    versions = list(db.execute(select(FileVersion).where(FileVersion.engagement_id == eng.id).order_by(FileVersion.file_id, FileVersion.version_no)).scalars())
    runs = list(db.execute(select(Run).where(Run.engagement_id == eng.id).order_by(Run.created_at)).scalars())
    findings = list(db.execute(select(FindingRow).where(FindingRow.engagement_id == eng.id)).scalars())
    notes = list(db.execute(select(ReviewNote).where(ReviewNote.engagement_id == eng.id)).scalars())
    events = list(db.execute(select(AuditEvent).where(AuditEvent.engagement_id == eng.id).order_by(AuditEvent.seq)).scalars())
    base = {f.id: (workflow.latest_version(db, f.id).id if workflow.latest_version(db, f.id) else None) for f in files}  # type: ignore[union-attr]
    manifest: dict[str, Any] = {
        "format": "fspkg/1",
        "exported_at": utcnow().isoformat(),
        "tenant_id": eng.tenant_id,
        "client_id": eng.client_id,
        "engagement": {"id": eng.id, "entity_id": eng.entity_id, "name": eng.name, "type": eng.type, "period_end": eng.period_end.isoformat(),
                       "status": eng.status, "periods_in_scope": eng.periods_in_scope_json, "version_line": eng.version_line},
        "base_version_ids": base,
        "files": [{"id": f.id, "kind": f.kind, "title": f.title} for f in files],
        "versions": [{"id": v.id, "file_id": v.file_id, "version_no": v.version_no, "version_line": v.version_line, "sha256": v.sha256, "size": v.size,
                      "content_type": v.content_type, "original_name": v.original_name, "parent_version_id": v.parent_version_id,
                      "produced_by_run_id": v.produced_by_run_id, "change_summary": v.change_summary_json, "created_at": v.created_at.isoformat(),
                      "blob": f"blobs/{v.sha256}"} for v in versions],
        "runs": [{"id": r.id, "kind": r.kind, "status": r.status, "input_version_ids": r.input_version_ids, "input_hashes": r.input_hashes,
                  "engine_version": r.engine_version, "rule_set_version": r.rule_set_version, "rule_set_hash": r.rule_set_hash, "config_hash": r.config_hash,
                  "params": r.params_json, "result": r.result_json, "output_version_id": r.output_version_id, "created_at": r.created_at.isoformat()} for r in runs],
        "findings": [{"run_id": f.run_id, "fid": f.fid, "fingerprint": f.fingerprint, "rule_id": f.rule_id, "rule_version": f.rule_version, "family": f.family,
                      "severity": f.severity, "certainty": f.certainty, "category": f.category, "sheet": f.sheet, "cells": f.cells, "caption": f.caption,
                      "printed": f.printed, "expected": f.expected, "diff": f.diff, "period": f.period, "evidence": f.evidence_json, "explanation": f.explanation,
                      "suggested_fix": f.suggested_fix, "fix_kind": f.fix_kind, "status": f.status, "status_reason": f.status_reason} for f in findings],
        "review_notes": [{"id": n.id, "target_type": n.target_type, "target_ref": n.target_ref, "text": n.text, "status": n.status, "audience": n.audience,
                          "raised_by": n.raised_by, "assigned_to": n.assigned_to, "cleared_by": n.cleared_by,
                          "cleared_at": n.cleared_at.isoformat() if n.cleared_at else None,
                          "messages": [{"user_id": m.user_id, "text": m.text, "created_at": m.created_at.isoformat()} for m in
                                       db.execute(select(ReviewNoteMsg).where(ReviewNoteMsg.review_note_id == n.id).order_by(ReviewNoteMsg.created_at)).scalars()]}
                         for n in notes],
        "audit_events": [_event_payload(e) for e in events],
    }
    zbuf = io.BytesIO()
    with zipfile.ZipFile(zbuf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(manifest, sort_keys=True, indent=1, default=str))
        seen: set[str] = set()
        for v in versions:
            if v.sha256 in seen:
                continue
            seen.add(v.sha256)
            z.writestr(f"blobs/{v.sha256}", state.storage.get_bytes(v.object_key))
    plain = zbuf.getvalue()
    kid, key = package_key(state.settings)
    nonce = os.urandom(12)
    header = {"v": 1, "alg": "AES-256-GCM/Ed25519", "engagement_id": eng.id, "tenant_id": eng.tenant_id, "client_id": eng.client_id,
              "created_at": utcnow().isoformat(), "nonce": _b64(nonce), "key_id": kid, "signer": public_key_b64(state.settings),
              "sha256_plain": hashlib.sha256(plain).hexdigest()}
    hbytes = json.dumps(header, sort_keys=True, separators=(",", ":")).encode()
    ct = AESGCM(key).encrypt(nonce, plain, hbytes)
    sig = signing_key(state.settings).sign(MAGIC + hbytes + ct)
    return MAGIC + struct.pack(">I", len(hbytes)) + hbytes + struct.pack(">H", len(sig)) + sig + ct


@dataclass
class Package:
    header: dict[str, Any]
    manifest: dict[str, Any]
    blobs: dict[str, bytes]


def read_package(data: bytes, settings: Settings) -> Package:
    if not data.startswith(MAGIC) or len(data) < len(MAGIC) + 6:
        raise PackageError("not an FS-Platform package")
    off = len(MAGIC)
    (hlen,) = struct.unpack(">I", data[off : off + 4])
    off += 4
    hbytes = data[off : off + hlen]
    off += hlen
    (slen,) = struct.unpack(">H", data[off : off + 2])
    off += 2
    sig = data[off : off + slen]
    off += slen
    ct = data[off:]
    try:
        header = json.loads(hbytes)
    except ValueError as exc:
        raise PackageError("corrupt header") from exc
    signer = str(header.get("signer", ""))
    if signer not in trusted_keys(settings):
        raise PackageError("package signed by an untrusted key")
    try:
        Ed25519PublicKey.from_public_bytes(base64.b64decode(signer)).verify(sig, MAGIC + hbytes + ct)
    except (InvalidSignature, ValueError) as exc:
        raise PackageError("signature verification failed (package tampered)") from exc
    _, key = package_key(settings, header.get("key_id"))
    try:
        plain = AESGCM(key).decrypt(base64.b64decode(header["nonce"]), ct, hbytes)
    except InvalidTag as exc:
        raise PackageError("decryption failed (package tampered or wrong key)") from exc
    if hashlib.sha256(plain).hexdigest() != header.get("sha256_plain"):
        raise PackageError("content hash mismatch")
    blobs: dict[str, bytes] = {}
    with zipfile.ZipFile(io.BytesIO(plain)) as z:
        manifest = json.loads(z.read("manifest.json"))
        for name in z.namelist():
            if name.startswith("blobs/"):
                b = z.read(name)
                if hashlib.sha256(b).hexdigest() != name.split("/", 1)[1]:
                    raise PackageError(f"blob {name} does not match its hash")
                blobs[name.split("/", 1)[1]] = b
    for v in manifest.get("versions", []):
        if v["sha256"] not in blobs:
            raise PackageError(f"version {v['id']} is missing its content")
    for ev in manifest.get("audit_events", []):
        payload = {k: v for k, v in ev.items() if k not in {"prev_event_hash", "event_hash"}}
        if audit.compute_hash(ev["prev_event_hash"], payload) != ev["event_hash"]:
            raise PackageError(f"audit event seq {ev.get('seq')} fails hash verification")
    return Package(header, manifest, blobs)


def import_engagement(db: Session, state: AppState, data: bytes, actor: audit.Actor, *, tenant_id: str) -> Engagement:
    pkg = read_package(data, state.settings)
    m = pkg.manifest
    if m.get("tenant_id") != tenant_id:
        raise PackageError("the package belongs to another firm (tenant)")
    client = db.get(Client, m["client_id"])
    if client is None:
        raise PackageError("the package's client does not exist here")
    src = m["engagement"]
    from fsweb.models import Entity

    if db.get(Entity, src["entity_id"]) is None:
        raise PackageError("the package's entity does not exist here; create it first")
    live = db.get(Engagement, src["id"])
    conflict = False
    if live is not None:
        for fid, base_vid in (m.get("base_version_ids") or {}).items():
            latest = workflow.latest_version(db, fid)
            if latest is not None and base_vid is not None and latest.id != base_vid:
                conflict = True
    from datetime import date

    eng = Engagement(tenant_id=tenant_id, client_id=client.id, entity_id=src["entity_id"], name=f"{src['name']} (restored {utcnow():%d-%b-%Y})",
                     type=src["type"], period_end=date.fromisoformat(src["period_end"]), periods_in_scope_json=src.get("periods_in_scope") or [],
                     status=src["status"] if src["status"] in workflow.STATES else "CHECKED", is_quarantine=True, merge_required=conflict,
                     source_engagement_id=src["id"], version_line=int(src.get("version_line", 1)), created_by=actor.user_id)
    db.add(eng)
    db.flush()
    file_map: dict[str, str] = {}
    for f in m.get("files", []):
        nf = File(tenant_id=tenant_id, client_id=client.id, engagement_id=eng.id, kind=f["kind"], title=f["title"], created_by=actor.user_id)
        db.add(nf)
        db.flush()
        file_map[f["id"]] = nf.id
    ver_map: dict[str, str] = {}
    for v in m.get("versions", []):
        key = object_key(tenant_id, client.id, eng.id, "files", file_map[v["file_id"]], f"v{v['version_no']}-{v['sha256'][:16]}{os.path.splitext(v['original_name'])[1] or '.bin'}")
        state.storage.put_bytes(key, pkg.blobs[v["sha256"]], v["content_type"])
        nv = FileVersion(tenant_id=tenant_id, client_id=client.id, engagement_id=eng.id, file_id=file_map[v["file_id"]], version_no=v["version_no"],
                         version_line=v.get("version_line", 1), object_key=key, sha256=v["sha256"], size=v["size"], content_type=v["content_type"],
                         original_name=v["original_name"], parent_version_id=ver_map.get(v.get("parent_version_id") or ""),
                         change_summary_json={**(v.get("change_summary") or {}), "imported_from_version": v["id"]}, created_by=actor.user_id)
        db.add(nv)
        db.flush()
        ver_map[v["id"]] = nv.id
    run_map: dict[str, str] = {}
    for r in m.get("runs", []):
        nr = Run(id=new_id(), tenant_id=tenant_id, client_id=client.id, engagement_id=eng.id, kind=r["kind"], status=r["status"],
                 input_version_ids=[ver_map.get(x, x) for x in r.get("input_version_ids") or []], input_hashes=r.get("input_hashes") or [],
                 engine_version=r.get("engine_version"), rule_set_version=r.get("rule_set_version"), rule_set_hash=r.get("rule_set_hash"),
                 config_hash=r.get("config_hash"), params_json={**(r.get("params") or {}), "imported_from_run": r["id"]}, result_json=r.get("result") or {},
                 output_version_id=ver_map.get(r.get("output_version_id") or ""), created_by=actor.user_id)
        db.add(nr)
        run_map[r["id"]] = nr.id
    db.flush()
    for f in m.get("findings", []):
        if f["run_id"] not in run_map:
            continue
        db.add(FindingRow(tenant_id=tenant_id, client_id=client.id, engagement_id=eng.id, run_id=run_map[f["run_id"]], fid=f["fid"], fingerprint=f["fingerprint"],
                          rule_id=f["rule_id"], rule_version=f["rule_version"], family=f["family"], severity=f["severity"], certainty=f["certainty"],
                          category=f["category"], sheet=f["sheet"], cells=f["cells"], caption=f["caption"], printed=f["printed"], expected=f["expected"],
                          diff=f["diff"], period=f["period"], evidence_json=f["evidence"], explanation=f["explanation"], suggested_fix=f["suggested_fix"],
                          fix_kind=f["fix_kind"], status=f["status"], status_reason=f["status_reason"], created_by=actor.user_id))
    for n in m.get("review_notes", []):
        nn = ReviewNote(tenant_id=tenant_id, client_id=client.id, engagement_id=eng.id, target_type=n["target_type"], target_ref=n["target_ref"], text=n["text"],
                        status=n["status"], audience=n["audience"], raised_by=n["raised_by"], assigned_to=n["assigned_to"], cleared_by=n["cleared_by"],
                        created_by=actor.user_id)
        db.add(nn)
        db.flush()
        for msg in n.get("messages", []):
            db.add(ReviewNoteMsg(tenant_id=tenant_id, client_id=client.id, review_note_id=nn.id, user_id=msg["user_id"], text=msg["text"], created_by=actor.user_id))
    import_run = Run(id=new_id(), tenant_id=tenant_id, client_id=client.id, engagement_id=eng.id, kind="import", status="succeeded",
                     input_hashes=[pkg.header["sha256_plain"]], params_json={"source_engagement_id": src["id"]}, created_by=actor.user_id,
                     started_at=utcnow(), finished_at=utcnow())
    db.add(import_run)
    db.flush()
    store_artefact(db, state.storage, import_run, "report", "imported_audit_events.json",
                   json.dumps(m.get("audit_events", []), indent=1).encode(), "application/json")
    audit.append(db, actor, "engagement.imported", client_id=client.id, engagement_id=eng.id, object_type="engagement", object_id=eng.id,
                 after=pkg.header["sha256_plain"], details={"source_engagement_id": src["id"], "versions": len(ver_map), "merge_required": conflict,
                                                            "signer": pkg.header.get("signer")})
    return eng


def version_hashes(db: Session, engagement_id: str) -> list[str]:
    return sorted(v.sha256 for v in db.execute(select(FileVersion).where(FileVersion.engagement_id == engagement_id)).scalars())

