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


def test_local_storage_is_byte_exact(tmp_path: Path) -> None:
    """Stored files come back byte for byte — on Windows a descriptor opened without O_BINARY would turn \\n into \\r\\n."""
    store = LocalStorage(tmp_path / "objects")
    payload = b"PK\x03\x04\n\r\n\x1a\x00" * 1000 + bytes(range(256))
    key = object_key("t1", "c1", "e1", "files", "book.xlsx")
    assert store.put_bytes(key, payload).size == len(payload)
    assert store.get_bytes(key) == payload
    src = tmp_path / "upload.bin"
    src.write_bytes(payload)
    key2 = object_key("t1", "c1", "e1", "files", "copy.bin")
    store.put_file(key2, src)
    assert store.download_to(key2, tmp_path / "out" / "copy.bin").read_bytes() == payload


def test_native_path_uses_extended_length_form_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    from fsweb import storage

    assert storage.native_path(Path("/var/objects/a")) == Path("/var/objects/a")  # unchanged elsewhere
    monkeypatch.setattr(storage.os, "name", "nt")
    assert str(storage.native_path(Path("C:\\ProgramData\\FSPlatform\\objects\\t"))) == "\\\\?\\C:\\ProgramData\\FSPlatform\\objects\\t"
    assert str(storage.native_path(Path("\\\\server\\share\\objects"))) == "\\\\?\\UNC\\server\\share\\objects"
    already = Path("\\\\?\\C:\\x")
    assert storage.native_path(already) == already


@pytest.mark.skipif(os.name != "nt", reason="MAX_PATH applies to Windows only")
def test_deep_object_keys_beyond_max_path_on_windows(tmp_path: Path) -> None:
    store = LocalStorage(tmp_path / "objects")
    ids = ["0" * 8 + "-1111-2222-3333-" + "4" * 12] * 4
    key = object_key(ids[0], ids[1], ids[2], "runs", ids[3], "register-0123456789ab-Exceptions register with a long name.xlsx")
    assert len(str(tmp_path / "objects" / key)) > 260
    store.put_bytes(key, b"data\n")
    assert store.get_bytes(key) == b"data\n" and store.exists(key)


def test_clamd_over_tcp(tmp_path: Path) -> None:
    """Windows has no Unix sockets: clamd is reached over tcp://host:port."""
    import socket
    import struct
    import threading

    from fsweb.services.uploads import UploadRejected, clamav_scan

    server = socket.create_server(("127.0.0.1", 0))
    port = server.getsockname()[1]
    received: list[bytes] = []

    def fake_clamd() -> None:
        conn, _ = server.accept()
        with conn:
            buf = b""
            while not buf.endswith(struct.pack("!L", 0)):
                buf += conn.recv(65536)
            received.append(buf)
            conn.sendall(b"stream: Eicar-Test-Signature FOUND\0")

    t = threading.Thread(target=fake_clamd, daemon=True)
    t.start()
    assert clamav_scan(f"tcp://127.0.0.1:{port}", b"X5O!P%@AP") == "Eicar-Test-Signature"
    t.join(5)
    server.close()
    assert received and received[0].startswith(b"zINSTREAM\0")
    with pytest.raises(UploadRejected) as exc:
        clamav_scan(f"tcp://127.0.0.1:{port}", b"data", timeout=1.0)  # nothing listens any more
    assert exc.value.code == "scanner_unavailable"


@pytest.mark.parametrize("bad", ["t/1/c/2/e/3/files/x.xlsx:stream", "t/1/c/2/e/3/files/CON", "t/1/c/2/e/3/files/nul.txt",
                                 "t/1/c/2/e/3/files/name.", "t/1/c/2/e/3/files/name ", "t/1/c/2/e/3/files/a|b", "t/1//x"])
def test_keys_that_windows_cannot_store_are_refused(tmp_path: Path, bad: str) -> None:
    with pytest.raises(StorageError):
        LocalStorage(tmp_path / "objects").put_bytes(bad, b"x")


def test_object_key_makes_names_portable() -> None:
    from fsweb.storage import safe_segment

    assert object_key("t", "c", "e", "files", "FS: FY26?.xlsx") == "t/t/c/c/e/e/files/FS_ FY26_.xlsx"
    assert safe_segment("CON") == "_CON" and safe_segment("report. ") == "report" and safe_segment("...") == "_"
