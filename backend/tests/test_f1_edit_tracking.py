"""F1 的兩件事:訴願書的「事實」要抽出來,以及承辦人改過哪幾欄要看得出來。

改過的欄位若與模型抽的長得一模一樣,承辦人下次回頭看就分不出哪些字是自己確認過的、
哪些還是模型講的——那正是這套系統最該避免的失效(同 screening_system 擋的那一種)。
"""
import app.main as main_module
from app.models import Case, CaseInfo


def _headers():
    from app.config import settings

    return {"X-API-Key": settings.API_KEY}


def _info(**overrides) -> CaseInfo:
    base = dict(
        appellant="王大明",
        agency="新北市政府環境保護局",
        disposition_date="114年5月16日",
        disposition_no="新北環稽字第1號",
        disposition_summary="裁處罰鍰",
        appeal_facts=["訴願人於114年6月27日在三峽區遭稽查"],
        appeal_reasons=["原處分認事用法有誤"],
        case_type="廢棄物清理法",
        receipt_date="114年5月28日",
        service_date="114年5月28日",
    )
    base.update(overrides)
    return CaseInfo(**base)


def _case(case_id: str, **overrides) -> Case:
    fields = dict(
        case_id=case_id,
        created_at="2026-08-17T00:00:00+00:00",
        title="測試案件",
        status="done",
        current_stage="done",
        source="text",
        input_text="訴願書內容",
        f1=_info(),
    )
    fields.update(overrides)
    main_module.store.create(Case(**fields))
    return main_module.store.get(case_id)


# ---------- 訴願書的事實 ----------


def test_case_info_carries_the_appellant_stated_facts():
    """訴願法§56 I⑤ 是「訴願之事實及理由」一款兩件事;只抽理由等於漏掉訴願人自己講的經過。"""
    info = _info()
    assert info.appeal_facts == ["訴願人於114年6月27日在三峽區遭稽查"]


def test_appeal_facts_default_to_empty_for_older_samples():
    """舊樣本與舊測資沒有這一欄,不補也要驗得過。"""
    info = CaseInfo(
        appellant="王大明",
        agency="機關",
        disposition_date="114年5月16日",
        disposition_no="字第1號",
        disposition_summary="裁處罰鍰",
        case_type="廢棄物清理法",
    )
    assert info.appeal_facts == []


# ---------- 承辦人改過哪幾欄 ----------


def test_the_first_edit_keeps_the_model_output_as_f1_system():
    from fastapi.testclient import TestClient

    client = TestClient(main_module.app)
    _case("c-f1e0001")
    edited = _info(service_date="114年6月1日").model_dump()

    resp = client.patch("/api/cases/c-f1e0001/f1", json=edited, headers=_headers())

    assert resp.status_code == 200
    stored = main_module.store.get("c-f1e0001")
    assert stored.f1.service_date == "民國114年6月1日"  # PATCH /f1 經 normalize_case_info_dates 正規化
    # 模型原本抽的留著,前端才畫得出「這一欄被改過」
    assert stored.f1_system is not None
    assert stored.f1_system.service_date == "114年5月28日"


def test_a_second_edit_does_not_overwrite_the_model_output():
    """第二次改若把 f1_system 換成第一次改完的值,第一次改過的欄位就不再標記為已修改。"""
    from fastapi.testclient import TestClient

    client = TestClient(main_module.app)
    _case("c-f1e0002")
    client.patch(
        "/api/cases/c-f1e0002/f1", json=_info(service_date="114年6月1日").model_dump(), headers=_headers()
    )

    client.patch(
        "/api/cases/c-f1e0002/f1",
        json=_info(service_date="114年6月1日", disposition_no="新北環稽字第2號").model_dump(),
        headers=_headers(),
    )

    system = main_module.store.get("c-f1e0002").f1_system
    assert system.service_date == "114年5月28日"
    assert system.disposition_no == "新北環稽字第1號"


def test_an_unedited_case_has_no_system_snapshot():
    """沒改過就不該有快照——有快照本身就是「被改過」的判準之一,不能人人都有。"""
    assert _case("c-f1e0003").f1_system is None


def test_rerunning_clears_the_snapshot_so_the_new_output_is_not_marked_as_edited():
    """重跑會重新擷取,新結果不是承辦人改的;留著舊快照會讓整份都標成已修改。"""
    from fastapi.testclient import TestClient

    client = TestClient(main_module.app)
    _case("c-f1e0004", status="done")
    client.patch(
        "/api/cases/c-f1e0004/f1", json=_info(service_date="114年6月1日").model_dump(), headers=_headers()
    )
    assert main_module.store.get("c-f1e0004").f1_system is not None

    client.post("/api/cases/c-f1e0004/reanalyze", json={"from": "f1"}, headers=_headers())

    assert main_module.store.get("c-f1e0004").f1_system is None
