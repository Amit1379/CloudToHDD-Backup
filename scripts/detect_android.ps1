$ErrorActionPreference = 'SilentlyContinue'
$result = @{
    status = 'not_found'
    device_name = ''
    storage_path = ''
    folders = @()
    file_count = 0
    message = 'No Android phone detected. Connect via USB, enable File Transfer (MTP), unlock phone.'
}

$shell = New-Object -ComObject Shell.Application
$computer = $shell.Namespace(17)
if (-not $computer) {
    $result.message = 'Shell API unavailable.'
    $result | ConvertTo-Json -Compress
    exit 0
}

foreach ($device in $computer.Items()) {
    $name = $device.Name
    $path = $device.Path
    # MTP phones only — skip local disks (C:, E:), iPhone, shortcuts
    if ($path -notmatch 'usb#') { continue }
    if ($name -match 'iPhone|Apple|iPad') { continue }

    $result.device_name = $name
    $result.status = 'connected_locked'
    $result.message = 'Phone connected but storage not accessible. Unlock phone and set USB to File Transfer / MTP.'

    $devNs = $shell.Namespace($device.Path)
    if (-not $devNs) { break }

    foreach ($storage in $devNs.Items()) {
        $storagePath = $storage.Path
        if (-not $storagePath) { continue }
        $result.storage_path = $storagePath

        $storNs = $shell.Namespace($storagePath)
        if (-not $storNs) { continue }

        $folderNames = @('DCIM', 'Pictures', 'Download', 'Downloads', 'Camera',
            'WhatsApp/Media/WhatsApp Images', 'WhatsApp/Media/WhatsApp Video')
        $found = @()
        $fileCount = 0

        foreach ($item in $storNs.Items()) {
            if (-not $item.IsFolder) { continue }
            $found += $item.Name
        }

        foreach ($fname in $folderNames) {
            $target = Join-Path $storagePath $fname
            if (Test-Path -LiteralPath $target) {
                if ($fname -notin $found) { $found += $fname }
                try {
                    $c = (Get-ChildItem -LiteralPath $target -Recurse -File -ErrorAction Stop | Measure-Object).Count
                    $fileCount += $c
                } catch { }
            }
        }

        $result.folders = $found
        $result.file_count = $fileCount
        if ($fileCount -gt 0 -or $found.Count -gt 0) {
            $result.status = 'ready'
            $result.message = "Ready: $($found.Count) folder(s), $fileCount file(s) visible."
        } else {
            $result.status = 'connected_locked'
            $result.message = 'Phone found but no folders readable. Unlock, enable MTP/File Transfer, swipe notification to allow USB access.'
        }
        $result | ConvertTo-Json -Compress
        exit 0
    }
    break
}

$result | ConvertTo-Json -Compress
