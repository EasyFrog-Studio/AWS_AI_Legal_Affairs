"""POST /api/cases/{id}/reanalyze:起跑點由呼叫端明確指定(from=f1|screening|f2)。
前面的階段只影響後面的——這份測資逐項核對三種 from 各自保留 / 清除的欄位。
見 specs/審理歷程線性重跑與全欄位擷取.md §1.2。
"""
from fastapi.testclient import TestClient

import app.main as main_module
from app.config import settings
from app.models import Case, CaseInfo, DraftResult, ScreeningResult


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


def _draft(**overrides) -> DraftResult:
    base = dict(
        draft_type="駁回", fact="事實內容", reason="理由內容", main_text="訴願駁回。", cited_laws=["訴願法#77"]
    )
    base.update(overrides)
    return DraftResult(**base)


def _seed(case_id: str, status="done", **fields) -> Case:
    main_module.store.create(
        Case(
            case_id=case_id,
            created_at="2026-08-17T00:00:00+00:00",
            title="重跑契約測試案",
            status=status,
            current_stage="done",
            source="text",
            input_text="測試訴願書內容",
        )
    )
    if fields:
        main_module.store.update(case_id, fields)
    if fields.get("f4") is not None:
        # 全文在 F4 產出時就攤平寫入(見 pipeline);這裡直接塞 f4,得自己補這一步
        from app.pdf_render import decision_plain_text

        main_module.store.update(case_id, {"draft_plain_text": decision_plain_text(main_module.store.get(case_id))})
    return main_module.store.get(case_id)


# ---------- 基本契約:body 驗證與狀態擋門 ----------


def test_reanalyze_requires_a_from_field():
    _seed("c-ra0001")
    resp = _client().post("/api/cases/c-ra0001/reanalyze", json={}, headers=_headers())
    assert resp.status_code == 422


def test_reanalyze_rejects_an_invalid_from_value():
    _seed("c-ra0002")
    resp = _client().post("/api/cases/c-ra0002/reanalyze", json={"from": "f3"}, headers=_headers())
    assert resp.status_code == 422


def test_reanalyze_while_processing_returns_409_for_every_from():
    _seed("c-ra0003", status="processing")
    for stage in ("f1", "screening", "f2"):
        resp = _client().post("/api/cases/c-ra0003/reanalyze", json={"from": stage}, headers=_headers())
        assert resp.status_code == 409, stage


def test_reanalyze_nonexistent_case_returns_404():
    resp = _client().post("/api/cases/c-notexist/reanalyze", json={"from": "f1"}, headers=_headers())
    assert resp.status_code == 404


def test_reanalyze_from_screening_requires_f1():
    _seed("c-ra0004", status="done")  # f1 仍是 None

    resp = _client().post("/api/cases/c-ra0004/reanalyze", json={"from": "screening"}, headers=_headers())

    assert resp.status_code == 409


def test_reanalyze_from_f2_requires_f1_and_screening():
    _seed("c-ra0005", status="done")

    resp = _client().post("/api/cases/c-ra0005/reanalyze", json={"from": "f2"}, headers=_headers())
    assert resp.status_code == 409

    main_module.store.update("c-ra0005", {"f1": _info()})
    resp2 = _client().post("/api/cases/c-ra0005/reanalyze", json={"from": "f2"}, headers=_headers())
    assert resp2.status_code == 409  # 仍缺 screening

    main_module.store.update("c-ra0005", {"screening": _screening()})
    resp3 = _client().post("/api/cases/c-ra0005/reanalyze", json={"from": "f2"}, headers=_headers())
    assert resp3.status_code == 200  # 兩者皆備才放行


# ---------- from=f1:整條重跑,清掉所有人工快照 ----------


def test_reanalyze_from_f1_clears_every_human_snapshot_and_reruns_everything(monkeypatch):
    from tests.test_pipeline import StubAdmissibleProvider

    _seed(
        "c-ra0010",
        status="done",
        f1=_info(appellant="王大明(人工)"),
        f1_system=_info(appellant="王大明(模型)"),
        f1_edited=True,
        screening=_screening(reasoning="人工推翻"),
        screening_system=_screening(reasoning="系統原判"),
        screening_input_f1=_info(appellant="王大明(人工)"),
        retrieval_input_screening=_screening(reasoning="人工推翻"),
        f4=_draft(draft_type="撤銷另處"),
        f4_system=_draft(draft_type="駁回"),
    )
    monkeypatch.setattr(main_module, "get_provider", lambda: StubAdmissibleProvider())

    resp = _client().post("/api/cases/c-ra0010/reanalyze", json={"from": "f1"}, headers=_headers())

    assert resp.status_code == 200
    case = main_module.store.get("c-ra0010")
    assert case.f1_system is None
    assert case.f1_edited is False
    assert case.screening_system is None
    assert case.f4_system is None
    assert case.status == "done"
    # StubAdmissibleProvider 重新擷取,不是留著人工改過的舊值
    assert case.f1.appellant == "王大明"
    # 重跑完成後,兩個快照又被 _screen_and_draft/_retrieval_and_draft 寫回新的一份
    assert case.screening_input_f1 is not None
    assert case.retrieval_input_screening is not None


