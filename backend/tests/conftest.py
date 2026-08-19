import pytest


@pytest.fixture(autouse=True)
def _fast_sleep(monkeypatch):
    """mock provider 每階段 sleep(1) 模擬處理感,測試時跳過以加速。"""
    import app.providers.mock as mock_module

    monkeypatch.setattr(mock_module.time, "sleep", lambda *_args, **_kwargs: None)
