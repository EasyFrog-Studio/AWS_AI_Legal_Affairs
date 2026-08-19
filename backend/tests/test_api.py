import os

os.environ.setdefault("AI_PROVIDER", "mock")
os.environ.setdefault("API_KEY", "demo-key-2026")

from pathlib import Path

from fastapi.testclient import TestClient

import app.main as main_module
from app.config import settings

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _headers():
    return {"X-API-Key": settings.API_KEY}


def test_health_no_api_key_required():
    client = TestClient(main_module.app)
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "provider": "mock"}


def test_cases_endpoints_require_api_key():
    client = TestClient(main_module.app)
    resp = client.get("/api/cases")
    assert resp.status_code == 401


def test_create_case_with_text_returns_case_id(monkeypatch):
    monkeypatch.setattr(settings, "MOCK_DATA_DIR", str(FIXTURES_DIR))
    client = TestClient(main_module.app)
    resp = client.post("/api/cases", data={"text": "訴願人王大明不服彰化縣環境保護局裁處罰鍰,提起廢棄物清理訴願。"}, headers=_headers())
    assert resp.status_code == 200
    body = resp.json()
    assert "case_id" in body
    assert body["case_id"].startswith("c-")


def test_create_case_missing_input_returns_400():
    client = TestClient(main_module.app)
    resp = client.post("/api/cases", data={}, headers=_headers())
    assert resp.status_code == 400


def test_full_flow_create_list_get_completed_case(monkeypatch):
    monkeypatch.setattr(settings, "MOCK_DATA_DIR", str(FIXTURES_DIR))
    client = TestClient(main_module.app)

    create_resp = client.post(
        "/api/cases",
        data={"text": "訴願人王大明不服彰化縣環境保護局裁處罰鍰,提起廢棄物清理訴願。"},
        headers=_headers(),
    )
    assert create_resp.status_code == 200
    case_id = create_resp.json()["case_id"]

    list_resp = client.get("/api/cases", headers=_headers())
    assert list_resp.status_code == 200
    summaries = list_resp.json()
    assert any(c["case_id"] == case_id for c in summaries)

    get_resp = client.get(f"/api/cases/{case_id}", headers=_headers())
    assert get_resp.status_code == 200
    case = get_resp.json()
    assert case["case_id"] == case_id
    assert case["status"] == "done"
    assert case["current_stage"] == "done"
    assert case["track"] == "admissible"
    assert case["f1"]["appellant"] == "王大明"
    assert case["f4"] is not None


def test_get_nonexistent_case_returns_404():
    client = TestClient(main_module.app)
    resp = client.get("/api/cases/c-notexist", headers=_headers())
    assert resp.status_code == 404


def test_source_endpoint_mock_mode_returns_text_field():
    client = TestClient(main_module.app)
    resp = client.get("/api/source", params={"key": "markdown/相關法規/不存在.md"}, headers=_headers())
    assert resp.status_code == 200
    assert "text" in resp.json()
