"""交叉驗證 specs/審理歷程線性重跑與全欄位擷取.md §一時發現的既有測試缺口與邊界情境。

每個測項標明對照規格哪一句、以及它在補上之前用突變測試證實過套件不會抓到的原因。
"""
from pathlib import Path

from fastapi.testclient import TestClient

import app.main as main_module
from app.config import settings
from app.models import Case, CaseDocument, CaseInfo, DecisionHeader, DocumentCheck, DraftResult, ScreeningResult


def _headers():
    return {"X-API-Key": settings.API_KEY}


def _client():
    return TestClient(main_module.app)


def _info(**overrides) -> CaseInfo:
    base = dict(
        appellant="王大明",
        agency="彰化縣環境保護局",
        disposition_date="110年3月5日",
        disposition_no="彰環廢字第1號",
        disposition_summary="裁處罰鍰",
        case_type="廢棄物清理",
    )
    base.update(overrides)
    return CaseInfo(**base)


def _screening(**overrides) -> ScreeningResult:
    base = dict(passed=True, matched_clause=None, reasoning="無不受理事由")
    base.update(overrides)
    return ScreeningResult(**base)


# ---------- (1) GET .../file 對非 pdf 槽必須 404,不能只是「檔案剛好不存在」----------
# 突變測試:main.get_document_file 拿掉 `document.source != "pdf"` 這個判準之後,
# 既有測試 test_document_file_for_a_text_slot_returns_404 仍然綠——因為那個案子從沒落過
# PDF,404 是「檔案不存在」路徑給出的,不是「這槽不是 PDF」擋下的。這裡讓磁碟上真的躺著一份
# PDF(重傳成文字前留下的舊檔),藉此把兩種 404 的成因分開。


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


def test_document_file_for_a_text_slot_returns_404_even_if_a_stale_pdf_sits_on_disk(monkeypatch):
    monkeypatch.setattr(settings, "MOCK_DATA_DIR", str(Path(__file__).parent / "fixtures"))
    client = _client()
    original_bytes = _text_pdf_bytes(_APPEAL_TEXT)
    case_id = client.post(
        "/api/cases",
        data={
            "service_text": _SERVICE_TEXT,
            "disposition_text": _DISPOSITION_TEXT,
            "answer_text": _ANSWER_TEXT,
        },
        files={"appeal_file": ("01_訴願書.pdf", original_bytes, "application/pdf")},
        headers=_headers(),
    ).json()["case_id"]
    # 重傳同一槽改貼文字:source 變成 text,但落地目錄裡舊的 appeal.pdf 沒有被清掉
    client.patch(f"/api/cases/{case_id}/documents/appeal", data={"text": "訴願書\n訴願人:王大明"}, headers=_headers())
    assert (Path(settings.CASE_FILES_DIR) / case_id / "appeal.pdf").is_file()  # 舊檔案確實還在磁碟上

    resp = client.get(f"/api/cases/{case_id}/documents/appeal/file", headers=_headers())

    assert resp.status_code == 404  # 現在的槽是文字,不該因為磁碟上有殘留檔案就吐出舊 PDF


def _text_pdf_bytes(text: str) -> bytes:
    import fitz

    doc = fitz.open()
    page = doc.new_page()
    page.insert_textbox(fitz.Rect(40, 40, 560, 800), text, fontname="china-t", fontsize=11)
    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


# ---------- (2) PATCH /screening 在 processing 中必須 409 ----------
# 突變測試:main.override_screening 拿掉這個判準之後,整個套件仍是綠的——沒有任何既有測試
# 呼叫過「案件正在分析中時 PATCH /screening」這個組合。


def test_patch_screening_while_processing_returns_409():
    case_id = "c-guard0001"
    main_module.store.create(
        Case(
            case_id=case_id,
            created_at="2026-09-12T00:00:00+00:00",
            title="processing 擋門測試",
            status="processing",
            current_stage="screening",
            source="text",
            input_text="x",
        )
    )
    main_module.store.update(case_id, {"screening": _screening()})

    resp = _client().patch(
        f"/api/cases/{case_id}/screening",
        json={"passed": True, "matched_clause": None, "reasoning": "改一下"},
        headers=_headers(),
    )

    assert resp.status_code == 409
    assert "分析中" in resp.json()["detail"]


# ---------- 邊界 (a):舊資料形狀讀出時,全文剝表頭、f1_stale 正常 ----------


