"""Settings 機密欄位預設值:不得帶硬編憑證,未設環境變數時一律空字串。"""
import pytest
from pydantic import ValidationError

from app.config import Settings


def test_api_key_defaults_to_empty_when_unset(monkeypatch):
    monkeypatch.delenv("API_KEY", raising=False)
    assert Settings().API_KEY == ""


def test_postgres_url_defaults_to_empty_when_unset(monkeypatch):
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    assert Settings().POSTGRES_URL == ""


def test_local_llm_timeout_reads_env(monkeypatch):
    """兩個不同值都要讀得到:單一案例會被寫死的回傳值蒙混過去。"""
    monkeypatch.setenv("LOCAL_LLM_TIMEOUT", "900")
    assert Settings().LOCAL_LLM_TIMEOUT == 900.0
    monkeypatch.setenv("LOCAL_LLM_TIMEOUT", "1800.5")
    assert Settings().LOCAL_LLM_TIMEOUT == 1800.5


def test_local_llm_timeout_defaults_when_unset(monkeypatch):
    monkeypatch.delenv("LOCAL_LLM_TIMEOUT", raising=False)
    assert Settings().LOCAL_LLM_TIMEOUT == 300.0


def test_local_llm_timeout_rejects_non_numeric(monkeypatch):
    """打錯字必須在啟動時就炸,不能被當成 0 而讓每個請求立刻逾時。"""
    monkeypatch.setenv("LOCAL_LLM_TIMEOUT", "半小時")
    with pytest.raises(ValidationError):
        Settings()
