# CloudToHDD Backup

**Photo Backup from Cloud to Local HDD** — securely copy photos and files from **iPhone**, **Android**, **Google Photos**, **Google Drive**, **OneDrive**, and **iCloud** to a local drive with checksum verification, incremental sync, structured organization, and full audit logs.

## Features

- **Double-click launchers** — no terminal knowledge required
- **Setup wizard** — guided configuration in plain English
- **Interactive menu** — run, preview, verify, or reconfigure
- **100% verification mode** — checks every file; auto-retries missing ones (up to 3 rounds)
- **Windows robocopy engine** — fast, reliable bulk copy for OneDrive/local sync
- **rclone integration** — full cloud download for Google Drive & iCloud
- **Pre-flight checks** — disk space and write permission validation
- **Incremental sync** — skips unchanged files on subsequent runs
- **SHA-256 checksums** — integrity verification after copy
- **Progress bars** — live status during copy and verification
- **Audit reports** — JSON logs in `reports/` folder

## Download & run (no Python required)

Two standalone `.exe` files are built into `dist\` (rebuild with `scripts\build_exe.ps1`):

| File | Use when |
|------|----------|
| **`CloudToHDD-Backup.exe`** | Portable — one file, double-click to run. `config.yaml` is created next to the exe. |
| **`CloudToHDD-Backup-Setup.exe`** | Install — copies app to `%LOCALAPPDATA%\CloudToHDD Backup` and adds **Desktop + Start Menu** shortcuts. |

**Notes:**
- Windows 10/11 only
- No Python install needed
- First launch may take a few seconds (app unpacks internally)
- [rclone](https://rclone.org/) is optional (for full Google Drive / iCloud download)

### Build the exe yourself

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1
```

Output: `dist\CloudToHDD-Backup.exe` and `dist\CloudToHDD-Backup-Setup.exe`

## Easiest Way (from source — Windows)

| File | What it does |
|------|--------------|
| **`START_GUI.bat`** | **Graphical UI** — easiest visual interface |
| **`START_BACKUP.bat`** | Opens a simple menu — best for first-time users |
| **`QUICK_BACKUP.bat`** | Runs backup immediately (no menu) |

Just double-click `START_BACKUP.bat`, follow the wizard, then choose **1. Run backup now**.

## Quick Start (Command Line)

### 1. Prerequisites

- Windows 10/11
- Python 3.10+
- (Optional) [rclone](https://rclone.org/) for Google Drive / iCloud full download

### 2. Setup wizard (recommended)

```powershell
cd "c:\AllUserData\Amit\Files Backup from Cloud to Local Drive"
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py wizard
```

### 3. Run backup

```powershell
# Interactive menu (no commands to remember)
.\.venv\Scripts\python.exe main.py

# Or direct commands:
.\.venv\Scripts\python.exe main.py run --dry-run    # Preview
.\.venv\Scripts\python.exe main.py run              # Full backup with 100% verification
.\.venv\Scripts\python.exe main.py verify           # Re-check existing backup
```

## Output Structure

```
D:\CloudBackups\
├── onedrive\
│   └── 2026-06-07\
│       └── Documents\
│           └── report.pdf
├── google_drive\
│   └── 2026-06-07\
│       └── ...
├── icloud\
│   └── 2026-06-07\
│       └── ...
├── .manifests\
│   ├── onedrive.json
│   ├── google_drive.json
│   └── icloud.json
└── reports\
    └── backup_report_2026-06-07T12-00-00Z.json
```

## Backup Methods

| Method | When to use | Pros | Cons |
|--------|-------------|------|------|
| `sync_folder` | Cloud desktop app installed | Simple, no credentials in tool | Only files already synced locally |
| `rclone` | No sync app, or full cloud pull | Complete cloud access | Requires OAuth setup |
| `auto` | Default | Tries sync folder, falls back to rclone | — |

### Setting up rclone (optional)

```powershell
.\scripts\Setup-Rclone.ps1
```

Then match remote names in `config.yaml`:

```yaml
providers:
  onedrive:
    rclone_remote: "onedrive"
  google_drive:
    rclone_remote: "gdrive"
  icloud:
    rclone_remote: "icloud"
```

## Security

- Credentials stay in rclone's own config (not in this tool)
- Config file contains paths only — no passwords
- Checksum verification catches silent corruption
- Copy mode never deletes source files
- Mirror mode only removes extras in the **destination**

## Scheduling (Task Scheduler)

1. Open **Task Scheduler** → Create Basic Task
2. Trigger: Daily (or your preference)
3. Action: Start a program
   - Program: `powershell.exe`
   - Arguments: `-ExecutionPolicy Bypass -File "c:\AllUserData\Amit\Files Backup from Cloud to Local Drive\scripts\Run-CloudBackup.ps1"`
4. Run whether user is logged on or not (if using sync folders, user session may be required)

## CLI Reference

| Command | Description |
|---------|-------------|
| `python main.py init` | Create `config.yaml` from template |
| `python main.py detect` | Find sync folders and rclone |
| `python main.py status` | Show provider configuration |
| `python main.py run` | Execute backup |
| `python main.py run --dry-run` | Preview without copying |
| `python main.py run -p onedrive` | Backup one provider |

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Provider shows "unavailable" | Install the cloud desktop app, or set up rclone |
| OneDrive not detected | Set `sync_folder` manually in config.yaml |
| Permission denied on destination | Choose a writable drive; run as your user |
| iCloud files missing | Ensure iCloud for Windows has downloaded files locally |
| Slow backup | Enable `incremental: true`; run during off-hours |

## License

MIT — use freely for personal and commercial backup workflows.
