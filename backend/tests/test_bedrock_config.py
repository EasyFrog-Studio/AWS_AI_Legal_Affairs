"""Bedrock 用戶端的逾時設定,以及兩個建 client 的地方是否真的套上它。"""
import boto3
import pytest
from botocore.config import Config

from app import ocr as ocr_module
from app.bedrock import bedrock_config
from app.config import settings
from app.providers.aws import AWSProvider


@pytest.fixture
def captured_clients(monkeypatch):
    captured: dict = {}

    def _fake_client(service, **kwargs):
        captured[service] = kwargs
        return object()

    monkeypatch.setattr(boto3, "client", _fake_client)
    monkeypatch.setattr(boto3, "resource", lambda *a, **k: object())
    return captured


@pytest.mark.parametrize("read_timeout,attempts", [(120, 2), (600, 5)])
def test_config_values_come_from_settings(monkeypatch, read_timeout, attempts):
    monkeypatch.setattr(settings, "BEDROCK_READ_TIMEOUT_SECONDS", read_timeout)
    monkeypatch.setattr(settings, "BEDROCK_MAX_ATTEMPTS", attempts)

    cfg = bedrock_config()

    assert cfg.read_timeout == read_timeout
    assert cfg.retries == {"max_attempts": attempts, "mode": "standard"}
    assert cfg.connect_timeout == settings.BEDROCK_CONNECT_TIMEOUT_SECONDS


def test_default_read_timeout_exceeds_botocore_default():
    """預設值若回到 botocore 的 60 秒,F1 在大份卷證上會整批讀取逾時。"""
    assert settings.BEDROCK_READ_TIMEOUT_SECONDS > Config().read_timeout


def test_provider_clients_get_the_config(captured_clients):
    AWSProvider()

    for service in ("bedrock-runtime", "bedrock-agent-runtime"):
        cfg = captured_clients[service]["config"]
        assert cfg.read_timeout == settings.BEDROCK_READ_TIMEOUT_SECONDS


def test_ocr_client_gets_the_config(captured_clients):
    ocr_module.BedrockOcrClient()

    cfg = captured_clients["bedrock-runtime"]["config"]
    assert cfg.read_timeout == settings.BEDROCK_READ_TIMEOUT_SECONDS


def test_injected_client_is_left_alone(captured_clients):
    """呼叫端自己給 client 時不另外建一個——測試替身不該被換成真的 boto3 client。"""
    stub = object()

    provider = AWSProvider(bedrock_runtime=stub, bedrock_agent_runtime=object(),
                           dynamodb_resource=object())

    assert provider._brt is stub
    assert "bedrock-runtime" not in captured_clients


def test_no_internal_retries_so_the_rate_gate_stays_hard():
    """botocore 自己重試會在同一次 acquire() 放行內重發請求,1 RPS 的保證就破了。"""
    assert settings.BEDROCK_MAX_ATTEMPTS == 1
