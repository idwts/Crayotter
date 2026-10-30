from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata


ROOT = Path(SPECPATH).resolve().parent
ICON_PATH = ROOT / "packaging" / "generated" / "Crayotter.ico"

webview_datas, webview_binaries, webview_hiddenimports = collect_all("webview")
bilibili_datas, bilibili_binaries, bilibili_hiddenimports = collect_all("bilibili_api")

datas = [
    (str(ROOT / "app" / "frontend"), "app/frontend"),
    (str(ROOT / "packaging/runtime_defaults/memory_experience/latest_skills.md"), "memory_experience"),
    (
        str(ROOT / "AlibabaPuHuiTi-3-55-Regular" / "AlibabaPuHuiTi-3-55-Regular.ttf"),
        "AlibabaPuHuiTi-3-55-Regular",
    ),
]

binaries = [
    (str(ROOT / "packaging/generated/windows/ffmpeg.exe"), "script/dep/windows"),
    (str(ROOT / "packaging/generated/windows/ffprobe.exe"), "script/dep/windows"),
    (str(ROOT / "script" / "dep" / "windows" / "yt-dlp.exe"), "script/dep/windows"),
]

hiddenimports = [
    "agent",
    "graph",
    "model_runtime",
    "script.desktop_smoke",
    *collect_submodules("tiktoken_ext"),
    "webview.platforms.edgechromium",
    "webview.platforms.winforms",
    *webview_hiddenimports,
    *bilibili_hiddenimports,
]

for package in ("dashscope", "langchain_core", "langchain_openai", "langgraph"):
    package_datas, package_binaries, package_imports = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_imports
for distribution in ("langchain-core", "langchain-openai", "langgraph", "langgraph-prebuilt", "bilibili-api-python"):
    datas += copy_metadata(distribution)

a = Analysis(
    [str(ROOT / "script" / "run_desktop.py")],
    pathex=[str(ROOT), str(ROOT / "script")],
    binaries=[*binaries, *webview_binaries, *bilibili_binaries],
    datas=[*datas, *webview_datas, *bilibili_datas],
    hiddenimports=hiddenimports,
    hookspath=[str(ROOT / "packaging/hooks")],
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
