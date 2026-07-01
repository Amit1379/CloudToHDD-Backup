#Requires -Version 5.1
<#
.SYNOPSIS
    Runs CloudToHDD Backup with safety checks.
.DESCRIPTION
    Installs Python dependencies if needed, validates config, and executes backup.
.PARAMETER DryRun
    Preview backup without copying files.
.PARAMETER Provider
    Run a specific provider: onedrive, google_drive, or icloud.
.EXAMPLE
    .\Run-CloudBackup.ps1
.EXAMPLE
    .\Run-CloudBackup.ps1 -DryRun -Provider onedrive
#>
[CmdletBinding()]
param(
    [switch]$DryRun,
    [ValidateSet("onedrive", "google_drive", "icloud")]
    [string]$Provider
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

function Test-Python {
    $python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $python) {
        throw "Python 3 is required. Install from https://www.python.org/downloads/"
    }
    $version = & python --version 2>&1
    Write-Host "Using $version"
}

function Install-Dependencies {
    if (-not (Test-Path "$ProjectRoot\.venv")) {
        Write-Host "Creating virtual environment..."
        python -m venv .venv
    }
    & "$ProjectRoot\.venv\Scripts\python.exe" -m pip install --upgrade pip -q
    & "$ProjectRoot\.venv\Scripts\pip.exe" install -r requirements.txt -q
}

function Ensure-Config {
    if (-not (Test-Path "$ProjectRoot\config.yaml")) {
        Write-Host "Creating config.yaml from template..."
        & "$ProjectRoot\.venv\Scripts\python.exe" main.py init
    }
}

function Test-DestinationWritable {
    $configPath = Join-Path $ProjectRoot "config.yaml"
    $destLine = Select-String -Path $configPath -Pattern 'destination_root:' | Select-Object -First 1
    if (-not $destLine) { return }

    $dest = $destLine.Line -replace '.*destination_root:\s*"?([^"]+)"?.*', '$1'
    $dest = $dest.Trim()
    if (-not $dest) { return }

    try {
        if (-not (Test-Path $dest)) {
            New-Item -ItemType Directory -Path $dest -Force | Out-Null
        }
        $testFile = Join-Path $dest ".write_test_$(Get-Random)"
        "ok" | Set-Content -Path $testFile -Encoding UTF8
        Remove-Item $testFile -Force
        Write-Host "Destination writable: $dest"
    }
    catch {
        throw "Destination not writable: $dest. $_"
    }
}

Write-Host "=== CloudToHDD Backup ===" -ForegroundColor Cyan
Test-Python
Install-Dependencies
Ensure-Config
Test-DestinationWritable

$python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$args = @("main.py", "detect")
& $python @args

if ($DryRun) {
    $runArgs = @("main.py", "run", "--dry-run")
    if ($Provider) { $runArgs += @("--provider", $Provider) }
    Write-Host "`nStarting dry-run preview..." -ForegroundColor Cyan
    & $python @runArgs
    exit $LASTEXITCODE
}

if ($Provider) {
    $runArgs = @("main.py", "run", "--provider", $Provider)
    Write-Host "`nStarting backup for $Provider..." -ForegroundColor Cyan
    & $python @runArgs
    exit $LASTEXITCODE
}

Write-Host "`nOpening interactive menu..." -ForegroundColor Cyan
& $python @("main.py")
exit $LASTEXITCODE
