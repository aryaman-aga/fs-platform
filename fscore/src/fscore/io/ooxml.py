"""Low-level OOXML package access shared by the reader and the patch writer."""

from __future__ import annotations

import hashlib
import posixpath
import zipfile
from pathlib import Path
from typing import IO

from lxml import etree

NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
NS_CT = "http://schemas.openxmlformats.org/package/2006/content-types"
NS_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
NS = {"m": NS_MAIN, "r": NS_R, "pr": NS_PKG_REL, "ct": NS_CT, "a": NS_A}

REL_WORKSHEET = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"
REL_SHARED_STRINGS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings"
REL_STYLES = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles"
REL_THEME = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme"
REL_CALC_CHAIN = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/calcChain"
REL_EXTERNAL_LINK = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/externalLink"
REL_OFFICE_DOC = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"


def q(tag: str, ns: str = NS_MAIN) -> str:
    return f"{{{ns}}}{tag}"


# Hardened parser: no network, no entity expansion, no DTD loading (XXE-safe).
_SAFE_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=True, remove_blank_text=False)


def parse_xml(data: bytes) -> etree._Element:
    return etree.fromstring(data, parser=_SAFE_PARSER)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def resolve_target(base_part: str, target: str) -> str:
    """Resolve a relationship target relative to the part that owns the .rels file."""
    if target.startswith("/"):
        return target.lstrip("/")
    base_dir = posixpath.dirname(base_part)
    return posixpath.normpath(posixpath.join(base_dir, target))


def rels_path_for(part: str) -> str:
    d, f = posixpath.split(part)
    return posixpath.join(d, "_rels", f + ".rels")


class Package:
    """Read-only view of an OOXML zip package."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.zip = zipfile.ZipFile(path, "r")
        self.names = set(self.zip.namelist())

    def close(self) -> None:
        self.zip.close()

    def __enter__(self) -> Package:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def has(self, part: str) -> bool:
        return part in self.names

    def read(self, part: str) -> bytes:
        return self.zip.read(part)

    def xml(self, part: str) -> etree._Element:
        return parse_xml(self.read(part))

    def open(self, part: str) -> IO[bytes]:
        return self.zip.open(part)

    def rels(self, part: str) -> dict[str, tuple[str, str, str]]:
        """rId -> (type, resolved target, target mode)."""
        rp = rels_path_for(part)
        if rp not in self.names:
            return {}
        root = self.xml(rp)
        out: dict[str, tuple[str, str, str]] = {}
        for rel in root.findall(q("Relationship", NS_PKG_REL)):
            mode = rel.get("TargetMode", "Internal")
            target = rel.get("Target", "")
            resolved = target if mode == "External" else resolve_target(part, target)
            out[rel.get("Id", "")] = (rel.get("Type", ""), resolved, mode)
        return out

    def workbook_part(self) -> str:
        for typ, target, _m in self._root_rels().values():
            if typ == REL_OFFICE_DOC:
                return target
        return "xl/workbook.xml"

    def _root_rels(self) -> dict[str, tuple[str, str, str]]:
        if "_rels/.rels" not in self.names:
            return {}
        root = self.xml("_rels/.rels")
        out: dict[str, tuple[str, str, str]] = {}
        for rel in root.findall(q("Relationship", NS_PKG_REL)):
            out[rel.get("Id", "")] = (rel.get("Type", ""), rel.get("Target", "").lstrip("/"), rel.get("TargetMode", "Internal"))
        return out
