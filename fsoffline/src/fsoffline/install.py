"""Where an installed copy lives and how it is configured.

The Windows installer writes ``fsplatform.ini`` into the install folder (Program Files: writable by administrators
only): version, data folder, web UI folder, bundled LibreOffice and preferred port. Settings an administrator may
change later live in ``fsplatform.ini`` inside the data folder. A source checkout has neither file and keeps the
per-user development defaults.
"""

from __future__ import annotations

import configparser
import sys
from dataclasses import dataclass
from pathlib import Path

INI_NAME = "fsplatform.ini"
SECTION = "fsplatform"
DEFAULT_PORT = 8746


@dataclass(frozen=True)
class InstallConfig:
    root: Path | None = None  # install folder; None in a source checkout
    version: str = ""
    data_dir: Path | None = None
    ui_dir: Path | None = None
    soffice: Path | None = None
    icon: Path | None = None
    port: int = DEFAULT_PORT

    @property
    def installed(self) -> bool:
        return self.root is not None


def install_root(executable: str | None = None) -> Path | None:
    """The install folder: ``<root>\\python\\pythonw.exe`` runs the app, ``<root>\\fsplatform.ini`` describes it."""
    exe = Path(executable or sys.executable).resolve()
    for candidate in (exe.parent.parent, exe.parent):
        if (candidate / INI_NAME).is_file():
            return candidate
    return None


def _path(root: Path, value: str) -> Path | None:
    if not value.strip():
        return None
    p = Path(value.strip())
    return p if p.is_absolute() else root / p


def load_install_config(root: Path | None = None) -> InstallConfig:
    root = root if root is not None else install_root()
    if root is None:
        return InstallConfig()
    cp = configparser.ConfigParser(interpolation=None)
    cp.read(root / INI_NAME, encoding="utf-8")
    sec = cp[SECTION] if cp.has_section(SECTION) else {}
    try:
        port = int(sec.get("port", DEFAULT_PORT))
    except ValueError:
        port = DEFAULT_PORT
    return InstallConfig(
        root=root,
        version=sec.get("version", ""),
        data_dir=_path(root, sec.get("data_dir", "")),
        ui_dir=_path(root, sec.get("ui_dir", "ui")),
        soffice=_path(root, sec.get("soffice", "")),
        icon=_path(root, sec.get("icon", "FS-Platform.ico")),
        port=port if 1024 <= port <= 65535 else DEFAULT_PORT,
    )


def load_data_config(data_dir: Path) -> dict[str, str]:
    """Administrator-editable settings in ``<data>\\fsplatform.ini`` ([app] section), e.g. ``ai_enabled = true``."""
    cp = configparser.ConfigParser(interpolation=None)
    cp.read(data_dir / INI_NAME, encoding="utf-8")
    return dict(cp["app"]) if cp.has_section("app") else {}
