"""Optional residential proxy config from env (for server Cloudflare bypass)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import upwork_connect  # noqa: E402


def test_no_proxy_when_unset(monkeypatch):
    monkeypatch.delenv("UPWORK_PROXY_SERVER", raising=False)
    assert upwork_connect.proxy_from_env() is None


def test_proxy_server_only(monkeypatch):
    monkeypatch.setenv("UPWORK_PROXY_SERVER", "http://proxy.example:8000")
    monkeypatch.delenv("UPWORK_PROXY_USERNAME", raising=False)
    monkeypatch.delenv("UPWORK_PROXY_PASSWORD", raising=False)
    assert upwork_connect.proxy_from_env() == {"server": "http://proxy.example:8000"}


def test_proxy_with_credentials(monkeypatch):
    monkeypatch.setenv("UPWORK_PROXY_SERVER", "socks5://proxy.example:1080")
    monkeypatch.setenv("UPWORK_PROXY_USERNAME", "user1")
    monkeypatch.setenv("UPWORK_PROXY_PASSWORD", "secret")
    assert upwork_connect.proxy_from_env() == {
        "server": "socks5://proxy.example:1080",
        "username": "user1",
        "password": "secret",
    }
