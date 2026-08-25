[CmdletBinding()]
param(
    [switch]$Clean
)

$ErrorActionPreference = "Stop"
$ProjectDir = $PSScriptRoot
$VenvPython = Join-Path $ProjectDir ".venv\Scripts\python.exe"
$Python = if (Test-Path -LiteralPath $VenvPython) { $VenvPython } else { "python" }

if ($Clean) {
    $BuildDir = Join-Path $ProjectDir "build"
    $DistDir = Join-Path $ProjectDir "dist"

    if (Test-Path -LiteralPath $BuildDir) {
        Remove-Item -LiteralPath $BuildDir -Recurse -Force
    }
    if (Test-Path -LiteralPath $DistDir) {
        Remove-Item -LiteralPath $DistDir -Recurse -Force
    }
}

& $Python -c "import PyInstaller" 2>$null
if ($LASTEXITCODE -ne 0) {
    throw 'PyInstaller is not installed. Run: .\.venv\Scripts\python.exe -m pip install -e ".[build]"'
}

Push-Location $ProjectDir
try {
    & $Python -m PyInstaller --noconfirm "PlaylistSaver.spec"
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller exited with code $LASTEXITCODE."
    }
}
finally {
    Pop-Location
}

Write-Host "Build completed: $ProjectDir\dist\PlaylistSaver"

