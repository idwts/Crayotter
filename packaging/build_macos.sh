#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "Build the macOS app on macOS." >&2
    exit 1
fi
PYTHON="${CRAYOTTER_BUILD_PYTHON:-python3}"
if [[ "${1:-}" != "--skip-install" ]]; then
    "$PYTHON" -m pip install -r requirements.txt -r requirements-desktop.txt
fi
"$PYTHON" packaging/prepare_macos_assets.py
"$PYTHON" -m PyInstaller --noconfirm --clean packaging/crayotter_macos.spec
test -x dist/Crayotter.app/Contents/MacOS/Crayotter
codesign --verify --deep --strict dist/Crayotter.app
ARCH="$("$PYTHON" -c 'import platform; print(platform.machine())')"
ditto -c -k --sequesterRsrc --keepParent dist/Crayotter.app "dist/Crayotter-macOS-${ARCH}.zip"
echo "Created dist/Crayotter.app and dist/Crayotter-macOS-${ARCH}.zip"
