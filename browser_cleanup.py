"""Self-heal a stuck Edge/Chrome profile so a crashed/force-killed run doesn't
wedge every subsequent launch.

Two failure modes we hit in practice:
- Stale Chromium singleton lock files left in the profile dir (Linux/macOS).
- Zombie browser processes still holding the profile's user-data-dir (Windows),
  which makes `launch_persistent_context` hang until timeout.

Both helpers are best-effort and SCOPED TO THE PROFILE PATH, so the operator's
personal browser (a different --user-data-dir) is never touched.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_LOCK_NAMES = ("SingletonLock", "SingletonCookie", "SingletonSocket", "lockfile")


def remove_stale_locks(profile_dir: str | Path) -> int:
    """Delete leftover Chromium singleton lock files in the profile dir. Returns count removed."""
    p = Path(profile_dir)
    removed = 0
    for name in _LOCK_NAMES:
        f = p / name
        try:
            if f.exists() or f.is_symlink():
                f.unlink()
                removed += 1
        except OSError:
            pass
    return removed


def kill_profile_processes(profile_dir: str | Path) -> int:
    """Kill browser processes whose command line references THIS profile dir.

    Windows-only (PowerShell); a no-op elsewhere. Scoped by the profile path so
    the user's personal browser is left alone. Returns processes asked to stop.
    """
    if not sys.platform.startswith("win"):
        return 0
    target = str(Path(profile_dir)).replace("'", "''")
    ps = (
        "$n=0; "
        "Get-CimInstance Win32_Process -Filter \"Name='msedge.exe' OR Name='chrome.exe'\" | "
        f"Where-Object {{ $_.CommandLine -like '*{target}*' }} | "
        "ForEach-Object { $n++; Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }; "
        "Write-Output $n"
    )
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, text=True, timeout=20,
        )
        return int((out.stdout or "0").strip() or 0)
    except (subprocess.SubprocessError, ValueError, OSError):
        return 0


def cleanup_profile(profile_dir: str | Path) -> dict:
    """Full self-heal: kill stray profile processes + remove stale lock files."""
    killed = kill_profile_processes(profile_dir)
    removed = remove_stale_locks(profile_dir)
    return {"killed": killed, "removed_locks": removed}
