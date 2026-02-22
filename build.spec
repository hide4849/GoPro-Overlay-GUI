# build.spec
# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_all, copy_metadata

# ---- collect_all は「submodules + datas + binaries」をまとめて拾う ----
gopro_datas, gopro_bins, gopro_hidden = collect_all("gopro_overlay")
dnd_datas,  dnd_bins,  dnd_hidden  = collect_all("tkinterdnd2")
pil_datas,  pil_bins,  pil_hidden  = collect_all("PIL")

# 地図/通信/証明書
geo_datas,  geo_bins,  geo_hidden  = collect_all("geotiler")
req_datas,  req_bins,  req_hidden  = collect_all("requests")
u3_datas,   u3_bins,   u3_hidden   = collect_all("urllib3")
cf_datas,   cf_bins,   cf_hidden   = collect_all("certifi")
idna_datas, idna_bins, idna_hidden = collect_all("idna")
cn_datas,   cn_bins,   cn_hidden   = collect_all("charset_normalizer")

# setuptools / pkg_resources（地図タイル周りなどで必要になることあり）
st_datas, st_bins, st_hidden = collect_all("setuptools")

datas = []
binaries = []
hiddenimports = []

datas += (
    gopro_datas + dnd_datas + pil_datas +
    geo_datas + req_datas + u3_datas + cf_datas + idna_datas + cn_datas +
    st_datas
)
binaries += (
    gopro_bins + dnd_bins + pil_bins +
    geo_bins + req_bins + u3_bins + cf_bins + idna_bins + cn_bins +
    st_bins
)
hiddenimports += (
    gopro_hidden + dnd_hidden + pil_hidden +
    geo_hidden + req_hidden + u3_hidden + cf_hidden + idna_hidden + cn_hidden +
    st_hidden
)

hiddenimports += [
    "pkg_resources",
    "setuptools",
    "setuptools._vendor",
]

# メタデータ同梱（依存がmetadata参照で落ちる対策）
datas += copy_metadata("gopro-overlay")
datas += copy_metadata("setuptools")
datas += copy_metadata("geotiler")
datas += copy_metadata("requests")
datas += copy_metadata("urllib3")
datas += copy_metadata("certifi")

# ★同梱ツール類
# ffmpeg/ffprobe は binaries 推奨（EXEに格納される）
binaries += [
    ("third_party/ffmpeg/ffmpeg.exe", "."),
    ("third_party/ffmpeg/ffprobe.exe", "."),
]

# スクリプト・フォントは datas
datas += [
    ("third_party/Roboto/Roboto-Regular.ttf", "."),
    ("third_party/gopro-dashboard/gopro-dashboard.py", "."),
]

a = Analysis(
    ["gopro_overlay_GUI.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=True,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=None)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="GoProOverlayGUI",
    console=False,
    onefile=True,
)
