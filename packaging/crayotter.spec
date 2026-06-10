from pathlib import Path

from PyInstaller.utils.hooks import collect_all


ROOT = Path(SPECPATH).resolve().parent
ICON_PATH = ROOT / "packaging" / "generated" / "Crayotter.ico"

webview_datas, webview_binaries, webview_hiddenimports = collect_all("webview")

datas = [
    (str(ROOT / "app" / "frontend"), "app/frontend"),
    (str(ROOT / "memory_experience"), "memory_experience"),
    (
        str(ROOT / "AlibabaPuHuiTi-3-55-Regular" / "AlibabaPuHuiTi-3-55-Regular.ttf"),
        "AlibabaPuHuiTi-3-55-Regular",
    ),
]

binaries = [
    (str(ROOT / "script" / "dep" / "windows" / "ffmpeg.exe"), "script/dep/windows"),
    (str(ROOT / "script" / "dep" / "windows" / "yt-dlp.exe"), "script/dep/windows"),
]

hiddenimports = [
    "agent",
    "model_runtime",
    "webview.platforms.edgechromium",
    "webview.platforms.winforms",
    *webview_hiddenimports,
]

a = Analysis(
    [str(ROOT / "script" / "run_desktop.py")],
    pathex=[str(ROOT), str(ROOT / "script")],
    binaries=[*binaries, *webview_binaries],
    datas=[*datas, *webview_datas],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "PyQt5", "PyQt6", "PySide2", "PySide6"],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Crayotter",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ICON_PATH),
    version=str(ROOT / "packaging" / "windows_version_info.txt"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Crayotter",
)
