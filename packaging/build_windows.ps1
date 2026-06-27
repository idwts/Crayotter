param(
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$DistDir = Join-Path $Root "dist"
$AppDir = Join-Path $DistDir "Crayotter"
$ZipPath = Join-Path $Root "Crayotter-Windows-x64.zip"
$ExePath = Join-Path $AppDir "Crayotter.exe"

if (-not [Environment]::Is64BitProcess) {
    throw "Windows x64 package must be built with a 64-bit Python process."
}

Push-Location $Root
try {
    $PythonVersion = python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
    if ($PythonVersion -ne "3.12") {
        throw "Python 3.12 is required. Current version: $PythonVersion"
    }

    if (-not $SkipInstall) {
        python -m pip install -r requirements.txt -r requirements-desktop.txt
    }

    python packaging\prepare_windows_assets.py
    python -m PyInstaller --noconfirm --clean packaging\crayotter.spec

    if (-not (Test-Path -LiteralPath $ExePath)) {
        throw "Build completed without producing dist\Crayotter\Crayotter.exe."
    }

    $SignCertPath = $env:CRAYOTTER_SIGN_CERT_PATH
    if ($SignCertPath) {
        $SignTool = if ($env:CRAYOTTER_SIGNTOOL_PATH) { $env:CRAYOTTER_SIGNTOOL_PATH } else { "signtool.exe" }
        $TimestampUrl = if ($env:CRAYOTTER_SIGN_TIMESTAMP_URL) { $env:CRAYOTTER_SIGN_TIMESTAMP_URL } else { "http://timestamp.digicert.com" }
        $SignArgs = @("sign", "/fd", "SHA256", "/td", "SHA256", "/tr", $TimestampUrl, "/f", $SignCertPath)
        if ($env:CRAYOTTER_SIGN_CERT_PASSWORD) {
            $SignArgs += @("/p", $env:CRAYOTTER_SIGN_CERT_PASSWORD)
        }
        $SignArgs += $ExePath
        & $SignTool @SignArgs
        if ($LASTEXITCODE -ne 0) {
            throw "Code signing failed with exit code $LASTEXITCODE."
        }
    } else {
        Write-Warning "CRAYOTTER_SIGN_CERT_PATH is not set; Crayotter.exe will be unsigned and may trigger Windows SmartScreen."
    }

    Copy-Item -LiteralPath packaging\README_WINDOWS_CN.txt -Destination (Join-Path $AppDir "使用说明.txt") -Force

    if (Test-Path -LiteralPath $ZipPath) {
        Remove-Item -LiteralPath $ZipPath -Force
    }
    Compress-Archive -LiteralPath $AppDir -DestinationPath $ZipPath -CompressionLevel Optimal

    Write-Host ""
    Write-Host "Windows release created:"
    Write-Host "  $AppDir"
    Write-Host "  $ZipPath"
}
finally {
    Pop-Location
}
