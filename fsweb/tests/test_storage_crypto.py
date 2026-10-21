"""Object storage (local + S3/MinIO via moto) and envelope encryption with key rotation (E-SEC-07)."""

from __future__ import annotations

import base64
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import boto3
import pytest
from moto import mock_aws
from sqlalchemy import select

from fsweb.db import SYSTEM
from fsweb.models import Client, DataKey
from fsweb.security.crypto import FieldCipher, LocalKMS
from fsweb.storage import LocalStorage, S3Storage, StorageError, make_storage, object_key
from tests.conftest import World


def test_local_storage_is_immutable_and_worm(tmp_path: Path) -> None:
    st = LocalStorage(tmp_path / "objects")
    obj = st.put_bytes("t/a/file.bin", b"hello")
    assert obj.sha256 == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824" and st.get_bytes("t/a/file.bin") == b"hello"
    with pytest.raises(StorageError):
        st.put_bytes("t/a/file.bin", b"again")
    with pytest.raises(StorageError):
        st.put_bytes("../escape", b"x")
    st.set_worm("t/a/file.bin", datetime.now(UTC) + timedelta(days=1))
    with pytest.raises(StorageError, match="WORM"):
        st.delete("t/a/file.bin")
    st.put_bytes("t/a/other.bin", b"x")
    st.delete("t/a/other.bin")
    assert not st.exists("t/a/other.bin") and st.health()


@mock_aws
def test_s3_storage_with_versioning_and_object_lock() -> None:
    client = boto3.client("s3", region_name="ap-south-1")
    st = S3Storage("fs-objects", client=client)
    st.ensure_bucket(object_lock=True)
    obj = st.put_bytes("t/1/c/2/e/3/files/v1.xlsx", b"workbook-bytes")
    assert obj.version_id and st.exists("t/1/c/2/e/3/files/v1.xlsx") and st.get_bytes("t/1/c/2/e/3/files/v1.xlsx") == b"workbook-bytes"
    with pytest.raises(StorageError):
        st.put_bytes("t/1/c/2/e/3/files/v1.xlsx", b"overwrite")
    until = datetime.now(UTC) + timedelta(days=365 * 7)
    st.set_worm("t/1/c/2/e/3/files/v1.xlsx", until)
    got = st.worm_until("t/1/c/2/e/3/files/v1.xlsx")
    assert got is not None and got.year == until.year
    with pytest.raises(StorageError, match="WORM"):
        st.delete("t/1/c/2/e/3/files/v1.xlsx")
    assert st.health()
    head = client.head_object(Bucket="fs-objects", Key="t/1/c/2/e/3/files/v1.xlsx")
    assert head["ServerSideEncryption"] == "AES256" and head["Metadata"]["sha256"] == obj.sha256


def test_make_storage_and_keys(tmp_path: Path) -> None:
    assert isinstance(make_storage(f"local:{tmp_path}"), LocalStorage)
    with pytest.raises(StorageError):
        make_storage("ftp://nope")
    assert object_key("t", "c", "e", "files", "a/b") == "t/t/c/c/e/e/files/a_b"


def test_field_encryption_round_trip(world: World) -> None:
    with world.db() as db:
        c = db.execute(select(Client).where(Client.name == "Alpha Synthetic Ltd")).scalar_one()
        assert c.pan_enc and "AAACA1234A" not in c.pan_enc
        assert world.state.cipher.decrypt(db, world.tenant_id, c.pan_enc) == "AAACA1234A"
        assert FieldCipher.mask("AAACA1234A") == "••••••234A"


def test_kek_rotation_without_downtime(world: World) -> None:
    old_keys = world.state.settings.master_keys()
    new_kek = os.urandom(32)
    rotated = LocalKMS({**old_keys, "k2": new_kek})
    cipher2 = FieldCipher(rotated)
    with world.state.db.owner_session(SYSTEM) as db:
        token = world.state.cipher.encrypt(db, world.tenant_id, "U72900KA2015PTC012345")
        db.commit()
        assert cipher2.rotate_kek(db) >= 1
        db.commit()
        assert {k.kek_id for k in db.execute(select(DataKey)).scalars()} == {"k2"}
        only_new = FieldCipher(LocalKMS({"k2": new_kek}))  # the old KEK can now be retired
        assert only_new.decrypt(db, world.tenant_id, token) == "U72900KA2015PTC012345"
        new_dek = only_new.rotate_dek(db, world.tenant_id)
        token2 = only_new.encrypt(db, world.tenant_id, "07654321")
        assert new_dek in token2 and only_new.decrypt(db, world.tenant_id, token) == "U72900KA2015PTC012345"
        db.commit()


def test_settings_master_key_parsing() -> None:
    from fsweb.settings import Settings

    k1, k2 = base64.b64encode(os.urandom(32)).decode(), base64.b64encode(os.urandom(32)).decode()
    s = Settings(kms_master_key=f"old:{k1},new:{k2}")
    assert list(s.master_keys()) == ["old", "new"]
    with pytest.raises(RuntimeError):
        Settings(kms_master_key=base64.b64encode(b"short").decode()).master_keys()
