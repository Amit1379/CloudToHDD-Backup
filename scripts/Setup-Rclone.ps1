#Requires -Version 5.1
<#
.SYNOPSIS
    Helper to install rclone and configure cloud remotes.
.DESCRIPTION
    Downloads rclone for Windows and guides configuration for OneDrive,
    Google Drive, and iCloud. rclone enables direct cloud API backup when
    sync folders are unavailable or incomplete.
#>
[CmdletBinding()]
param(
    [ValidateSet("onedrive", "gdrive", "icloud", "all")]
    [string]$Provider = "all"
)

$ErrorActionPreference = "Stop"

function Install-Rclone {
    $installDir = Join-Path $env:LOCALAPPDATA "rclone"
    $rcloneExe = Join-Path $installDir "rclone.exe"

    if (Test-Path $rcloneExe) {
        Write-Host "rclone already installed at $rcloneExe"
        return $rcloneExe
    }

    Write-Host "Downloading rclone for Windows..."
    New-Item -ItemType Directory -Path $installDir -Force | Out-Null
    $zipUrl = "https://downloads.rclone.org/rclone-current-windows-amd64.zip"
    $zipPath = Join-Path $env:TEMP "rclone.zip"

    Invoke-WebRequest -Uri $zipUrl -OutFile $zipPath -UseBasicParsing
    Expand-Archive -Path $zipPath -DestinationPath $installDir -Force

    $extracted = Get-ChildItem -Path $installDir -Recurse -Filter "rclone.exe" | Select-Object -First 1
    if ($extracted -and $extracted.FullName -ne $rcloneExe) {
        Copy-Item $extracted.FullName $rcloneExe -Force
    }

    if (-not (Test-Path $rcloneExe)) {
        throw "rclone installation failed."
    }

    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if ($userPath -notlike "*$installDir*") {
        [Environment]::SetEnvironmentVariable("Path", "$userPath;$installDir", "User")
        $env:Path += ";$installDir"
    }

    Write-Host "rclone installed: $rcloneExe" -ForegroundColor Green
    return $rcloneExe
}

function Configure-Remote {
    param([string]$RcloneExe, [string]$RemoteName, [string]$ProviderType)

    Write-Host "`nConfiguring remote: $RemoteName ($ProviderType)" -ForegroundColor Cyan
    Write-Host "Follow the interactive prompts. Use default values where unsure."
    & $RcloneExe config create $RemoteName $ProviderType
}

$rclone = Install-Rclone
& $rclone version

$providers = @{
    onedrive = @{ Name = "onedrive"; Type = "onedrive" }
    gdrive   = @{ Name = "gdrive";   Type = "drive" }
    icloud   = @{ Name = "icloud";   Type = "iclouddrive" }
}

if ($Provider -eq "all") {
    foreach ($key in $providers.Keys) {
        $p = $providers[$key]
        Configure-Remote -RcloneExe $rclone -RemoteName $p.Name -ProviderType $p.Type
    }
}
else {
    $p = $providers[$Provider]
    Configure-Remote -RcloneExe $rclone -RemoteName $p.Name -ProviderType $p.Type
}

Write-Host "`nVerify remotes:" -ForegroundColor Cyan
& $rclone listremotes
Write-Host "`nUpdate config.yaml rclone_remote names to match the remotes above."
