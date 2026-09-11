import pytest

from app.config import settings


@pytest.fixture(autouse=True)
def _fast_sleep(monkeypatch):
    """mock provider 每階段 sleep(1) 模擬處理感,測試時跳過以加速。"""
    import app.providers.mock as mock_module

    monkeypatch.setattr(mock_module.time, "sleep", lambda *_args, **_kwargs: None)


@pytest.fixture(autouse=True)
def _test_api_key(monkeypatch):
    """API_KEY 預設為空字串(未設定即拒絕);測試環境需要一個非空值供 header 比對。"""
    monkeypatch.setattr(settings, "API_KEY", "test-key")
