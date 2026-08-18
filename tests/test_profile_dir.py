"""Which browser profile the agent drives.

The profile must match the configured channel: an Edge run cannot borrow
Chrome's User Data dir. Getting this wrong silently sends the agent to a
profile that was never logged in.
"""
from pathlib import Path

import upwork_connect as uc

EDGE = Path(r"C:\Users\x\AppData\Local\Microsoft\Edge\User Data")
CHROME = Path(r"C:\Users\x\AppData\Local\Google\Chrome\User Data")


def _profiles(monkeypatch, edge=EDGE, chrome=CHROME):
    monkeypatch.setattr(uc, "edge_user_data", lambda: edge)
    monkeypatch.setattr(uc, "chrome_user_data", lambda: chrome)


def test_real_profile_matches_msedge_channel(monkeypatch):
    _profiles(monkeypatch)
    monkeypatch.setenv("UPWORK_BROWSER_CHANNEL", "msedge")
    assert uc.real_user_data() == EDGE  # NOT Chrome's dir


def test_real_profile_matches_chrome_channel(monkeypatch):
    _profiles(monkeypatch)
    monkeypatch.setenv("UPWORK_BROWSER_CHANNEL", "chrome")
    assert uc.real_user_data() == CHROME


def test_bundled_chromium_has_no_real_profile(monkeypatch):
    _profiles(monkeypatch)
    monkeypatch.setenv("UPWORK_BROWSER_CHANNEL", "chromium")
    assert uc.real_user_data() is None


def test_pick_profile_uses_personal_edge_when_enabled(monkeypatch):
    _profiles(monkeypatch)
    monkeypatch.delenv("UPWORK_USER_DATA_DIR", raising=False)
    monkeypatch.setenv("UPWORK_BROWSER_CHANNEL", "msedge")
    monkeypatch.setenv("UPWORK_USE_REAL_PROFILE", "1")
    assert uc.pick_profile_dir() == EDGE


def test_pick_profile_uses_project_profile_when_disabled(monkeypatch):
    _profiles(monkeypatch)
    monkeypatch.delenv("UPWORK_USER_DATA_DIR", raising=False)
    monkeypatch.setenv("UPWORK_BROWSER_CHANNEL", "msedge")
    monkeypatch.setenv("UPWORK_USE_REAL_PROFILE", "0")
    assert uc.pick_profile_dir() == uc.project_profile_dir()


def test_legacy_chrome_profile_flag_still_honoured(monkeypatch):
    _profiles(monkeypatch)
    monkeypatch.delenv("UPWORK_USER_DATA_DIR", raising=False)
    monkeypatch.delenv("UPWORK_USE_REAL_PROFILE", raising=False)
    monkeypatch.setenv("UPWORK_BROWSER_CHANNEL", "msedge")
    monkeypatch.setenv("UPWORK_USE_CHROME_PROFILE", "0")  # pre-existing .env files
    assert uc.pick_profile_dir() == uc.project_profile_dir()


def test_new_flag_wins_over_legacy_alias(monkeypatch):
    _profiles(monkeypatch)
    monkeypatch.delenv("UPWORK_USER_DATA_DIR", raising=False)
    monkeypatch.setenv("UPWORK_BROWSER_CHANNEL", "msedge")
    monkeypatch.setenv("UPWORK_USE_REAL_PROFILE", "1")
    monkeypatch.setenv("UPWORK_USE_CHROME_PROFILE", "0")
    assert uc.pick_profile_dir() == EDGE


def test_explicit_user_data_dir_overrides_everything(monkeypatch):
    _profiles(monkeypatch)
    monkeypatch.setenv("UPWORK_USE_REAL_PROFILE", "1")
    monkeypatch.setenv("UPWORK_USER_DATA_DIR", r"D:\custom\profile")
    assert uc.pick_profile_dir() == Path(r"D:\custom\profile")


def test_falls_back_to_project_profile_when_no_real_profile(monkeypatch):
    _profiles(monkeypatch, edge=None)  # Edge not installed
    monkeypatch.delenv("UPWORK_USER_DATA_DIR", raising=False)
    monkeypatch.setenv("UPWORK_BROWSER_CHANNEL", "msedge")
    monkeypatch.setenv("UPWORK_USE_REAL_PROFILE", "1")
    assert uc.pick_profile_dir() == uc.project_profile_dir()
