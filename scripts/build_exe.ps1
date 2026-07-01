#Requires -Version 5.1
<#
.SYNOPSIS
    Build standalone CloudToHDD Backup executables.
.OUTPUTS
    dist\CloudToHDD-Backup.exe         - Single-file portable app (all-in-one)
    dist\CloudToHDD-Backup-Setup.exe - Installer with Desktop + Start Menu shortcuts
#>
$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

Write-Host "=== Building CloudToHDD Backup ===" -ForegroundColor Cyan

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    python -m venv .venv
}

& ".venv\Scripts\pip.exe" install -r requirements.txt -q
& ".venv\Scripts\pip.exe" install -r requirements-build.txt -q

# Remove old folder build output to avoid confusion
$oldFolder = Join-Path $ProjectRoot "dist\CloudToHDD-Backup"
if (Test-Path $oldFolder) {
    Remove-Item $oldFolder -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host "Building single-file app..." -ForegroundColor Yellow
& ".venv\Scripts\pyinstaller.exe" --noconfirm --clean cloudtohdd.spec

$appExe = Join-Path $ProjectRoot "dist\CloudToHDD-Backup.exe"
if (-not (Test-Path $appExe)) {
    throw "Build failed: $appExe not found"
}

Write-Host "Building installer..." -ForegroundColor Yellow
& ".venv\Scripts\pyinstaller.exe" --noconfirm --clean installer.spec

$setupExe = Join-Path $ProjectRoot "dist\CloudToHDD-Backup-Setup.exe"
if (-not (Test-Path $setupExe)) {
    throw "Installer build failed: $setupExe not found"
}

$appMb = [math]::Round((Get-Item $appExe).Length / 1MB, 1)
$setupMb = [math]::Round((Get-Item $setupExe).Length / 1MB, 1)

Write-Host ""
Write-Host "Build complete:" -ForegroundColor Green
Write-Host "  Portable (single file):  dist\CloudToHDD-Backup.exe  ($appMb MB)"
Write-Host "    - Double-click to run. No install. Config saved next to the exe."
Write-Host ""
Write-Host "  Installer (recommended):   dist\CloudToHDD-Backup-Setup.exe  ($setupMb MB)"
Write-Host "    - Installs to %LOCALAPPDATA%\CloudToHDD Backup"
Write-Host "    - Creates Desktop + Start Menu shortcuts"
