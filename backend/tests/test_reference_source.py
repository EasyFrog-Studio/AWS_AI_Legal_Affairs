"""參考見解的原文來源:官方語料是前處理產出的 markdown,爬蟲語料是原始 PDF。

爬蟲語料的 2,415 份參考資料只有 PDF、沒有 markdown,`viewable_source_key` 因此一律回 None,
結果是 F2+ 那三筆在畫面上**一個可點的來源都沒有**——既沒有原文、也推不出官方網址。
PDF 檔就在本機,把它服務出去比給一個搜尋頁實在。
"""
import app.main as main_module
from app.config import settings
from app.providers.aws import archived_source_key, build_references


def _headers():
    return {"X-API-Key": settings.API_KEY}


def test_a_crawled_reference_points_at_its_archived_pdf():
    for doc_kind, source_file in (
        ("行政法院裁判", "113-03-15_臺北高等行政法院_112年度簡字第329號.pdf"),
        ("行政函釋", "民國97年09月24日_行政院環境保護署 環署廢字第0970068068號_行政函釋.pdf"),
        ("司法院釋字", "釋字第0001號_038-01-06_立委就任官吏時仍保有立委職位？.pdf"),
    ):
        key = archived_source_key({"doc_kind": doc_kind, "source_file": source_file})
        assert key == f"reference/{doc_kind}/{source_file}"


def test_official_markdown_is_not_rerouted_to_the_pdf_store():
    """官方語料的 source_file 已經是 markdown 路徑,那一條走既有的 viewable_source_key。"""
    metadata = {"doc_kind": "行政函釋", "source_file": "markdown/行政函釋/內政部函釋.md"}
    assert archived_source_key(metadata) is None


def test_a_reference_without_a_usable_file_gets_nothing():
    """認不出檔案就不給鍵——畫出一個按下去必定回「找不到」的按鈕比沒有按鈕更糟。"""
    for metadata in (
        {"doc_kind": "行政函釋", "source_file": ""},
        {"doc_kind": "行政函釋", "source_file": "NTPC-114"},  # 不是檔名
        {"doc_kind": "法規", "source_file": "某某法.pdf"},  # 不是參考見解那三類
        {},
    ):
        assert archived_source_key(metadata) is None


def test_a_traversal_attempt_is_refused():
    """source_file 來自語料 metadata,不是使用者輸入,但它會被組成路徑送進 /api/source。"""
    for bad in ("../../.env", "行政函釋/../../etc/passwd.pdf", "a/b.pdf"):
        assert archived_source_key({"doc_kind": "行政函釋", "source_file": bad}) is None


def test_build_references_carries_the_archived_key():
    rows = [
        (
            "臺北高等行政法院 112年度簡字第329號",
            "判決全文",
            {
                "doc_kind": "行政法院裁判",
                "source_file": "113-03-15_臺北高等行政法院_112年度簡字第329號.pdf",
            },
        )
    ]

    refs = build_references(rows, "向量檢索命中")

    assert refs[0].source_key == "reference/行政法院裁判/113-03-15_臺北高等行政法院_112年度簡字第329號.pdf"


# ---------- /api/source 服務 PDF ----------


