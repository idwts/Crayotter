import os
import platform
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules, copy_metadata

ROOT = Path(SPECPATH).resolve().parent
ARCH = {"aarch64": "arm64", "amd64": "x86_64"}.get(platform.machine().lower(), platform.machine().lower())
TOOLS = f"script/dep/macos/{ARCH}"
datas = [
    (str(ROOT / "app/frontend"), "app/frontend"),
    (str(ROOT / "memory_experience/latest_skills.md"), "memory_experience"),
    (str(ROOT / "AlibabaPuHuiTi-3-55-Regular/AlibabaPuHuiTi-3-55-Regular.ttf"), "AlibabaPuHuiTi-3-55-Regular"),
    *collect_data_files("webview", excludes=["lib/**"]),
]
binaries = [(str(ROOT / TOOLS / name), TOOLS) for name in ("ffmpeg", "ffprobe")]
binaries += [(str(ROOT / "packaging/generated/yt-dlp"), TOOLS)]
hiddenimports = ["agent", "graph", "model_runtime", "script.desktop_smoke", "webview.platforms.cocoa", *collect_submodules("tiktoken_ext")]
for package in ("bilibili_api", "dashscope", "langchain_core", "langchain_openai", "langgraph"):
    package_datas, package_binaries, package_imports = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_imports
for distribution in ("langchain-core", "langchain-openai", "langgraph", "langgraph-prebuilt", "bilibili-api-python"):
    datas += copy_metadata(distribution)

a = Analysis(
    [str(ROOT / "script/run_desktop.py")], pathex=[str(ROOT), str(ROOT / "script")],
    binaries=binaries, datas=datas, hiddenimports=hiddenimports,
    hookspath=[str(ROOT / "packaging/hooks")], hooksconfig={}, runtime_hooks=[],
    excludes=["tkinter", "PyQt5", "PyQt6", "PySide2", "PySide6", "webview.platforms.winforms", "webview.platforms.edgechromium", "webview.platforms.gtk", "webview.platforms.qt", "webview.platforms.android"],
    noarchive=False, optimize=0,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True, name="Crayotter", debug=False,
    bootloader_ignore_signals=False, strip=False, upx=False, console=False,
    argv_emulation=False, target_arch=ARCH,
    codesign_identity=os.environ.get("CRAYOTTER_MACOS_SIGN_IDENTITY") or None,
    entitlements_file=None,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="Crayotter")
app = BUNDLE(
    coll, name="Crayotter.app", icon=str(ROOT / "packaging/generated/Crayotter.icns"),
    bundle_identifier="com.crayotter.desktop", version="1.0.0",
    codesign_identity=os.environ.get("CRAYOTTER_MACOS_SIGN_IDENTITY") or None,
    info_plist={
        "LSMinimumSystemVersion": "15.0",
        "NSHighResolutionCapable": True,
        "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
    },
)
