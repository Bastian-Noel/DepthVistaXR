$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$installerScript = Join-Path $projectRoot "installer\DepthVistaXR.iss"
$isccCandidates = @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
)
$iscc = $isccCandidates |
    Where-Object { Test-Path -LiteralPath $_ } |
    Select-Object -First 1

if (-not $iscc) {
    throw "Inno Setup 6 is required. Install it with: winget install JRSoftware.InnoSetup"
}

New-Item -ItemType Directory -Force -Path (Join-Path $projectRoot "dist") | Out-Null
& $iscc $installerScript
if ($LASTEXITCODE -ne 0) {
    throw "Inno Setup failed with exit code $LASTEXITCODE."
}

Write-Host ""
Write-Host "Installer created:"
Get-ChildItem -LiteralPath (Join-Path $projectRoot "dist") -Filter "DepthVistaXR-Setup-*.exe" |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1 -ExpandProperty FullName |
    Write-Host
