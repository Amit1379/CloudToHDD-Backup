param(
    [Parameter(Mandatory = $true)][string]$Source,
    [Parameter(Mandatory = $true)][string]$Destination
)
$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $Destination)) {
    New-Item -ItemType Directory -Path $Destination -Force | Out-Null
}
& robocopy $Source $Destination /E /COPY:DAT /DCOPY:DAT /R:2 /W:3 /MT:8 /XJ /NP /NFL /NDL
$code = $LASTEXITCODE
if ($code -le 7) { exit 0 }
Copy-Item -LiteralPath $Source -Destination $Destination -Recurse -Force
exit 0
