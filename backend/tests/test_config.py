"""Settings 機密欄位預設值:不得帶硬編憑證,未設環境變數時一律空字串。"""
from app.config import Settings


def test_api_key_defaults_to_empty_when_unset(monkeypatch):
    monkeypatch.delenv("API_KEY", raising=False)
    assert Settings().API_KEY == ""


def test_postgres_url_defaults_to_empty_when_unset(monkeypatch):
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    assert Settings().POSTGRES_URL == ""
