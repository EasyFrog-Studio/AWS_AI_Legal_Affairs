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


def test_missing_api_key_returns_401():
    client = TestClient(_make_app())
    resp = client.get("/protected")
    assert resp.status_code == 401


def test_wrong_api_key_returns_401():
    client = TestClient(_make_app())
    resp = client.get("/protected", headers={"X-API-Key": "wrong"})
    assert resp.status_code == 401


def test_correct_api_key_returns_200():
    client = TestClient(_make_app())
    resp = client.get("/protected", headers={"X-API-Key": settings.API_KEY})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
