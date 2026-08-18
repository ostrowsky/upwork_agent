"""Gating around the .env-credential auto-login.

The mechanism itself (filling Upwork's login form) needs a browser; what is
worth protecting here is the guard that decides WHETHER to attempt it, because
an unthrottled retry loop is what gets an account flagged as a bot.
"""
import json
import time

import upwork_connect as uc


def _state_file(tmp_path, monkeypatch):
    monkeypatch.setattr(uc, "BASE_DIR", tmp_path)
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    return tmp_path / "data" / "auto_login_state.json"


def test_auto_login_on_by_default(monkeypatch):
    monkeypatch.delenv("UPWORK_AUTO_LOGIN", raising=False)
    assert uc.auto_login_enabled() is True


def test_auto_login_can_be_disabled(monkeypatch):
    monkeypatch.setenv("UPWORK_AUTO_LOGIN", "0")
    assert uc.auto_login_enabled() is False


def test_first_attempt_allowed_then_throttled(tmp_path, monkeypatch):
    _state_file(tmp_path, monkeypatch)
    assert uc._auto_login_allowed_now() is True
    # probe_session runs every tick — the next one must NOT retry the login.
    assert uc._auto_login_allowed_now() is False


def test_attempt_allowed_again_after_cooldown(tmp_path, monkeypatch):
    state = _state_file(tmp_path, monkeypatch)
    monkeypatch.setenv("AUTO_LOGIN_COOLDOWN", "60")
    assert uc._auto_login_allowed_now() is True
    state.write_text(json.dumps({"last_attempt": time.time() - 61}), encoding="utf-8")
    assert uc._auto_login_allowed_now() is True


def test_corrupt_state_file_does_not_block(tmp_path, monkeypatch):
    state = _state_file(tmp_path, monkeypatch)
    state.write_text("not json at all", encoding="utf-8")
    assert uc._auto_login_allowed_now() is True


def test_cooldown_falls_back_on_bad_env(monkeypatch):
    monkeypatch.setenv("AUTO_LOGIN_COOLDOWN", "abc")
    assert uc._auto_login_cooldown_s() == 1800
