from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.auth import require_api_key
from app.config import settings


def _make_app():
    app = FastAPI()

    @app.get("/protected", dependencies=[Depends(require_api_key)])
    def protected():
        return {"ok": True}

    return app


def test_missing_header_returns_401_when_server_key_unset(monkeypatch):
    monkeypatch.setattr(settings, "API_KEY", "")
    client = TestClient(_make_app())
    resp = client.get("/protected")
    assert resp.status_code == 401


def test_empty_header_returns_401_when_server_key_unset(monkeypatch):
    # 空字串 header 不得等於未設定(空字串)的伺服器 key 而放行
    monkeypatch.setattr(settings, "API_KEY", "")
    client = TestClient(_make_app())
    resp = client.get("/protected", headers={"X-API-Key": ""})
    assert resp.status_code == 401


def test_any_header_returns_401_when_server_key_unset(monkeypatch):
    monkeypatch.setattr(settings, "API_KEY", "")
    client = TestClient(_make_app())
    resp = client.get("/protected", headers={"X-API-Key": "anything"})
    assert resp.status_code == 401


def test_correct_api_key_returns_200(monkeypatch):
    monkeypatch.setattr(settings, "API_KEY", "k1")
    client = TestClient(_make_app())
    resp = client.get("/protected", headers={"X-API-Key": "k1"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_wrong_api_key_returns_401(monkeypatch):
    monkeypatch.setattr(settings, "API_KEY", "k1")
    client = TestClient(_make_app())
    resp = client.get("/protected", headers={"X-API-Key": "k2"})
    assert resp.status_code == 401
