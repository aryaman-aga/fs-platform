# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the FS-Platform offline edition (Mode C).
#
# Build on a clean machine from the repository root, after `npm run build` in fsui/:
#   pip install --no-index --find-links wheelhouse --require-hashes -r requirements.lock
#   pip install -e fscore -e fsweb -e fsoffline pyinstaller
#   pyinstaller fsoffline/fsoffline.spec --noconfirm
# Output: dist/FS-Platform-Offline/ (one folder; sign the executable with the firm's
# code-signing certificate before distribution, and record the release, E-ITGC-01).
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

SPEC_DIR = Path(SPECPATH).resolve()  # noqa: F821 - provided by PyInstaller
ROOT = SPEC_DIR.parent

hiddenimports = (
    collect_submodules("fsoffline")
    + collect_submodules("fsweb")
    + collect_submodules("fscore")
    + collect_submodules("uvicorn")
    + collect_submodules("sqlalchemy.dialects.sqlite")
    + ["alembic", "argon2", "pyotp", "multipart"]
)

datas = collect_data_files("fscore") + collect_data_files("fsweb")
ui_dist = ROOT / "fsui" / "dist"
if not (ui_dist / "index.html").is_file():
    raise SystemExit("fsui/dist is missing: run `npm ci && npm run build` in fsui/ first")
datas.append((str(ui_dist), "fsui/dist"))
# Alembic scripts live next to the fsweb package; bundle them so `fsweb.manage migrate`
# works inside the frozen application.
alembic_dir = ROOT / "fsweb" / "alembic"
if alembic_dir.is_dir():
    datas.append((str(alembic_dir), "fsweb_alembic/alembic"))
if (ROOT / "fsweb" / "alembic.ini").is_file():
    datas.append((str(ROOT / "fsweb" / "alembic.ini"), "fsweb_alembic"))

a = Analysis(
    [str(SPEC_DIR / "src" / "fsoffline" / "__main__.py")],
    pathex=[str(SPEC_DIR / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "pytest", "hypothesis", "moto", "IPython"],
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821
exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="FS-Platform-Offline",
    console=True,  # keeps the one-time bootstrap output and logs visible
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="FS-Platform-Offline", upx=False)  # noqa: F821
