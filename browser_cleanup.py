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

import json
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


def channel_process_name() -> str:
    """Process name of the browser the AGENT drives (NOT the user's daily browser).

    Default msedge — the whole point of the msedge channel is to not collide with
    a daily Google Chrome, so the nuclear kill must NOT touch chrome unless the
    agent itself is configured to use chrome.
    """
    channel = os.getenv("UPWORK_BROWSER_CHANNEL", "msedge").strip().lower() or "msedge"
    return {"msedge": "msedge", "chrome": "chrome", "chromium": "chrome"}.get(channel, "msedge")


def kill_all_browsers() -> int:
    """Last-resort: kill every process of the AGENT's browser channel only.

    Edge 'startup boost'/background mode respawns a profile holder that a
    profile-scoped kill can't outrun, so we kill all of THAT channel's processes
    (e.g. all msedge) — but never the user's other browser (e.g. Chrome).
    Windows-only; no-op elsewhere.
    """
    if not sys.platform.startswith("win"):
        return 0
    proc = channel_process_name()
    ps = (
        f"$n=0; Get-Process {proc} -ErrorAction SilentlyContinue | "
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


def clear_crash_flags(profile_dir: str | Path) -> int:
    """Mark the profile as cleanly exited. Returns how many files were fixed.

    Every heal path here force-kills the browser, which leaves
    `profile.exit_type = "Crashed"` in the profile's Preferences. Edge then
    opens its crash-recovery prompt on the NEXT launch, and that prompt blocks
    the CDP handshake until Playwright times out — so a force-kill reliably
    wedges the following run. Resetting the flag is what Chromium itself does
    after a clean shutdown.
    """
    fixed = 0
    root = Path(profile_dir)
    # The profile may be the user-data root (…/Default/Preferences) or, for a
    # real browser User Data dir, any of its profile subfolders.
    for pref in list(root.glob("*/Preferences")) + [root / "Preferences"]:
        if not pref.is_file():
            continue
        try:
            data = json.loads(pref.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        prof = data.get("profile")
        if not isinstance(prof, dict):
            continue
        if prof.get("exit_type") == "Normal" and prof.get("exited_cleanly") is True:
            continue
        prof["exit_type"] = "Normal"
        prof["exited_cleanly"] = True
        try:
            pref.write_text(json.dumps(data), encoding="utf-8")
            fixed += 1
        except OSError:
            continue
    return fixed


def cleanup_profile(profile_dir: str | Path) -> dict:
    """Full self-heal: kill stray profile processes, remove stale lock files,
    and clear the crash flag our own force-kill just set."""
    killed = kill_profile_processes(profile_dir)
    removed = remove_stale_locks(profile_dir)
    cleared = clear_crash_flags(profile_dir)
    return {"killed": killed, "removed_locks": removed, "cleared_crash_flags": cleared}
