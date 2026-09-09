import os

os.environ.setdefault("AI_PROVIDER", "mock")
os.environ.setdefault("API_KEY", "demo-key-2026")

from pathlib import Path

from fastapi.testclient import TestClient

import app.main as main_module
from app.config import settings

FIXTURES_DIR = Path(__file__).parent / "fixtures"

# 三個槽位的文字都要能被 document_check 的規則層命中(含強特徵字面),
# 否則會落到 Gemini 備援;測試環境未設 GEMINI_API_KEY,備援會回 matched=None 而非出錯,
# 但若三槽本可規則判斷卻落到備援,測試意圖就不明確了,故三槽文字都內嵌強特徵。
_APPEAL_TEXT = (
    "訴願書\n訴願人:王大明\n原處分機關:彰化縣環境保護局\n請求事項:撤銷原處分。\n"
    "訴願人不服彰化縣環境保護局裁處罰鍰,提起廢棄物清理訴願。"
)
_SERVICE_TEXT = "送達證書\n送達時間:中華民國110年3月10日\n送達方式:寄存於派出所。"
_DISPOSITION_TEXT = (
    "原處分書\n主旨:違反廢棄物清理法,裁處罰鍰6000元。\n"
    "事實:訴願人於指定清除地區內棄置廢棄物。\n理由:經稽查屬實。\n教示條款:如不服本處分得提起訴願。"
)

_INADMISSIBLE_APPEAL_TEXT = "訴願書\n訴願人:李小華對臺北市政府社會局不服,逾期提起社會救助訴願。"


def _headers():
    return {"X-API-Key": settings.API_KEY}


def _create_case_form(appeal_text=_APPEAL_TEXT, service_text=_SERVICE_TEXT, disposition_text=_DISPOSITION_TEXT):
    return {"appeal_text": appeal_text, "service_text": service_text, "disposition_text": disposition_text}


def test_health_no_api_key_required():
    client = TestClient(main_module.app)
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["provider"] == "mock"


def test_health_reports_reference_data_coverage():
    """假日表與在途表會過期,health 是唯一會主動講「該重抓了」的地方(Ticket 14)。"""
    client = TestClient(main_module.app)
    body = client.get("/api/health").json()

    holidays = body["reference_data"]["holidays"]
    assert holidays["min_year"] and holidays["max_year"]
    assert body["reference_data"]["transit_days"]["source"]
    assert "warning" in body  # 目前應為空字串,但欄位恆存在,前端不必判斷有沒有這個 key


def test_cases_endpoints_require_api_key():
    client = TestClient(main_module.app)
    resp = client.get("/api/cases")
    assert resp.status_code == 401


def test_create_case_with_all_three_documents_returns_case_id_and_checks(monkeypatch):
    monkeypatch.setattr(settings, "MOCK_DATA_DIR", str(FIXTURES_DIR))
    client = TestClient(main_module.app)
    resp = client.post("/api/cases", data=_create_case_form(), headers=_headers())
    assert resp.status_code == 200
    body = resp.json()
    assert body["case_id"].startswith("c-")
    assert set(body["documents"].keys()) == {"appeal", "service", "disposition"}
    # 三槽文字皆內嵌各自的強特徵字面,規則層應能直接判斷,不必落到 Gemini 備援
    for slot, check in body["documents"].items():
        assert check["matched"] is True, f"{slot} 應能被規則判斷命中:{check}"
        assert check["method"] == "rule"


def test_create_case_missing_any_document_returns_400():
    client = TestClient(main_module.app)
    resp = client.post("/api/cases", data={}, headers=_headers())
    assert resp.status_code == 400
    assert "訴願書" in resp.json()["detail"]


def test_create_case_missing_appeal_document_returns_400():
    """訴願書是訴願標的本身,缺了無案可審;送達證書則另有本無此文書的案型,見下一則。"""
    client = TestClient(main_module.app)
    resp = client.post(
        "/api/cases",
        data={"service_text": _SERVICE_TEXT, "disposition_text": _DISPOSITION_TEXT},
        headers=_headers(),
    )
    assert resp.status_code == 400
    assert "訴願書" in resp.json()["detail"]


def test_create_case_missing_disposition_document_returns_400():
    """原處分是受審查的標的,缺了無從認定爭點。"""
    client = TestClient(main_module.app)
    resp = client.post(
        "/api/cases",
        data={"appeal_text": _APPEAL_TEXT, "service_text": _SERVICE_TEXT},
        headers=_headers(),
    )
    assert resp.status_code == 400
    assert "原處分書" in resp.json()["detail"]


