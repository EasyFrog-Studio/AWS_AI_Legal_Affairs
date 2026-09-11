"""承辦人推翻程序審查結論與重跑檢索。
自動判之後承辦人只做確認,那就必須有推翻的入口——否則自動判等於終局判斷。"""
from fastapi.testclient import TestClient

import app.main as main_module
from app.config import settings
from app.models import Case, DraftResult, ScreeningResult


def _headers():
    return {"X-API-Key": settings.API_KEY}


def _client():
    return TestClient(main_module.app)


def _system_screening():
    return ScreeningResult(
        passed=False,
        matched_clause="77條第2款",
        reasoning="送達生效日114年5月28日,末日114年6月27日,機關收文日114年10月31日,已逾期。",
        review_note="",
    )


def _draft():
    return DraftResult(
        draft_type="不受理", fact="", reason="本件訴願逾法定期間。", main_text="訴願不受理。", cited_laws=["訴願法#77"]
    )


def _seed(case_id: str, status="done", screening=None, with_f4=True, track="inadmissible"):
    main_module.store.create(
        Case(
            case_id=case_id,
            created_at="2026-08-17T00:00:00+00:00",
            title="推翻測試案",
            status=status,
            current_stage="done",
            track=track,
            source="text",
            input_text="測試訴願書內容",
        )
    )
    fields = {"screening": screening if screening is not None else _system_screening()}
    if with_f4:
        fields["f4"] = _draft()
    main_module.store.update(case_id, fields)
    if with_f4:
        # 全文在 F4 產出時就攤平寫入(見 pipeline);這裡直接塞 f4,得自己補這一步
        from app.pdf_render import decision_plain_text

        main_module.store.update(
            case_id, {"draft_plain_text": decision_plain_text(main_module.store.get(case_id))}
        )
    return main_module.store.get(case_id)


def test_override_keeps_the_system_verdict_and_takes_effect():
    """人改成受理:screening 換成人工結論、track 跟著翻面,系統原判搬進 screening_system——
    「有沒有被推翻過」因此變成可判斷的事實,不必另立旗標。"""
    _seed("c-ovr0001")
    client = _client()

    resp = client.patch(
        "/api/cases/c-ovr0001/screening",
        json={"passed": True, "matched_clause": None, "reasoning": "經核送達證書,送達日應為7月1日,未逾期。"},
        headers=_headers(),
    )

    assert resp.status_code == 200
    case = client.get("/api/cases/c-ovr0001", headers=_headers()).json()
    assert case["screening"]["passed"] is True
    assert case["screening_system"]["matched_clause"] == "77條第2款"
    assert case["track"] == "admissible"


def test_second_override_does_not_overwrite_the_system_verdict():
    """再改一次仍要看得到系統當初判什麼,不能被第一次的人工結論頂掉。"""
    _seed("c-ovr0002")
    client = _client()
    body = {"passed": True, "matched_clause": None, "reasoning": "第一次推翻"}
    client.patch("/api/cases/c-ovr0002/screening", json=body, headers=_headers())

    client.patch(
        "/api/cases/c-ovr0002/screening",
        json={"passed": False, "matched_clause": "77條第3款", "reasoning": "第二次推翻"},
        headers=_headers(),
    )

    case = client.get("/api/cases/c-ovr0002", headers=_headers()).json()
    assert case["screening_system"]["matched_clause"] == "77條第2款"
    assert case["screening"]["matched_clause"] == "77條第3款"


def test_override_without_a_screening_result_returns_409():
    _seed("c-ovr0003", screening=None, with_f4=False)
    main_module.store.update("c-ovr0003", {"screening": None})
    client = _client()

    resp = client.patch(
        "/api/cases/c-ovr0003/screening",
        json={"passed": True, "matched_clause": None, "reasoning": "無從推翻"},
        headers=_headers(),
    )

    assert resp.status_code == 409


def test_override_nonexistent_case_returns_404():
    resp = _client().patch(
        "/api/cases/c-notexist/screening",
        json={"passed": True, "matched_clause": None, "reasoning": "x"},
        headers=_headers(),
    )
    assert resp.status_code == 404


# ---------- POST /reanalyze ----------


def test_reanalyze_is_allowed_on_a_finished_case():
    """已跑完但要重跑是正常業務操作(推翻程序審查之後就是這個情境),不是只有 error 才能重跑。"""
    _seed("c-ovr0010", status="done")
    client = _client()

    resp = client.post("/api/cases/c-ovr0010/reanalyze", headers=_headers())

    assert resp.status_code == 200


