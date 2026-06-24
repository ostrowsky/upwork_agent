"""Portable session bootstrap: seed a fresh profile from storage_state JSON."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import upwork_connect  # noqa: E402


class _FakeContext:
    def __init__(self):
        self.added = None

    def add_cookies(self, cookies):
        self.added = cookies


def _write_state(tmp_path, cookies):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "upwork_storage_state.json").write_text(
        json.dumps({"cookies": cookies, "origins": []}), encoding="utf-8"
    )


def test_bootstrap_injects_cookies(monkeypatch, tmp_path):
    monkeypatch.setattr(upwork_connect, "BASE_DIR", tmp_path)
    monkeypatch.delenv("UPWORK_BOOTSTRAP_COOKIES", raising=False)
    _write_state(tmp_path, [{"name": "sid", "value": "abc", "domain": ".upwork.com", "path": "/"}])

    ctx = _FakeContext()
    upwork_connect._bootstrap_cookies(ctx)
    assert ctx.added and ctx.added[0]["name"] == "sid"


def test_bootstrap_normalizes_bad_samesite(monkeypatch, tmp_path):
    monkeypatch.setattr(upwork_connect, "BASE_DIR", tmp_path)
    _write_state(tmp_path, [{"name": "x", "value": "1", "domain": ".upwork.com",
                             "path": "/", "sameSite": "unspecified"}])

    ctx = _FakeContext()
    upwork_connect._bootstrap_cookies(ctx)
    assert "sameSite" not in ctx.added[0]  # invalid value dropped


def test_bootstrap_noop_without_file(monkeypatch, tmp_path):
    monkeypatch.setattr(upwork_connect, "BASE_DIR", tmp_path)
    ctx = _FakeContext()
    upwork_connect._bootstrap_cookies(ctx)
    assert ctx.added is None


def test_bootstrap_disabled_by_env(monkeypatch, tmp_path):
    monkeypatch.setattr(upwork_connect, "BASE_DIR", tmp_path)
    monkeypatch.setenv("UPWORK_BOOTSTRAP_COOKIES", "0")
    _write_state(tmp_path, [{"name": "sid", "value": "abc", "domain": ".upwork.com", "path": "/"}])

    ctx = _FakeContext()
    upwork_connect._bootstrap_cookies(ctx)
    assert ctx.added is None