def test_create_case_wrong_document_in_slot_is_flagged_not_matched(monkeypatch):
    """送達證書的文字誤傳進訴願書槽,規則層應判斷不符,不應假裝正確。"""
    monkeypatch.setattr(settings, "MOCK_DATA_DIR", str(FIXTURES_DIR))
    client = TestClient(main_module.app)
    resp = client.post(
        "/api/cases",
        data=_create_case_form(appeal_text=_SERVICE_TEXT),
        headers=_headers(),
    )
    assert resp.status_code == 200
    check = resp.json()["documents"]["appeal"]
    assert check["matched"] is False
    assert check["method"] == "rule"


def test_created_case_starts_in_collecting_status_without_running_pipeline(monkeypatch):
    """建案只做文件確認,不應自動觸發分析——要等 /analyze。"""
    monkeypatch.setattr(settings, "MOCK_DATA_DIR", str(FIXTURES_DIR))
    client = TestClient(main_module.app)
    case_id = client.post("/api/cases", data=_create_case_form(), headers=_headers()).json()["case_id"]

    get_resp = client.get(f"/api/cases/{case_id}", headers=_headers())
    case = get_resp.json()
    assert case["status"] == "collecting"
    assert case["f1"] is None


def test_replace_document_before_analysis_rechecks_type(monkeypatch):
    monkeypatch.setattr(settings, "MOCK_DATA_DIR", str(FIXTURES_DIR))
    client = TestClient(main_module.app)
    case_id = client.post("/api/cases", data=_create_case_form(), headers=_headers()).json()["case_id"]

    resp = client.patch(
        f"/api/cases/{case_id}/documents/service",
        data={"text": _SERVICE_TEXT + "補充內容"},
        headers=_headers(),
    )
    assert resp.status_code == 200
    assert resp.json()["documents"]["service"]["matched"] is True


def test_replace_document_rebuilds_input_text(monkeypatch):
    """重傳一槽後 input_text 必須重建,否則 F1/程序審查分析用的仍是重傳前的舊文字
    (見實作計畫 Ticket 3a:input_text 只是 documents 的衍生值,不能各自維護一份)。"""
    monkeypatch.setattr(settings, "MOCK_DATA_DIR", str(FIXTURES_DIR))
    client = TestClient(main_module.app)
    case_id = client.post("/api/cases", data=_create_case_form(), headers=_headers()).json()["case_id"]

    new_service_text = _SERVICE_TEXT + "本次重傳新增的獨有標記文字ABCDEF"
    resp = client.patch(
        f"/api/cases/{case_id}/documents/service",
        data={"text": new_service_text},
        headers=_headers(),
    )
    assert resp.status_code == 200

    case = client.get(f"/api/cases/{case_id}", headers=_headers()).json()
    assert "ABCDEF" in case["input_text"]  # 新文字要出現在合併字串裡
    assert "【送達證書】" in case["input_text"]  # 分段標頭仍完整,不是憑空塞一份


def test_replace_document_after_analysis_started_returns_409(monkeypatch):
    monkeypatch.setattr(settings, "MOCK_DATA_DIR", str(FIXTURES_DIR))
    client = TestClient(main_module.app)
    case_id = client.post("/api/cases", data=_create_case_form(), headers=_headers()).json()["case_id"]
    client.post(f"/api/cases/{case_id}/analyze", headers=_headers())

    resp = client.patch(
        f"/api/cases/{case_id}/documents/service",
        data={"text": _SERVICE_TEXT},
        headers=_headers(),
    )
    assert resp.status_code == 409


def _scanned_pdf_bytes() -> bytes:
    """無文字層的 PDF:空白頁在抽字層與掃描件是同一種東西(get_text 回空字串)。"""
    import fitz

    doc = fitz.open()
    doc.new_page()
    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


def test_scanned_pdf_in_mock_mode_is_refused_with_a_reason(monkeypatch):
    """掃描件不要偽裝成「文書寫得不清楚」:mock 模式沒有 OCR,就明確拒收,
    不要讓使用者拿到一個空白案件(見實作計畫 Ticket 4)。"""
    monkeypatch.setattr(settings, "MOCK_DATA_DIR", str(FIXTURES_DIR))
    client = TestClient(main_module.app)

    resp = client.post(
        "/api/cases",
        data={"service_text": _SERVICE_TEXT, "disposition_text": _DISPOSITION_TEXT},
        files={"appeal_file": ("掃描件.pdf", _scanned_pdf_bytes(), "application/pdf")},
        headers=_headers(),
    )

    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "訴願書" in detail and "掃描件" in detail
    assert "未接 OCR" in detail  # 講出原因與替代做法,不只說失敗