def test_the_source_endpoint_reports_a_pdf_key_as_a_file(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    client = TestClient(main_module.app)
    resp = client.get(
        "/api/source", params={"key": "reference/行政函釋/不存在的檔.pdf"}, headers=_headers()
    )

    assert resp.status_code == 200
    # PDF 不能當文字塞進 overlay;回 file 讓前端改用下載後開新分頁的路徑
    assert resp.json()["file"] == "reference/行政函釋/不存在的檔.pdf"


def test_the_file_endpoint_serves_the_pdf(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    root = tmp_path / "reference" / "行政函釋"
    root.mkdir(parents=True)
    (root / "測試函釋.pdf").write_bytes(b"%PDF-1.7\n test")
    monkeypatch.setattr(main_module, "_REFERENCE_DIR", tmp_path / "reference")

    client = TestClient(main_module.app)
    resp = client.get(
        "/api/source/file", params={"key": "reference/行政函釋/測試函釋.pdf"}, headers=_headers()
    )

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/pdf")
    assert resp.content.startswith(b"%PDF")


def test_the_file_endpoint_refuses_paths_outside_the_store(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setattr(main_module, "_REFERENCE_DIR", tmp_path / "reference")
    client = TestClient(main_module.app)

    for bad in ("reference/../../.env", "../.env", "reference/行政函釋/沒這個檔.pdf"):
        resp = client.get("/api/source/file", params={"key": bad}, headers=_headers())
        assert resp.status_code == 404, bad


def test_the_file_endpoint_requires_the_api_key():
    from fastapi.testclient import TestClient

    client = TestClient(main_module.app)
    resp = client.get("/api/source/file", params={"key": "reference/行政函釋/x.pdf"})
    assert resp.status_code == 401


# ---------- /api/source/file 的 aws 模式 S3 後援(本機掛載找不到時) ----------


class _FakeBody:
    def __init__(self, data: bytes):
        self._data = data

    def read(self) -> bytes:
        return self._data


class _FakeS3Client:
    """假 boto3 s3 client:get_object 依 side_effect/return_value 決定行為,記錄呼叫參數。"""

    def __init__(self, return_value=None, side_effect=None):
        self._return_value = return_value
        self._side_effect = side_effect
        self.calls: list = []

    def get_object(self, **kwargs):
        self.calls.append(kwargs)
        if self._side_effect is not None:
            raise self._side_effect
        return self._return_value


def test_the_file_endpoint_falls_back_to_s3_when_the_local_mount_is_missing(tmp_path, monkeypatch):
    """本機掛載(compose volume)沒有這份檔案時,aws 模式的實際落地是 S3,同一個 key 當 S3 Key 用。"""
    from fastapi.testclient import TestClient

    monkeypatch.setattr(main_module, "_REFERENCE_DIR", tmp_path / "reference")  # 本機目錄故意留空
    monkeypatch.setattr(settings, "S3_BUCKET", "appeal-ai-test-bucket")
    fake_client = _FakeS3Client(
        return_value={"Body": _FakeBody(b"%PDF-1.7\n s3 data"), "ContentType": "application/pdf"}
    )
    monkeypatch.setattr(main_module, "_s3_client", lambda: fake_client)

    client = TestClient(main_module.app)
    resp = client.get(
        "/api/source/file", params={"key": "reference/司法院釋字/釋字第469號.pdf"}, headers=_headers()
    )

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/pdf")
    assert resp.content == b"%PDF-1.7\n s3 data"
    assert fake_client.calls == [
        {"Bucket": "appeal-ai-test-bucket", "Key": "reference/司法院釋字/釋字第469號.pdf"}
    ]


def test_the_file_endpoint_returns_404_when_s3_reports_no_such_key(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from botocore.exceptions import ClientError

    monkeypatch.setattr(main_module, "_REFERENCE_DIR", tmp_path / "reference")
    monkeypatch.setattr(settings, "S3_BUCKET", "appeal-ai-test-bucket")
    fake_client = _FakeS3Client(
        side_effect=ClientError({"Error": {"Code": "NoSuchKey", "Message": "not found"}}, "GetObject")
    )
    monkeypatch.setattr(main_module, "_s3_client", lambda: fake_client)

    client = TestClient(main_module.app)
    resp = client.get(
        "/api/source/file", params={"key": "reference/行政函釋/不存在的檔.pdf"}, headers=_headers()
    )

    assert resp.status_code == 404


def test_the_file_endpoint_stays_404_without_an_s3_bucket_and_never_builds_a_client(tmp_path, monkeypatch):
    """S3_BUCKET 未設定(local/mock 的常態)時不得嘗試建立 s3 client——那沒有憑證可用。"""
    from fastapi.testclient import TestClient

    monkeypatch.setattr(main_module, "_REFERENCE_DIR", tmp_path / "reference")
    monkeypatch.setattr(settings, "S3_BUCKET", "")

    def _explode():
        raise AssertionError("S3_BUCKET 為空時不該建立 s3 client")

    monkeypatch.setattr(main_module, "_s3_client", _explode)

    client = TestClient(main_module.app)
    resp = client.get(
        "/api/source/file", params={"key": "reference/行政函釋/不存在的檔.pdf"}, headers=_headers()
    )

    assert resp.status_code == 404