def test_reanalyze_from_f1_saves_the_previous_draft_as_a_version_first(monkeypatch):
    from tests.test_pipeline import StubAdmissibleProvider

    seeded = _seed("c-ra0011", status="done", f4=_draft())
    previous_text = seeded.draft_plain_text
    monkeypatch.setattr(main_module, "get_provider", lambda: StubAdmissibleProvider())

    resp = _client().post("/api/cases/c-ra0011/reanalyze", json={"from": "f1"}, headers=_headers())

    assert resp.status_code == 200
    case = main_module.store.get("c-ra0011")
    assert len(case.draft_versions) >= 1
    assert case.draft_versions[0].text == previous_text


def test_reanalyze_does_not_duplicate_the_version_when_it_already_matches(monkeypatch):
    from app.main import _version_of
    from tests.test_pipeline import StubAdmissibleProvider

    seeded = _seed("c-ra0012", status="done", f4=_draft())
    main_module.store.update("c-ra0012", {"draft_versions": [_version_of(seeded.draft_plain_text)]})
    monkeypatch.setattr(main_module, "get_provider", lambda: StubAdmissibleProvider())

    _client().post("/api/cases/c-ra0012/reanalyze", json={"from": "f1"}, headers=_headers())

    # 重跑前已經存過同一份內容,不再重複存一版
    assert len(main_module.store.get("c-ra0012").draft_versions) == 1


# ---------- from=screening:保留 f1/f1_system,清 screening 以下的快照 ----------


def test_reanalyze_from_screening_preserves_f1_but_clears_screening_snapshots(monkeypatch):
    from tests.test_pipeline import StubAdmissibleProvider

    class _NoExtractProvider(StubAdmissibleProvider):
        def extract_case_info(self, text):
            raise AssertionError("from=screening 不得重跑 F1")

    _seed(
        "c-ra0020",
        status="done",
        f1=_info(appellant="王大明(人工)"),
        f1_system=_info(appellant="王大明(模型)"),
        screening=_screening(reasoning="人工推翻"),
        screening_system=_screening(reasoning="系統原判"),
        screening_input_f1=_info(appellant="王大明(人工)"),
        retrieval_input_screening=_screening(reasoning="人工推翻"),
        f4=_draft(),
        f4_system=_draft(draft_type="撤銷另處"),
    )
    monkeypatch.setattr(main_module, "get_provider", lambda: _NoExtractProvider())

    resp = _client().post("/api/cases/c-ra0020/reanalyze", json={"from": "screening"}, headers=_headers())

    assert resp.status_code == 200
    case = main_module.store.get("c-ra0020")
    assert case.f1.appellant == "王大明(人工)"  # 保留
    assert case.f1_system.appellant == "王大明(模型)"  # 保留
    assert case.screening_system is None  # 清
    assert case.f4_system is None  # 清
    assert case.status == "done"


# ---------- from=f2:保留 f1/f1_system/screening/screening_system,只清 f4_system ----------


def test_reanalyze_from_f2_preserves_f1_and_screening_but_clears_f4_system(monkeypatch):
    from tests.test_pipeline import StubAdmissibleProvider

    class _NoScreeningProvider(StubAdmissibleProvider):
        def extract_case_info(self, text):
            raise AssertionError("from=f2 不得重跑 F1")

        def screen_admissibility(self, info, text):
            raise AssertionError("from=f2 不得重跑程序審查")

    _seed(
        "c-ra0030",
        status="done",
        f1=_info(appellant="王大明(人工)"),
        f1_system=_info(appellant="王大明(模型)"),
        screening=_screening(reasoning="人工推翻", passed=True),
        screening_system=_screening(reasoning="系統原判", passed=False, matched_clause="77條第2款"),
        f4=_draft(),
        f4_system=_draft(draft_type="撤銷另處"),
    )
    monkeypatch.setattr(main_module, "get_provider", lambda: _NoScreeningProvider())

    resp = _client().post("/api/cases/c-ra0030/reanalyze", json={"from": "f2"}, headers=_headers())

    assert resp.status_code == 200
    case = main_module.store.get("c-ra0030")
    assert case.f1.appellant == "王大明(人工)"
    assert case.f1_system.appellant == "王大明(模型)"
    assert case.screening.reasoning == "人工推翻"
    assert case.screening_system.reasoning == "系統原判"
    assert case.f4_system is None
    assert case.status == "done"


def test_reanalyze_from_f2_sets_current_stage_by_the_screening_result(monkeypatch):
    """current_stage 依 screening.passed 為 f2/f2_refs——在背景任務真的重跑之前就要先落地,
    故換掉 rerun_case 觀察 main.py 端點自己寫下的欄位。"""
    calls = []
    monkeypatch.setattr(
        main_module, "rerun_case", lambda case_id, store, provider, start: calls.append((case_id, start))
    )

    _seed("c-ra0040", status="done", f1=_info(), screening=_screening(passed=False, matched_clause="77條第2款"))
    resp = _client().post("/api/cases/c-ra0040/reanalyze", json={"from": "f2"}, headers=_headers())
    assert resp.status_code == 200
    assert main_module.store.get("c-ra0040").current_stage == "f2_refs"

    _seed("c-ra0041", status="done", f1=_info(), screening=_screening(passed=True))
    resp2 = _client().post("/api/cases/c-ra0041/reanalyze", json={"from": "f2"}, headers=_headers())
    assert resp2.status_code == 200
    assert main_module.store.get("c-ra0041").current_stage == "f2"

    assert calls == [("c-ra0040", "f2"), ("c-ra0041", "f2")]