def test_scanned_pdf_goes_through_ocr_and_the_slot_is_flagged(monkeypatch):
    """有 OCR 的模式:逐頁抽字接回,而且該槽一律標記待人工核對原件——
    OCR 取得的日期不得逕採(模型抽字會編字)。"""
    monkeypatch.setattr(settings, "MOCK_DATA_DIR", str(FIXTURES_DIR))
    ocr_text = "送達證書\n送達時間:中華民國110年3月10日\n送達方式:寄存於派出所。" * 3

    class _FakeOcrClient:
        def extract_page(self, image: bytes) -> str:
            return ocr_text

    monkeypatch.setattr(main_module, "get_ocr_client", lambda: _FakeOcrClient())
    client = TestClient(main_module.app)

    resp = client.post(
        "/api/cases",
        data={"appeal_text": _APPEAL_TEXT, "disposition_text": _DISPOSITION_TEXT},
        files={"service_file": ("掃描件.pdf", _scanned_pdf_bytes(), "application/pdf")},
        headers=_headers(),
    )

    assert resp.status_code == 200
    case_id = resp.json()["case_id"]
    case = client.get(f"/api/cases/{case_id}", headers=_headers()).json()
    service_doc = case["documents"]["service"]
    assert service_doc["ocr"] is True
    assert "OCR" in service_doc["review_note"]
    assert "送達時間" in service_doc["text"]


def test_ocr_output_that_is_still_unreadable_is_refused(monkeypatch):
    """逐頁抽字後仍是亂碼/字數不足,一樣拒收:辨識不出來就是辨識不出來。"""
    monkeypatch.setattr(settings, "MOCK_DATA_DIR", str(FIXTURES_DIR))

    class _NoisyOcrClient:
        def extract_page(self, image: bytes) -> str:
            return "▓▓▒▒░░"

    monkeypatch.setattr(main_module, "get_ocr_client", lambda: _NoisyOcrClient())
    client = TestClient(main_module.app)

    resp = client.post(
        "/api/cases",
        data={"appeal_text": _APPEAL_TEXT, "disposition_text": _DISPOSITION_TEXT},
        files={"service_file": ("掃描件.pdf", _scanned_pdf_bytes(), "application/pdf")},
        headers=_headers(),
    )

    assert resp.status_code == 400
    assert "無法辨識" in resp.json()["detail"]


def test_oversized_case_file_returns_400_not_a_crashed_case(monkeypatch):
    """aws 模式下卷證超過單筆儲存門檻:回 400 並說明,不讓 boto3 的 ValidationException
    把案件打成 status=error 只留一串英文(見實作計畫 Ticket 3c)。"""
    from unittest.mock import MagicMock

    from app.store import DynamoDBStore

    monkeypatch.setattr(main_module, "store", DynamoDBStore(table=MagicMock()))
    client = TestClient(main_module.app)

    resp = client.post(
        "/api/cases",
        # 以 ASCII 灌量:form 欄位本身有 1024KB 上限,中文經 urlencode 會膨脹成 9 bytes/字
        # 而先撞到那道限制,測不到 store 這一層的門檻
        data=_create_case_form(appeal_text=_APPEAL_TEXT + "A" * 400_000),
        headers=_headers(),
    )

    assert resp.status_code == 400
    assert "上限" in resp.json()["detail"]


def test_analyze_nonexistent_case_returns_404():
    client = TestClient(main_module.app)
    resp = client.post("/api/cases/c-notexist/analyze", headers=_headers())
    assert resp.status_code == 404


def test_analyze_twice_returns_409(monkeypatch):
    monkeypatch.setattr(settings, "MOCK_DATA_DIR", str(FIXTURES_DIR))
    client = TestClient(main_module.app)
    case_id = client.post("/api/cases", data=_create_case_form(), headers=_headers()).json()["case_id"]

    first = client.post(f"/api/cases/{case_id}/analyze", headers=_headers())
    assert first.status_code == 200
    second = client.post(f"/api/cases/{case_id}/analyze", headers=_headers())
    assert second.status_code == 409


