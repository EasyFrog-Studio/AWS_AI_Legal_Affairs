import fitz
import pytest
from pydantic import ValidationError

import app.main as main_module
from app.config import settings
from app.models import Case, DraftResult


def _headers():
    return {"X-API-Key": settings.API_KEY}


def _make_case(case_id: str, with_f4: bool) -> Case:
    case = Case(
        case_id=case_id,
        created_at="2026-08-17T00:00:00+00:00",
        title="測試案件",
        source="text",
        input_text="測試訴願書內容",
    )
    main_module.store.create(case)
    if with_f4:
        f4 = DraftResult(
            draft_type="駁回",
            fact="訴願人於民國110年間因違反廢棄物清理法遭裁處罰鍰。",
            reason="原處分認事用法並無違誤,訴願為無理由。",
            main_text="訴願駁回。",
            cited_laws=["廢棄物清理法#46"],
        )
        main_module.store.update(case_id, {"f4": f4})
    return main_module.store.get(case_id)


def test_patch_draft_updates_f4_and_persists():
    from fastapi.testclient import TestClient

    client = TestClient(main_module.app)
    case = _make_case("c-draft001", with_f4=True)
    assert case.f4 is not None

    resp = client.patch(
        "/api/cases/c-draft001/draft",
        json={"fact": "修改後事實內容。", "reason": "修改後理由內容。", "main_text": "訴願駁回(修改)。"},
        headers=_headers(),
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert resp.json()["version"] == 1  # 每次 PATCH 存一版,回傳版本數供前端帶下一次的 base_version

    get_resp = client.get("/api/cases/c-draft001", headers=_headers())
    assert get_resp.status_code == 200
    body = get_resp.json()
    assert body["f4"]["fact"] == "修改後事實內容。"
    assert body["f4"]["reason"] == "修改後理由內容。"
    assert body["f4"]["main_text"] == "訴願駁回(修改)。"
    # draft_type / cited_laws 應保留原值,未被 patch 覆寫
    assert body["f4"]["draft_type"] == "駁回"
    assert body["f4"]["cited_laws"] == ["廢棄物清理法#46"]


def test_patch_draft_case_not_found_returns_404():
    from fastapi.testclient import TestClient

    client = TestClient(main_module.app)
    resp = client.patch(
        "/api/cases/c-notexist/draft",
        json={"fact": "a", "reason": "b", "main_text": "c"},
        headers=_headers(),
    )
    assert resp.status_code == 404


def test_patch_draft_without_f4_returns_409():
    from fastapi.testclient import TestClient

    client = TestClient(main_module.app)
    _make_case("c-draft002", with_f4=False)
    resp = client.patch(
        "/api/cases/c-draft002/draft",
        json={"fact": "a", "reason": "b", "main_text": "c"},
        headers=_headers(),
    )
    assert resp.status_code == 409


def test_patch_draft_missing_api_key_returns_401():
    from fastapi.testclient import TestClient

    client = TestClient(main_module.app)
    _make_case("c-draft003", with_f4=True)
    resp = client.patch(
        "/api/cases/c-draft003/draft",
        json={"fact": "a", "reason": "b", "main_text": "c"},
    )
    assert resp.status_code == 401


def test_get_draft_pdf_returns_pdf_with_chinese_content():
    from fastapi.testclient import TestClient

    client = TestClient(main_module.app)
    _make_case("c-draft004", with_f4=True)

    resp = client.get("/api/cases/c-draft004/draft.pdf", headers=_headers())
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content.startswith(b"%PDF")
    assert "filename*=UTF-8''" in resp.headers["content-disposition"]

    doc = fitz.open(stream=resp.content, filetype="pdf")
    extracted = "".join(page.get_text() for page in doc)
    assert extracted.strip() != ""
    assert "新北市政府訴願決定書" in extracted
    assert "訴願人於民國110年間因違反廢棄物清理法遭裁處罰鍰" in extracted
    assert "訴願駁回" in extracted


def test_get_draft_pdf_inadmissible_omits_empty_fact_section():
    """不受理決定得不記載事實,PDF 不應留下沒有內文的「事　實」標題。"""
    from fastapi.testclient import TestClient

    client = TestClient(main_module.app)
    case = Case(
        case_id="c-draft007",
        created_at="2026-08-17T00:00:00+00:00",
        title="不受理測試案件",
        source="text",
        input_text="測試訴願書內容",
    )
    main_module.store.create(case)
    main_module.store.update(
        "c-draft007",
        {
            "f4": DraftResult(
                draft_type="不受理",
                fact="",
                reason="本件訴願逾法定期間,依訴願法第77條第2款規定應為不受理之決定。",
                main_text="訴願不受理。",
                cited_laws=[],
            )
        },
    )

    resp = client.get("/api/cases/c-draft007/draft.pdf", headers=_headers())
    assert resp.status_code == 200

    doc = fitz.open(stream=resp.content, filetype="pdf")
    extracted = "".join(page.get_text() for page in doc)
    assert "主　文" in extracted
    assert "理　由" in extracted
    assert "事　實" not in extracted  # 內文為空,整段不輸出
    assert "訴願不受理" in extracted
    assert "本件訴願逾法定期間" in extracted


def test_get_draft_pdf_admissible_keeps_heading_of_empty_section():
    """受理案缺欄仍要印標題:少一欄的決定書讀起來仍然通順,靜默略過會讓它更難發現。"""
    from fastapi.testclient import TestClient

    client = TestClient(main_module.app)
    case = Case(
        case_id="c-draft008",
        created_at="2026-08-17T00:00:00+00:00",
        title="受理案缺理由欄",
        source="text",
        input_text="測試訴願書內容",
    )
    main_module.store.create(case)
    main_module.store.update(
        "c-draft008",
        {
            "f4": DraftResult(
                draft_type="駁回",
                fact="訴願人於民國110年間遭裁處罰鍰。",
                reason="",  # 模型漏產理由欄
                main_text="訴願駁回。",
                cited_laws=[],
            )
        },
    )

    resp = client.get("/api/cases/c-draft008/draft.pdf", headers=_headers())
    assert resp.status_code == 200

    doc = fitz.open(stream=resp.content, filetype="pdf")
    extracted = "".join(page.get_text() for page in doc)
    assert "事　實" in extracted
    assert "理　由" in extracted  # 內文雖空,標題仍在


def test_get_draft_pdf_case_not_found_returns_404():
    from fastapi.testclient import TestClient

    client = TestClient(main_module.app)
    resp = client.get("/api/cases/c-notexist/draft.pdf", headers=_headers())
    assert resp.status_code == 404


def test_get_draft_pdf_without_f4_returns_409():
    from fastapi.testclient import TestClient

    client = TestClient(main_module.app)
    _make_case("c-draft005", with_f4=False)
    resp = client.get("/api/cases/c-draft005/draft.pdf", headers=_headers())
    assert resp.status_code == 409


def test_get_draft_pdf_missing_api_key_returns_401():
    from fastapi.testclient import TestClient

    client = TestClient(main_module.app)
    _make_case("c-draft006", with_f4=True)
    resp = client.get("/api/cases/c-draft006/draft.pdf")
    assert resp.status_code == 401


def test_decision_skeleton_returns_the_same_layout_as_the_pdf_with_editable_slots():
    """網站上的決定書與 PDF 共用同一份版面定義,前端只把 slot 換成可編輯欄位。"""
    from fastapi.testclient import TestClient

    client = TestClient(main_module.app)
    _make_case("c-draft020", with_f4=True)

    resp = client.get("/api/cases/c-draft020/decision-skeleton", headers=_headers())
    assert resp.status_code == 200
    blocks = resp.json()["blocks"]

    kinds = [b["kind"] for b in blocks]
    assert kinds[0] == "title"
    assert [b["text"] for b in blocks if b["kind"] == "slot"] == ["main_text", "fact", "reason"]
    joined = "".join(b["text"] for b in blocks)
    assert "新北市政府訴願決定書" in joined
    assert "訴願審議委員會主任委員" in joined
    assert "如不服本決定" in joined


def test_decision_skeleton_case_not_found_returns_404():
    from fastapi.testclient import TestClient

    client = TestClient(main_module.app)
    resp = client.get("/api/cases/c-notexist/decision-skeleton", headers=_headers())
    assert resp.status_code == 404


def test_decision_skeleton_without_f4_returns_409():
    from fastapi.testclient import TestClient

    client = TestClient(main_module.app)
    _make_case("c-draft021", with_f4=False)
    resp = client.get("/api/cases/c-draft021/decision-skeleton", headers=_headers())
    assert resp.status_code == 409


@pytest.mark.parametrize("draft_type", ["撤銷另處", "部分不受理部分駁回"])
def test_draft_result_accepts_the_two_new_draft_types(draft_type):
    draft = DraftResult(draft_type=draft_type, fact="事實", reason="理由", main_text="主文")
    assert draft.draft_type == draft_type


def test_draft_result_rejects_a_typo_of_a_valid_draft_type():
    with pytest.raises(ValidationError):
        DraftResult(draft_type="撤銷另處理", fact="事實", reason="理由", main_text="主文")
