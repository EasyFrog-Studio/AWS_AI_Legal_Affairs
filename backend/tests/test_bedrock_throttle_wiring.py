"""每一個 Bedrock 呼叫點都必須先過節流閘。拆掉任何一處 acquire() 都要有測試變紅,
否則「1 RPS 以下」這條競賽規範在程式裡沒有任何東西守著。"""
from unittest.mock import MagicMock

import pytest

from app import ocr as ocr_module
from app.providers import aws as aws_module
from app.providers.aws import AWSProvider


class _RecordingGate:
    def __init__(self, events: list) -> None:
        self._events = events

    def acquire(self) -> None:
        self._events.append("acquire")


def _tool_use_brt(events: list, *, fail: bool = False):
    client = MagicMock()

    def _converse(**kwargs):
        events.append("converse")
        if fail:
            raise RuntimeError("throttled")
        return {"output": {"message": {"content": [{"toolUse": {"input": {"ok": True}}}]}}}

    client.converse.side_effect = _converse
    return client


def _text_brt(events: list, *, fail: bool = False):
    client = MagicMock()

    def _converse(**kwargs):
        events.append("converse")
        if fail:
            raise RuntimeError("throttled")
        return {"output": {"message": {"content": [{"text": "頁面文字"}]}}}

    client.converse.side_effect = _converse
    return client


def _retrieving_bart(events: list):
    client = MagicMock()

    def _retrieve(**kwargs):
        events.append("retrieve")
        return {"retrievalResults": []}

    client.retrieve.side_effect = _retrieve
    return client


def test_converse_acquires_gate_first(monkeypatch):
    events: list = []
    monkeypatch.setattr(aws_module, "bedrock_gate", _RecordingGate(events))
    provider = AWSProvider(bedrock_runtime=_tool_use_brt(events),
                           bedrock_agent_runtime=MagicMock(), dynamodb_resource=MagicMock())

    provider._converse_json("sys", "user", "tool", {"type": "object"})

    assert events == ["acquire", "converse"]


def test_retrieve_acquires_gate_first(monkeypatch):
    events: list = []
    monkeypatch.setattr(aws_module, "bedrock_gate", _RecordingGate(events))
    provider = AWSProvider(bedrock_runtime=MagicMock(),
                           bedrock_agent_runtime=_retrieving_bart(events),
                           dynamodb_resource=MagicMock())

    provider._retrieve("kb-id", "查詢", None)

    assert events == ["acquire", "retrieve"]


def test_ocr_page_acquires_gate_first(monkeypatch):
    events: list = []
    monkeypatch.setattr(ocr_module, "bedrock_gate", _RecordingGate(events))
    client = ocr_module.BedrockOcrClient(bedrock_runtime=_text_brt(events))

    assert client.extract_page(b"png-bytes") == "頁面文字"
    assert events == ["acquire", "converse"]


def test_gate_is_acquired_even_when_the_call_fails(monkeypatch):
    """失敗的請求一樣打到 Bedrock,不放行就等於用重試繞過速率上限。"""
    events: list = []
    monkeypatch.setattr(aws_module, "bedrock_gate", _RecordingGate(events))
    provider = AWSProvider(bedrock_runtime=_tool_use_brt(events, fail=True),
                           bedrock_agent_runtime=MagicMock(), dynamodb_resource=MagicMock())

    with pytest.raises(RuntimeError):
        provider._converse_json("sys", "user", "tool", {"type": "object"})

    assert events == ["acquire", "converse"]


def test_every_ocr_retry_passes_through_the_gate(monkeypatch):
    """每頁重試 3 次,三次都是各自獨立的 Bedrock 請求,每次都要重新排隊。"""
    events: list = []
    monkeypatch.setattr(ocr_module, "bedrock_gate", _RecordingGate(events))
    client = ocr_module.BedrockOcrClient(bedrock_runtime=_text_brt(events, fail=True))

    with pytest.raises(ocr_module.OcrFailedError):
        ocr_module._extract_with_retry(client, b"png", 1, lambda _: None)

    assert events == ["acquire", "converse"] * 3
