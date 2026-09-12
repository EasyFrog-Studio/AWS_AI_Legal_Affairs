"""aws 模式的 PDF 落地與預覽:_land_pdf_if_any 落 S3、GET .../file 從 S3 讀回。
見 specs/審理歷程線性重跑與全欄位擷取.md §1.3;non-aws 分支已由 test_api.py 的
test_uploaded_pdf_lands_on_disk_and_can_be_read_back_byte_for_byte 等測試涵蓋。"""
import io

from botocore.exceptions import ClientError
from fastapi.testclient import TestClient

import app.main as main_module
from app.config import settings

_APPEAL_TEXT = (
    "訴願書\n訴願人:王大明\n原處分機關:彰化縣環境保護局\n請求事項:撤銷原處分。\n"
    "訴願人不服彰化縣環境保護局裁處罰鍰,提起廢棄物清理訴願。"
)
_SERVICE_TEXT = "送達證書\n送達時間:中華民國110年3月10日\n送達方式:寄存於派出所。"
_DISPOSITION_TEXT = (
    "原處分書\n主旨:違反廢棄物清理法,裁處罰鍰6000元。\n"
    "事實:訴願人於指定清除地區內棄置廢棄物。\n理由:經稽查屬實。\n教示條款:如不服本處分得提起訴願。"
)
_ANSWER_TEXT = (
    "訴願答辯書\n原處分機關：彰化縣環境保護局\n"
    "訴願人因違反廢棄物清理法事件，不服本局裁處書，提起訴願，謹依法答辯如下：\n"
    "答辯聲明：本件訴願駁回。\n理由：一、程序答辯：本件訴願為合法。\n三、檢附原卷1宗，敬請察核。"
)


def _headers():
    return {"X-API-Key": settings.API_KEY}


def _client():
    return TestClient(main_module.app)


def _text_pdf_bytes(text: str) -> bytes:
    import fitz

    doc = fitz.open()
    page = doc.new_page()
    page.insert_textbox(fitz.Rect(40, 40, 560, 800), text, fontname="china-t", fontsize=11)
    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


def _aws_mode(monkeypatch):
    monkeypatch.setattr(settings, "AI_PROVIDER", "aws")
    monkeypatch.setattr(settings, "S3_BUCKET", "appeal-bucket")


class _FakeS3Put:
    def __init__(self):
        self.calls = []

    def put_object(self, **kwargs):
        self.calls.append(kwargs)


def test_create_case_in_aws_mode_uploads_the_pdf_to_s3(monkeypatch):
    """建案時的 PDF 落地在 aws 模式走 S3 put_object,key 為 cases/{case_id}/{slot}.pdf。"""
    _aws_mode(monkeypatch)
    fake_s3 = _FakeS3Put()
    monkeypatch.setattr(main_module, "_s3_client", lambda: fake_s3)
    original_bytes = _text_pdf_bytes(_APPEAL_TEXT)

    resp = _client().post(
        "/api/cases",
        data={
            "service_text": _SERVICE_TEXT,
            "disposition_text": _DISPOSITION_TEXT,
            "answer_text": _ANSWER_TEXT,
        },
        files={"appeal_file": ("01_訴願書.pdf", original_bytes, "application/pdf")},
        headers=_headers(),
    )

    assert resp.status_code == 200, resp.json()
    case_id = resp.json()["case_id"]
    assert len(fake_s3.calls) == 1
    call = fake_s3.calls[0]
    assert call["Bucket"] == "appeal-bucket"
    assert call["Key"] == f"cases/{case_id}/appeal.pdf"
    assert call["Body"] == original_bytes
    assert call["ContentType"] == "application/pdf"


def test_get_document_file_in_aws_mode_streams_the_same_bytes_back(monkeypatch):
    """GET .../file 在 aws 模式走 S3 get_object,讀回的位元組要與上傳的一模一樣。"""
    _aws_mode(monkeypatch)
    original_bytes = _text_pdf_bytes(_APPEAL_TEXT)

    class _FakeS3:
        def put_object(self, **kwargs):
            pass

        def get_object(self, Bucket, Key):
            assert Bucket == "appeal-bucket"
            assert Key.endswith("/appeal.pdf")
            return {"Body": io.BytesIO(original_bytes)}

    monkeypatch.setattr(main_module, "_s3_client", lambda: _FakeS3())
    case_id = _client().post(
        "/api/cases",
        data={
            "service_text": _SERVICE_TEXT,
            "disposition_text": _DISPOSITION_TEXT,
            "answer_text": _ANSWER_TEXT,
        },
        files={"appeal_file": ("01_訴願書.pdf", original_bytes, "application/pdf")},
        headers=_headers(),
    ).json()["case_id"]

    resp = _client().get(f"/api/cases/{case_id}/documents/appeal/file", headers=_headers())

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content == original_bytes


def test_get_document_file_in_aws_mode_returns_404_when_the_object_is_missing(monkeypatch):
    """S3 物件已不存在(NoSuchKey)時回 404,不是把 boto3 的例外原樣往外拋成 500。"""
    _aws_mode(monkeypatch)
    original_bytes = _text_pdf_bytes(_APPEAL_TEXT)

    class _FakeS3Missing:
        def put_object(self, **kwargs):
            pass

        def get_object(self, Bucket, Key):
            raise ClientError({"Error": {"Code": "NoSuchKey", "Message": "not found"}}, "GetObject")

    monkeypatch.setattr(main_module, "_s3_client", lambda: _FakeS3Missing())
    case_id = _client().post(
        "/api/cases",
        data={
            "service_text": _SERVICE_TEXT,
            "disposition_text": _DISPOSITION_TEXT,
            "answer_text": _ANSWER_TEXT,
        },
        files={"appeal_file": ("01_訴願書.pdf", original_bytes, "application/pdf")},
        headers=_headers(),
    ).json()["case_id"]

    resp = _client().get(f"/api/cases/{case_id}/documents/appeal/file", headers=_headers())

    assert resp.status_code == 404
