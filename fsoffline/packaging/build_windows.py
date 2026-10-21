"""Build the Windows installer of the desktop edition: ``dist/windows/FS-Platform-Setup-<version>.exe``.

    python fsoffline/packaging/build_windows.py [--out dist/windows] [--skip-ui-build] [--stage-only]

Runs on macOS, Linux or Windows; needs CPython 3.13 (to precompile for the bundled 3.13), Node.js 22 (the web UI) and
``makensis`` (``brew install nsis`` / ``apt install nsis`` / ``choco install nsis``). Nothing is frozen: the bundle is
the official embeddable CPython plus pre-built win_amd64 wheels from ``requirements-windows.lock`` (hash-checked),
FS-Platform's own packages as wheels, the built web UI, the Visual C++ runtime and LibreOffice. Downloads are cached in
``build/windows/cache`` and must match the SHA-256 values below.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from email.parser import Parser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGING = Path(__file__).resolve().parent
BUILD = ROOT / "build" / "windows"
CACHE = BUILD / "cache"

PYTHON_VERSION = "3.13.15"
PY_TAG = "313"
DOWNLOADS: dict[str, dict[str, str]] = {
    "python": {
        "url": f"https://www.python.org/ftp/python/{PYTHON_VERSION}/python-{PYTHON_VERSION}-embed-amd64.zip",
        "sha256": "d1f04d990aee1253d8569e8e5104e30fa9f5fa830899f14843448872d936a2cf",
    },
    "vc_redist": {
        "url": "https://download.visualstudio.microsoft.com/download/pr/990c720a-46ce-4dc7-87b9-c2eb615d34b9/"
               "CC0FF0EB1DC3F5188AE6300FAEF32BF5BEEBA4BDD6E8E445A9184072096B713B/VC_redist.x64.exe",
        "sha256": "cc0ff0eb1dc3f5188ae6300faef32bf5beeba4bdd6e8e445a9184072096b713b",  # also in the URL (Microsoft)
    },
    "libreoffice": {
        "url": "https://download.documentfoundation.org/libreoffice/stable/26.8.1/win/x86_64/LibreOffice_26.8.1_Win_x86-64.msi",
        "sha256": "c6298b10cfa1748bcbedad94c62ce771e240e5021bfd6d7a967cd571b04b5312",  # published by TDF (.sha256)
    },
}

# native modules that must be present (and built for Windows) in the bundle
REQUIRED_BINARIES = [
    "lxml/etree.cp313-win_amd64.pyd",
    "pydantic_core/_pydantic_core.cp313-win_amd64.pyd",
    "cryptography/hazmat/bindings/_rust.pyd",
    "pypdfium2_raw/pdfium.dll",
    "pywin32_system32/pywintypes313.dll",
    "pywin32_system32/pythoncom313.dll",
    "win32/win32api.pyd",
    "rapidfuzz",
    "_argon2_cffi_bindings",
]


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def run(cmd: list[str], cwd: Path | None = None) -> None:
    log("$ " + " ".join(str(c) for c in cmd))
    subprocess.run(cmd, cwd=cwd, check=True)  # noqa: S603 - fixed build commands


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(name: str) -> Path:
    spec = DOWNLOADS[name]
    target = CACHE / spec["url"].rsplit("/", 1)[-1]
    if target.exists() and sha256(target) == spec["sha256"]:
        return target
    CACHE.mkdir(parents=True, exist_ok=True)
    part = target.with_suffix(target.suffix + ".part")
    log(f"downloading {spec['url']}")
    with urllib.request.urlopen(spec["url"]) as resp, open(part, "wb") as out:  # noqa: S310 - fixed https URLs
        shutil.copyfileobj(resp, out, 1 << 20)
    digest = sha256(part)
    if digest != spec["sha256"]:
        part.unlink()
        raise SystemExit(f"{name}: SHA-256 {digest} does not match the pinned {spec['sha256']} — refusing to use it")
    part.replace(target)
    return target


# --------------------------------------------------------------------------- steps
def build_ui(skip: bool) -> Path:
    dist = ROOT / "fsui" / "dist"
    if not skip:
        npm = shutil.which("npm") or "npm"
        run([npm, "ci"], cwd=ROOT / "fsui")
        run([npm, "run", "build"], cwd=ROOT / "fsui")
    if not (dist / "index.html").is_file():
        raise SystemExit("fsui/dist/index.html is missing: build the UI first (or drop --skip-ui-build)")
    return dist


def build_wheels() -> list[Path]:
    out = BUILD / "wheels"
    shutil.rmtree(out, ignore_errors=True)
    for package in ("fscore", "fsweb", "fsoffline"):  # setuptools' in-tree build folder keeps files deleted from src
        shutil.rmtree(ROOT / package / "build", ignore_errors=True)
    run([sys.executable, "-m", "pip", "wheel", "--no-deps", "--no-build-isolation", "-q", "-w", str(out),
         str(ROOT / "fscore"), str(ROOT / "fsweb"), str(ROOT / "fsoffline")])
    return sorted(out.glob("*.whl"))


def stage_python(stage: Path) -> Path:
    py = stage / "python"
    with zipfile.ZipFile(download("python")) as z:
        z.extractall(py)
    pth = py / f"python{PY_TAG}._pth"
    # site-packages + `import site`, so pywin32's .pth (which finds its DLLs) and our packages load; nothing else is
    # added to sys.path (the ._pth also makes Python ignore PYTHONPATH and friends from the environment)
    pth.write_text(f"python{PY_TAG}.zip\n.\nLib\\site-packages\nimport site\n", encoding="ascii")
    return py


def install_packages(py: Path, wheels: list[Path]) -> Path:
    site = py / "Lib" / "site-packages"
    site.mkdir(parents=True, exist_ok=True)
    run([sys.executable, "-m", "pip", "install", "-q", "--target", str(site), "--platform", "win_amd64", "--python-version", "3.13",
         "--implementation", "cp", "--abi", f"cp{PY_TAG}", "--abi", "abi3", "--abi", "none", "--only-binary=:all:", "--no-deps",
         "--require-hashes", "--no-compile", "-r", str(PACKAGING / "requirements-windows.lock")])
    shutil.rmtree(site / "bin", ignore_errors=True)  # console scripts generated for the build machine, not Windows
    run([sys.executable, "-m", "pip", "install", "-q", "--target", str(site), "--no-deps", "--no-compile", *map(str, wheels)])
    shutil.rmtree(site / "bin", ignore_errors=True)
    gen_py = site / "win32com" / "gen_py"
    gen_py.mkdir(parents=True, exist_ok=True)
    (gen_py / "__init__.py").touch()  # pywin32 keeps its COM cache here (read-only) instead of the user's %TEMP%
    missing = [b for b in REQUIRED_BINARIES if not (site / b).exists()]
    foreign = [str(p.relative_to(site)) for p in site.rglob("*") if p.suffix in (".so", ".dylib")]
    if missing or foreign:
        raise SystemExit(f"bundle check failed: missing {missing}; non-Windows binaries {foreign[:5]}")
    run([sys.executable, "-m", "compileall", "-q", "-j", "0", "--invalidation-mode", "unchecked-hash", str(site)])
    return site


def make_icon(path: Path) -> None:
    from PIL import Image, ImageDraw, ImageFont

    size = 256
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((8, 8, size - 8, size - 8), radius=48, fill=(31, 59, 109, 255))
    d.rounded_rectangle((8, size - 72, size - 8, size - 8), radius=48, fill=(201, 145, 44, 255))
    d.rectangle((8, size - 72, size - 8, size - 48), fill=(201, 145, 44, 255))
    font_path = ROOT / "fscore" / "src" / "fscore" / "pdf" / "fonts" / "DejaVuSansCondensed-Bold.ttf"
    font = ImageFont.truetype(str(font_path), 120)
    box = d.textbbox((0, 0), "FS", font=font)
    d.text(((size - (box[2] - box[0])) / 2 - box[0], (size - 64 - (box[3] - box[1])) / 2 - box[1]), "FS", font=font, fill="white")
    img.save(path, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])


def _python_licenses(site: Path) -> list[tuple[str, str, str]]:
    out = []
    for meta in sorted(site.glob("*.dist-info/METADATA")):
        msg = Parser().parsestr(meta.read_text(encoding="utf-8", errors="replace"), headersonly=True)
        lic = msg.get("License-Expression") or ""
        if not lic:
            classifiers = [c.split("::")[-1].strip() for c in msg.get_all("Classifier") or [] if c.startswith("License ::")]
            lic = ", ".join(classifiers) or (msg.get("License") or "").splitlines()[0] if (classifiers or msg.get("License")) else "see package"
        out.append((str(msg.get("Name")), str(msg.get("Version")), lic.strip()[:120]))
    return out


def _npm_licenses() -> list[tuple[str, str, str]]:
    lock = json.loads((ROOT / "fsui" / "package-lock.json").read_text(encoding="utf-8"))
    out = []
    for key, meta in sorted(lock.get("packages", {}).items()):
        if not key.startswith("node_modules/") or meta.get("dev") or meta.get("devOptional"):
            continue
        out.append((key.removeprefix("node_modules/"), str(meta.get("version", "")), str(meta.get("license", "see package"))))
    return out


def write_notices(stage: Path, site: Path) -> None:
    lines = [
        "FS-Platform desktop edition — third-party software",
        "",
        "FS-Platform includes the following third-party components, each under its own licence (full texts are in the",
        "packages' folders under python\\Lib\\site-packages\\*.dist-info, LibreOffice\\, and on the projects' websites).",
        "",
        f"CPython {PYTHON_VERSION} (embeddable) — Python Software Foundation License",
        "LibreOffice 26.8.1 — Mozilla Public License 2.0 (unmodified; https://www.libreoffice.org/about-us/source-code/)",
        "Microsoft Visual C++ 2015-2022 Redistributable (x64) — Microsoft Software License Terms (redistributable)",
        "DejaVu fonts — Bitstream Vera / DejaVu licence (fscore\\pdf\\fonts\\LICENSE-DejaVu.txt)",
        "",
        "Python packages:",
        *[f"  {n} {v} — {lic}" for n, v, lic in _python_licenses(site)],
        "",
        "Web UI packages:",
        *[f"  {n} {v} — {lic}" for n, v, lic in _npm_licenses()],
        "",
    ]
    (stage / "THIRD-PARTY-NOTICES.txt").write_text("\r\n".join(lines), encoding="utf-8")
    copyleft = [f"{n} ({lic})" for n, _v, lic in _python_licenses(site) if any(w in lic.upper() for w in ("AGPL", " GPL", "GPL-", "GPLV"))
                and "LGPL" not in lic.upper()]
    if copyleft:
        raise SystemExit(f"copyleft licences in the bundle: {copyleft}")


def makensis(stage: Path, out_dir: Path, version: str) -> Path:
    exe = shutil.which("makensis")
    if exe is None:
        raise SystemExit("makensis not found: install NSIS (brew install nsis / apt install nsis / choco install nsis)")
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"FS-Platform-Setup-{version}.exe"
    redist = stage.parent / "redist"
    redist.mkdir(exist_ok=True)
    lo, vc = download("libreoffice"), download("vc_redist")
    for f in (lo, vc):
        target = redist / f.name
        if not target.exists():
            shutil.copyfile(f, target)
    # -WX: any NSIS warning (e.g. an unknown constant) fails the build instead of producing a broken installer
    run([exe, "-V2", "-WX", "-INPUTCHARSET", "UTF8", f"-DVERSION={version}", f"-DSTAGE={stage}", f"-DREDIST={redist}", f"-DLO_MSI={lo.name}",
         f"-DVCREDIST={vc.name}", f"-DOUTFILE={out}", str(PACKAGING / "installer.nsi")])
    (out.with_suffix(".exe.sha256")).write_text(f"{sha256(out)}  {out.name}\n", encoding="ascii")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default=str(ROOT / "dist" / "windows"), help="where the installer is written")
    ap.add_argument("--skip-ui-build", action="store_true", help="use the existing fsui/dist")
    ap.add_argument("--stage-only", action="store_true", help="assemble build/windows/stage without running makensis")
    a = ap.parse_args(argv)
    if sys.version_info[:2] != (3, 13):
        raise SystemExit("run the build with CPython 3.13 (it precompiles for the bundled 3.13)")
    sys.path.insert(0, str(ROOT / "fsoffline" / "src"))
    from fsoffline import __version__

    stage = BUILD / "stage"
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True)
    ui = build_ui(a.skip_ui_build)
    wheels = build_wheels()
    py = stage_python(stage)
    site = install_packages(py, wheels)
    shutil.copytree(ui, stage / "ui")
    make_icon(stage / "FS-Platform.ico")
    write_notices(stage, site)
    log(f"staged {sum(1 for _ in stage.rglob('*'))} files in {stage}")
    if a.stage_only:
        return 0
    out = makensis(stage, Path(a.out), __version__)
    log(f"built {out} ({out.stat().st_size / 1e6:.0f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
