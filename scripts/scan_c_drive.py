"""Quick scan of common large folders on C: (read-only)."""
from __future__ import annotations

import os
import shutil
from pathlib import Path


def size_of(path: str | Path) -> int:
    total = 0
    p = Path(path)
    if not p.exists():
        return 0
    try:
        for root, _dirs, files in os.walk(p):
            for name in files:
                fp = os.path.join(root, name)
                try:
                    total += os.path.getsize(fp)
                except OSError:
                    pass
    except OSError:
        pass
    return total


def fmt_gb(n: int) -> str:
    return f"{n / (1024**3):.2f} GB"


def main() -> None:
    candidates = [
        r"C:\ProgramData\Package Cache",
        r"C:\ProgramData\Microsoft\Windows\AppRepository",
        r"C:\ProgramData\Microsoft\Windows\WER",
        r"C:\ProgramData\Docker",
        r"C:\ProgramData\VMware",
        r"C:\ProgramData\USOShared",
        r"C:\ProgramData\NVIDIA Corporation",
        r"C:\ProgramData\Adobe",
        r"C:\Program Files\WindowsApps",
        os.path.expandvars(r"%LOCALAPPDATA%\Packages"),
        os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Windows\INetCache"),
        os.path.expandvars(r"%LOCALAPPDATA%\Temp"),
        os.path.expandvars(r"%USERPROFILE%\Downloads"),
        os.path.expandvars(r"%USERPROFILE%\Videos\Screen Recordings"),
        os.path.expandvars(r"%USERPROFILE%\Downloads\DaVinci_Resolve_21.0_Windows"),
        r"C:\Windows\SoftwareDistribution\Download",
        r"C:\Windows\Temp",
        r"C:\$Recycle.Bin",
    ]

    rows: list[tuple[int, str]] = []
    for c in candidates:
        s = size_of(c)
        if s > 50 * 1024 * 1024:
            rows.append((s, c))
    rows.sort(reverse=True)

    print("=== Large folders you may be able to clean (>50 MB) ===\n")
    for s, c in rows:
        print(f"  {fmt_gb(s):>10}  {c}")

    # ProgramData top-level (may hit permission errors)
    pd = Path(r"C:\ProgramData")
    if pd.exists():
        print("\n=== C:\\ProgramData top-level folders ===\n")
        pd_rows: list[tuple[int, str]] = []
        try:
            for entry in os.scandir(pd):
                if entry.is_dir():
                    s = size_of(entry.path)
                    if s > 100 * 1024 * 1024:
                        pd_rows.append((s, entry.name))
        except OSError as exc:
            print(f"  (limited access: {exc})")
        pd_rows.sort(reverse=True)
        for s, name in pd_rows[:15]:
            print(f"  {fmt_gb(s):>10}  {name}")

    total_bytes, used_bytes, free_bytes = shutil.disk_usage("C:/")
    need = 220 * (1024**3)
    print(f"\n=== C: drive ===")
    print(f"  Free:      {fmt_gb(free_bytes)}")
    print(f"  Total:     {fmt_gb(total_bytes)}")
    print(f"  Used:      {fmt_gb(used_bytes)}")
    print(f"  Need:      ~220 GB for OneDrive download")
    print(f"  Shortfall: {fmt_gb(max(0, need - free_bytes))}")


if __name__ == "__main__":
    main()