def test_reanalyze_is_allowed_on_a_broken_case():
    _seed("c-ovr0011", status="error")

    resp = _client().post("/api/cases/c-ovr0011/reanalyze", headers=_headers())

    assert resp.status_code == 200


def test_reanalyze_while_processing_returns_409():
    _seed("c-ovr0012", status="processing")

    resp = _client().post("/api/cases/c-ovr0012/reanalyze", headers=_headers())

    assert resp.status_code == 409


def test_reanalyze_while_still_collecting_returns_409():
    """收案中的案件該走 /analyze,不是 /reanalyze。"""
    _seed("c-ovr0013", status="collecting")

    resp = _client().post("/api/cases/c-ovr0013/reanalyze", headers=_headers())

    assert resp.status_code == 409
    assert "analyze" in resp.json()["detail"]


def test_reanalyze_nonexistent_case_returns_404():
    resp = _client().post("/api/cases/c-notexist/reanalyze", headers=_headers())
    assert resp.status_code == 404


def test_reanalyze_saves_the_existing_draft_as_a_version_first():
    """否則承辦人編輯過的草稿會被無聲蓋掉。"""
    _seed("c-ovr0014", status="done")
    client = _client()

    client.post("/api/cases/c-ovr0014/reanalyze", headers=_headers())

    case = client.get("/api/cases/c-ovr0014", headers=_headers()).json()
    assert len(case["draft_versions"]) >= 1
    assert "訴願不受理。" in case["draft_versions"][0]["text"]


def test_reanalyze_after_an_override_keeps_the_human_verdict(monkeypatch):
    """曾被推翻的案件重跑時不得再呼叫 screen_admissibility——人剛剛改的判斷會被模型改回去,
    那等於推翻入口不存在。保留 f1 與 screening,自 F2/F3 起跑。"""
    from tests.test_pipeline import StubAdmissibleProvider
    from app.models import CaseInfo

    class _NoScreeningProvider(StubAdmissibleProvider):
        def extract_case_info(self, text):
            raise AssertionError("曾被推翻的案件不得重跑 F1")

        def screen_admissibility(self, info, text):
            raise AssertionError("曾被推翻的案件不得重跑程序審查")

    _seed("c-ovr0015", status="done")
    main_module.store.update(
        "c-ovr0015",
        {
            "f1": CaseInfo(
                appellant="王大明",
                agency="彰化縣環境保護局",
                disposition_date="110年3月5日",
                disposition_no="彰環廢字第1號",
                disposition_summary="裁處罰鍰",
                case_type="廢棄物清理",
            )
        },
    )
    client = _client()
    client.patch(
        "/api/cases/c-ovr0015/screening",
        json={"passed": True, "matched_clause": None, "reasoning": "人工認定未逾期"},
        headers=_headers(),
    )
    monkeypatch.setattr(main_module, "get_provider", lambda: _NoScreeningProvider())

    resp = client.post("/api/cases/c-ovr0015/reanalyze", headers=_headers())

    assert resp.status_code == 200
    case = client.get("/api/cases/c-ovr0015", headers=_headers()).json()
    assert case["status"] == "done"
    assert case["screening"]["passed"] is True  # 人工結論還在
    assert case["f2"] is not None  # 檢索確實重跑了


def test_reanalyze_from_scratch_when_never_overridden(monkeypatch):
    """沒被推翻過的案件整條自 F1 重跑。"""
    from tests.test_pipeline import StubAdmissibleProvider

    _seed("c-ovr0016", status="error")
    monkeypatch.setattr(main_module, "get_provider", lambda: StubAdmissibleProvider())
    client = _client()

    client.post("/api/cases/c-ovr0016/reanalyze", headers=_headers())

    case = client.get("/api/cases/c-ovr0016", headers=_headers()).json()
    assert case["status"] == "done"
    assert case["f1"]["appellant"] == "王大明"  # F1 重跑過
    assert case["error"] is None  # 上一輪的錯誤訊息清掉,不留在畫面上誤導


def test_reanalyze_a_finalized_case_still_works_and_keeps_the_mark(tmp_path, monkeypatch):
    """定稿不鎖:定稿後仍可重跑,定稿標記保留(它記的是「曾經定稿」這件事)。"""
    monkeypatch.setattr(settings, "FINALIZED_DIR", str(tmp_path))
    _seed("c-ovr0017", status="done")
    client = _client()
    client.post("/api/cases/c-ovr0017/finalize", headers=_headers())

    resp = client.post("/api/cases/c-ovr0017/reanalyze", headers=_headers())

    assert resp.status_code == 200
    case = client.get("/api/cases/c-ovr0017", headers=_headers()).json()
    assert case["finalized_at"]
