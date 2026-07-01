$ErrorActionPreference = 'Continue'
Write-Output '=== USB / Apple PnP devices ==='
Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue |
    Where-Object { $_.FriendlyName -match 'iPhone|Apple|iPad|Mobile Device' } |
    ForEach-Object { Write-Output ("  $($_.Status) | $($_.Class) | $($_.FriendlyName)") }

Write-Output ''
Write-Output '=== Removable volumes ==='
Get-Volume -ErrorAction SilentlyContinue |
    Where-Object { $_.DriveType -eq 'Removable' -and $_.DriveLetter } |
    ForEach-Object { Write-Output ("  $($_.DriveLetter): $($_.FileSystemLabel) free=$([math]::Round($_.SizeRemaining/1GB,2))GB") }

Write-Output ''
Write-Output '=== Shell This PC (namespace 17) ==='
$shell = New-Object -ComObject Shell.Application
$computer = $shell.Namespace(17)
if (-not $computer) {
    Write-Output 'ERROR: Could not open Shell namespace 17'
    exit 1
}

foreach ($device in $computer.Items()) {
    Write-Output ("DEVICE: $($device.Name)")
    Write-Output ("  Path: $($device.Path)")
    $devNs = $shell.Namespace($device.Path)
    if (-not $devNs) { continue }
    foreach ($storage in $devNs.Items()) {
        Write-Output ("  STORAGE: $($storage.Name)")
        Write-Output ("    Path: $($storage.Path)")
        $dcim = Join-Path $storage.Path 'DCIM'
        $exists = Test-Path -LiteralPath $dcim
        Write-Output ("    DCIM exists: $exists -> $dcim")
        if ($exists) {
            $count = (Get-ChildItem -LiteralPath $dcim -Recurse -File -ErrorAction SilentlyContinue | Measure-Object).Count
            Write-Output ("    DCIM file count: $count")
        }
    }
}
