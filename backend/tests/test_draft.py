import fitz
import pytest
from pydantic import ValidationError

import app.main as main_module
from app.config import settings
from app.models import DRAFT_TYPES, Case, DraftResult, draft_types_for
from app.pdf_render import decision_plain_text


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
        # 全文在 F4 產出時就攤平寫入(見 pipeline);測試直接塞 f4,得自己補這一步
        main_module.store.update(
            case_id, {"draft_plain_text": decision_plain_text(main_module.store.get(case_id))}
        )
    return main_module.store.get(case_id)


def test_patch_draft_updates_f4_and_persists():
    from fastapi.testclient import TestClient

    client = TestClient(main_module.app)
    case = _make_case("c-draft001", with_f4=True)
    assert case.f4 is not None

    resp = client.patch(
        "/api/cases/c-draft001/draft-text",
        json={"text": "新北市政府訴願決定書 主文 訴願駁回(修改)。"},
        headers=_headers(),
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert resp.json()["version"] == 1  # 每次 PATCH 存一版,回傳版本數供前端帶下一次的 base_version

    get_resp = client.get("/api/cases/c-draft001", headers=_headers())
    assert get_resp.status_code == 200
    body = get_resp.json()
    assert body["draft_plain_text"] == "新北市政府訴願決定書 主文 訴願駁回(修改)。"
    # f4 是產生全文的素材,編輯全文不動它——draft_type 還要給印章與體例守門用
    assert body["f4"]["draft_type"] == "駁回"
    assert body["f4"]["cited_laws"] == ["廢棄物清理法#46"]


def test_patch_draft_case_not_found_returns_404():
    from fastapi.testclient import TestClient

    client = TestClient(main_module.app)
    resp = client.patch(
        "/api/cases/c-notexist/draft-text",
        json={"text": "a"},
        headers=_headers(),
    )
    assert resp.status_code == 404


def test_patch_draft_without_f4_returns_409():
    from fastapi.testclient import TestClient

    client = TestClient(main_module.app)
    _make_case("c-draft002", with_f4=False)
    resp = client.patch(
        "/api/cases/c-draft002/draft-text",
        json={"text": "a"},
        headers=_headers(),
    )
    assert resp.status_code == 409


def test_patch_draft_missing_api_key_returns_401():
    from fastapi.testclient import TestClient

    client = TestClient(main_module.app)
    _make_case("c-draft003", with_f4=True)
    resp = client.patch(
        "/api/cases/c-draft003/draft-text",
        json={"text": "a"},
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
    main_module.store.update(
        "c-draft007", {"draft_plain_text": decision_plain_text(main_module.store.get("c-draft007"))}
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
    main_module.store.update(
        "c-draft008", {"draft_plain_text": decision_plain_text(main_module.store.get("c-draft008"))}
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


@pytest.mark.parametrize("draft_type", ["撤銷另處", "部分不受理部分駁回"])
def test_draft_result_accepts_the_two_new_draft_types(draft_type):
    draft = DraftResult(draft_type=draft_type, fact="事實", reason="理由", main_text="主文")
    assert draft.draft_type == draft_type


def test_draft_result_rejects_a_typo_of_a_valid_draft_type():
    with pytest.raises(ValidationError):
        DraftResult(draft_type="撤銷另處理", fact="事實", reason="理由", main_text="主文")


# ---------- draft_types_for:受理側的值域由 schema 擋,不只靠 prompt ----------


def test_admissible_track_cannot_offer_inadmissible_as_a_draft_type():
    """prompt 早就寫明受理案三擇一,但 schema 五值全開,模型照樣挑得到「不受理」,
    產出一份 track=admissible 而草稿寫不受理的自相矛盾案件(local 模式實測 2 件)。"""
    assert "不受理" not in draft_types_for(passed=True)


def test_admissible_track_keeps_every_substantive_outcome():
    """三種實體決定都要留著,否則模型只能在更少的錯誤選項裡挑。"""
    allowed = draft_types_for(passed=True)
    for value in ("駁回", "撤銷另處", "原處分撤銷"):
        assert value in allowed


def test_partial_decision_stays_available_on_both_tracks():
    """部分不受理部分駁回本來就是「一部進入實體審查」,關在不受理側等於讓那種案永遠答不對。"""
    assert "部分不受理部分駁回" in draft_types_for(passed=True)
    assert "部分不受理部分駁回" in draft_types_for(passed=False)


def test_inadmissible_track_still_offers_every_value():
    """不受理側由 enforce_inadmissible_format 事後校正體例,值域不需在 schema 再收一次。"""
    assert set(draft_types_for(passed=False)) == set(DRAFT_TYPES)


# ---------- 決定書抬頭的紀年不得重複 ----------


def _opening_line(disposition_date: str) -> str:
    from app.models import CaseInfo
    from app.pdf_render import build_decision_blocks

    case = _make_case(f"c-open{abs(hash(disposition_date)) % 10000:04d}", with_f4=True)
    main_module.store.update(
        case.case_id,
        {
            "f1": CaseInfo(
                appellant="王大明",
                agency="新北市政府環境保護局",
                disposition_date=disposition_date,
                disposition_no="新北環稽字第1號",
                disposition_summary="裁處罰鍰",
                case_type="廢棄物清理法",
            )
        },
    )
    blocks = build_decision_blocks(main_module.store.get(case.case_id))
    return next(text for _, text in blocks if "上列訴願人因" in text)


def test_the_opening_paragraph_never_repeats_the_era_name():
    """F1 的 prompt 要求 disposition_date 保留原文寫法,公文書通常自帶「民國」/「中華民國」;
    抬頭模板又寫死一個「民國」,不脫掉就會印出「民國民國110年8月31日」。"""
    for written in ("民國110年8月31日", "中華民國110年8月31日", "中華民國110 年8 月31 日"):
        line = _opening_line(written)
        assert "民國民國" not in line
        assert "民國中華民國" not in line
        assert line.count("民國") == 1


def test_a_date_without_an_era_name_still_gets_one():
    line = _opening_line("110年8月31日")
    assert "不服原處分機關民國110年8月31日" in line
