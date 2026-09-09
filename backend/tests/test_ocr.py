"""掃描件逐頁抽字:呼叫形狀、重試、整槽拒收與頁數上限,見實作計畫 Ticket 4。
真實 Bedrock/ollama 多模態呼叫未驗證(無憑證),這裡以 fake client 測呼叫形狀。"""
import base64

import fitz
import pytest

from app.ocr import (
    MAX_OCR_PAGES,
    BedrockOcrClient,
    OcrFailedError,
    OcrTooManyPagesError,
    OcrUnavailableError,
    OllamaOcrClient,
    get_ocr_client,
    ocr_pdf,
    page_images,
)


def _scanned_pdf(pages: int = 1) -> bytes:
    """無文字層的 PDF:空白頁即可,get_text 回空字串,與掃描件在抽字層是同一種東西。"""
    doc = fitz.open()
    for _ in range(pages):
        doc.new_page()
    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


class _FakeClient:
    """逐頁回傳可辨識的文字,並記錄收到幾張圖。"""

    def __init__(self, fail_times: int = 0, texts=None) -> None:
        self.calls: list[bytes] = []
        self.fail_times = fail_times
        self.texts = texts

    def extract_page(self, image: bytes) -> str:
        self.calls.append(image)
        if len(self.calls) <= self.fail_times:
            raise RuntimeError("ThrottlingException")
        index = len(self.calls) - self.fail_times
        return self.texts[index - 1] if self.texts else f"第{index}頁文字"


def test_page_images_renders_one_image_per_page():
    images = page_images(_scanned_pdf(pages=3))

    assert len(images) == 3
    assert all(img.startswith(b"\x89PNG") for img in images)


def test_ocr_pdf_joins_pages_in_order():
    client = _FakeClient(texts=["送達時間", "民國114年5月28日"])

    text = ocr_pdf(_scanned_pdf(pages=2), client, sleep=lambda _s: None)

    assert text.index("送達時間") < text.index("民國114年5月28日")
    assert len(client.calls) == 2


def test_transient_failures_are_retried_before_giving_up():
    """逐頁同步呼叫會遇到 throttling,先做有限次重試,這一層處理暫時性的 rate limit。"""
    client = _FakeClient(fail_times=2, texts=["第1頁文字"])
    delays: list[float] = []

    text = ocr_pdf(_scanned_pdf(pages=1), client, sleep=delays.append)

    assert "第1頁文字" in text
    assert len(delays) == 2
    assert delays[1] > delays[0]  # 指數退避,不是等固定秒數硬撞同一個 rate limit


def test_a_page_that_keeps_failing_rejects_the_whole_slot():
    """第 N 頁失敗時不得保留前 N-1 頁:缺掉的那一頁很可能正是載有送達時間的那一頁,
    抽取層拿到看起來完整的句子,只會算出一個沒有人知道是錯的日期。"""
    client = _FakeClient(fail_times=99)

    with pytest.raises(OcrFailedError) as exc:
        ocr_pdf(_scanned_pdf(pages=3), client, sleep=lambda _s: None)

    assert "第1頁" in str(exc.value)
    assert "電子檔" in str(exc.value)  # 講得出替代做法,不只說失敗


def test_failure_on_a_later_page_keeps_no_partial_text():
    class _FailsOnSecondPage:
        def extract_page(self, image: bytes) -> str:
            if not hasattr(self, "_seen"):
                self._seen = 0
            self._seen += 1
            if self._seen > 1:
                raise RuntimeError("timeout")
            return "第1頁文字"

    with pytest.raises(OcrFailedError) as exc:
        ocr_pdf(_scanned_pdf(pages=2), _FailsOnSecondPage(), sleep=lambda _s: None)

    assert "第2頁" in str(exc.value)


def test_page_count_over_the_cap_is_refused_with_the_limit_stated():
    client = _FakeClient()

    with pytest.raises(OcrTooManyPagesError) as exc:
        ocr_pdf(_scanned_pdf(pages=MAX_OCR_PAGES + 1), client, sleep=lambda _s: None)

    assert str(MAX_OCR_PAGES) in str(exc.value)
    assert client.calls == []  # 超過上限就整份不送,不是先送前 20 頁


def test_bedrock_client_sends_one_image_block_per_call():
    class _FakeBedrock:
        def __init__(self) -> None:
            self.kwargs = None

        def converse(self, **kwargs):
            self.kwargs = kwargs
            return {"output": {"message": {"content": [{"text": "送達時間 中華民國114年5月28日"}]}}}

    fake = _FakeBedrock()

    text = BedrockOcrClient(bedrock_runtime=fake).extract_page(b"\x89PNG-fake")

    assert text == "送達時間 中華民國114年5月28日"
    content = fake.kwargs["messages"][0]["content"]
    image_blocks = [b for b in content if "image" in b]
    assert len(image_blocks) == 1
    assert image_blocks[0]["image"]["format"] == "png"
    assert image_blocks[0]["image"]["source"]["bytes"] == b"\x89PNG-fake"
    assert fake.kwargs["inferenceConfig"]["temperature"] == 0  # 抽字不要創意


def test_ollama_client_sends_base64_images():
    class _FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"message": {"content": "送達時間 中華民國114年5月28日"}}

    class _FakeHttp:
        def __init__(self) -> None:
            self.payload = None

        def post(self, url, json):
            self.payload = (url, json)
            return _FakeResponse()

    http = _FakeHttp()

    text = OllamaOcrClient(http_client=http).extract_page(b"\x89PNG-fake")

    assert "送達時間" in text
    url, payload = http.payload
    assert url == "/api/chat"
    # ollama 的多模態輸入是訊息內的 images 陣列(base64),不是獨立的頂層欄位
    assert payload["messages"][0]["images"] == [base64.b64encode(b"\x89PNG-fake").decode()]
    assert payload["options"]["temperature"] == 0


def test_mock_mode_has_no_ocr_and_says_so(monkeypatch):
    """mock 是給 demo 用的、不呼叫任何模型,這裡不接 OCR 是刻意的——但要講清楚,
    不能讓使用者拿到一個空白案件。"""
    from app.config import settings

    monkeypatch.setattr(settings, "AI_PROVIDER", "mock")

    with pytest.raises(OcrUnavailableError) as exc:
        get_ocr_client()

    assert "未接 OCR" in str(exc.value)
