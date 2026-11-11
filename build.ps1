$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectDir

$ExistingApp = Get-Process -Name PulseDesk -ErrorAction SilentlyContinue
if ($ExistingApp) {
    throw "Close PulseDesk before rebuilding it."
}

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "Could not create the Python virtual environment." }
}

$PythonExe = Join-Path $ProjectDir ".venv\Scripts\python.exe"
& $PythonExe -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "Dependency setup failed." }
& $PythonExe -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "Dependency setup failed." }
& $PythonExe -m pytest
if ($LASTEXITCODE -ne 0) { throw "Tests failed; executable was not rebuilt." }
& $PythonExe -m PyInstaller --noconfirm --clean --onefile --windowed --name PulseDesk app.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed." }

Write-Host "Built: $ProjectDir\dist\PulseDesk.exe"