def test_analyze_blocked_when_a_document_is_flagged_mismatched(monkeypatch):
    """前端擋了未確認的槽,但後端自己也要擋——直接打 API 不能繞過文件確認這一關。"""
    monkeypatch.setattr(settings, "MOCK_DATA_DIR", str(FIXTURES_DIR))
    client = TestClient(main_module.app)
    # appeal 槽故意塞送達證書的文字,規則層會判斷 matched=False
    case_id = client.post(
        "/api/cases", data=_create_case_form(appeal_text=_SERVICE_TEXT), headers=_headers()
    ).json()["case_id"]

    resp = client.post(f"/api/cases/{case_id}/analyze", headers=_headers())

    assert resp.status_code == 409
    assert "訴願書" in resp.json()["detail"]


def test_analyze_blocked_when_a_document_check_is_inconclusive(monkeypatch):
    """matched=None(規則與 Gemini 皆判斷不出來)同樣不算確認完成,不可誤當「已放行」。"""
    monkeypatch.setattr(settings, "MOCK_DATA_DIR", str(FIXTURES_DIR))
    client = TestClient(main_module.app)
    case_id = client.post(
        "/api/cases", data=_create_case_form(appeal_text="內容含糊,規則判斷不出特徵"), headers=_headers()
    ).json()["case_id"]

    resp = client.post(f"/api/cases/{case_id}/analyze", headers=_headers())

    assert resp.status_code == 409
    assert "訴願書" in resp.json()["detail"]


def test_full_flow_create_analyze_list_get_completed_case(monkeypatch):
    monkeypatch.setattr(settings, "MOCK_DATA_DIR", str(FIXTURES_DIR))
    client = TestClient(main_module.app)

    case_id = client.post("/api/cases", data=_create_case_form(), headers=_headers()).json()["case_id"]
    analyze_resp = client.post(f"/api/cases/{case_id}/analyze", headers=_headers())
    assert analyze_resp.status_code == 200

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


def test_full_flow_inadmissible_case_skips_f2_and_uses_fixed_draft(monkeypatch):
    """不受理案(逾期,sample_b)全程走 API:F2 應為 None,F4 依§89 I③ 套用固定體例。"""
    monkeypatch.setattr(settings, "MOCK_DATA_DIR", str(FIXTURES_DIR))
    client = TestClient(main_module.app)

    case_id = client.post(
        "/api/cases",
        data=_create_case_form(appeal_text=_INADMISSIBLE_APPEAL_TEXT),
        headers=_headers(),
    ).json()["case_id"]
    client.post(f"/api/cases/{case_id}/analyze", headers=_headers())

    get_resp = client.get(f"/api/cases/{case_id}", headers=_headers())
    assert get_resp.status_code == 200
    case = get_resp.json()

    assert case["status"] == "done"
    assert case["track"] == "inadmissible"
    assert case["screening"]["passed"] is False
    assert case["screening"]["matched_clause"] == "77條第2款"
    assert case["f2"] is None  # 不受理案跳過 F2,前端據此顯示「未進行法規推薦」
    assert case["f3"][0]["result"] == "不受理"
    assert case["f4"]["draft_type"] == "不受理"
    assert case["f4"]["fact"] == ""  # 訴願法第89條第1項第3款:不受理決定得不記載事實
    assert case["f4"]["main_text"] == "訴願不受理。"  # 語料 90/90 件主文逐字相同,不交模型決定


def test_get_nonexistent_case_returns_404():
    client = TestClient(main_module.app)
    resp = client.get("/api/cases/c-notexist", headers=_headers())
    assert resp.status_code == 404


def test_source_endpoint_mock_mode_returns_text_field():
    client = TestClient(main_module.app)
    resp = client.get("/api/source", params={"key": "markdown/相關法規/不存在.md"}, headers=_headers())
    assert resp.status_code == 200
    assert "text" in resp.json()


def test_case_can_be_created_without_a_service_certificate():
    """觀念通知等案件本無送達證書,不得為了湊齊三份而擋在收案;缺槽由後續期間計算標記人工確認。"""
    client = TestClient(main_module.app)
    form = _create_case_form()
    del form["service_text"]

    resp = client.post("/api/cases", data=form, headers=_headers())

    assert resp.status_code == 200
    assert resp.json()["documents"]["service"]["matched"] is None


def test_analyze_is_not_blocked_by_an_absent_optional_slot(monkeypatch):
    """空槽沒有東西可確認(觀念通知案本無送達證書),擋在這裡等於收案放行、分析卻走不了。"""
    monkeypatch.setattr(settings, "MOCK_DATA_DIR", str(FIXTURES_DIR))
    client = TestClient(main_module.app)
    form = _create_case_form()
    del form["service_text"]
    case_id = client.post("/api/cases", data=form, headers=_headers()).json()["case_id"]

    resp = client.post(f"/api/cases/{case_id}/analyze", headers=_headers())

    assert resp.status_code == 200