def test_legacy_shaped_case_data_is_stripped_and_not_stale_on_read():
    """status="collecting"、無 decision_header、draft_plain_text 含表頭列的舊資料,
    經 Case(**data) 讀出後全文只剩本文,f1_stale 不因為是舊資料而誤判為 True。"""
    legacy_full_text = (
        "新北市政府訴願決定書\n案　　號：1140700123\n"
        "　訴願人　王大明\n　原處分機關　彰化縣環境保護局\n"
        "上列訴願人因廢棄物清理事件，不服原處分機關民國110年3月5日彰環廢字第1號"
        "所為之處分，提起訴願一案，本府依法決定如下：\n"
        "主　文\n訴願駁回。\n\n理　由\n一、按…\n\n"
        "訴願審議委員會主任委員　蔡庭榕\n委員　陳明燦\n中華民國110年6月1日\n"
    )
    data = dict(
        case_id="c-legacy-guard",
        created_at="2026-09-12T00:00:00+00:00",
        title="舊資料案",
        status="collecting",
        current_stage="done",
        source="text",
        input_text="卷證",
        f1=_info().model_dump(),
        f4=DraftResult(draft_type="駁回", fact="事實段", reason="理由段", main_text="訴願駁回。").model_dump(),
        draft_plain_text=legacy_full_text,
    )

    case = Case(**data)

    assert case.draft_plain_text.startswith("主　文")
    assert "新北市政府訴願決定書" not in case.draft_plain_text
    assert "訴願審議委員會主任委員" not in case.draft_plain_text
    assert case.decision_header == DecisionHeader()  # 舊資料沒有這個欄位,預設空白,不臆測補值
    assert case.f1_stale is False  # 尚未起跑過下游(screening_input_f1 為 None),不算改過


# ---------- 邊界 (b):PATCH /f1 送含「未載明」的日期欄,保留原文、其餘欄正規化 ----------


def test_patch_f1_keeps_an_unparseable_date_verbatim_while_normalizing_the_rest():
    case_id = "c-guard0002"
    main_module.store.create(
        Case(
            case_id=case_id,
            created_at="2026-09-12T00:00:00+00:00",
            title="日期正規化測試",
            status="done",
            current_stage="done",
            source="text",
            input_text="x",
            f1=_info(),
        )
    )

    payload = _info(receipt_date="未載明", appeal_date="114.7.4").model_dump()
    resp = _client().patch(f"/api/cases/{case_id}/f1", json=payload, headers=_headers())

    assert resp.status_code == 200
    stored = main_module.store.get(case_id)
    assert stored.f1.receipt_date == "未載明"  # 解析不出,保留原文供前端顯示「無法辨識」
    assert stored.f1.appeal_date == "民國114年7月4日"  # 解析得出的仍要正規化


# ---------- 邊界 (c):reanalyze from=f2、status=error、f4=None——不存版本、正常起跑 ----------


def test_reanalyze_from_f2_on_an_errored_case_without_a_draft_does_not_save_a_version(monkeypatch):
    from tests.test_pipeline import StubAdmissibleProvider

    case_id = "c-guard0003"
    main_module.store.create(
        Case(
            case_id=case_id,
            created_at="2026-09-12T00:00:00+00:00",
            title="error 無草稿重跑測試",
            status="error",
            current_stage="f2",
            source="text",
            input_text="測試訴願書內容",
            error="上一輪失敗",
            f1=_info(),
            screening=_screening(),
        )
    )
    monkeypatch.setattr(main_module, "get_provider", lambda: StubAdmissibleProvider())

    resp = _client().post(f"/api/cases/{case_id}/reanalyze", json={"from": "f2"}, headers=_headers())

    assert resp.status_code == 200
    case = main_module.store.get(case_id)
    assert case.draft_versions == []  # f4 原本是 None,沒有東西可存版本
    assert case.error is None
    assert case.status == "done"  # 正常跑完,不是卡在 processing 或又落回 error


# ---------- 邊界 (d):兩次 PATCH decision-header,第二次仍成功且日期欄正規化 ----------


def test_patching_decision_header_twice_both_succeed_and_normalize_dates(monkeypatch):
    monkeypatch.setattr(settings, "MOCK_DATA_DIR", str(Path(__file__).parent / "fixtures"))
    client = _client()
    case_id = client.post(
        "/api/cases",
        data={
            "appeal_text": _APPEAL_TEXT,
            "service_text": _SERVICE_TEXT,
            "disposition_text": _DISPOSITION_TEXT,
            "answer_text": _ANSWER_TEXT,
        },
        headers=_headers(),
    ).json()["case_id"]
    base = client.get(f"/api/cases/{case_id}", headers=_headers()).json()["decision_header"]

    first = client.patch(
        f"/api/cases/{case_id}/decision-header",
        json={**base, "issued_date": "114.7.4"},
        headers=_headers(),
    )
    assert first.status_code == 200

    second = client.patch(
        f"/api/cases/{case_id}/decision-header",
        json={**base, "issued_date": "114.7.4", "decided_date": "114/07/09"},
        headers=_headers(),
    )

    assert second.status_code == 200
    stored = client.get(f"/api/cases/{case_id}", headers=_headers()).json()["decision_header"]
    assert stored["issued_date"] == "民國114年7月4日"
    assert stored["decided_date"] == "民國114年7月9日"
