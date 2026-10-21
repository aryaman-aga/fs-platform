"""Upload validation (05 §5): allowed types, size limits, magic bytes, no external
links / DDE, macros stored but never run, optional ClamAV scan and DLP rule."""

from __future__ import annotations

import io
import re
import socket
import struct
import zipfile
from dataclasses import dataclass
from pathlib import Path

ALLOWED = {".xlsx", ".xlsm", ".pdf", ".csv"}
MAX_UNCOMPRESSED = 2 * 1024**3  # zip-bomb guard
MAX_RATIO = 200

_AADHAAR = re.compile(rb"(?<!\d)(\d{4}) (\d{4}) (\d{4})(?!\d)")


class UploadRejected(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class UploadInfo:
    ext: str
    has_macros: bool = False


# Verhoeff checksum (Aadhaar numbers carry one) ------------------------------
_D = [[0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 2, 3, 4, 0, 6, 7, 8, 9, 5], [2, 3, 4, 0, 1, 7, 8, 9, 5, 6], [3, 4, 0, 1, 2, 8, 9, 5, 6, 7],
      [4, 0, 1, 2, 3, 9, 5, 6, 7, 8], [5, 9, 8, 7, 6, 0, 4, 3, 2, 1], [6, 5, 9, 8, 7, 1, 0, 4, 3, 2], [7, 6, 5, 9, 8, 2, 1, 0, 4, 3],
      [8, 7, 6, 5, 9, 3, 2, 1, 0, 4], [9, 8, 7, 6, 5, 4, 3, 2, 1, 0]]
_P = [[0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 5, 7, 6, 2, 8, 3, 0, 9, 4], [5, 8, 0, 3, 7, 9, 6, 1, 4, 2], [8, 9, 1, 6, 0, 4, 3, 5, 2, 7],
      [9, 4, 5, 3, 1, 2, 6, 8, 7, 0], [4, 2, 8, 6, 5, 7, 3, 9, 0, 1], [2, 7, 9, 3, 8, 0, 6, 4, 1, 5], [7, 0, 4, 6, 9, 1, 3, 2, 5, 8]]


def verhoeff_ok(num: str) -> bool:
    c = 0
    for i, ch in enumerate(reversed(num)):
        c = _D[c][_P[i % 8][int(ch)]]
    return c == 0


def contains_aadhaar(data: bytes) -> bool:
    for m in _AADHAAR.finditer(data):
        digits = b"".join(m.groups()).decode()
        if digits[0] not in "01" and verhoeff_ok(digits):
            return True
    return False


def _check_zip(data: bytes, ext: str) -> UploadInfo:
    if not data.startswith(b"PK\x03\x04"):
        raise UploadRejected("bad_magic", "the file is not a valid Excel workbook")
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise UploadRejected("bad_zip", "the workbook is corrupt") from exc
    total = 0
    has_macros = False
    for info in zf.infolist():
        total += info.file_size
        if info.compress_size and info.file_size / max(1, info.compress_size) > MAX_RATIO and info.file_size > 50 * 1024**2:
            raise UploadRejected("zip_bomb", "the workbook has an implausible compression ratio")
        name = info.filename.lower()
        if name.startswith("xl/externallinks/"):
            raise UploadRejected("external_links", "workbooks with external links are not accepted; break the links and upload again")
        if name.endswith("vbaproject.bin"):
            has_macros = True
    if total > MAX_UNCOMPRESSED:
        raise UploadRejected("too_large", "the workbook is too large when uncompressed")
    if "[content_types].xml" not in {i.filename.lower() for i in zf.infolist()}:
        raise UploadRejected("bad_ooxml", "the file is not an Office Open XML workbook")
    for info in zf.infolist():
        name = info.filename.lower()
        if name.endswith((".xml", ".rels")):
            body = zf.read(info.filename)
            if b"<ddeLink" in body or b"ddeService" in body:
                raise UploadRejected("dde", "workbooks with DDE links are not accepted")
            if name.endswith(".rels") and b"relationships/externalLink" in body:
                raise UploadRejected("external_links", "workbooks with external links are not accepted")
    if ext == ".xlsx" and has_macros:
        raise UploadRejected("macro_in_xlsx", "an .xlsx file must not contain macros")
    return UploadInfo(ext, has_macros=has_macros)


def validate(name: str, data: bytes, *, max_mb: int, dlp: bool = True) -> UploadInfo:
    ext = Path(name).suffix.lower()
    if ext not in ALLOWED:
        raise UploadRejected("file_type", f"only {', '.join(sorted(ALLOWED))} files are accepted")
    if len(data) == 0:
        raise UploadRejected("empty", "the file is empty")
    if len(data) > max_mb * 1024 * 1024:
        raise UploadRejected("too_large", f"the file exceeds {max_mb} MB")
    if ext in {".xlsx", ".xlsm"}:
        info = _check_zip(data, ext)
    elif ext == ".pdf":
        if not data.startswith(b"%PDF-"):
            raise UploadRejected("bad_magic", "the file is not a PDF")
        if b"/Launch" in data or b"/JavaScript" in data:
            raise UploadRejected("active_content", "PDFs with active content (JavaScript / launch actions) are not accepted")
        info = UploadInfo(ext)
    else:
        if b"\x00" in data[:65536]:
            raise UploadRejected("bad_csv", "the CSV contains binary data")
        info = UploadInfo(ext)
    if dlp and ext in {".csv", ".pdf"} and contains_aadhaar(data):
        raise UploadRejected("dlp_blocked", "the file appears to contain Aadhaar numbers; remove them before uploading (DLP)")
    return info


def clamav_scan(sock_path: str, data: bytes, timeout: float = 30.0) -> str | None:  # pragma: no cover - needs clamd
    """INSTREAM scan through clamd. Returns the signature name if infected."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect(sock_path)
    try:
        s.sendall(b"zINSTREAM\0")
        for i in range(0, len(data), 1 << 16):
            chunk = data[i : i + (1 << 16)]
            s.sendall(struct.pack("!L", len(chunk)) + chunk)
        s.sendall(struct.pack("!L", 0))
        reply = s.recv(4096).decode(errors="replace").strip("\0").strip()
    finally:
        s.close()
    if reply.endswith("FOUND"):
        return reply.split(":", 1)[-1].replace("FOUND", "").strip()
    return None
