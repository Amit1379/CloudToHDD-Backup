$ErrorActionPreference = 'SilentlyContinue'
$shell = New-Object -ComObject Shell.Application
$computer = $shell.Namespace(17)
Write-Output '=== Portable / phone devices ==='
foreach ($device in $computer.Items()) {
    $name = $device.Name
    if ($name -match 'OS \(C:\)|WD_BLACK|DVD|Network|Apple iPhone') { continue }
    Write-Output ("DEVICE: " + $name)
    Write-Output ("  Path: " + $device.Path)
    $devNs = $shell.Namespace($device.Path)
    if (-not $devNs) { continue }
    foreach ($storage in $devNs.Items()) {
        Write-Output ("  STORAGE: " + $storage.Name)
        Write-Output ("    Path: " + $storage.Path)
        $storNs = $shell.Namespace($storage.Path)
        if ($storNs) {
            foreach ($item in $storNs.Items()) {
                if ($item.IsFolder) {
                    Write-Output ("    FOLDER: " + $item.Name)
                }
            }
        }
    }
}
