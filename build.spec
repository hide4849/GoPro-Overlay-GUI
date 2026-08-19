# -*- mode: python ; coding: utf-8 -*-
#
# build_0003.spec (single spec for macOS + Windows)
# - Run this spec ON EACH OS separately. It switches behavior by platform.
#
# Required project files next to this spec:
#   - gopro_overlay_GUI.py
#   - third_party/ffmpeg/ffmpeg(.exe)
#   - third_party/ffmpeg/ffprobe(.exe)
#   - third_party/Roboto/Roboto-Regular.ttf
#   - third_party/gopro-dashboard/gopro-dashboard.py
# Optional (macOS):
#   - entitlements_disable_lib_validation.plist

import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import (
    collect_all,
    collect_submodules,
    collect_data_files,
    copy_metadata,
)

HERE = Path(globals().get("SPECPATH", os.getcwd())).resolve()
IS_WIN = (os.name == "nt")
IS_MAC = (sys.platform == "darwin")

block_cipher = None

# ------------------------------------------------------------
# Common assets (placed where runtime expects them)
# ------------------------------------------------------------
ffmpeg_rel  = "third_party/ffmpeg/ffmpeg.exe"  if IS_WIN else "third_party/ffmpeg/ffmpeg"
ffprobe_rel = "third_party/ffmpeg/ffprobe.exe" if IS_WIN else "third_party/ffmpeg/ffprobe"

COMMON_ASSETS = [
    (ffmpeg_rel,  "."),
    (ffprobe_rel, "."),
    ("third_party/Roboto/Roboto-Regular.ttf", "third_party/Roboto"),
    ("third_party/gopro-dashboard/gopro-dashboard.py", "."),
]

def _must_exist(rel_path: str) -> str:
    p = HERE / rel_path
    if not p.exists():
        raise SystemExit(f"ERROR: missing required file next to spec: {p}")
    return str(p)

# ------------------------------------------------------------
# Platform-specific collection
# ------------------------------------------------------------
datas = []
binaries = []
hiddenimports = []

# Always include common assets
for src, dest in COMMON_ASSETS:
    datas.append((_must_exist(src), dest))

if IS_MAC:
    # ---- Force-collect Tcl/Tk script libraries (init.tcl etc.) from python.org framework ----
    # 3.12/3.13 どちらでも動くように、実行中Pythonのバージョンに追従させる
    _py_ver = f"{sys.version_info.major}.{sys.version_info.minor}"
    TCL_SRC = Path(f"/Library/Frameworks/Python.framework/Versions/{_py_ver}/lib/tcl8.6")
    TK_SRC  = Path(f"/Library/Frameworks/Python.framework/Versions/{_py_ver}/lib/tk8.6")
    if not TCL_SRC.exists():
        TCL_SRC = Path("/Library/Frameworks/Python.framework/Versions/Current/lib/tcl8.6")
    if not TK_SRC.exists():
        TK_SRC  = Path("/Library/Frameworks/Python.framework/Versions/Current/lib/tk8.6")

    def _add_tree_files(src_dir: Path, dest_root: str):
        out = []
        if not src_dir.exists():
            return out
        for root, _, files in os.walk(src_dir):
            root_p = Path(root)
            rel = root_p.relative_to(src_dir)
            dest_dir = str(Path(dest_root) / rel)
            for fn in files:
                out.append((str(root_p / fn), dest_dir))
        return out

    datas += _add_tree_files(TCL_SRC, "_tcl_data")
    datas += _add_tree_files(TK_SRC, "_tk_data")

    # ---- Hidden imports ----
    # gopro-dashboard.py is executed via runpy.run_path, so make sure dependencies are included.
    hiddenimports += collect_submodules("gopro_overlay")
    datas += collect_data_files("gopro_overlay", include_py_files=True)
    datas += collect_data_files("tzdata")

    hiddenimports += ["pkg_resources"]
    try:
        datas += copy_metadata("setuptools")
    except Exception:
        pass

    # Often-missed deps for map tiles / images / requests stack
    for pkg in ["geotiler", "PIL", "requests", "urllib3", "certifi", "charset_normalizer", "idna", "tkinterdnd2"]:
        try:
            hiddenimports += collect_submodules(pkg)
            datas += collect_data_files(pkg)
        except Exception:
            # If a package isn't installed, fail fast (it's likely required at runtime).
            raise

    for _m in ["geotiler", "Pillow", "requests", "urllib3", "certifi", "charset-normalizer", "idna"]:
        try:
            datas += copy_metadata(_m)
        except Exception:
            pass

    runtime_hooks = []  # Tk path is handled in-script (gopro_overlay_GUI.py)

else:
    # Windows: use collect_all to grab submodules + datas + binaries for common deps.
    pkgs = [
        "gopro_overlay",
        "tkinterdnd2",
        "PIL",
        "geotiler",
        "requests",
        "urllib3",
        "certifi",
        "idna",
        "charset_normalizer",
        "setuptools",
        "tzdata",
    ]
    for pkg in pkgs:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h

    hiddenimports += ["pkg_resources", "setuptools", "setuptools._vendor"]

    # Metadata (deps sometimes look these up)
    for md in ["gopro-overlay", "setuptools", "geotiler", "requests", "urllib3", "certifi", "charset-normalizer", "idna"]:
        try:
            datas += copy_metadata(md)
        except Exception:
            pass

    runtime_hooks = []

# ------------------------------------------------------------
# Main script
# ------------------------------------------------------------
entry_script = HERE / "gopro_overlay_GUI.py"
if not entry_script.exists():
    raise SystemExit(f"ERROR: missing entry script next to spec: {entry_script}")

a = Analysis(
    [str(entry_script)],
    pathex=[str(HERE)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=runtime_hooks,
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=(True if IS_WIN else False),
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# ------------------------------------------------------------
# Build targets
# ------------------------------------------------------------
APP_NAME = "GoProOverlayGUI"
APP_VERSION = "1.6"

if IS_MAC:
    # macOS: build .app bundle, with ad-hoc signing for local use.
    ent = HERE / "entitlements_disable_lib_validation.plist"
    entitlements_file = str(ent) if ent.exists() else None

    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name=APP_NAME,
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity="-",
        entitlements_file=entitlements_file,
    )

    coll = COLLECT(
        exe,
        a.binaries,
        a.zipfiles,
        a.datas,
        strip=False,
        upx=False,
        upx_exclude=[],
        name=APP_NAME,
    )

    app = BUNDLE(
        coll,
        name=f"{APP_NAME}.app",
        bundle_identifier="jp.saka.GoProOverlayGUI",
        info_plist={
            "CFBundleShortVersionString": APP_VERSION,
            "CFBundleVersion": APP_VERSION,
        },
    )

else:
    # Windows: onefile exe
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.zipfiles,
        a.datas,
        [],
        name=APP_NAME,
        console=False,
        onefile=True,
    )
