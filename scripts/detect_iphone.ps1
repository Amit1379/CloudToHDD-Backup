$ErrorActionPreference = 'SilentlyContinue'
$result = @{
    status = 'not_found'
    device_name = ''
    storage_name = ''
    dcim_path = ''
    dcim_file_count = 0
    message = 'No iPhone detected. Connect via USB and unlock the phone.'
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
    if ($name -notmatch 'iPhone|iPad|Apple') { continue }

    $result.device_name = $name
    $result.status = 'connected_locked'
    $result.message = 'iPhone connected but storage is not accessible. Unlock iPhone, tap Trust This Computer, and allow Full Access to photos if prompted.'

    $devNs = $shell.Namespace($device.Path)
    if (-not $devNs) { break }

    foreach ($storage in $devNs.Items()) {
        $result.storage_name = $storage.Name
        $storNs = $shell.Namespace($storage.Path)
        if (-not $storNs) { continue }

        $result.status = 'connected_no_dcim'
        $result.message = 'iPhone storage found but DCIM folder is not visible. Unlock iPhone, tap Trust, then open Photos app once.'

        foreach ($item in $storNs.Items()) {
            if ($item.Name -ne 'DCIM') { continue }
            $dcimPath = $item.Path
            if (-not $dcimPath) { continue }

            $fileCount = 0
            try {
                $fileCount = (Get-ChildItem -LiteralPath $dcimPath -Recurse -File -ErrorAction Stop | Measure-Object).Count
            } catch {
                $fileCount = 0
            }

            if ($fileCount -gt 0 -or (Test-Path -LiteralPath $dcimPath)) {
                $result.status = 'ready'
                $result.dcim_path = $dcimPath
                $result.dcim_file_count = $fileCount
                $result.message = "Ready: $fileCount photo/video file(s) in DCIM."
                $result | ConvertTo-Json -Compress
                exit 0
            }
        }
    }
    break
}

$result | ConvertTo-Json -Compress
