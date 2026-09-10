"""needs_review 貫穿:清單上要一眼看出哪幾件不能直接送。
要複核的案子與正常案子長得一模一樣,是這套系統最糟的失效樣態。"""
from app.models import (
    Case,
    CaseDocument,
    CaseInfo,
    DeadlineCheck,
    DocumentCheck,
    DraftResult,
    ScreeningResult,
)
from app.review import needs_review


def _info(**overrides):
    base = dict(
        appellant="王大明",
        agency="新北市政府環境保護局",
        disposition_date="114年5月16日",
        disposition_no="新北環稽字第1號",
        disposition_summary="裁處罰鍰",
        appeal_reasons=["原處分認事用法有誤"],
        case_type="廢棄物清理",
        receipt_date="114年5月28日",
        disposition_recipient="王大明",
    )
    base.update(overrides)
    return CaseInfo(**base)


def _confirmed_documents():
    return {
        slot: CaseDocument(
            slot=slot, source="text", text=f"{slot} 內容", check=DocumentCheck(matched=True, method="rule")
        )
        for slot in ("appeal", "service", "disposition")
    }


def _case(**overrides):
    base = dict(
        case_id="c-review01",
        created_at="2026-08-17T00:00:00+00:00",
        title="複核測試案",
        status="done",
        current_stage="done",
        source="text",
        input_text="內容",
        documents=_confirmed_documents(),
        f1=_info(),
        screening=ScreeningResult(passed=True, matched_clause=None, reasoning="無不受理事由"),
        deadline=DeadlineCheck(overdue=False, detail="未逾期。"),
    )
    base.update(overrides)
    return Case(**base)


def test_a_clean_case_does_not_need_review():
    assert needs_review(_case()) is False


def test_deadline_review_note_needs_review():
    case = _case(deadline=DeadlineCheck(review_note="期間未計算,在途期間卷內未載且無法由住居所查表認定"))

    assert needs_review(case) is True


def test_screening_review_note_needs_review():
    case = _case(
        screening=ScreeningResult(
            passed=False, matched_clause="77條第7款", reasoning="模型判第7款", review_note="本版不判訴願法第7款,須人工認定"
        )
    )

    assert needs_review(case) is True


def test_missing_required_field_needs_review():
    """訴願法§56 I 必要記載事項有缺漏:即使結論是「應通知補正」而非不受理,也不能直接送。"""
    case = _case(f1=_info(appeal_reasons=[]))

    assert needs_review(case) is True


def test_standing_dispute_needs_review():
    """處分相對人與訴願人不一致就是當事人適格爭點,利害關係屬價值判斷,一律人工看。"""
    case = _case(f1=_info(disposition_recipient="李小華"))

    assert needs_review(case) is True


def test_unconfirmed_document_slot_needs_review():
    documents = _confirmed_documents()
    documents["service"] = documents["service"].model_copy(
        update={"check": DocumentCheck(matched=None, method="none", note="規則判斷特徵不足")}
    )

    assert needs_review(_case(documents=documents)) is True


def test_ocr_slot_needs_review():
    """經 OCR 取得文字的槽帶 review_note,日期須人工核對原件。"""
    documents = _confirmed_documents()
    documents["service"] = documents["service"].model_copy(
        update={"ocr": True, "review_note": "本槽文字由 OCR 取得,日期須人工核對原件"}
    )

    assert needs_review(_case(documents=documents)) is True


def test_a_collecting_case_without_analysis_is_not_flagged_yet():
    """還在收案、三槽都確認過的案件不算待複核——那只是還沒分析,不是有疑義。"""
    case = _case(status="collecting", current_stage="f1", f1=None, screening=None, deadline=None)

    assert needs_review(case) is False


def test_case_list_exposes_needs_review():
    from fastapi.testclient import TestClient

    import app.main as main_module
    from app.config import settings

    client = TestClient(main_module.app)
    flagged = _case(case_id="c-review02", deadline=DeadlineCheck(review_note="期間未計算"))
    main_module.store.create(flagged)

    summaries = client.get("/api/cases", headers={"X-API-Key": settings.API_KEY}).json()

    entry = next(s for s in summaries if s["case_id"] == "c-review02")
    assert entry["needs_review"] is True


def test_an_admissible_case_whose_draft_says_inadmissible_needs_review():
    """受理案件的草稿型別只可能是駁回或撤銷;跑出「不受理」是分流與草稿自相矛盾,
    不受理那一側有 enforce_inadmissible_format 保證體例,這一側沒有,只能標出來給人看。"""
    case = _case(
        track="admissible",
        f4=DraftResult(draft_type="不受理", fact="事實", reason="理由", main_text="訴願不受理。"),
    )

    assert needs_review(case) is True


def test_an_admissible_case_with_a_consistent_draft_does_not_need_review():
    for draft_type, main_text in (("駁回", "訴願駁回。"), ("原處分撤銷", "原處分撤銷。")):
        case = _case(
            track="admissible",
            f4=DraftResult(draft_type=draft_type, fact="事實", reason="理由", main_text=main_text),
        )

        assert needs_review(case) is False, draft_type


def test_a_remand_draft_that_names_a_period_does_not_need_review():
    """訴願法§81 II:撤銷發回應指定相當期間;主文寫得出期間就沒有這一項疑義。"""
    for main_text in (
        "原處分撤銷,由原處分機關於2個月內另為適法之處分。",
        "原處分撤銷,由原處分機關於30日內另為適法之處理。",
    ):
        case = _case(
            track="admissible",
            f4=DraftResult(draft_type="撤銷另處", fact="事實", reason="理由", main_text=main_text),
        )

        assert needs_review(case) is False, main_text


def test_a_remand_draft_without_a_period_needs_review():
    case = _case(
        track="admissible",
        f4=DraftResult(
            draft_type="撤銷另處", fact="事實", reason="理由", main_text="原處分撤銷,由原處分機關另為適法之處分。"
        ),
    )

    assert needs_review(case) is True


def test_a_partial_decision_always_needs_review():
    """語料只有 1 件,主文逐標的分項是否對得上原處分的標的,系統驗不了。"""
    for track in ("inadmissible", "admissible"):
        case = _case(
            track=track,
            screening=ScreeningResult(passed=False, matched_clause="77條第8款", reasoning="限期改善部分非處分"),
            f4=DraftResult(
                draft_type="部分不受理部分駁回",
                fact="事實",
                reason="理由",
                main_text="原處分關於罰鍰部分,訴願駁回。原處分關於限期改善部分,訴願不受理。",
            ),
        )

        assert needs_review(case) is True, track


def test_an_inadmissible_case_with_an_inadmissible_draft_does_not_need_review():
    case = _case(
        track="inadmissible",
        screening=ScreeningResult(passed=False, matched_clause="77條第2款", reasoning="逾期"),
        f4=DraftResult(draft_type="不受理", fact="", reason="理由", main_text="訴願不受理。"),
    )

    assert needs_review(case) is False
