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
    """Kill the browser process TREE bound to THIS profile dir.

    Windows-only (PowerShell); a no-op elsewhere. Edge's renderer/GPU children
    don't carry the profile path on their command line, so matching by cmdline
    alone orphans them and leaves the profile locked. We therefore find the
    profile-matched ROOT processes and kill them plus every descendant (walked
    via ParentProcessId) in one pass. Scoped to the profile tree, so the user's
    personal browser (a different profile/tree) is never touched.
    """
    if not sys.platform.startswith("win"):
        return 0
    target = str(Path(profile_dir)).replace("'", "''")
    ps = (
        "$all = Get-CimInstance Win32_Process -Filter \"Name='msedge.exe' OR Name='chrome.exe'\"; "
        "$byParent = @{}; foreach ($p in $all) { $byParent[$p.ProcessId] = $p }; "
        f"$roots = $all | Where-Object {{ $_.CommandLine -like '*{target}*' }} | "
        "Select-Object -ExpandProperty ProcessId; "
        "$kill = New-Object System.Collections.Generic.HashSet[int]; "
        "$queue = New-Object System.Collections.Generic.Queue[int]; "
        "foreach ($r in $roots) { [void]$queue.Enqueue($r) }; "
        "while ($queue.Count -gt 0) { $id = $queue.Dequeue(); if (-not $kill.Add($id)) { continue }; "
        "foreach ($p in $all) { if ($p.ParentProcessId -eq $id) { [void]$queue.Enqueue($p.ProcessId) } } }; "
        "$n=0; foreach ($id in $kill) { try { Stop-Process -Id $id -Force -ErrorAction Stop; $n++ } catch {} }; "
        "Write-Output $n"
    )
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, text=True, timeout=25,
        )
        return int((out.stdout or "0").strip() or 0)
    except (subprocess.SubprocessError, ValueError, OSError):
        return 0


def kill_all_browsers() -> int:
    """Last-resort: kill EVERY msedge/chrome process (closes the user's browser too).

    Edge 'startup boost'/background mode respawns a profile holder that a
    profile-scoped kill can't outrun, and ANY live Edge can wedge a persistent
    launch. The operator workflow already force-closes all Edge before agent
    runs, so this matches reality. Windows-only; no-op elsewhere.
    """
    if not sys.platform.startswith("win"):
        return 0
    ps = (
        "$n=0; Get-Process msedge,chrome -ErrorAction SilentlyContinue | "
        "ForEach-Object { try { Stop-Process -Id $_.Id -Force -ErrorAction Stop; $n++ } catch {} }; "
        "Write-Output $n"
    )
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, text=True, timeout=25,
        )
        return int((out.stdout or "0").strip() or 0)
    except (subprocess.SubprocessError, ValueError, OSError):
        return 0


def kill_all_on_stuck_enabled() -> bool:
    """Allow the nuclear all-Edge kill when a profile-scoped heal fails (default on)."""
    return os.getenv("BROWSER_KILL_ALL_ON_STUCK", "1").strip().lower() in ("1", "true", "yes")


def cleanup_profile(profile_dir: str | Path) -> dict:
    """Full self-heal: kill stray profile processes + remove stale lock files."""
    killed = kill_profile_processes(profile_dir)
    removed = remove_stale_locks(profile_dir)
    return {"killed": killed, "removed_locks": removed}
