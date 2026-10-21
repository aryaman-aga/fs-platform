"""Object storage for workbooks, PDFs, reports and payloads (C1: immutable versions).

* ``local:/path`` – filesystem (single node, offline edition, tests);
* ``s3://bucket`` – S3 / MinIO with versioning and object lock (WORM) for finals.

Keys are never overwritten: a put to an existing key is refused, so every stored
version is immutable. WORM retention blocks deletion until it expires.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO, Protocol


class StorageError(RuntimeError):
    pass


@dataclass(frozen=True)
class StoredObject:
    key: str
    sha256: str
    size: int
    version_id: str | None = None


class Storage(Protocol):
    kind: str

    def put_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> StoredObject: ...

    def put_file(self, key: str, path: Path, content_type: str = "application/octet-stream") -> StoredObject: ...

    def get_bytes(self, key: str) -> bytes: ...

    def download_to(self, key: str, dest: Path) -> Path: ...

    def exists(self, key: str) -> bool: ...

    def set_worm(self, key: str, retain_until: datetime) -> None: ...

    def worm_until(self, key: str) -> datetime | None: ...

    def delete(self, key: str) -> None: ...

    def health(self) -> bool: ...


# Names Windows reserves for devices (in any case, with or without an extension) and characters it cannot store in a
# file name; a ":" would also address a hidden NTFS alternate data stream.
_WIN_RESERVED = re.compile(r"^(con|prn|aux|nul|com[0-9]|lpt[0-9])(\..*)?$", re.I)
_WIN_INVALID = re.compile(r'[<>:"|?*\x00-\x1f]')


def _check_key(key: str) -> str:
    if not key or key.startswith("/") or ".." in key.split("/") or "\\" in key:
        raise StorageError(f"invalid object key: {key!r}")
    for seg in key.split("/"):
        if not seg or _WIN_INVALID.search(seg) or _WIN_RESERVED.match(seg) or seg[-1] in ". ":
            raise StorageError(f"invalid object key: {key!r}")
    return key


def safe_segment(name: str) -> str:
    """A key segment that is a valid file name on every platform (uploads keep their name in the version record)."""
    out = _WIN_INVALID.sub("_", name.replace("/", "_").replace("\\", "_")).rstrip(". ") or "_"
    return f"_{out}" if _WIN_RESERVED.match(out) else out


# On Windows a descriptor from os.open is in text mode unless O_BINARY is set: every \n byte written would become \r\n.
O_BINARY = getattr(os, "O_BINARY", 0)


def native_path(p: Path) -> Path:
    """Windows: the extended-length form of an absolute path, so deep object keys (three UUIDs, a run id and a file
    name) are not cut off at the 260-character MAX_PATH limit. Other platforms: unchanged."""
    if not _windows():
        return p
    s = str(p)
    if s.startswith("\\\\?\\"):
        return p
    if s.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + s[2:])
    if len(s) > 2 and s[1] == ":" and s[2] == "\\":
        return Path("\\\\?\\" + s)
    return p  # relative or without a drive: the extended-length form needs a full path


def _windows() -> bool:
    return os.name == "nt"


class LocalStorage:
    kind = "local"

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / ".worm").mkdir(exist_ok=True)

    def _path(self, key: str) -> Path:
        key = _check_key(key)
        if _windows():  # the key is already checked segment by segment: no resolve() of a possibly over-long path
            return native_path(self.root.joinpath(*key.split("/")))
        p = (self.root / key).resolve()
        if self.root not in p.parents:
            raise StorageError("key escapes the storage root")
        return p

    def _worm_meta(self, key: str) -> Path:
        return native_path(self.root / ".worm" / (hashlib.sha256(key.encode()).hexdigest() + ".json"))

    def put_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> StoredObject:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL | O_BINARY, 0o600)
        except FileExistsError as exc:
            raise StorageError(f"object {key} already exists (versions are immutable)") from exc
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.chmod(p, 0o400)
        return StoredObject(key, hashlib.sha256(data).hexdigest(), len(data))

    def put_file(self, key: str, path: Path, content_type: str = "application/octet-stream") -> StoredObject:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.exists():
            raise StorageError(f"object {key} already exists (versions are immutable)")
        h = hashlib.sha256()
        size = 0
        tmp = p.with_name(p.name + ".part")
        with open(path, "rb") as src, open(tmp, "xb") as dst:
            for chunk in iter(lambda: src.read(1 << 20), b""):
                h.update(chunk)
                size += len(chunk)
                dst.write(chunk)
        os.replace(tmp, p)
        os.chmod(p, 0o400)
        return StoredObject(key, h.hexdigest(), size)

    def get_bytes(self, key: str) -> bytes:
        p = self._path(key)
        if not p.exists():
            raise StorageError(f"object {key} not found")
        return p.read_bytes()

    def open(self, key: str) -> BinaryIO:
        return open(self._path(key), "rb")

    def download_to(self, key: str, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self._path(key), dest)
        os.chmod(dest, 0o600)
        return dest

    def exists(self, key: str) -> bool:
        return self._path(key).exists()

    def set_worm(self, key: str, retain_until: datetime) -> None:
        if not self.exists(key):
            raise StorageError(f"object {key} not found")
        current = self.worm_until(key)
        if current and current > retain_until:
            return  # retention can only be extended
        self._worm_meta(key).write_text(json.dumps({"key": key, "retain_until": retain_until.isoformat()}))

    def worm_until(self, key: str) -> datetime | None:
        m = self._worm_meta(key)
        if not m.exists():
            return None
        return datetime.fromisoformat(json.loads(m.read_text())["retain_until"])

    def delete(self, key: str) -> None:
        until = self.worm_until(key)
        if until and until > datetime.now(UTC):
            raise StorageError(f"object {key} is under WORM retention until {until.isoformat()}")
        p = self._path(key)
        if p.exists():
            os.chmod(p, 0o600)
            p.unlink()

    def health(self) -> bool:
        probe = self.root / ".health"
        try:
            probe.write_text(datetime.now(UTC).isoformat())
            return True
        except OSError:
            return False


class S3Storage:
    kind = "s3"

    def __init__(
        self,
        bucket: str,
        *,
        endpoint: str | None = None,
        access_key: str | None = None,
        secret_key: str | None = None,
        region: str = "ap-south-1",
        client: Any = None,
    ) -> None:
        import boto3

        self.bucket = bucket
        self.s3 = client or boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
        )

    def ensure_bucket(self, object_lock: bool = True) -> None:
        """Create the bucket with versioning + object lock (idempotent; for dev/test)."""
        existing = {b["Name"] for b in self.s3.list_buckets().get("Buckets", [])}
        if self.bucket not in existing:
            kwargs: dict[str, Any] = {"Bucket": self.bucket, "ObjectLockEnabledForBucket": object_lock}
            region = self.s3.meta.region_name
            if region and region != "us-east-1":
                kwargs["CreateBucketConfiguration"] = {"LocationConstraint": region}
            self.s3.create_bucket(**kwargs)
        self.s3.put_bucket_versioning(Bucket=self.bucket, VersioningConfiguration={"Status": "Enabled"})

    def put_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> StoredObject:
        _check_key(key)
        if self.exists(key):
            raise StorageError(f"object {key} already exists (versions are immutable)")
        digest = hashlib.sha256(data).hexdigest()
        resp = self.s3.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
            Metadata={"sha256": digest},
            ServerSideEncryption="AES256",
        )
        return StoredObject(key, digest, len(data), resp.get("VersionId"))

    def put_file(self, key: str, path: Path, content_type: str = "application/octet-stream") -> StoredObject:
        return self.put_bytes(key, Path(path).read_bytes(), content_type)

    def get_bytes(self, key: str) -> bytes:
        try:
            return bytes(self.s3.get_object(Bucket=self.bucket, Key=_check_key(key))["Body"].read())
        except self.s3.exceptions.NoSuchKey as exc:
            raise StorageError(f"object {key} not found") from exc

    def download_to(self, key: str, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self.get_bytes(key))
        return dest

    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self.s3.head_object(Bucket=self.bucket, Key=_check_key(key))
            return True
        except ClientError:
            return False

    def set_worm(self, key: str, retain_until: datetime) -> None:
        self.s3.put_object_retention(
            Bucket=self.bucket,
            Key=_check_key(key),
            Retention={"Mode": "COMPLIANCE", "RetainUntilDate": retain_until},
        )

    def worm_until(self, key: str) -> datetime | None:
        from botocore.exceptions import ClientError

        try:
            h = self.s3.head_object(Bucket=self.bucket, Key=_check_key(key))
        except ClientError:
            return None
        until = h.get("ObjectLockRetainUntilDate")
        return until if isinstance(until, datetime) else None

    def delete(self, key: str) -> None:
        until = self.worm_until(key)
        if until and until > datetime.now(UTC):
            raise StorageError(f"object {key} is under WORM retention until {until.isoformat()}")
        self.s3.delete_object(Bucket=self.bucket, Key=_check_key(key))

    def health(self) -> bool:
        try:
            self.s3.head_bucket(Bucket=self.bucket)
            return True
        except Exception:
            return False


def make_storage(spec: str, *, endpoint: str | None = None, access_key: str | None = None, secret_key: str | None = None, region: str = "ap-south-1") -> Storage:
    if spec.startswith("local:"):
        return LocalStorage(spec[len("local:") :])
    if spec.startswith("s3://"):
        return S3Storage(spec[len("s3://") :].strip("/"), endpoint=endpoint, access_key=access_key, secret_key=secret_key, region=region)
    raise StorageError(f"unsupported FSWEB_STORAGE {spec!r} (use local:/path or s3://bucket)")


def object_key(tenant_id: str, client_id: str, engagement_id: str, *parts: str) -> str:
    safe = [safe_segment(p) for p in parts]
    return "/".join(["t", tenant_id, "c", client_id, "e", engagement_id, *safe])
