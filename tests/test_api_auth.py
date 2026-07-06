from fastapi.testclient import TestClient

import api.server as srv
from config import Config

client = TestClient(srv.app, raise_server_exceptions=False)


def _login(user: str, password: str) -> int:
    return client.get("/api/status", auth=(user, password)).status_code


def test_healthz_needs_no_auth():
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_plain_ascii_password(monkeypatch):
    monkeypatch.setattr(Config, "DASHBOARD_PASS", "Secret123")
    assert _login("admin", "Secret123") == 200
    assert _login("admin", "wrong") == 401


def test_non_ascii_password(monkeypatch):
    monkeypatch.setattr(Config, "DASHBOARD_PASS", "sécret£123")
    assert _login("admin", "sécret£123") == 200
    assert _login("admin", "secret123") == 401


def test_env_value_whitespace_is_stripped(monkeypatch):
    monkeypatch.setattr(Config, "DASHBOARD_PASS", "Secret123 \n")
    assert _login("admin", "Secret123") == 200


def test_no_credentials_rejected():
    assert client.get("/api/status").status_code == 401
