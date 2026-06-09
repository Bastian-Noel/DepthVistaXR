$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$runtimeDir = Join-Path $projectRoot "runtime"
$pythonDir = Join-Path $runtimeDir "python"
$tempDir = Join-Path $env:TEMP "DepthVistaXR-install"
$pythonZip = Join-Path $tempDir "python.zip"
$getPip = Join-Path $tempDir "get-pip.py"
$pythonUrl = "https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip"
$getPipUrl = "https://bootstrap.pypa.io/get-pip.py"

Write-Host "Installing the portable Python runtime..."
New-Item -ItemType Directory -Force -Path $tempDir, $runtimeDir | Out-Null

if (Test-Path -LiteralPath $pythonDir) {
    Remove-Item -LiteralPath $pythonDir -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $pythonDir | Out-Null

Invoke-WebRequest -Uri $pythonUrl -OutFile $pythonZip
Expand-Archive -LiteralPath $pythonZip -DestinationPath $pythonDir -Force

$pthFile = Join-Path $pythonDir "python312._pth"
$pthLines = Get-Content -LiteralPath $pthFile |
    Where-Object { $_ -ne "#import site" -and $_ -ne "import site" }
$pthLines += "..\..\app"
$pthLines += "import site"
Set-Content -LiteralPath $pthFile -Value $pthLines -Encoding ASCII

Invoke-WebRequest -Uri $getPipUrl -OutFile $getPip
$python = Join-Path $pythonDir "python.exe"
& $python -X utf8 $getPip
& $python -X utf8 -m pip install --upgrade pip
& $python -X utf8 -m pip install -r (Join-Path $projectRoot "requirements.txt")

Write-Host ""
Write-Host "DepthVista XR runtime installed."
Write-Host "Run DepthVista-XR.bat to start the application."
