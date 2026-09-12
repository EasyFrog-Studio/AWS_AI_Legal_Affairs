"""f1_stale / screening_stale:案件資訊或程序審查結論改過而下游尚未依它重跑。
見 specs/審理歷程線性重跑與全欄位擷取.md §1.2、models.Case.f1_stale/screening_stale。
"""
from fastapi.testclient import TestClient

import app.main as main_module
from app.config import settings

FIXTURES_DIR = None  # 設在各測試裡,避免匯入順序問題


def _headers():
    return {"X-API-Key": settings.API_KEY}


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
    "答辯聲明：本件訴願駁回。\n"
    "理由：一、程序答辯：本件訴願為合法。二、實體答辯：違規事證明確。\n"
    "三、檢附原卷1宗，敬請察核。"
)


def _create_case_form():
    return {
        "appeal_text": _APPEAL_TEXT,
        "service_text": _SERVICE_TEXT,
        "disposition_text": _DISPOSITION_TEXT,
        "answer_text": _ANSWER_TEXT,
    }


def _analyzed_case(client, monkeypatch) -> str:
    from pathlib import Path

    monkeypatch.setattr(settings, "MOCK_DATA_DIR", str(Path(__file__).parent / "fixtures"))
    return client.post("/api/cases", data=_create_case_form(), headers=_headers()).json()["case_id"]


# ---------- f1_stale ----------


def test_freshly_run_case_is_not_stale(monkeypatch):
    """跑完未改:兩個 computed field 皆為 False。"""
    client = TestClient(main_module.app)
    case_id = _analyzed_case(client, monkeypatch)

    case = client.get(f"/api/cases/{case_id}", headers=_headers()).json()

    assert case["f1_stale"] is False
    assert case["screening_stale"] is False


def test_editing_f1_makes_it_stale(monkeypatch):
    """改 f1:f1_stale 變 True。"""
    client = TestClient(main_module.app)
    case_id = _analyzed_case(client, monkeypatch)
    case = client.get(f"/api/cases/{case_id}", headers=_headers()).json()
    info = dict(case["f1"])
    info["appellant"] = "王大明(更正)"

    client.patch(f"/api/cases/{case_id}/f1", json=info, headers=_headers())

    stored = client.get(f"/api/cases/{case_id}", headers=_headers()).json()
    assert stored["f1_stale"] is True


def test_editing_f1_back_to_the_original_value_is_no_longer_stale(monkeypatch):
    """改回原值:f1 又與 screening_input_f1 相等,f1_stale 回 False。"""
    client = TestClient(main_module.app)
    case_id = _analyzed_case(client, monkeypatch)
    original = client.get(f"/api/cases/{case_id}", headers=_headers()).json()["f1"]
    edited = dict(original)
    edited["appellant"] = "王大明(更正)"
    client.patch(f"/api/cases/{case_id}/f1", json=edited, headers=_headers())
    assert client.get(f"/api/cases/{case_id}", headers=_headers()).json()["f1_stale"] is True

    client.patch(f"/api/cases/{case_id}/f1", json=original, headers=_headers())

    stored = client.get(f"/api/cases/{case_id}", headers=_headers()).json()
    assert stored["f1_stale"] is False


def test_reanalyze_from_screening_clears_f1_stale(monkeypatch):
    """from=screening 重跑後,screening_input_f1 追上目前的 f1,f1_stale 回 False。"""
    client = TestClient(main_module.app)
    case_id = _analyzed_case(client, monkeypatch)
    case = client.get(f"/api/cases/{case_id}", headers=_headers()).json()
    info = dict(case["f1"])
    info["appellant"] = "王大明(更正)"
    client.patch(f"/api/cases/{case_id}/f1", json=info, headers=_headers())
    assert client.get(f"/api/cases/{case_id}", headers=_headers()).json()["f1_stale"] is True

    resp = client.post(f"/api/cases/{case_id}/reanalyze", json={"from": "screening"}, headers=_headers())

    assert resp.status_code == 200
    stored = client.get(f"/api/cases/{case_id}", headers=_headers()).json()
    assert stored["f1_stale"] is False


# ---------- screening_stale ----------


def test_editing_only_the_review_note_does_not_make_screening_stale():
    """只改 review_note:screening_stale 只比 (passed, matched_clause, reasoning) 三元組,不算數。"""
    from app.models import Case, ScreeningResult

    case_id = "c-stale0001"
    main_module.store.create(
        Case(
            case_id=case_id,
            created_at="2026-08-17T00:00:00+00:00",
            title="staleness 測試案",
            status="done",
            current_stage="done",
            source="text",
            input_text="x",
        )
    )
    screening = ScreeningResult(passed=True, matched_clause=None, reasoning="無不受理事由")
    main_module.store.update(case_id, {"screening": screening, "retrieval_input_screening": screening})

    client = TestClient(main_module.app)
    resp = client.patch(
        f"/api/cases/{case_id}/screening",
        json={"passed": True, "matched_clause": None, "reasoning": "無不受理事由"},
        headers=_headers(),
    )
    assert resp.status_code == 200
    # PATCH /screening 的三個 body 欄位與既有結論相同,review_note 不受影響也不比

    stored = client.get(f"/api/cases/{case_id}", headers=_headers()).json()
    assert stored["screening_stale"] is False


def test_editing_the_reasoning_makes_screening_stale():
    """改 reasoning(三元組其一):screening_stale 變 True。"""
    from app.models import Case, ScreeningResult

    case_id = "c-stale0002"
    main_module.store.create(
        Case(
            case_id=case_id,
            created_at="2026-08-17T00:00:00+00:00",
            title="staleness 測試案",
            status="done",
            current_stage="done",
            source="text",
            input_text="x",
        )
    )
    screening = ScreeningResult(passed=True, matched_clause=None, reasoning="無不受理事由")
    main_module.store.update(case_id, {"screening": screening, "retrieval_input_screening": screening})

    client = TestClient(main_module.app)
    resp = client.patch(
        f"/api/cases/{case_id}/screening",
        json={"passed": True, "matched_clause": None, "reasoning": "人工重新認定,無不受理事由"},
        headers=_headers(),
    )
    assert resp.status_code == 200

    stored = client.get(f"/api/cases/{case_id}", headers=_headers()).json()
    assert stored["screening_stale"] is True
