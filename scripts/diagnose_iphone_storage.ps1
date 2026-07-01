$ErrorActionPreference = 'SilentlyContinue'
$shell = New-Object -ComObject Shell.Application
$computer = $shell.Namespace(17)
foreach ($device in $computer.Items()) {
    if ($device.Name -notmatch 'iPhone|Apple|iPad') { continue }
    Write-Output "=== $($device.Name) ==="
    $devNs = $shell.Namespace($device.Path)
    foreach ($storage in $devNs.Items()) {
        Write-Output "STORAGE: $($storage.Name)"
        Write-Output "  Path: $($storage.Path)"
        $storNs = $shell.Namespace($storage.Path)
        if (-not $storNs) {
            Write-Output "  (cannot open storage namespace)"
            continue
        }
        Write-Output "  Folders/files at root:"
        foreach ($item in $storNs.Items()) {
            $isFolder = $item.IsFolder
            Write-Output ("    [{0}] {1}" -f $(if ($isFolder) {'DIR'} else {'FILE'}), $item.Name)
            if ($isFolder -and $item.Name -match 'DCIM|Photo|100APPLE|Camera') {
                $subNs = $shell.Namespace($item.Path)
                if ($subNs) {
                    $subCount = ($subNs.Items() | Measure-Object).Count
                    Write-Output "      -> children: $subCount"
                }
            }
        }
        # Try recursive search for DCIM anywhere 2 levels deep
        foreach ($item in $storNs.Items()) {
            if (-not $item.IsFolder) { continue }
            $subNs = $shell.Namespace($item.Path)
            if (-not $subNs) { continue }
            foreach ($sub in $subNs.Items()) {
                if ($sub.Name -eq 'DCIM' -or $sub.Name -match 'APPLE') {
                    Write-Output "  FOUND nested: $($item.Name)/$($sub.Name) at $($sub.Path)"
                }
            }
        }
    }
}
